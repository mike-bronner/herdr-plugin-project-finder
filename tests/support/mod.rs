#![allow(dead_code)]

use std::collections::{HashMap, HashSet};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};

use pick_project::api::Workspace;
use pick_project::config::Environment;

pub const LAUNCHD_PATH: &str = "/usr/bin:/bin:/usr/sbin:/sbin";

pub struct TempDir {
    path: PathBuf,
}

static NEXT_DIR: AtomicU32 = AtomicU32::new(0);

impl TempDir {
    pub fn new() -> TempDir {
        let n = NEXT_DIR.fetch_add(1, Ordering::SeqCst);
        let path = PathBuf::from(format!(
            "/private/tmp/pick-project-t{}-{}",
            std::process::id(),
            n
        ));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).expect("cannot make the temporary directory");
        TempDir { path }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn join(&self, name: &str) -> PathBuf {
        self.path.join(name)
    }

    pub fn dir(&self, name: &str) -> PathBuf {
        let path = self.join(name);
        std::fs::create_dir_all(&path).expect("cannot make the directory");
        path
    }

    pub fn write(&self, name: &str, body: &str) -> PathBuf {
        let path = self.join(name);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).expect("cannot make the parent directory");
        }
        std::fs::write(&path, body).expect("cannot write the file");
        path
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

pub fn make_repo(path: &Path) -> PathBuf {
    std::fs::create_dir_all(path.join(".git")).expect("cannot make the repo");
    path.to_path_buf()
}

pub fn make_worktree(path: &Path, gitdir: &str) -> PathBuf {
    std::fs::create_dir_all(path).expect("cannot make the worktree");
    std::fs::write(path.join(".git"), format!("gitdir: {}\n", gitdir))
        .expect("cannot write the pointer");
    path.to_path_buf()
}

pub fn touch(path: &Path, when: u64) {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).expect("cannot make the parent directory");
    }
    std::fs::write(path, "x").expect("cannot write the file");
    set_mtime(path, when);
}

pub fn set_mtime(path: &Path, when: u64) {
    use std::time::{Duration, UNIX_EPOCH};
    let file = std::fs::File::open(path).expect("cannot open the path to stamp it");
    let times = std::fs::FileTimes::new().set_modified(UNIX_EPOCH + Duration::from_secs(when));
    file.set_times(times).expect("cannot set the mtime");
}

pub fn workspace(label: &str, id: &str) -> Value {
    json!({"workspace_id": id, "label": label, "focused": false,
           "agent_status": "idle", "number": 1, "pane_count": 1,
           "tab_count": 1, "active_tab_id": "t1"})
}

pub fn workspace_with(label: &str, id: &str, focused: bool, status: &str) -> Value {
    json!({"workspace_id": id, "label": label, "focused": focused,
           "agent_status": status, "number": 1, "pane_count": 1,
           "tab_count": 1, "active_tab_id": "t1"})
}

pub fn workspace_in_repo(label: &str, id: &str, repo_root: &str, linked: bool) -> Value {
    let mut row = workspace(label, id);
    row["worktree"] = json!({"repo_key": format!("{}/.git", repo_root),
                             "repo_name": basename(repo_root),
                             "repo_root": repo_root,
                             "checkout_path": repo_root,
                             "is_linked_worktree": linked});
    row
}

fn basename(path: &str) -> String {
    path.rsplit('/').next().unwrap_or(path).to_string()
}

pub fn open(label: &str, id: &str) -> Workspace {
    Workspace {
        workspace_id: id.to_string(),
        label: label.to_string(),
        focused: false,
        agent_status: "idle".to_string(),
        linked_worktree: false,
    }
}

pub fn open_worktree(label: &str, id: &str) -> Workspace {
    Workspace {
        linked_worktree: true,
        ..open(label, id)
    }
}

#[derive(Clone, Default)]
pub struct Script {
    pub workspaces: Vec<Value>,
    pub plugins: Vec<Value>,
    pub fail: Vec<(String, String)>,
    pub fail_at: Vec<(String, String, u32)>,
    pub list_result: Option<Value>,
}

impl Script {
    pub fn open(mut self, workspaces: Vec<Value>) -> Script {
        self.workspaces = workspaces;
        self
    }

    pub fn plugins(mut self, plugins: Vec<Value>) -> Script {
        self.plugins = plugins;
        self
    }

    pub fn failing(mut self, method: &str, code: &str) -> Script {
        self.fail.push((method.to_string(), code.to_string()));
        self
    }

    pub fn failing_at(mut self, method: &str, code: &str, nth: u32) -> Script {
        self.fail_at
            .push((method.to_string(), code.to_string(), nth));
        self
    }

    pub fn listing(mut self, result: Value) -> Script {
        self.list_result = Some(result);
        self
    }
}

pub struct Stub {
    socket: PathBuf,
    recorded: Arc<Mutex<Vec<Value>>>,
    stop: Arc<AtomicBool>,
    _dir: TempDir,
}

impl Stub {
    pub fn start(script: Script) -> Stub {
        let dir = TempDir::new();
        let socket = dir.join("herdr.sock");
        let listener = UnixListener::bind(&socket).expect("cannot bind the stub socket");
        let recorded = Arc::new(Mutex::new(Vec::new()));
        let stop = Arc::new(AtomicBool::new(false));

        let thread_log = Arc::clone(&recorded);
        let thread_stop = Arc::clone(&stop);
        std::thread::spawn(move || {
            let opened = AtomicU32::new(0);
            let shut = Mutex::new(HashSet::new());
            let calls = Mutex::new(HashMap::new());
            for stream in listener.incoming() {
                if thread_stop.load(Ordering::SeqCst) {
                    break;
                }
                let Ok(stream) = stream else { break };
                serve(&stream, &script, &thread_log, &opened, &shut, &calls);
            }
        });

        Stub {
            socket,
            recorded,
            stop,
            _dir: dir,
        }
    }

    pub fn socket(&self) -> &Path {
        &self.socket
    }

    pub fn client(&self) -> pick_project::api::Client {
        pick_project::api::Client::new(self.socket.to_path_buf())
    }

    pub fn requests(&self) -> Vec<Value> {
        self.recorded.lock().unwrap().clone()
    }

    pub fn methods(&self) -> Vec<String> {
        self.requests()
            .iter()
            .filter_map(|r| r.get("method")?.as_str().map(|s| s.to_string()))
            .collect()
    }

    pub fn params_for(&self, method: &str) -> Vec<Value> {
        self.requests()
            .iter()
            .filter(|r| r.get("method").and_then(Value::as_str) == Some(method))
            .map(|r| r.get("params").cloned().unwrap_or(Value::Null))
            .collect()
    }

    pub fn changing(&self) -> Vec<String> {
        self.methods()
            .into_iter()
            .filter(|m| {
                matches!(
                    m.as_str(),
                    "workspace.create" | "workspace.close" | "workspace.focus" | "worktree.open"
                )
            })
            .collect()
    }
}

impl Drop for Stub {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        let _ = UnixStream::connect(&self.socket);
    }
}

fn serve(
    stream: &UnixStream,
    script: &Script,
    log: &Arc<Mutex<Vec<Value>>>,
    opened: &AtomicU32,
    shut: &Mutex<HashSet<String>>,
    calls: &Mutex<HashMap<String, u32>>,
) {
    let mut line = String::new();
    if BufReader::new(stream).read_line(&mut line).is_err() || line.trim().is_empty() {
        return;
    }
    let Ok(request) = serde_json::from_str::<Value>(&line) else {
        return;
    };
    let method = request
        .get("method")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let params = request.get("params").cloned().unwrap_or(json!({}));

    log.lock()
        .unwrap()
        .push(json!({"method": method, "params": params}));

    let id = request.get("id").cloned().unwrap_or(json!("stub"));
    let nth = {
        let mut calls = calls.lock().unwrap();
        let seen = calls.entry(method.clone()).or_insert(0);
        *seen += 1;
        *seen
    };
    let answer = answer_for(&method, &params, script, opened, shut, nth, &id);
    let mut out = stream;
    let _ = out.write_all(format!("{}\n", answer).as_bytes());
    let _ = out.flush();
}

fn linked_worktrees_still_open(script: &Script, target: &str, shut: &HashSet<String>) -> bool {
    let Some(row) = script
        .workspaces
        .iter()
        .find(|w| w["workspace_id"] == json!(target))
    else {
        return false;
    };
    if row["worktree"]["is_linked_worktree"] == json!(true) {
        return false;
    }
    let root = row["worktree"]["repo_root"].clone();
    if root.is_null() {
        return false;
    }
    script.workspaces.iter().any(|w| {
        w["worktree"]["is_linked_worktree"] == json!(true)
            && w["worktree"]["repo_root"] == root
            && !shut.contains(w["workspace_id"].as_str().unwrap_or(""))
    })
}

fn answer_for(
    method: &str,
    params: &Value,
    script: &Script,
    opened: &AtomicU32,
    shut: &Mutex<HashSet<String>>,
    nth: u32,
    id: &Value,
) -> Value {
    let fail = |code: &str| {
        json!({"id": id, "error": {"code": code,
               "message": format!("stub refused {}", method)}})
    };
    let ok = |result: Value| json!({"id": id, "result": result});

    if let Some((_, code)) = script.fail.iter().find(|(m, _)| m == method) {
        return fail(code);
    }
    if let Some((_, code, _)) = script
        .fail_at
        .iter()
        .find(|(m, _, at)| m == method && *at == nth)
    {
        return fail(code);
    }

    match method {
        "workspace.list" => ok(script
            .list_result
            .clone()
            .unwrap_or_else(|| json!({"type": "workspace_list", "workspaces": script.workspaces}))),
        "workspace.create" | "worktree.open" => {
            let n = opened.fetch_add(1, Ordering::SeqCst) + 1;
            let label = params.get("label").cloned().unwrap_or(json!(""));
            ok(json!({"type": "workspace_created",
                      "workspace": {"workspace_id": format!("new{}", n),
                                    "label": label}}))
        }
        "workspace.close" => {
            let target = params
                .get("workspace_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let mut shut = shut.lock().unwrap();
            if linked_worktrees_still_open(script, &target, &shut) {
                return json!({"id": id,
                    "error": {"code": "workspace_group_close_required",
                              "message": "workspace has linked worktree workspaces; \
                                          use --group (close_group=true in the API) \
                                          to close the group"}});
            }
            shut.insert(target.clone());
            ok(json!({"type": "workspace_closed", "workspace_id": target}))
        }
        "workspace.focus" => ok(json!({"type": "workspace_focused",
                                       "workspace_id": params.get("workspace_id")})),
        "plugin.list" => ok(json!({"type": "plugin_list", "plugins": script.plugins})),
        "notification.show" => ok(json!({"type": "notification_show", "shown": false})),
        _ => fail("unhandled_by_stub"),
    }
}

pub fn env_for(stub: &Stub, home: &Path, pairs: &[(&str, &str)]) -> Environment {
    let mut all: Vec<(String, String)> = vec![
        ("PATH".to_string(), LAUNCHD_PATH.to_string()),
        ("HOME".to_string(), home.to_string_lossy().to_string()),
        (
            "HERDR_SOCKET_PATH".to_string(),
            stub.socket().to_string_lossy().to_string(),
        ),
    ];
    all.extend(pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())));
    let borrowed: Vec<(&str, &str)> = all.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
    Environment::from_pairs(&borrowed)
}

pub fn bare_env(pairs: &[(&str, &str)]) -> Environment {
    let mut all: Vec<(&str, &str)> = vec![("PATH", LAUNCHD_PATH), ("HOME", "/private/tmp")];
    all.extend(pairs.iter().copied());
    Environment::from_pairs(&all)
}

pub fn labels(workspaces: &[Workspace]) -> Vec<String> {
    workspaces.iter().map(|w| w.label.clone()).collect()
}

pub fn names(paths: &[PathBuf]) -> HashSet<String> {
    paths
        .iter()
        .map(|p| p.to_string_lossy().to_string())
        .collect()
}

pub fn repo_source() -> String {
    let mut text = String::new();
    for entry in std::fs::read_dir(concat!(env!("CARGO_MANIFEST_DIR"), "/src")).unwrap() {
        let path = entry.unwrap().path();
        if path.extension().map(|e| e == "rs").unwrap_or(false) {
            text.push_str(&std::fs::read_to_string(&path).unwrap());
        }
    }
    text
}

static PTY_OPEN: Mutex<()> = Mutex::new(());

pub struct Pty {
    master: std::fs::File,
    slave: std::fs::File,
    seen: Vec<u8>,
}

impl Pty {
    pub fn new() -> Pty {
        use std::os::fd::FromRawFd;

        let _held = PTY_OPEN.lock().unwrap_or_else(|e| e.into_inner());
        unsafe {
            let fd = libc::posix_openpt(libc::O_RDWR | libc::O_NOCTTY);
            assert!(fd >= 0, "cannot open a pseudo terminal");
            assert_eq!(libc::grantpt(fd), 0, "cannot grant the pseudo terminal");
            assert_eq!(libc::unlockpt(fd), 0, "cannot unlock the pseudo terminal");
            let name = libc::ptsname(fd);
            assert!(!name.is_null(), "the pseudo terminal has no name");
            let path = std::ffi::CStr::from_ptr(name).to_string_lossy().to_string();
            let master = std::fs::File::from_raw_fd(fd);
            let slave = std::fs::OpenOptions::new()
                .read(true)
                .write(true)
                .open(&path)
                .expect("cannot open the terminal side of the pair");
            Pty {
                master,
                slave,
                seen: Vec::new(),
            }
        }
    }

    pub fn resize(&self, rows: u16, cols: u16) {
        use std::os::fd::AsRawFd;

        let size = libc::winsize {
            ws_row: rows,
            ws_col: cols,
            ws_xpixel: 0,
            ws_ypixel: 0,
        };
        unsafe {
            assert_eq!(
                libc::ioctl(self.master.as_raw_fd(), libc::TIOCSWINSZ, &size),
                0,
                "cannot size the pseudo terminal"
            );
        }
    }

    pub fn attach(&self) -> std::process::Stdio {
        std::process::Stdio::from(self.slave.try_clone().expect("cannot clone the terminal"))
    }

    fn unblock(&self) {
        use std::os::fd::AsRawFd;

        unsafe {
            let flags = libc::fcntl(self.master.as_raw_fd(), libc::F_GETFL);
            libc::fcntl(
                self.master.as_raw_fd(),
                libc::F_SETFL,
                flags | libc::O_NONBLOCK,
            );
        }
    }

    pub fn wait_for(&mut self, ready: impl Fn(&str) -> bool) {
        use std::io::Read;
        use std::time::{Duration, Instant};

        self.unblock();
        let deadline = Instant::now() + Duration::from_secs(30);
        let mut chunk = [0u8; 4096];
        loop {
            match self.master.read(&mut chunk) {
                Ok(0) => break,
                Ok(n) => self.seen.extend_from_slice(&chunk[..n]),
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(10))
                }
                Err(_) => break,
            }
            if ready(&String::from_utf8_lossy(&self.seen)) {
                return;
            }
            assert!(
                Instant::now() < deadline,
                "the terminal never showed what the test waited for: {:?}",
                String::from_utf8_lossy(&self.seen)
            );
        }
        panic!(
            "the terminal closed before it showed what the test waited for: {:?}",
            String::from_utf8_lossy(&self.seen)
        );
    }

    pub fn read_until_quiet(self, child: &mut std::process::Child) -> String {
        use std::io::Read;
        use std::time::{Duration, Instant};

        self.unblock();
        let Pty {
            mut master,
            slave,
            mut seen,
        } = self;
        drop(slave);

        let deadline = Instant::now() + Duration::from_secs(30);
        let quiet = Duration::from_millis(300);
        let mut chunk = [0u8; 4096];
        let mut last = Instant::now();
        loop {
            match master.read(&mut chunk) {
                Ok(0) => break,
                Ok(n) => {
                    seen.extend_from_slice(&chunk[..n]);
                    last = Instant::now();
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    let ended = child.try_wait().expect("cannot check the child").is_some();
                    if ended && last.elapsed() >= quiet {
                        break;
                    }
                    assert!(
                        Instant::now() < deadline,
                        "something is still writing to the terminal: {:?}",
                        String::from_utf8_lossy(&seen)
                    );
                    std::thread::sleep(Duration::from_millis(10));
                }
                Err(_) => break,
            }
        }
        String::from_utf8_lossy(&seen).to_string()
    }
}

pub fn signal(pid: u32, number: i32) {
    unsafe {
        assert_eq!(
            libc::kill(-(pid as i32), number),
            0,
            "cannot signal {}",
            pid
        );
    }
}

pub fn manifest_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

pub fn read_repo_file(name: &str) -> String {
    std::fs::read_to_string(manifest_dir().join(name))
        .unwrap_or_else(|_| panic!("cannot read {}", name))
}

#[derive(Clone, Default)]
pub struct Recorder {
    written: Arc<Mutex<Vec<u8>>>,
}

impl Recorder {
    pub fn new() -> Recorder {
        Recorder::default()
    }

    pub fn text(&self) -> String {
        String::from_utf8_lossy(&self.written.lock().unwrap()).to_string()
    }
}

impl Write for Recorder {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.written.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
