use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;

use serde_json::{json, Map, Value};

pub const SOCKET_VAR: &str = "HERDR_SOCKET_PATH";

#[derive(Debug)]
pub struct ApiError {
    pub code: String,
    pub message: String,
}

#[derive(Debug)]
pub enum CallError {
    Transport(String),
    Api(ApiError),
}

impl CallError {
    pub fn code(&self) -> Option<&str> {
        match self {
            CallError::Api(e) => Some(e.code.as_str()),
            CallError::Transport(_) => None,
        }
    }
}

impl std::fmt::Display for CallError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CallError::Transport(m) => write!(f, "{}", m),
            CallError::Api(e) => write!(f, "{} ({})", e.message, e.code),
        }
    }
}

pub struct Client {
    socket: PathBuf,
}

impl Client {
    pub fn new(socket: PathBuf) -> Client {
        Client { socket }
    }

    pub fn from_env_of(env: &crate::config::Environment) -> Result<Client, String> {
        match env.get(SOCKET_VAR) {
            Some(v) if !v.is_empty() => Ok(Client::new(PathBuf::from(v))),
            _ => Err(format!("{} is not set", SOCKET_VAR)),
        }
    }

    pub fn call(&self, method: &str, params: Value) -> Result<Value, CallError> {
        let request = json!({"id": format!("pick-project:{}", method),
                             "method": method,
                             "params": params});

        let stream = UnixStream::connect(&self.socket).map_err(|e| {
            CallError::Transport(format!("cannot reach {}: {}", self.socket.display(), e))
        })?;
        let mut writer = &stream;
        writer
            .write_all(format!("{}\n", request).as_bytes())
            .and_then(|()| writer.flush())
            .map_err(|e| CallError::Transport(format!("cannot send {}: {}", method, e)))?;

        let mut line = String::new();
        BufReader::new(&stream).read_line(&mut line).map_err(|e| {
            CallError::Transport(format!("cannot read the answer to {}: {}", method, e))
        })?;
        if line.trim().is_empty() {
            return Err(CallError::Transport(format!(
                "the server closed the connection without answering {}",
                method
            )));
        }

        let answer: Value = serde_json::from_str(&line).map_err(|e| {
            CallError::Transport(format!("the answer to {} is not JSON: {}", method, e))
        })?;

        if let Some(err) = answer.get("error") {
            return Err(CallError::Api(ApiError {
                code: string_at(err, "code").unwrap_or_default(),
                message: string_at(err, "message")
                    .unwrap_or_else(|| format!("{} failed with no message", method)),
            }));
        }
        match answer.get("result") {
            Some(result) => Ok(result.clone()),
            None => Err(CallError::Transport(format!(
                "the answer to {} carries neither a result nor an error",
                method
            ))),
        }
    }
}

fn string_at(value: &Value, key: &str) -> Option<String> {
    value.get(key)?.as_str().map(|s| s.to_string())
}

pub fn params(pairs: Vec<(&str, Value)>) -> Value {
    let mut map = Map::new();
    for (key, value) in pairs {
        if !value.is_null() {
            map.insert(key.to_string(), value);
        }
    }
    Value::Object(map)
}

#[derive(Debug, Clone, PartialEq)]
pub struct Workspace {
    pub workspace_id: String,
    pub label: String,
    pub focused: bool,
    pub agent_status: String,
    pub linked_worktree: bool,
}

pub fn workspaces(client: &Client) -> Vec<Workspace> {
    let Ok(result) = client.call("workspace.list", json!({})) else {
        return Vec::new();
    };
    let listed = result
        .get("workspaces")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    listed
        .iter()
        .filter_map(|w| {
            Some(Workspace {
                workspace_id: string_at(w, "workspace_id")?,
                label: string_at(w, "label").unwrap_or_default(),
                focused: w.get("focused").and_then(Value::as_bool).unwrap_or(false),
                agent_status: string_at(w, "agent_status").unwrap_or_else(|| "unknown".to_string()),
                linked_worktree: w
                    .get("worktree")
                    .and_then(|t| t.get("is_linked_worktree"))
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
            })
        })
        .collect()
}

pub fn open_workspaces(client: &Client, home_label: &str) -> (Option<Workspace>, Vec<Workspace>) {
    let listed = workspaces(client);
    let home_at = listed.iter().position(|w| w.label == home_label);
    match home_at {
        Some(at) => {
            let mut rest = listed;
            let home = rest.remove(at);
            (Some(home), rest)
        }
        None => (None, listed),
    }
}

fn opened_workspace_id(result: &Value) -> Option<String> {
    result
        .get("workspace")
        .and_then(|w| string_at(w, "workspace_id"))
}

pub fn worktree_open(
    client: &Client,
    cwd: &str,
    path: &str,
    label: &str,
) -> Result<String, CallError> {
    let result = client.call(
        "worktree.open",
        json!({"cwd": cwd, "path": path, "label": label, "focus": false}),
    )?;
    opened_workspace_id(&result).ok_or_else(|| {
        CallError::Transport("worktree.open answered without a workspace id".to_string())
    })
}

pub fn workspace_create(client: &Client, cwd: &str, label: &str) -> Result<String, CallError> {
    let result = client.call(
        "workspace.create",
        json!({"cwd": cwd, "label": label, "focus": false}),
    )?;
    opened_workspace_id(&result).ok_or_else(|| {
        CallError::Transport("workspace.create answered without a workspace id".to_string())
    })
}

pub fn workspace_close(client: &Client, workspace_id: &str) -> Result<(), CallError> {
    client
        .call("workspace.close", json!({"workspace_id": workspace_id}))
        .map(|_| ())
}

pub fn workspace_focus(client: &Client, workspace_id: &str) -> Result<(), CallError> {
    client
        .call("workspace.focus", json!({"workspace_id": workspace_id}))
        .map(|_| ())
}

pub fn plugin_root(client: &Client, plugin_id: &str) -> Option<String> {
    let result = client
        .call("plugin.list", json!({"plugin_id": plugin_id}))
        .ok()?;
    result
        .get("plugins")
        .and_then(Value::as_array)?
        .iter()
        .find_map(|p| string_at(p, "plugin_root").filter(|r| !r.is_empty()))
}

pub fn notify(client: &Client, body: &str) {
    let _ = client.call(
        "notification.show",
        json!({"title": "project finder", "body": body}),
    );
}
