mod support;

use std::path::{Path, PathBuf};
use std::process::Command;

use support::*;

fn manifest() -> toml::Table {
    read_repo_file("herdr-plugin.toml")
        .parse::<toml::Table>()
        .expect("herdr-plugin.toml is not valid TOML")
}

fn rust_files() -> Vec<PathBuf> {
    let mut found = Vec::new();
    for dir in ["src", "tests", "tests/support"] {
        let Ok(entries) = std::fs::read_dir(manifest_dir().join(dir)) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().map(|e| e == "rs").unwrap_or(false) {
                found.push(path);
            }
        }
    }
    found
}

#[test]
fn the_manifest_parses_as_toml() {
    manifest();
}

#[test]
fn a_typo_in_the_manifest_is_really_caught() {
    let broken = format!(
        "{}\nname = \"unterminated\n",
        read_repo_file("herdr-plugin.toml")
    );
    assert!(broken.parse::<toml::Table>().is_err());
}

#[test]
fn the_herdr_floor_stays_where_it_was_set() {
    assert_eq!(
        manifest()["min_herdr_version"].as_str(),
        Some("0.9.0"),
        "the floor is a decision, not a detail"
    );
}

#[test]
fn the_pane_is_dispatched_through_the_shim_not_a_build_artifact() {
    let parsed = manifest();
    let panes = parsed["panes"].as_array().unwrap();
    let command: Vec<&str> = panes[0]["command"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap())
        .collect();
    assert_eq!(command, vec!["sh", "bin/pick-project"]);
    assert!(
        !command.iter().any(|a| a.contains("target/")),
        "a build-artifact path breaks the moment the profile changes"
    );
}

#[test]
fn the_manifest_declares_a_build_step_so_a_github_install_shows_one() {
    let parsed = manifest();
    let build = parsed["build"].as_array().unwrap();
    let command: Vec<&str> = build[0]["command"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap())
        .collect();
    assert_eq!(command, vec!["sh", "bin/build"]);
}

#[test]
fn the_manifest_rebuilds_at_server_start_so_a_linked_plugin_is_covered_too() {
    let parsed = manifest();
    let startup = parsed["startup"]
        .as_array()
        .expect("herdr-plugin.toml declares no startup command");
    let command: Vec<&str> = startup[0]["command"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap())
        .collect();
    assert_eq!(command, vec!["sh", "bin/build"]);
    assert!(
        !command.iter().any(|a| a.contains("target/")),
        "a build-artifact path breaks the moment the profile changes"
    );
}

#[test]
fn the_startup_rebuild_never_paints_a_spinner_into_the_server_log() {
    let dir = TempDir::new();
    let root = fake_root(&dir, true);
    let with_cargo = noisy_cargo(&dir, CHATTY);

    let run = run_build(&root, &[("PATH", &with_cargo)]);

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert!(
        !run.stderr.contains('\u{1b}'),
        "the startup path paints escape sequences into the log: {:?}",
        run.stderr
    );
    assert!(
        !run.stderr.contains(SPINNER_MESSAGE),
        "the startup path draws the popup's message: {:?}",
        run.stderr
    );
}

#[test]
fn no_file_in_the_repo_names_a_release_artifact_path() {
    for name in ["herdr-plugin.toml", "README.md", "defaults.toml"] {
        assert!(
            !read_repo_file(name).contains("target/release"),
            "{} names a build artifact",
            name
        );
    }
}

#[test]
fn the_manifest_and_the_crate_agree_on_the_version() {
    let parsed = read_repo_file("Cargo.toml").parse::<toml::Table>().unwrap();
    let crate_version = parsed["package"]["version"].as_str().unwrap().to_string();
    assert_eq!(manifest()["version"].as_str(), Some(crate_version.as_str()));
}

#[test]
fn the_shipped_defaults_keep_the_comments_that_are_their_interface() {
    let text = read_repo_file("defaults.toml");
    let comments = text
        .lines()
        .filter(|l| l.trim_start().starts_with('#'))
        .count();
    assert!(
        comments >= 20,
        "defaults.toml is edited by hand, so its comments are its interface: {} left",
        comments
    );
}

#[test]
fn no_rust_source_file_carries_a_comment() {
    for path in rust_files() {
        let text = std::fs::read_to_string(&path).unwrap();
        for (n, line) in text.lines().enumerate() {
            let trimmed = line.trim_start();
            assert!(
                !(trimmed.starts_with("//") || trimmed.starts_with("/*")),
                "{}:{} carries a comment: {}",
                path.display(),
                n + 1,
                line
            );
            if let Some(at) = line.find("//") {
                assert!(
                    line[..at].contains('"') || line[..at].contains(':'),
                    "{}:{} carries a trailing comment: {}",
                    path.display(),
                    n + 1,
                    line
                );
            }
        }
    }
}

#[test]
fn every_rust_source_file_is_covered_by_that_guard() {
    let found: Vec<String> = rust_files()
        .iter()
        .filter_map(|p| p.file_name().map(|n| n.to_string_lossy().to_string()))
        .collect();
    for name in [
        "main.rs",
        "lib.rs",
        "app.rs",
        "picker.rs",
        "ui.rs",
        "mod.rs",
    ] {
        assert!(
            found.contains(&name.to_string()),
            "{} was not scanned",
            name
        );
    }
}

fn fake_root(dir: &TempDir, real_build: bool) -> PathBuf {
    let root = dir.dir("plugin");
    std::fs::create_dir_all(root.join("bin")).unwrap();
    std::fs::create_dir_all(root.join("src")).unwrap();
    std::fs::create_dir_all(root.join("target/release")).unwrap();
    std::fs::copy(
        manifest_dir().join("bin/pick-project"),
        root.join("bin/pick-project"),
    )
    .unwrap();
    std::fs::copy(
        manifest_dir().join("herdr-plugin.toml"),
        root.join("herdr-plugin.toml"),
    )
    .unwrap();
    if real_build {
        std::fs::copy(manifest_dir().join("bin/build"), root.join("bin/build")).unwrap();
    }
    std::fs::write(root.join("Cargo.toml"), "").unwrap();
    std::fs::write(root.join("Cargo.lock"), "").unwrap();
    std::fs::write(root.join("src/main.rs"), "").unwrap();
    root
}

fn executable(path: &Path, body: &str) {
    use std::os::unix::fs::PermissionsExt;
    std::fs::write(path, body).unwrap();
    let mut perms = std::fs::metadata(path).unwrap().permissions();
    perms.set_mode(0o755);
    std::fs::set_permissions(path, perms).unwrap();
}

struct Shim {
    status: i32,
    stdout: String,
    stderr: String,
}

fn run_shim(root: &Path, extra: &[(&str, &str)]) -> Shim {
    let mut command = Command::new("/bin/sh");
    command
        .arg(root.join("bin/pick-project"))
        .env_clear()
        .env("PATH", LAUNCHD_PATH)
        .env("HOME", "/private/tmp")
        .env("HERDR_PLUGIN_ROOT", root);
    for (key, value) in extra {
        command.env(key, value);
    }
    let out = command.output().expect("cannot run the shim");
    Shim {
        status: out.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&out.stdout).to_string(),
        stderr: String::from_utf8_lossy(&out.stderr).to_string(),
    }
}

fn stamp(path: &Path, when: u64) {
    set_mtime(path, when);
}

#[test]
fn the_shim_runs_the_binary_and_builds_nothing_when_it_is_current() {
    let dir = TempDir::new();
    let root = fake_root(&dir, false);
    executable(&root.join("bin/build"), "#!/bin/sh\necho BUILT >&2\n");
    executable(
        &root.join("target/release/pick-project"),
        "#!/bin/sh\necho RAN\n",
    );
    stamp(&root.join("src/main.rs"), 1000);
    stamp(&root.join("src"), 1000);
    stamp(&root.join("Cargo.toml"), 1000);
    stamp(&root.join("Cargo.lock"), 1000);
    stamp(&root.join("target/release/pick-project"), 2000);

    let run = run_shim(&root, &[]);
    assert_eq!(run.status, 0, "{}", run.stderr);
    assert_eq!(run.stdout, "RAN\n");
    assert!(!run.stderr.contains("BUILT"), "{}", run.stderr);
}

#[test]
fn the_shim_rebuilds_when_a_source_file_is_newer_than_the_binary() {
    let dir = TempDir::new();
    let root = fake_root(&dir, false);
    executable(&root.join("bin/build"), "#!/bin/sh\necho BUILT >&2\n");
    executable(
        &root.join("target/release/pick-project"),
        "#!/bin/sh\necho RAN\n",
    );
    stamp(&root.join("target/release/pick-project"), 1000);
    stamp(&root.join("src/main.rs"), 2000);

    let run = run_shim(&root, &[]);
    assert_eq!(run.status, 0, "{}", run.stderr);
    assert!(run.stderr.contains("BUILT"), "{}", run.stderr);
    assert_eq!(run.stdout, "RAN\n", "it still runs afterwards");
}

#[test]
fn the_shim_rebuilds_when_the_manifest_or_the_lockfile_moves() {
    for changed in ["Cargo.toml", "Cargo.lock"] {
        let dir = TempDir::new();
        let root = fake_root(&dir, false);
        executable(&root.join("bin/build"), "#!/bin/sh\necho BUILT >&2\n");
        executable(&root.join("target/release/pick-project"), "#!/bin/sh\n");
        stamp(&root.join("target/release/pick-project"), 1000);
        stamp(&root.join("src/main.rs"), 500);
        stamp(&root.join("src"), 500);
        stamp(&root.join("Cargo.toml"), 500);
        stamp(&root.join("Cargo.lock"), 500);
        stamp(&root.join(changed), 2000);
        assert!(run_shim(&root, &[]).stderr.contains("BUILT"), "{}", changed);
    }
}

#[test]
fn the_shim_builds_when_there_is_no_binary_at_all() {
    let dir = TempDir::new();
    let root = fake_root(&dir, false);
    executable(
        &root.join("bin/build"),
        "#!/bin/sh\nprintf '#!/bin/sh\\necho RAN\\n' > \"$HERDR_PLUGIN_ROOT/target/release/pick-project\"\nchmod +x \"$HERDR_PLUGIN_ROOT/target/release/pick-project\"\n",
    );
    let run = run_shim(&root, &[]);
    assert_eq!(run.status, 0, "{}", run.stderr);
    assert_eq!(run.stdout, "RAN\n");
}

#[test]
fn a_failed_build_runs_the_binary_already_there_and_says_it_may_be_stale() {
    let dir = TempDir::new();
    let root = fake_root(&dir, false);
    executable(&root.join("bin/build"), "#!/bin/sh\nexit 1\n");
    executable(
        &root.join("target/release/pick-project"),
        "#!/bin/sh\necho RAN\n",
    );
    stamp(&root.join("target/release/pick-project"), 1000);
    stamp(&root.join("src/main.rs"), 2000);

    let run = run_shim(&root, &[]);
    assert_eq!(run.status, 0, "{}", run.stderr);
    assert_eq!(run.stdout, "RAN\n");
    assert!(run.stderr.contains("may be stale"), "{}", run.stderr);
}

#[test]
fn a_failed_build_with_no_binary_at_all_refuses_and_names_the_path() {
    let dir = TempDir::new();
    let root = fake_root(&dir, false);
    executable(&root.join("bin/build"), "#!/bin/sh\nexit 1\n");
    let run = run_shim(&root, &[]);
    assert_eq!(run.status, 1);
    assert!(
        run.stderr.contains("target/release/pick-project"),
        "{}",
        run.stderr
    );
}

#[test]
fn the_shim_hands_its_arguments_to_the_binary() {
    let dir = TempDir::new();
    let root = fake_root(&dir, false);
    executable(&root.join("bin/build"), "#!/bin/sh\n");
    executable(
        &root.join("target/release/pick-project"),
        "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
    );
    stamp(&root.join("src/main.rs"), 1000);
    stamp(&root.join("src"), 1000);
    stamp(&root.join("Cargo.toml"), 1000);
    stamp(&root.join("Cargo.lock"), 1000);
    stamp(&root.join("target/release/pick-project"), 2000);

    let mut command = Command::new("/bin/sh");
    let out = command
        .arg(root.join("bin/pick-project"))
        .args(["--one", "two words"])
        .env_clear()
        .env("PATH", LAUNCHD_PATH)
        .env("HOME", "/private/tmp")
        .env("HERDR_PLUGIN_ROOT", &root)
        .output()
        .unwrap();
    assert_eq!(String::from_utf8_lossy(&out.stdout), "--one\ntwo words\n");
}

fn run_build(root: &Path, extra: &[(&str, &str)]) -> Shim {
    let mut command = Command::new("/bin/sh");
    command
        .arg(root.join("bin/build"))
        .env_clear()
        .env("PATH", LAUNCHD_PATH)
        .env("HOME", "/private/tmp")
        .env("HERDR_PLUGIN_ROOT", root);
    for (key, value) in extra {
        command.env(key, value);
    }
    let out = command.output().expect("cannot run the build script");
    Shim {
        status: out.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&out.stdout).to_string(),
        stderr: String::from_utf8_lossy(&out.stderr).to_string(),
    }
}

fn fake_cargo(dir: &TempDir, rel: &str, log: &Path) -> PathBuf {
    let path = dir.join(rel);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    executable(
        &path,
        &format!(
            "#!/bin/sh\nprintf '%s\\n%s\\n' \"$0\" \"$PATH\" > '{}'\n",
            log.to_string_lossy()
        ),
    );
    path
}

#[test]
fn the_build_script_prepends_the_cargo_directory_to_the_path_it_builds_under() {
    let dir = TempDir::new();
    let root = fake_root(&dir, true);
    let log = dir.join("cargo-log");
    let cargo = fake_cargo(&dir, "rustup/bin/cargo", &log);
    let with_cargo = format!(
        "{}:{}",
        cargo.parent().unwrap().to_string_lossy(),
        LAUNCHD_PATH
    );

    let run = run_build(&root, &[("PATH", &with_cargo)]);
    assert_eq!(run.status, 0, "{}", run.stderr);
    let recorded = std::fs::read_to_string(&log).unwrap();
    let mut lines = recorded.lines();
    assert_eq!(lines.next().unwrap(), cargo.to_string_lossy());
    let path = lines.next().unwrap();
    assert!(
        path.starts_with(&cargo.parent().unwrap().to_string_lossy().to_string()),
        "cargo execs rustc from its own directory, so that directory must lead the PATH: {}",
        path
    );
}

#[test]
fn the_build_script_finds_cargo_off_the_path_when_the_launchd_path_hides_it() {
    let dir = TempDir::new();
    let root = fake_root(&dir, true);
    let log = dir.join("cargo-log");
    let cargo = fake_cargo(&dir, "elsewhere/cargo", &log);

    let run = run_build(&root, &[("CARGO", cargo.to_str().unwrap())]);
    assert_eq!(run.status, 0, "{}", run.stderr);
    let recorded = std::fs::read_to_string(&log).unwrap();
    assert!(
        recorded.starts_with(&cargo.to_string_lossy().to_string()),
        "{}",
        recorded
    );
}

#[test]
fn the_build_script_prefers_cargo_on_the_path_over_the_named_fallbacks() {
    let dir = TempDir::new();
    let root = fake_root(&dir, true);
    let wanted = dir.join("wanted-log");
    let ignored = dir.join("ignored-log");
    let on_path = fake_cargo(&dir, "onpath/cargo", &wanted);
    let fallback = fake_cargo(&dir, "fallback/cargo", &ignored);
    let with_cargo = format!(
        "{}:{}",
        on_path.parent().unwrap().to_string_lossy(),
        LAUNCHD_PATH
    );

    run_build(
        &root,
        &[("PATH", &with_cargo), ("CARGO", fallback.to_str().unwrap())],
    );
    assert!(wanted.exists(), "the cargo on the PATH must win");
    assert!(!ignored.exists());
}

#[test]
fn the_build_script_searches_for_cargo_in_the_measured_order() {
    let script = read_repo_file("bin/build");
    let order = [
        "${CARGO:-}",
        "${CARGO_HOME:-$HOME/.cargo}/bin/cargo",
        "$HOME/.cargo/bin/cargo",
        "/opt/homebrew/opt/rustup/bin/cargo",
        "/opt/homebrew/bin/cargo",
        "/usr/local/opt/rustup/bin/cargo",
        "/usr/local/bin/cargo",
    ];
    let mut at = script
        .find("command -v cargo")
        .expect("the PATH is searched first");
    for candidate in order {
        let next = script[at..]
            .find(candidate)
            .unwrap_or_else(|| panic!("bin/build never names {}", candidate));
        at += next + candidate.len();
    }
}

#[test]
fn the_build_script_carries_the_message_for_a_machine_with_no_toolchain() {
    let script = read_repo_file("bin/build");
    assert!(script.contains("cargo not found"), "{}", script);
    assert!(script.contains("then run"), "{}", script);
    assert!(script.contains("cargo build --release"), "{}", script);
    assert!(script.contains("$PLUGIN_ROOT"), "{}", script);
}

#[test]
fn a_failing_cargo_is_reported_rather_than_silently_producing_nothing() {
    let dir = TempDir::new();
    let root = fake_root(&dir, true);
    let cargo_dir = dir.dir("bin");
    executable(&cargo_dir.join("cargo"), "#!/bin/sh\nexit 3\n");
    let with_cargo = format!("{}:{}", cargo_dir.to_string_lossy(), LAUNCHD_PATH);

    let run = run_build(&root, &[("PATH", &with_cargo)]);
    assert_eq!(run.status, 1);
    assert!(
        run.stderr.contains("cargo build --release failed"),
        "{}",
        run.stderr
    );
}

#[test]
fn the_build_script_names_the_manifest_it_is_building() {
    let dir = TempDir::new();
    let root = fake_root(&dir, true);
    let log = dir.join("cargo-args");
    let cargo_dir = dir.dir("bin");
    executable(
        &cargo_dir.join("cargo"),
        &format!(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > '{}'\n",
            log.to_string_lossy()
        ),
    );
    let with_cargo = format!("{}:{}", cargo_dir.to_string_lossy(), LAUNCHD_PATH);

    run_build(&root, &[("PATH", &with_cargo)]);
    let args: Vec<String> = std::fs::read_to_string(&log)
        .unwrap()
        .lines()
        .map(str::to_string)
        .collect();
    assert_eq!(
        args,
        vec![
            "build".to_string(),
            "--release".to_string(),
            "--manifest-path".to_string(),
            root.join("Cargo.toml").to_string_lossy().to_string()
        ]
    );
}

fn run_build_on_a_terminal(
    root: &Path,
    extra: &[(&str, &str)],
    interrupt: Option<i32>,
) -> (i32, String) {
    run_build_on_a_pane(root, (40, 100), extra, interrupt)
}

fn run_build_on_a_pane(
    root: &Path,
    size: (u16, u16),
    extra: &[(&str, &str)],
    interrupt: Option<i32>,
) -> (i32, String) {
    use std::os::unix::process::CommandExt;

    let mut pty = Pty::new();
    pty.resize(size.0, size.1);
    let mut command = Command::new("/bin/sh");
    command
        .arg(root.join("bin/build"))
        .env_clear()
        .env("PATH", LAUNCHD_PATH)
        .env("HOME", "/private/tmp")
        .env("HERDR_PLUGIN_ROOT", root)
        .stdin(pty.attach())
        .stdout(pty.attach())
        .stderr(pty.attach())
        .process_group(0);
    for (key, value) in extra {
        command.env(key, value);
    }
    let mut child = command.spawn().expect("cannot run the build script");
    if let Some(number) = interrupt {
        pty.wait_for(|seen| !spinner_frames(&placements(seen)).is_empty());
        signal(child.id(), number);
    }
    let seen = pty.read_until_quiet(&mut child);
    let status = child.wait().expect("the build script never ended");
    (status.code().unwrap_or(-1), seen)
}

fn noisy_cargo(dir: &TempDir, body: &str) -> String {
    let cargo_dir = dir.dir("cargo-bin");
    executable(&cargo_dir.join("cargo"), body);
    format!("{}:{}", cargo_dir.to_string_lossy(), LAUNCHD_PATH)
}

fn is_empty(dir: &Path) -> bool {
    std::fs::read_dir(dir).unwrap().next().is_none()
}

const SPINNER_MESSAGE: &str = "Project Finder is compiling. This happens only during the first install or the first time it is opened after an update.";

#[derive(Clone, Debug)]
struct Placement {
    row: usize,
    col: usize,
    text: String,
}

fn placements(seen: &str) -> Vec<Placement> {
    let mut found = Vec::new();
    for part in seen.split('\u{1b}').skip(1) {
        let Some(rest) = part.strip_prefix('[') else {
            continue;
        };
        let Some(end) = rest.find(|c: char| c.is_ascii_alphabetic()) else {
            continue;
        };
        if rest.as_bytes()[end] != b'H' {
            continue;
        }
        let mut args = rest[..end].split(';');
        let (Some(row), Some(col)) = (args.next(), args.next()) else {
            continue;
        };
        let (Ok(row), Ok(col)) = (row.parse::<usize>(), col.parse::<usize>()) else {
            continue;
        };
        let text = &rest[end + 1..];
        if text.is_empty() {
            continue;
        }
        found.push(Placement {
            row,
            col,
            text: text.to_string(),
        });
    }
    found
}

fn spinner_frames(drawn: &[Placement]) -> Vec<Placement> {
    drawn
        .iter()
        .filter(|p| ["|", "/", "-", "\\"].contains(&p.text.as_str()))
        .cloned()
        .collect()
}

fn message_rows(drawn: &[Placement]) -> Vec<Placement> {
    let mut rows: Vec<Placement> = Vec::new();
    for p in drawn.iter().filter(|p| p.text.len() > 1) {
        if !rows.iter().any(|seen| seen.row == p.row) {
            rows.push(p.clone());
        }
    }
    rows.sort_by_key(|p| p.row);
    rows
}

fn run_a_slow_build_on_a_pane(rows: u16, cols: u16) -> String {
    let dir = TempDir::new();
    let root = fake_root(&dir, true);
    let tmp = dir.dir("tmp");
    let with_cargo = noisy_cargo(&dir, "#!/bin/sh\nsleep 0.6\n");

    let (status, seen) = run_build_on_a_pane(
        &root,
        (rows, cols),
        &[("PATH", &with_cargo), ("TMPDIR", tmp.to_str().unwrap())],
        None,
    );

    assert_eq!(status, 0, "{:?}", seen);
    assert!(is_empty(&tmp), "the captured build output is left behind");
    seen
}

#[test]
fn the_spinner_message_is_the_wording_that_was_asked_for() {
    let line = read_repo_file("bin/build")
        .lines()
        .find(|l| l.starts_with("BUILD_MESSAGE="))
        .expect("bin/build carries no spinner message")
        .to_string();
    assert_eq!(line, format!("BUILD_MESSAGE='{}'", SPINNER_MESSAGE));
}

#[test]
fn the_spinner_message_wraps_and_centres_itself_in_a_roomy_pane() {
    let seen = run_a_slow_build_on_a_pane(40, 100);
    let drawn = placements(&seen);
    let frames = spinner_frames(&drawn);
    let rows = message_rows(&drawn);

    assert!(!frames.is_empty(), "no spinner frame was drawn: {:?}", seen);
    assert!(rows.len() > 1, "the message never wrapped: {:?}", rows);

    let joined: String = rows
        .iter()
        .map(|p| p.text.as_str())
        .collect::<Vec<_>>()
        .join(" ");
    assert_eq!(joined, SPINNER_MESSAGE);

    for p in &rows {
        assert!(p.text.len() <= 96, "{:?} is wider than the margin", p);
        assert_eq!(
            p.col,
            (100 - p.text.len()) / 2 + 1,
            "{:?} is not centred across the pane",
            p
        );
    }

    let top = frames.iter().map(|p| p.row).min().unwrap();
    assert!(
        frames.iter().all(|p| p.row == top),
        "the spinner moved between frames: {:?}",
        frames
    );
    assert_eq!(
        frames[0].col, 50,
        "the spinner is not centred across the pane"
    );
    assert_eq!(rows.first().unwrap().row, top + 2);

    let bottom = rows.last().unwrap().row;
    let above = top - 1;
    let below = 40 - bottom;
    assert!(
        below >= above && below - above <= 1,
        "the block is not centred down the pane: {} above, {} below",
        above,
        below
    );
}

#[test]
fn a_narrow_pane_wraps_the_message_instead_of_running_off_the_edge() {
    let seen = run_a_slow_build_on_a_pane(40, 24);
    let drawn = placements(&seen);
    let rows = message_rows(&drawn);

    assert!(rows.len() > 5, "the message never wrapped: {:?}", rows);
    let joined: String = rows
        .iter()
        .map(|p| p.text.as_str())
        .collect::<Vec<_>>()
        .join(" ");
    assert_eq!(joined, SPINNER_MESSAGE);
    for p in &rows {
        assert!(p.col >= 1, "{:?} starts off the pane", p);
        assert!(
            p.col + p.text.len() - 1 <= 24,
            "{:?} runs past the edge of the pane",
            p
        );
        assert_eq!(p.col, (24 - p.text.len()) / 2 + 1, "{:?} is not centred", p);
    }
}

#[test]
fn a_pane_narrower_than_a_single_word_breaks_the_word_rather_than_overflowing() {
    let seen = run_a_slow_build_on_a_pane(40, 12);
    let drawn = placements(&seen);
    let rows = message_rows(&drawn);

    assert!(!rows.is_empty(), "nothing was drawn: {:?}", seen);
    for p in &rows {
        assert!(
            p.text.len() <= 8,
            "{:?} is wider than the pane allows for text",
            p
        );
        assert!(
            p.col + p.text.len() - 1 <= 12,
            "{:?} runs past the edge of the pane",
            p
        );
    }
    let letters: String = rows.iter().map(|p| p.text.as_str()).collect();
    assert_eq!(letters.replace(' ', ""), SPINNER_MESSAGE.replace(' ', ""));
    assert!(
        rows.iter().any(|p| p.text.ends_with("compilin")),
        "a word longer than the pane was never broken: {:?}",
        rows
    );
}

#[test]
fn a_pane_too_short_for_the_block_clamps_to_what_fits() {
    let seen = run_a_slow_build_on_a_pane(3, 100);
    let drawn = placements(&seen);
    let frames = spinner_frames(&drawn);

    assert!(!frames.is_empty(), "no spinner frame was drawn: {:?}", seen);
    for p in &drawn {
        assert!(p.row >= 1 && p.row <= 3, "{:?} is off the short pane", p);
    }
    assert_eq!(message_rows(&drawn).len(), 2);
    assert!(
        seen.ends_with("\u{1b}[2J\u{1b}[H\u{1b}[?25h"),
        "the short pane is left dirty: {:?}",
        seen
    );
}

#[test]
fn a_pane_of_one_row_still_shows_the_spinner_and_still_cleans_up() {
    let seen = run_a_slow_build_on_a_pane(1, 100);
    let drawn = placements(&seen);

    assert!(
        !spinner_frames(&drawn).is_empty(),
        "no spinner frame was drawn: {:?}",
        seen
    );
    for p in &drawn {
        assert_eq!(p.row, 1, "{:?} is off the single-row pane", p);
    }
    assert!(message_rows(&drawn).is_empty());
    assert!(
        seen.ends_with("\u{1b}[2J\u{1b}[H\u{1b}[?25h"),
        "the single-row pane is left dirty: {:?}",
        seen
    );
}

#[test]
fn a_pane_that_reports_no_size_at_all_is_drawn_as_twenty_four_by_eighty() {
    let seen = run_a_slow_build_on_a_pane(0, 0);
    let drawn = placements(&seen);
    let frames = spinner_frames(&drawn);
    let rows = message_rows(&drawn);

    assert!(!frames.is_empty(), "no spinner frame was drawn: {:?}", seen);
    assert_eq!(
        frames[0].col, 40,
        "the spinner is not centred across a default pane: {:?}",
        frames
    );
    for p in &drawn {
        assert!(p.row >= 1 && p.row <= 24, "{:?} is off a default pane", p);
        assert!(
            p.col + p.text.len() - 1 <= 80,
            "{:?} runs past the edge of a default pane",
            p
        );
    }
    let joined: String = rows
        .iter()
        .map(|p| p.text.as_str())
        .collect::<Vec<_>>()
        .join(" ");
    assert_eq!(joined, SPINNER_MESSAGE);
}

const CHATTY: &str =
    "#!/bin/sh\nsleep 0.4\necho 'Compiling nucleo v0.5.0'\necho 'Downloading ratatui' >&2\n";

#[test]
fn a_build_on_a_terminal_shows_a_spinner_instead_of_what_cargo_says() {
    let dir = TempDir::new();
    let root = fake_root(&dir, true);
    let tmp = dir.dir("tmp");
    let with_cargo = noisy_cargo(&dir, CHATTY);

    let (status, seen) = run_build_on_a_terminal(
        &root,
        &[("PATH", &with_cargo), ("TMPDIR", tmp.to_str().unwrap())],
        None,
    );

    assert_eq!(status, 0, "{}", seen);
    assert!(
        !seen.contains("Compiling"),
        "cargo's stdout reached the popup: {:?}",
        seen
    );
    assert!(
        !seen.contains("Downloading"),
        "cargo's stderr reached the popup: {:?}",
        seen
    );
    assert!(
        !spinner_frames(&placements(&seen)).is_empty(),
        "no spinner frame was drawn: {:?}",
        seen
    );
    assert!(
        seen.ends_with("\u{1b}[2J\u{1b}[H\u{1b}[?25h"),
        "the spinner screen is left behind: {:?}",
        seen
    );
    assert!(is_empty(&tmp), "the captured build output is left behind");
}

#[test]
fn a_failed_build_on_a_terminal_prints_everything_cargo_said() {
    let dir = TempDir::new();
    let root = fake_root(&dir, true);
    let tmp = dir.dir("tmp");
    let with_cargo = noisy_cargo(
        &dir,
        "#!/bin/sh\nsleep 0.4\necho 'error[E0425]: cannot find value'\necho 'Compiling nucleo v0.5.0' >&2\nexit 3\n",
    );

    let (status, seen) = run_build_on_a_terminal(
        &root,
        &[("PATH", &with_cargo), ("TMPDIR", tmp.to_str().unwrap())],
        None,
    );

    assert_eq!(status, 1, "{}", seen);
    assert!(
        !spinner_frames(&placements(&seen)).is_empty(),
        "the spinner never ran, so the capture path is untested: {:?}",
        seen
    );
    assert!(seen.contains("error[E0425]"), "{:?}", seen);
    assert!(seen.contains("Compiling nucleo"), "{:?}", seen);
    assert!(seen.contains("cargo build --release failed"), "{:?}", seen);
    assert!(is_empty(&tmp), "the captured build output is left behind");
}

#[test]
fn an_interrupted_build_takes_its_spinner_and_its_capture_file_with_it() {
    let dir = TempDir::new();
    let root = fake_root(&dir, true);
    let tmp = dir.dir("tmp");
    let with_cargo = noisy_cargo(&dir, "#!/bin/sh\nsleep 30\n");

    let (_, seen) = run_build_on_a_terminal(
        &root,
        &[("PATH", &with_cargo), ("TMPDIR", tmp.to_str().unwrap())],
        Some(libc::SIGTERM),
    );

    assert!(
        !spinner_frames(&placements(&seen)).is_empty(),
        "the spinner never ran, so the test proves nothing: {:?}",
        seen
    );
    assert!(
        is_empty(&tmp),
        "the captured build output survived the interruption"
    );
}

#[test]
fn a_build_with_no_terminal_attached_still_prints_what_cargo_says() {
    let dir = TempDir::new();
    let root = fake_root(&dir, true);
    let with_cargo = noisy_cargo(&dir, CHATTY);

    let run = run_build(&root, &[("PATH", &with_cargo)]);

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert!(
        run.stderr.contains("building the picker with"),
        "{:?}",
        run.stderr
    );
    assert!(run.stderr.contains("Compiling nucleo"), "{:?}", run.stderr);
    assert!(
        run.stderr.contains("Downloading ratatui"),
        "{:?}",
        run.stderr
    );
    assert!(
        !run.stderr.contains('\r') && !run.stderr.contains('\u{1b}'),
        "a spinner was drawn into a pipe: {:?}",
        run.stderr
    );
}

#[test]
fn a_failed_build_with_no_terminal_attached_says_everything_it_says_today() {
    let dir = TempDir::new();
    let root = fake_root(&dir, true);
    let with_cargo = noisy_cargo(
        &dir,
        "#!/bin/sh\necho 'error[E0425]: cannot find value'\nexit 3\n",
    );

    let run = run_build(&root, &[("PATH", &with_cargo)]);

    assert_eq!(run.status, 1);
    assert!(run.stderr.contains("error[E0425]"), "{:?}", run.stderr);
    assert!(
        run.stderr.contains("cargo build --release failed"),
        "{:?}",
        run.stderr
    );
    assert!(!run.stderr.contains('\r'), "{:?}", run.stderr);
}

const BASE_TOOLS: &[&str] = &[
    "uname", "mktemp", "mv", "chmod", "find", "sed", "cat", "rm", "mkdir", "cut", "dirname",
    "sleep", "stty",
];

const COMMIT: &str = "1f2e3d4c5b6a70819273645566778899aabbccdd";

fn on_launchd_path(tool: &str) -> Option<PathBuf> {
    LAUNCHD_PATH
        .split(':')
        .map(|dir| Path::new(dir).join(tool))
        .find(|path| path.exists())
}

fn link(farm: &Path, tool: &str) {
    let real =
        on_launchd_path(tool).unwrap_or_else(|| panic!("{} is not on the launchd PATH", tool));
    std::os::unix::fs::symlink(real, farm.join(tool)).unwrap();
}

fn farm(dir: &TempDir, name: &str) -> PathBuf {
    let farm = dir.dir(name);
    for tool in BASE_TOOLS {
        link(&farm, tool);
    }
    farm
}

fn hasher() -> (PathBuf, Vec<&'static str>) {
    if let Some(path) = on_launchd_path("sha256sum") {
        return (path, Vec::new());
    }
    let path = on_launchd_path("shasum").expect("no sha256 tool is on the launchd PATH");
    (path, vec!["-a", "256"])
}

fn link_hasher(farm: &Path) {
    let (real, _) = hasher();
    let name = real.file_name().unwrap().to_string_lossy().to_string();
    link(farm, &name);
}

fn sha256(path: &Path) -> String {
    let (bin, args) = hasher();
    let out = Command::new(bin)
        .args(args)
        .arg(path)
        .output()
        .expect("cannot run the hasher");
    assert!(out.status.success(), "the hasher failed on {:?}", path);
    String::from_utf8_lossy(&out.stdout)
        .split_whitespace()
        .next()
        .expect("the hasher printed nothing")
        .to_string()
}

fn fake_git(farm: &Path, toplevel: &Path, dirty: &str, commit: &str, origin: &str) {
    let toplevel = toplevel.to_string_lossy().to_string();
    executable(
        &farm.join("git"),
        &format!(
            r#"#!/bin/sh
case "$*" in
    *"rev-parse --show-toplevel") printf '%s\n' '{toplevel}' ;;
    *"rev-parse HEAD") printf '%s\n' '{commit}' ;;
    *"remote get-url origin") printf '%s\n' '{origin}' ;;
    *"status --porcelain"*)
        specs=''
        listed=''
        for arg in "$@"; do
            if [ -n "$listed" ]; then
                specs="$specs $arg"
            elif [ "$arg" = '--' ]; then
                listed=1
            fi
        done
        if [ -z "$listed" ]; then
            printf '%s' '{dirty}'
            exit 0
        fi
        printf '%s' '{dirty}' | while IFS= read -r entry || [ -n "$entry" ]; do
            for spec in $specs; do
                case "${{entry#???}}" in
                    "$spec"|"$spec"/*) printf '%s\n' "$entry"; break ;;
                esac
            done
        done
        ;;
    *) exit 1 ;;
esac
"#
        ),
    );
}

fn pristine_git(farm: &Path, root: &Path) {
    fake_git(farm, root, "", COMMIT, "git@github.com:owner/repo.git");
}

fn fake_server(farm: &Path, name: &str, log: &Path, serves: &str) {
    let log = log.to_string_lossy().to_string();
    executable(
        &farm.join(name),
        &format!(
            r#"#!/bin/sh
out=''
url=''
while [ $# -gt 0 ]; do
    case "$1" in
        -o|-O) out="$2"; shift 2 ;;
        --connect-timeout|--max-time) shift 2 ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
printf '%s\n' "$url" >> '{log}'
{serves}
"#
        ),
    );
}

fn serving(binary: &Path, sum: &str) -> String {
    let binary = binary.to_string_lossy().to_string();
    format!(
        r#"case "$url" in
    *.sha256) printf '%s  pick-project\n' '{sum}' > "$out" ;;
    *) cat '{binary}' > "$out" ;;
esac"#
    )
}

fn fake_cargo_that_writes_a_binary(farm: &Path, log: &Path) {
    let log = log.to_string_lossy().to_string();
    executable(
        &farm.join("cargo"),
        &format!(
            r#"#!/bin/sh
printf 'ran\n' >> '{log}'
mkdir -p "$HERDR_PLUGIN_ROOT/target/release"
printf '%s\n' '#!/bin/sh' 'echo COMPILED' > "$HERDR_PLUGIN_ROOT/target/release/pick-project"
chmod 755 "$HERDR_PLUGIN_ROOT/target/release/pick-project"
"#
        ),
    );
}

struct Fetch {
    root: PathBuf,
    farm: PathBuf,
    payload: PathBuf,
    sum: String,
    urls: PathBuf,
    compiles: PathBuf,
}

fn fetch_case(dir: &TempDir) -> Fetch {
    let root = fake_root(dir, true);
    for name in ["src/main.rs", "src", "Cargo.toml", "Cargo.lock"] {
        stamp(&root.join(name), 1000);
    }
    let farm = farm(dir, "tools");
    link_hasher(&farm);
    let payload = dir.write("payload", "#!/bin/sh\necho PICKED\n");
    let sum = sha256(&payload);
    let urls = dir.join("urls");
    let compiles = dir.join("compiles");
    fake_cargo_that_writes_a_binary(&farm, &compiles);
    Fetch {
        root,
        farm,
        payload,
        sum,
        urls,
        compiles,
    }
}

impl Fetch {
    fn run(&self) -> Shim {
        run_build(&self.root, &[("PATH", &self.farm.to_string_lossy())])
    }

    fn picker_says(&self) -> String {
        let out = Command::new(self.root.join("target/release/pick-project"))
            .output()
            .expect("the picker that was installed will not run");
        String::from_utf8_lossy(&out.stdout).trim().to_string()
    }

    fn asked_for(&self) -> Vec<String> {
        std::fs::read_to_string(&self.urls)
            .unwrap_or_default()
            .lines()
            .map(str::to_string)
            .collect()
    }

    fn compiled(&self) -> bool {
        self.compiles.exists()
    }
}

fn this_platform() -> String {
    let os = match std::env::consts::OS {
        "macos" => "macos",
        "linux" => "linux",
        other => panic!("the suite does not run on {}", other),
    };
    let arch = match std::env::consts::ARCH {
        "aarch64" => "arm64",
        "x86_64" => "x64",
        other => panic!("the suite does not run on {}", other),
    };
    format!("{}-{}", os, arch)
}

fn expected_url() -> String {
    format!(
        "https://github.com/owner/repo/releases/download/v{}/pick-project-{}-{}",
        manifest()["version"].as_str().unwrap(),
        this_platform(),
        &COMMIT[..12]
    )
}

#[test]
fn a_pristine_checkout_of_a_released_commit_runs_the_prebuilt_picker() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &serving(&case.payload, &case.sum),
    );

    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert_eq!(case.picker_says(), "PICKED");
    assert!(!case.compiled(), "it compiled anyway: {}", run.stderr);
}

#[test]
fn the_url_it_asks_for_names_the_release_the_platform_and_the_commit() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &serving(&case.payload, &case.sum),
    );

    case.run();

    assert_eq!(
        case.asked_for(),
        vec![expected_url(), format!("{}.sha256", expected_url())]
    );
}

#[test]
fn every_shape_of_github_remote_names_the_same_release() {
    for origin in [
        "git@github.com:owner/repo.git",
        "https://github.com/owner/repo.git",
        "https://github.com/owner/repo",
        "ssh://git@github.com/owner/repo.git",
    ] {
        let dir = TempDir::new();
        let case = fetch_case(&dir);
        fake_git(&case.farm, &case.root, "", COMMIT, origin);
        fake_server(
            &case.farm,
            "curl",
            &case.urls,
            &serving(&case.payload, &case.sum),
        );

        case.run();

        assert_eq!(
            case.asked_for(),
            vec![expected_url(), format!("{}.sha256", expected_url())],
            "{} was read as a different repository",
            origin
        );
        assert_eq!(case.picker_says(), "PICKED", "{}", origin);
    }
}

#[test]
fn a_working_tree_with_local_changes_compiles_and_downloads_nothing() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    fake_git(
        &case.farm,
        &case.root,
        " M src/picker.rs\n",
        COMMIT,
        "git@github.com:owner/repo.git",
    );
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &serving(&case.payload, &case.sum),
    );

    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert!(
        case.asked_for().is_empty(),
        "it downloaded over somebody's uncommitted work: {:?}",
        case.asked_for()
    );
    assert!(case.compiled());
    assert_eq!(case.picker_says(), "COMPILED");
}

#[test]
fn an_untracked_file_counts_as_a_local_change_too() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    fake_git(
        &case.farm,
        &case.root,
        "?? src/scratch.rs\n",
        COMMIT,
        "git@github.com:owner/repo.git",
    );
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &serving(&case.payload, &case.sum),
    );

    case.run();

    assert!(case.asked_for().is_empty(), "{:?}", case.asked_for());
    assert!(case.compiled());
}

#[test]
fn an_edited_readme_is_not_a_reason_to_compile() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    fake_git(
        &case.farm,
        &case.root,
        " M README.md\n",
        COMMIT,
        "git@github.com:owner/repo.git",
    );
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &serving(&case.payload, &case.sum),
    );

    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert_eq!(
        case.asked_for(),
        vec![expected_url(), format!("{}.sha256", expected_url())]
    );
    assert_eq!(case.picker_says(), "PICKED");
    assert!(!case.compiled(), "it compiled anyway: {}", run.stderr);
}

#[test]
fn nothing_the_compiler_never_reads_stands_between_a_release_and_its_binary() {
    for changed in [
        " M defaults.toml\n",
        " M herdr-plugin.toml\n",
        " M bin/build\n",
        " M bin/pick-project\n",
        " M tests/packaging.rs\n",
        " M .github/workflows/release.yml\n",
        "?? notes.md\n",
        "?? tests/scratch.rs\n",
    ] {
        let dir = TempDir::new();
        let case = fetch_case(&dir);
        fake_git(
            &case.farm,
            &case.root,
            changed,
            COMMIT,
            "git@github.com:owner/repo.git",
        );
        fake_server(
            &case.farm,
            "curl",
            &case.urls,
            &serving(&case.payload, &case.sum),
        );

        let run = case.run();

        assert_eq!(run.status, 0, "{}", run.stderr);
        assert_eq!(
            case.picker_says(),
            "PICKED",
            "{} was treated as code",
            changed.trim()
        );
        assert!(!case.compiled(), "{} was treated as code", changed.trim());
    }
}

#[test]
fn every_file_the_binary_is_built_from_still_forces_a_build() {
    for changed in [
        " M Cargo.toml\n",
        " M Cargo.lock\n",
        " M build.rs\n",
        " M .cargo/config.toml\n",
        " M rust-toolchain\n",
        " M rust-toolchain.toml\n",
        "?? build.rs\n",
        "?? .cargo/config.toml\n",
    ] {
        let dir = TempDir::new();
        let case = fetch_case(&dir);
        fake_git(
            &case.farm,
            &case.root,
            changed,
            COMMIT,
            "git@github.com:owner/repo.git",
        );
        fake_server(
            &case.farm,
            "curl",
            &case.urls,
            &serving(&case.payload, &case.sum),
        );

        let run = case.run();

        assert_eq!(run.status, 0, "{}", run.stderr);
        assert!(
            case.asked_for().is_empty(),
            "{} was downloaded over: {:?}",
            changed.trim(),
            case.asked_for()
        );
        assert_eq!(
            case.picker_says(),
            "COMPILED",
            "{} did not force a build",
            changed.trim()
        );
    }
}

#[test]
fn a_plugin_root_inside_somebody_elses_repository_compiles() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    fake_git(
        &case.farm,
        Path::new("/somewhere/else"),
        "",
        COMMIT,
        "git@github.com:owner/repo.git",
    );
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &serving(&case.payload, &case.sum),
    );

    case.run();

    assert!(
        case.asked_for().is_empty(),
        "a parent repository's state decided it: {:?}",
        case.asked_for()
    );
    assert!(case.compiled());
}

#[test]
fn a_checkout_that_is_not_a_git_tree_at_all_compiles() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &serving(&case.payload, &case.sum),
    );

    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert!(case.asked_for().is_empty(), "{:?}", case.asked_for());
    assert!(case.compiled());
}

#[test]
fn a_remote_that_is_not_github_compiles() {
    for origin in [
        "git@git.example.com:owner/repo.git",
        "https://gitlab.com/owner/repo.git",
        "mirror/repo",
    ] {
        let dir = TempDir::new();
        let case = fetch_case(&dir);
        fake_git(&case.farm, &case.root, "", COMMIT, origin);
        fake_server(
            &case.farm,
            "curl",
            &case.urls,
            &serving(&case.payload, &case.sum),
        );

        case.run();

        assert!(
            case.asked_for().is_empty(),
            "{} was treated as a GitHub repository: {:?}",
            origin,
            case.asked_for()
        );
        assert!(case.compiled(), "{}", origin);
    }
}

#[test]
fn a_release_with_no_asset_for_this_platform_compiles() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(&case.farm, "curl", &case.urls, "exit 22");

    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert_eq!(case.asked_for().len(), 1, "it kept going after the 404");
    assert!(case.compiled());
    assert_eq!(case.picker_says(), "COMPILED");
}

#[test]
fn a_machine_with_no_network_compiles() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(&case.farm, "curl", &case.urls, "exit 6");

    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert!(case.compiled());
    assert_eq!(case.picker_says(), "COMPILED");
}

#[test]
fn a_truncated_download_is_never_installed() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    let truncated = dir.write("truncated", "#!/bin/sh\n");
    pristine_git(&case.farm, &case.root);
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &serving(&truncated, &case.sum),
    );

    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert_eq!(
        case.picker_says(),
        "COMPILED",
        "a binary that failed its checksum was installed"
    );
    assert!(case.compiled());
}

#[test]
fn a_checksum_that_is_not_a_checksum_is_refused() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &serving(&case.payload, "not-a-checksum"),
    );

    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert_eq!(case.picker_says(), "COMPILED");
}

#[test]
fn an_empty_checksum_file_is_refused() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &format!(
            r#"case "$url" in
    *.sha256) : > "$out" ;;
    *) cat '{}' > "$out" ;;
esac"#,
            case.payload.to_string_lossy()
        ),
    );

    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert_eq!(case.picker_says(), "COMPILED");
}

#[test]
fn a_hasher_that_answers_with_nothing_is_not_a_verification() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &format!(
            r#"case "$url" in
    *.sha256) : > "$out" ;;
    *) cat '{}' > "$out" ;;
esac"#,
            case.payload.to_string_lossy()
        ),
    );
    let (real, _) = hasher();
    let silent = case.farm.join(real.file_name().unwrap());
    std::fs::remove_file(&silent).unwrap();
    executable(&silent, "#!/bin/sh\n");

    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert_eq!(
        case.picker_says(),
        "COMPILED",
        "an empty checksum matched an empty answer and the binary was installed"
    );
}

#[test]
fn a_machine_that_cannot_check_a_checksum_compiles_instead_of_trusting_one() {
    let dir = TempDir::new();
    let root = fake_root(&dir, true);
    let bare = farm(&dir, "tools");
    let compiles = dir.join("compiles");
    let urls = dir.join("urls");
    let payload = dir.write("payload", "#!/bin/sh\necho PICKED\n");
    fake_cargo_that_writes_a_binary(&bare, &compiles);
    pristine_git(&bare, &root);
    fake_server(&bare, "curl", &urls, &serving(&payload, &sha256(&payload)));

    let run = run_build(&root, &[("PATH", &bare.to_string_lossy())]);

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert!(
        !urls.exists(),
        "it downloaded a binary it had no way to verify"
    );
    assert!(compiles.exists());
}

#[test]
fn wget_carries_the_download_when_there_is_no_curl() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(
        &case.farm,
        "wget",
        &case.urls,
        &serving(&case.payload, &case.sum),
    );

    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert_eq!(case.picker_says(), "PICKED");
    assert_eq!(
        case.asked_for(),
        vec![expected_url(), format!("{}.sha256", expected_url())]
    );
}

#[test]
fn a_machine_with_neither_curl_nor_wget_compiles() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);

    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert!(case.compiled());
    assert_eq!(case.picker_says(), "COMPILED");
}

#[test]
fn a_toolchain_that_cannot_build_anything_is_never_reached_once_a_fetch_succeeds() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &serving(&case.payload, &case.sum),
    );
    executable(&case.farm.join("cargo"), "#!/bin/sh\nexit 3\n");

    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert_eq!(case.picker_says(), "PICKED");
}

#[test]
fn a_second_run_touches_neither_the_network_nor_cargo() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &serving(&case.payload, &case.sum),
    );

    case.run();
    let first = case.asked_for();
    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert_eq!(
        case.asked_for(),
        first,
        "every server start would download the picker again"
    );
    assert!(!case.compiled());
    assert_eq!(case.picker_says(), "PICKED");
}

#[test]
fn a_binary_already_current_is_left_exactly_where_it_is() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &serving(&case.payload, &case.sum),
    );
    executable(
        &case.root.join("target/release/pick-project"),
        "#!/bin/sh\necho MINE\n",
    );
    stamp(&case.root.join("target/release/pick-project"), 2000);

    let run = case.run();

    assert_eq!(run.status, 0, "{}", run.stderr);
    assert_eq!(case.picker_says(), "MINE");
    assert!(case.asked_for().is_empty(), "{:?}", case.asked_for());
    assert!(!case.compiled());
}

#[test]
fn a_download_leaves_nothing_of_its_own_behind() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &serving(&case.payload, &case.sum),
    );

    case.run();

    let left: Vec<String> = std::fs::read_dir(case.root.join("target"))
        .unwrap()
        .flatten()
        .map(|e| e.file_name().to_string_lossy().to_string())
        .collect();
    assert_eq!(left, vec!["release".to_string()], "{:?}", left);
}

#[test]
fn the_download_says_what_it_is_doing_where_there_is_no_terminal_to_draw_on() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &serving(&case.payload, &case.sum),
    );

    let run = case.run();

    assert!(
        run.stderr.contains("downloading the picker"),
        "{:?}",
        run.stderr
    );
    assert!(
        !run.stderr.contains('\u{1b}'),
        "the download painted escape sequences into the log: {:?}",
        run.stderr
    );
}

#[test]
fn a_download_that_falls_through_says_it_is_compiling_instead() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(&case.farm, "curl", &case.urls, "exit 22");

    let run = case.run();

    assert!(run.stderr.contains("compiling instead"), "{:?}", run.stderr);
    assert!(!run.stderr.contains('\u{1b}'), "{:?}", run.stderr);
}

#[test]
fn a_download_on_a_terminal_draws_its_own_message_and_never_cargo_s() {
    let dir = TempDir::new();
    let case = fetch_case(&dir);
    pristine_git(&case.farm, &case.root);
    fake_server(
        &case.farm,
        "curl",
        &case.urls,
        &format!("sleep 1\n{}", serving(&case.payload, &case.sum)),
    );

    let (status, seen) =
        run_build_on_a_terminal(&case.root, &[("PATH", &case.farm.to_string_lossy())], None);

    assert_eq!(status, 0, "{}", seen);
    assert!(!spinner_frames(&placements(&seen)).is_empty(), "{:?}", seen);
    let drawn: String = message_rows(&placements(&seen))
        .iter()
        .map(|p| p.text.clone())
        .collect::<Vec<String>>()
        .join(" ");
    assert!(drawn.contains("downloading"), "{:?}", drawn);
    assert!(!drawn.contains("compiling"), "{:?}", drawn);
    assert_eq!(case.picker_says(), "PICKED");
}

fn shell_scripts() -> Vec<PathBuf> {
    let mut found = Vec::new();
    for dir in ["bin", "scripts"] {
        let Ok(entries) = std::fs::read_dir(manifest_dir().join(dir)) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_file() {
                found.push(path);
            }
        }
    }
    found
}

#[test]
fn no_shell_script_carries_a_comment() {
    for path in shell_scripts() {
        let text = std::fs::read_to_string(&path).unwrap();
        for (n, line) in text.lines().enumerate().skip(1) {
            assert!(
                !line.trim_start().starts_with('#'),
                "{}:{} carries a comment: {}",
                path.display(),
                n + 1,
                line
            );
        }
    }
}

#[test]
fn every_shell_script_is_covered_by_that_guard() {
    let found: Vec<String> = shell_scripts()
        .iter()
        .filter_map(|p| p.file_name().map(|n| n.to_string_lossy().to_string()))
        .collect();
    for name in ["build", "pick-project"] {
        assert!(
            found.contains(&name.to_string()),
            "{} was not scanned",
            name
        );
    }
}

fn workflow() -> String {
    read_repo_file(".github/workflows/release.yml")
}

fn workflow_line(prefix: &str) -> String {
    workflow()
        .lines()
        .map(str::trim)
        .find(|line| line.starts_with(prefix))
        .unwrap_or_else(|| panic!("the workflow has no line starting with {}", prefix))
        .to_string()
}

#[test]
fn the_release_workflow_builds_one_binary_for_every_platform_the_manifest_declares() {
    let parsed = manifest();
    let declared: Vec<&str> = parsed["platforms"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap())
        .collect();
    assert_eq!(declared, vec!["macos", "linux"]);
    let text = workflow();
    for (platform, target) in [
        ("macos-arm64", "aarch64-apple-darwin"),
        ("macos-x64", "x86_64-apple-darwin"),
        ("linux-arm64", "aarch64-unknown-linux-musl"),
        ("linux-x64", "x86_64-unknown-linux-musl"),
    ] {
        assert!(
            text.contains(&format!("platform: {}", platform)),
            "the workflow builds nothing for {}",
            platform
        );
        assert!(
            text.contains(&format!("target: {}", target)),
            "the workflow names no target for {}",
            platform
        );
    }
}

#[test]
fn the_release_workflow_names_its_assets_the_way_the_build_script_asks_for_them() {
    assert_eq!(
        workflow_line("ASSET="),
        r#"ASSET="pick-project-$PLATFORM-$(git rev-parse HEAD | cut -c1-12)""#
    );
    let script = read_repo_file("bin/build");
    assert!(
        script.contains("pick-project-%s-%s"),
        "the script asks for a differently shaped name"
    );
    assert!(
        script.contains("cut -c1-12"),
        "both sides must shorten the commit to the same length"
    );
}

#[test]
fn the_release_workflow_publishes_a_checksum_beside_every_binary() {
    let text = workflow();
    assert!(
        text.contains(r#"sha256sum "$ASSET" > "$ASSET.sha256""#),
        "{}",
        text
    );
    assert!(
        text.contains(r#"shasum -a 256 "$ASSET" > "$ASSET.sha256""#),
        "{}",
        text
    );
    assert!(text.contains("${{ env.ASSET }}.sha256"), "{}", text);
    assert!(
        read_repo_file("bin/build").contains("$1.sha256"),
        "the script looks for the checksum somewhere else"
    );
}

#[test]
fn the_release_workflow_builds_the_commit_the_tag_names() {
    let text = workflow();
    assert!(
        text.contains("ref: ${{ github.event.release.tag_name || github.event.inputs.tag }}"),
        "the workflow would build the default branch and name it for the tag: {}",
        text
    );
}

#[test]
fn the_python_implementation_and_its_suite_are_gone() {
    assert!(!manifest_dir().join("tests/test_pick_project.py").exists());
    let shim = read_repo_file("bin/pick-project");
    assert!(
        shim.starts_with("#!/bin/sh"),
        "the entry point is a shell shim"
    );
    assert!(!shim.contains("python3"));
}

#[test]
fn nothing_in_the_repo_still_names_fzf_as_a_dependency() {
    for name in [
        "herdr-plugin.toml",
        "Cargo.toml",
        "bin/pick-project",
        "bin/build",
    ] {
        assert!(
            !read_repo_file(name).contains("fzf"),
            "{} still names fzf",
            name
        );
    }
}
