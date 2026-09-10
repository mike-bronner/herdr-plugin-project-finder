use std::collections::BTreeMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::Instant;

use crate::api::{self, Client};
use crate::config::{
    herdr_config_path, home_label, read_sources, resolve_root, resolve_settings,
    worktree_locations, Environment, HerdrConfig,
};
use crate::discover::{
    debug_line, human_age, labelled, now, order_rows, repo_name, repos, row_kind, touched_at,
};
use crate::layout::{hand_over, resolve_layout};
use crate::picker::Entry;
use crate::plan::{pick_focus, plan};
use crate::theme::{herdr_rejects_theme, resolve_theme, Theme};

pub const EXTRA_PATH_DIRS: [&str; 2] = ["/opt/homebrew/bin", "/usr/local/bin"];

pub type Chooser<'a> =
    &'a mut dyn FnMut(Vec<Entry>, &Theme, &Environment) -> std::io::Result<Option<Vec<PathBuf>>>;

#[derive(Debug, PartialEq)]
pub enum Fatal {
    NoRepos(String),
    NoServer(String),
    NotOpened(String),
    NoTerminal(String),
}

impl Fatal {
    pub fn message(&self) -> &str {
        match self {
            Fatal::NoRepos(m) | Fatal::NoServer(m) | Fatal::NotOpened(m) | Fatal::NoTerminal(m) => {
                m
            }
        }
    }
}

#[derive(Debug, Default, PartialEq)]
pub struct Outcome {
    pub cancelled: bool,
    pub closed: Vec<String>,
    pub created: Vec<String>,
    pub focused: Option<String>,
}

pub fn with_extra_path(env: Environment) -> Environment {
    let current = env.get("PATH").unwrap_or("").to_string();
    let mut dirs: Vec<String> = Vec::new();
    for dir in EXTRA_PATH_DIRS {
        if Path::new(dir).is_dir() && !current.split(':').any(|d| d == dir) {
            dirs.push(dir.to_string());
        }
    }
    if dirs.is_empty() {
        return env;
    }
    dirs.push(current);
    env.overridden("PATH", &dirs.join(":"))
}

pub fn herdr_binary(env: &Environment) -> Option<String> {
    if let Some(path) = env.get("HERDR_BIN_PATH").filter(|p| !p.is_empty()) {
        return Some(path.to_string());
    }
    crate::layout::which(env, "herdr").map(|p| p.to_string_lossy().to_string())
}

pub fn run(
    env: &Environment,
    own_root: &Path,
    out: &mut dyn Write,
    choose: Chooser,
) -> Result<Outcome, Fatal> {
    let sources = read_sources(env, own_root);
    let settings = resolve_settings(env, &sources);
    let child_env = env.overlaid(&settings.env_file);
    for complaint in &settings.complaints {
        let _ = writeln!(out, "picker: {}", complaint);
    }

    let herdr_config = HerdrConfig::read(&herdr_config_path(env));
    let (worktree_dirs, worktree_roots) = worktree_locations(&herdr_config, env);
    let root = resolve_root(env, &settings);

    let started = Instant::now();
    let (paths, counts) = repos(&root, &worktree_dirs, &worktree_roots);
    if settings.debug {
        let _ = out.write_all(
            debug_line(
                started.elapsed().as_secs_f64() * 1000.0,
                paths.len(),
                counts,
            )
            .as_bytes(),
        );
    }

    if paths.is_empty() {
        return Err(Fatal::NoRepos(format!(
            "No git repositories found under {}",
            root.display()
        )));
    }

    let client = Client::from_env_of(env)
        .map_err(|why| Fatal::NoServer(format!("the picker cannot reach Herdr: {}", why)))?;

    let home = home_label(&settings);
    let (_, open_ws) = api::open_workspaces(&client, &home);
    let herdr_bin = herdr_binary(env);
    let theme = resolve_theme(&herdr_config, &|field| {
        herdr_rejects_theme(herdr_bin.as_deref(), field)
    });

    let rows = labelled(&paths);
    let (head, rest) = order_rows(&rows, &open_ws, &mut |path| {
        touched_at(path, &worktree_dirs)
    });

    let stamp = now();
    let mut entries: Vec<Entry> = Vec::new();
    for (row, selected) in head
        .iter()
        .map(|r| (r, true))
        .chain(rest.iter().map(|r| (r, false)))
    {
        entries.push(Entry {
            label: row.label.clone(),
            path: row.path.clone(),
            repo: repo_name(&row.path),
            kind: row_kind(&row.path),
            age: human_age(row.touched, stamp),
            status: open_ws
                .iter()
                .find(|w| w.label == row.label)
                .map(|w| w.agent_status.clone()),
            selected,
        });
    }
    let path_of: BTreeMap<String, PathBuf> = entries
        .iter()
        .map(|e| (e.label.clone(), e.path.clone()))
        .collect();
    let label_of: BTreeMap<PathBuf, String> = entries
        .iter()
        .map(|e| (e.path.clone(), e.label.clone()))
        .collect();

    let chosen = match choose(entries, &theme, env) {
        Ok(Some(chosen)) => chosen,
        Ok(None) => {
            return Ok(Outcome {
                cancelled: true,
                ..Outcome::default()
            })
        }
        Err(e) => {
            return Err(Fatal::NoTerminal(format!(
                "the picker could not draw itself: {}",
                e
            )))
        }
    };

    let selected: Vec<String> = chosen
        .iter()
        .filter_map(|p| label_of.get(p).cloned())
        .collect();

    let (home, open_ws) = api::open_workspaces(&client, &home);
    let steps = plan(&selected, &open_ws);

    let mut closed: Vec<String> = Vec::new();
    for workspace in &steps.to_close {
        if api::workspace_close(&client, &workspace.workspace_id).is_ok() {
            closed.push(workspace.workspace_id.clone());
        }
    }

    let resolved = resolve_layout(
        !steps.to_create.is_empty(),
        settings.layout.as_deref(),
        env,
        &|plugin_id| api::plugin_root(&client, plugin_id),
    );
    if let Some(warning) = &resolved.warning {
        let _ = writeln!(out, "picker: {}", warning);
        api::notify(&client, warning);
    }

    let mut created: Vec<String> = Vec::new();
    for label in &steps.to_create {
        let Some(path) = path_of.get(label) else {
            continue;
        };
        let Some(workspace_id) = open_project(&client, label, path) else {
            return Err(Fatal::NotOpened(format!(
                "failed to create workspace for {}",
                path.display()
            )));
        };
        if let Some(argv) = &resolved.argv {
            let _ = hand_over(argv, &workspace_id, &child_env);
        }
        created.push(workspace_id);
    }

    let focused = open_ws
        .iter()
        .find(|w| w.focused)
        .map(|w| w.workspace_id.clone());
    let surviving: Vec<String> = open_ws
        .iter()
        .filter(|w| selected.contains(&w.label) && !closed.contains(&w.workspace_id))
        .map(|w| w.workspace_id.clone())
        .collect();
    let target = pick_focus(
        &created,
        &closed,
        focused.as_deref(),
        &surviving,
        home.as_ref().map(|w| w.workspace_id.as_str()),
    );
    if let Some(target) = &target {
        let _ = api::workspace_focus(&client, target);
    }

    Ok(Outcome {
        cancelled: false,
        closed,
        created,
        focused: target,
    })
}

pub fn open_project(client: &Client, label: &str, path: &Path) -> Option<String> {
    let repo = crate::discover::parent_repo(path)
        .or_else(|| path.join(".git").is_dir().then(|| path.to_path_buf()));
    if let Some(repo) = repo {
        if let Ok(id) = api::worktree_open(
            client,
            &repo.to_string_lossy(),
            &path.to_string_lossy(),
            label,
        ) {
            return Some(id);
        }
    }
    api::workspace_create(client, &path.to_string_lossy(), label).ok()
}
