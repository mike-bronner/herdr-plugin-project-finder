mod support;

use std::ffi::OsString;
use std::process::Command;

use pick_project::version::{requested, Request, FLAG};
use support::*;

fn args(of: &[&str]) -> Vec<OsString> {
    of.iter().map(OsString::from).collect()
}

struct Run {
    status: i32,
    stdout: String,
    stderr: String,
}

fn run(arguments: &[&str], root: &str) -> Run {
    let out = Command::new(env!("CARGO_BIN_EXE_pick-project"))
        .args(arguments)
        .env_clear()
        .env("PATH", LAUNCHD_PATH)
        .env("HOME", "/private/tmp")
        .env("HERDR_PLUGIN_ROOT", root)
        .output()
        .expect("cannot run the picker");
    Run {
        status: out.status.code().unwrap_or(-1),
        stdout: String::from_utf8_lossy(&out.stdout).to_string(),
        stderr: String::from_utf8_lossy(&out.stderr).to_string(),
    }
}

fn declared_version() -> String {
    read_repo_file("herdr-plugin.toml")
        .parse::<toml::Table>()
        .unwrap()["version"]
        .as_str()
        .unwrap()
        .to_string()
}

#[test]
fn no_arguments_opens_the_picker() {
    assert_eq!(requested(&args(&[])), Request::Pick);
}

#[test]
fn the_flag_on_its_own_asks_for_the_report() {
    assert_eq!(requested(&args(&[FLAG])), Request::Report);
}

#[test]
fn an_argument_nobody_defined_is_refused_rather_than_ignored() {
    assert_eq!(
        requested(&args(&["--colour"])),
        Request::Refuse("--colour".to_string())
    );
}

#[test]
fn something_trailing_the_flag_is_refused_rather_than_dropped() {
    assert_eq!(
        requested(&args(&[FLAG, "extra"])),
        Request::Refuse("extra".to_string())
    );
}

#[test]
fn a_refused_argument_leaves_by_its_own_exit_code_and_names_the_flag() {
    let run = run(&["--colour"], &manifest_dir().to_string_lossy());
    assert_eq!(run.status, 2, "{}", run.stderr);
    assert!(run.stderr.contains("--colour"), "{}", run.stderr);
    assert!(run.stderr.contains(FLAG), "{}", run.stderr);
}

#[test]
fn the_report_names_the_binary_and_the_version_the_crate_was_built_at() {
    let run = run(&[FLAG], &manifest_dir().to_string_lossy());
    assert_eq!(run.status, 0, "{}", run.stderr);
    let first = run.stdout.lines().next().unwrap_or_default();
    assert!(
        first.starts_with(&format!("pick-project {}", declared_version())),
        "{}",
        first
    );
    assert!(first.contains("built "), "{}", first);
}

#[test]
fn the_report_names_the_manifest_it_compared_itself_against() {
    let run = run(&[FLAG], &manifest_dir().to_string_lossy());
    assert!(
        run.stdout.contains(&format!(
            "manifest {} at {}/herdr-plugin.toml",
            declared_version(),
            manifest_dir().to_string_lossy()
        )),
        "{}",
        run.stdout
    );
}

#[test]
fn the_report_says_how_this_binary_arrived() {
    let run = run(&[FLAG], &manifest_dir().to_string_lossy());
    assert!(
        run.stdout.contains("built from source on this machine"),
        "{}",
        run.stdout
    );
}

#[test]
fn a_binary_that_agrees_with_its_manifest_says_nothing_about_staleness() {
    let run = run(&[FLAG], &manifest_dir().to_string_lossy());
    assert!(!run.stdout.contains("STALE:"), "{}", run.stdout);
}

#[test]
fn a_binary_that_disagrees_with_its_manifest_says_so_and_says_what_to_do() {
    let dir = TempDir::new();
    dir.write("herdr-plugin.toml", "version = \"99.0.0\"\n");
    let run = run(&[FLAG], &dir.path().to_string_lossy());
    assert!(run.stdout.contains("STALE:"), "{}", run.stdout);
    assert!(run.stdout.contains("99.0.0"), "{}", run.stdout);
    assert!(
        run.stdout.contains("cargo build --release"),
        "a binary built here is rebuilt, not reinstalled: {}",
        run.stdout
    );
}

#[test]
fn a_manifest_that_cannot_be_read_is_named_rather_than_guessed_at() {
    let dir = TempDir::new();
    let run = run(&[FLAG], &dir.path().to_string_lossy());
    assert_eq!(run.status, 0, "{}", run.stderr);
    assert!(
        run.stdout.contains("manifest unreadable at"),
        "{}",
        run.stdout
    );
}

#[test]
fn the_report_answers_when_herdr_is_not_running_at_all() {
    let out = Command::new(env!("CARGO_BIN_EXE_pick-project"))
        .arg(FLAG)
        .env_clear()
        .env("PATH", LAUNCHD_PATH)
        .env("HOME", "/private/tmp")
        .env("HERDR_PLUGIN_ROOT", manifest_dir())
        .env("HERDR_SOCKET_PATH", "/private/tmp/pick-project-absent.sock")
        .output()
        .expect("cannot run the picker");
    assert!(
        out.status.success(),
        "the report is what somebody runs when the plugin is already broken, which is exactly \
         when the socket is down: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(String::from_utf8_lossy(&out.stdout).contains("pick-project"));
}
