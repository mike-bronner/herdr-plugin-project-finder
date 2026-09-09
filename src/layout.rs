use std::path::{Path, PathBuf};

use crate::config::{Environment, LAYOUT_VAR};

pub const WORKSPACE_TOKEN: &str = "{workspace}";

pub fn split_command(line: &str) -> Result<Vec<String>, String> {
    let mut args: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut started = false;
    let mut quote: Option<char> = None;
    let mut chars = line.chars();

    while let Some(c) = chars.next() {
        match quote {
            Some(q) => {
                if c == q {
                    quote = None;
                } else if c == '\\' && q == '"' {
                    match chars.next() {
                        Some(next) => current.push(next),
                        None => return Err("the command line ends in a backslash".to_string()),
                    }
                } else {
                    current.push(c);
                }
            }
            None => {
                if c == '"' || c == '\'' {
                    quote = Some(c);
                    started = true;
                } else if c == '\\' {
                    match chars.next() {
                        Some(next) => {
                            current.push(next);
                            started = true;
                        }
                        None => return Err("the command line ends in a backslash".to_string()),
                    }
                } else if c.is_whitespace() {
                    if started {
                        args.push(std::mem::take(&mut current));
                        started = false;
                    }
                } else {
                    current.push(c);
                    started = true;
                }
            }
        }
    }
    if quote.is_some() {
        return Err("the command line has an unbalanced quote".to_string());
    }
    if started {
        args.push(current);
    }
    Ok(args)
}

pub fn substitute_plugin(arg: &str, root_of: &dyn Fn(&str) -> Option<String>) -> (String, Vec<String>) {
    let mut out = String::new();
    let mut missing = Vec::new();
    let mut rest = arg;
    loop {
        let Some(at) = rest.find("{plugin:") else {
            out.push_str(rest);
            return (out, missing);
        };
        let after = &rest[at + "{plugin:".len()..];
        let Some(end) = after.find('}') else {
            out.push_str(rest);
            return (out, missing);
        };
        let id = &after[..end];
        if id.is_empty() || id.contains(char::is_whitespace) || id.contains('{') {
            out.push_str(&rest[..at + "{plugin:".len()]);
            rest = after;
            continue;
        }
        out.push_str(&rest[..at]);
        match root_of(id) {
            Some(root) => out.push_str(&root),
            None => {
                missing.push(id.to_string());
                out.push_str(&rest[at..at + "{plugin:".len() + end + 1]);
            }
        }
        rest = &after[end + 1..];
    }
}

pub fn which(env: &Environment, command: &str) -> Option<PathBuf> {
    if command.contains('/') {
        let path = PathBuf::from(command);
        return is_executable(&path).then_some(path);
    }
    for dir in env.get("PATH").unwrap_or("").split(':') {
        if dir.is_empty() {
            continue;
        }
        let candidate = Path::new(dir).join(command);
        if is_executable(&candidate) {
            return Some(candidate);
        }
    }
    None
}

fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path)
        .map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

#[derive(Debug, Default, PartialEq)]
pub struct ResolvedLayout {
    pub argv: Option<Vec<String>>,
    pub warning: Option<String>,
}

pub fn resolve_layout(
    creating: bool,
    setting: Option<&str>,
    env: &Environment,
    root_of: &dyn Fn(&str) -> Option<String>,
) -> ResolvedLayout {
    if !creating {
        return ResolvedLayout::default();
    }
    let setting = setting.unwrap_or("").trim();
    if setting.is_empty() {
        return ResolvedLayout::default();
    }

    let argv = match split_command(setting) {
        Ok(argv) if !argv.is_empty() => argv,
        _ => {
            return ResolvedLayout {
                argv: None,
                warning: Some(format!(
                    "{} is not a command line I can read, so new workspaces open with one bare pane: {}",
                    LAYOUT_VAR, setting
                )),
            }
        }
    };

    let mut missing: Vec<String> = Vec::new();
    let argv: Vec<String> = argv
        .iter()
        .map(|arg| {
            let (expanded, gone) = substitute_plugin(arg, root_of);
            missing.extend(gone);
            expanded
        })
        .collect();
    if let Some(first) = missing.first() {
        return ResolvedLayout {
            argv: None,
            warning: Some(format!(
                "{} names the plugin {}, which Herdr has no checkout for, so new workspaces \
                 open with one bare pane. Install that plugin, or point the setting somewhere else.",
                LAYOUT_VAR, first
            )),
        };
    }

    match which(env, &argv[0]) {
        Some(command) => {
            let mut resolved = vec![command.to_string_lossy().to_string()];
            resolved.extend(argv.iter().skip(1).cloned());
            ResolvedLayout {
                argv: Some(resolved),
                warning: None,
            }
        }
        None => ResolvedLayout {
            argv: None,
            warning: Some(format!(
                "{} names a command that cannot be run, so new workspaces open with one bare pane: {}",
                LAYOUT_VAR, argv[0]
            )),
        },
    }
}

pub fn for_workspace(argv: &[String], workspace_id: &str) -> Vec<String> {
    argv.iter()
        .map(|arg| arg.replace(WORKSPACE_TOKEN, workspace_id))
        .collect()
}

pub fn hand_over(argv: &[String], workspace_id: &str, env: &Environment) -> std::io::Result<()> {
    use std::os::unix::process::CommandExt;
    let argv = for_workspace(argv, workspace_id);
    let mut command = std::process::Command::new(&argv[0]);
    command
        .args(&argv[1..])
        .env_clear()
        .envs(env.without_plugin_vars())
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    unsafe {
        command.pre_exec(|| {
            libc_setsid();
            Ok(())
        });
    }
    command.spawn().map(|_| ())
}

fn libc_setsid() {
    extern "C" {
        fn setsid() -> i32;
    }
    unsafe {
        setsid();
    }
}
