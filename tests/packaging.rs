mod support;

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

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
    interrupt: Option<(Duration, i32)>,
) -> (i32, String) {
    use std::os::unix::process::CommandExt;

    let pty = Pty::new();
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
    if let Some((after, number)) = interrupt {
        std::thread::sleep(after);
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
        seen.contains("| project-finder: building the picker"),
        "no spinner frame was drawn: {:?}",
        seen
    );
    assert!(
        seen.ends_with("\r\u{1b}[K"),
        "the spinner line is left on screen: {:?}",
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
        seen.contains("| project-finder: building the picker"),
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
        Some((Duration::from_millis(600), libc::SIGTERM)),
    );

    assert!(
        seen.contains("| project-finder: building the picker"),
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

#[test]
fn the_spinner_message_never_promises_that_the_build_happens_only_once() {
    let line = read_repo_file("bin/build")
        .lines()
        .find(|l| l.starts_with("BUILD_MESSAGE="))
        .expect("bin/build carries no spinner message")
        .to_lowercase();
    assert!(line.contains("update"), "{}", line);
    for wrong in ["first time", "first launch", "first run", "once", "only"] {
        assert!(!line.contains(wrong), "{} claims {}", line, wrong);
    }
}

#[test]
fn no_shell_script_carries_a_comment() {
    for name in ["bin/build", "bin/pick-project"] {
        for (n, line) in read_repo_file(name).lines().enumerate().skip(1) {
            assert!(
                !line.trim_start().starts_with('#'),
                "{}:{} carries a comment: {}",
                name,
                n + 1,
                line
            );
        }
    }
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
