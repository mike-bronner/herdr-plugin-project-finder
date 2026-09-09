mod support;

use std::cell::RefCell;
use std::path::{Path, PathBuf};

use pick_project::config::Environment;
use pick_project::layout::{
    for_workspace, hand_over, resolve_layout, split_command, which, WORKSPACE_TOKEN,
};
use support::*;

fn make_executable(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    let mut perms = std::fs::metadata(path).unwrap().permissions();
    perms.set_mode(0o755);
    std::fs::set_permissions(path, perms).unwrap();
}

fn script(dir: &TempDir, rel: &str, body: &str) -> PathBuf {
    let path = dir.write(rel, body);
    make_executable(&path);
    path
}

struct Resolved {
    argv: Option<Vec<String>>,
    warning: Option<String>,
    asked: Vec<String>,
}

fn resolve(setting: Option<&str>, creating: bool, root: Option<&str>, on_path: &[&str]) -> Resolved {
    let dir = TempDir::new();
    let bin = dir.dir("bin");
    for name in on_path {
        script(&dir, &format!("bin/{}", name), "#!/bin/sh\n");
    }
    let path = format!("{}:{}", bin.to_string_lossy(), LAUNCHD_PATH);
    let env = Environment::from_pairs(&[("PATH", &path), ("HOME", "/private/tmp")]);
    let asked = RefCell::new(Vec::new());
    let resolved = resolve_layout(creating, setting, &env, &|plugin_id| {
        asked.borrow_mut().push(plugin_id.to_string());
        root.map(str::to_string)
    });
    Resolved {
        argv: resolved.argv,
        warning: resolved.warning,
        asked: asked.into_inner(),
    }
}

fn absolute(dir: &TempDir, rel: &str) -> String {
    dir.join(rel).to_string_lossy().to_string()
}

#[test]
fn a_plain_command_line_is_split_the_way_a_shell_splits_one() {
    assert_eq!(
        split_command("/x/tool/lay --space {workspace} --quiet").unwrap(),
        vec!["/x/tool/lay", "--space", "{workspace}", "--quiet"]
    );
}

#[test]
fn a_quoted_argument_survives_as_one_argument() {
    assert_eq!(
        split_command("\"/x/my tool/lay\" --title \"two words\"").unwrap(),
        vec!["/x/my tool/lay", "--title", "two words"]
    );
}

#[test]
fn single_quotes_group_too() {
    assert_eq!(
        split_command("'/x/my tool/lay' --title 'two words'").unwrap(),
        vec!["/x/my tool/lay", "--title", "two words"]
    );
}

#[test]
fn an_unbalanced_quote_is_refused_rather_than_guessed_at() {
    assert!(split_command("/x/tool/lay --title \"unclosed").is_err());
    assert!(split_command("/x/tool/lay --title 'unclosed").is_err());
}

#[test]
fn an_empty_quoted_argument_is_still_an_argument() {
    assert_eq!(split_command("lay \"\"").unwrap(), vec!["lay", ""]);
}

#[test]
fn a_backslash_escapes_the_character_after_it() {
    assert_eq!(split_command("lay a\\ b").unwrap(), vec!["lay", "a b"]);
}

#[test]
fn a_command_line_of_only_spaces_splits_to_nothing() {
    assert!(split_command("   ").unwrap().is_empty());
}

#[test]
fn a_resolved_command_keeps_its_arguments_and_the_workspace_token() {
    let dir = TempDir::new();
    let lay = script(&dir, "tool/lay", "#!/bin/sh\n");
    let setting = format!("{} --space {} --quiet", lay.to_string_lossy(), WORKSPACE_TOKEN);
    let got = resolve(Some(&setting), true, None, &[]);
    assert_eq!(
        got.argv,
        Some(vec![
            lay.to_string_lossy().to_string(),
            "--space".to_string(),
            WORKSPACE_TOKEN.to_string(),
            "--quiet".to_string()
        ])
    );
    assert_eq!(got.warning, None);
    assert!(got.asked.is_empty());
}

#[test]
fn a_plugin_token_becomes_the_reported_checkout() {
    let dir = TempDir::new();
    script(&dir, "tool/bin/lay", "#!/bin/sh\n");
    let root = absolute(&dir, "tool");
    let got = resolve(
        Some("{plugin:some.plugin}/bin/lay --space {workspace}"),
        true,
        Some(&root),
        &[],
    );
    assert_eq!(
        got.argv,
        Some(vec![
            format!("{}/bin/lay", root),
            "--space".to_string(),
            WORKSPACE_TOKEN.to_string()
        ])
    );
    assert_eq!(got.asked, vec!["some.plugin"]);
    assert_eq!(got.warning, None);
}

#[test]
fn a_plugin_token_is_substituted_wherever_it_appears_in_an_argument() {
    let dir = TempDir::new();
    script(&dir, "tool/lay", "#!/bin/sh\n");
    let root = absolute(&dir, "tool");
    let setting = "{plugin:some.plugin}/lay --config={plugin:some.plugin}/c.toml";
    let got = resolve(Some(setting), true, Some(&root), &[]);
    assert_eq!(
        got.argv.unwrap()[1],
        format!("--config={}/c.toml", root)
    );
}

#[test]
fn an_uninstalled_plugin_yields_no_command_and_names_it_once() {
    let got = resolve(Some("{plugin:some.plugin}/bin/lay"), true, None, &[]);
    assert_eq!(got.argv, None);
    let warning = got.warning.expect("a missing plugin must be reported");
    assert!(warning.contains("some.plugin"), "{}", warning);
    assert!(
        warning.contains("Herdr has no checkout for"),
        "an uninstalled plugin is not the same complaint as an unrunnable command: {}",
        warning
    );
    assert_eq!(warning.matches("some.plugin").count(), 1, "{}", warning);
}

#[test]
fn two_uninstalled_plugins_are_still_one_notice() {
    let got = resolve(
        Some("{plugin:one.plugin}/lay --helper {plugin:two.plugin}/help"),
        true,
        None,
        &[],
    );
    assert_eq!(got.argv, None);
    let warning = got.warning.unwrap();
    assert!(warning.contains("one.plugin"), "{}", warning);
    assert!(!warning.contains("two.plugin"), "{}", warning);
    assert!(warning.contains("Herdr has no checkout for"), "{}", warning);
}

#[test]
fn a_command_that_cannot_be_run_yields_no_command_and_names_it() {
    let got = resolve(Some("/x/tool/lay"), true, None, &[]);
    assert_eq!(got.argv, None);
    let warning = got.warning.unwrap();
    assert!(warning.contains("/x/tool/lay"), "{}", warning);
    assert!(warning.contains("cannot be run"), "{}", warning);
}

#[test]
fn a_command_that_exists_but_is_not_executable_is_refused() {
    let dir = TempDir::new();
    let path = dir.write("tool/lay", "#!/bin/sh\n");
    let got = resolve(Some(path.to_str().unwrap()), true, None, &[]);
    assert_eq!(got.argv, None);
    assert!(got.warning.is_some());
}

#[test]
fn a_bare_name_is_resolved_on_the_path() {
    let got = resolve(Some("lay"), true, None, &["lay"]);
    let argv = got.argv.expect("a name on the PATH must resolve");
    assert!(argv[0].ends_with("/bin/lay"), "{}", argv[0]);
}

#[test]
fn an_unparseable_command_line_yields_no_command_and_warns() {
    let got = resolve(Some("/x/tool/lay --title \"unclosed"), true, None, &[]);
    assert_eq!(got.argv, None);
    assert!(got.warning.unwrap().contains("not a command line I can read"));
}

#[test]
fn an_unset_setting_yields_no_command_in_silence() {
    let got = resolve(None, true, None, &[]);
    assert_eq!(got.argv, None);
    assert_eq!(got.warning, None);
    assert!(got.asked.is_empty());
}

#[test]
fn an_emptied_setting_yields_no_command_in_silence() {
    for value in ["", "   "] {
        let got = resolve(Some(value), true, None, &[]);
        assert_eq!(got.argv, None, "{:?}", value);
        assert_eq!(got.warning, None, "{:?}", value);
    }
}

#[test]
fn a_run_that_creates_nothing_asks_herdr_nothing() {
    let got = resolve(Some("{plugin:some.plugin}/bin/lay"), false, None, &[]);
    assert_eq!(got.argv, None);
    assert_eq!(got.warning, None);
    assert!(got.asked.is_empty());
}

#[test]
fn a_run_that_creates_nothing_is_silent_with_a_broken_setting() {
    let got = resolve(Some("\"unclosed"), false, None, &[]);
    assert_eq!(got.argv, None);
    assert_eq!(got.warning, None);
    assert!(got.asked.is_empty());
}

#[test]
fn the_workspace_token_is_substituted_wherever_it_appears() {
    let argv = vec![
        "/x/lay".to_string(),
        format!("--space={}", WORKSPACE_TOKEN),
        format!("--log=/tmp/{}.log", WORKSPACE_TOKEN),
    ];
    assert_eq!(
        for_workspace(&argv, "w9"),
        vec!["/x/lay", "--space=w9", "--log=/tmp/w9.log"]
    );
}

#[test]
fn a_command_with_no_workspace_token_is_run_unchanged() {
    let argv = vec!["/x/lay".to_string()];
    assert_eq!(for_workspace(&argv, "w9"), vec!["/x/lay"]);
}

#[test]
fn no_flag_of_the_pickers_own_is_added_to_the_command() {
    let argv = vec!["/x/lay".to_string(), "--quiet".to_string()];
    assert_eq!(for_workspace(&argv, "w9").len(), argv.len());
}

#[test]
fn the_plugin_root_is_dropped_from_the_child_environment() {
    let env = Environment::from_pairs(&[("HERDR_PLUGIN_ROOT", "/x/picker"), ("PATH", "/usr/bin")]);
    let child = env.without_plugin_vars();
    assert!(!child.iter().any(|(k, _)| k == "HERDR_PLUGIN_ROOT"));
}

#[test]
fn the_plugin_config_dir_is_dropped_from_the_child_environment() {
    let env = Environment::from_pairs(&[("HERDR_PLUGIN_CONFIG_DIR", "/x/picker/cfg")]);
    assert!(env.without_plugin_vars().is_empty());
}

#[test]
fn neither_variable_being_set_is_not_an_error() {
    assert!(Environment::default().without_plugin_vars().is_empty());
}

#[test]
fn everything_else_passes_through_to_the_child() {
    let env = Environment::from_pairs(&[("SOME_TOOL_RATIO", "0.3"), ("PATH", "/usr/bin")]);
    let child = env.without_plugin_vars();
    assert!(child.contains(&("SOME_TOOL_RATIO".to_string(), "0.3".to_string())));
    assert!(child.contains(&("PATH".to_string(), "/usr/bin".to_string())));
}

#[test]
fn building_the_child_environment_leaves_the_original_alone() {
    let env = Environment::from_pairs(&[
        ("HERDR_PLUGIN_CONFIG_DIR", "/x/picker/config"),
        ("HERDR_PLUGIN_ROOT", "/x/picker"),
    ]);
    env.without_plugin_vars();
    assert_eq!(env.get("HERDR_PLUGIN_ROOT"), Some("/x/picker"));
    assert_eq!(env.get("HERDR_PLUGIN_CONFIG_DIR"), Some("/x/picker/config"));
}

fn wait_for(path: &Path) -> String {
    for _ in 0..200 {
        if let Ok(text) = std::fs::read_to_string(path) {
            if !text.is_empty() {
                return text;
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    panic!("the handed-over command never ran: {}", path.display());
}

#[test]
fn the_command_really_runs_with_the_workspace_substituted() {
    let dir = TempDir::new();
    let log = dir.join("log");
    let lay = script(
        &dir,
        "lay",
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$LAY_LOG\"\n",
    );
    let env = Environment::from_pairs(&[
        ("PATH", LAUNCHD_PATH),
        ("LAY_LOG", log.to_str().unwrap()),
    ]);
    let argv = vec![
        lay.to_string_lossy().to_string(),
        "--space".to_string(),
        WORKSPACE_TOKEN.to_string(),
        "--quiet".to_string(),
    ];
    hand_over(&argv, "w9", &env).unwrap();
    assert_eq!(wait_for(&log), "--space\nw9\n--quiet\n");
}

#[test]
fn the_command_runs_with_the_plugin_variables_stripped_and_the_rest_intact() {
    let dir = TempDir::new();
    let log = dir.join("log");
    let lay = script(
        &dir,
        "lay",
        "#!/bin/sh\nprintf 'root=[%s] ratio=[%s]\\n' \"${HERDR_PLUGIN_ROOT:-}\" \"${SOME_TOOL_RATIO:-}\" > \"$LAY_LOG\"\n",
    );
    let env = Environment::from_pairs(&[
        ("PATH", LAUNCHD_PATH),
        ("LAY_LOG", log.to_str().unwrap()),
        ("HERDR_PLUGIN_ROOT", "/x/picker"),
        ("HERDR_PLUGIN_CONFIG_DIR", "/x/picker/cfg"),
        ("SOME_TOOL_RATIO", "0.3"),
    ]);
    hand_over(&[lay.to_string_lossy().to_string()], "w9", &env).unwrap();
    assert_eq!(wait_for(&log), "root=[] ratio=[0.3]\n");
}

#[test]
fn the_command_outlives_the_picker_and_writes_nothing_to_its_screen() {
    let dir = TempDir::new();
    let log = dir.join("log");
    let lay = script(
        &dir,
        "lay",
        "#!/bin/sh\necho noise\necho more >&2\nps -o pgid= -p $$ > \"$LAY_LOG\"\n",
    );
    let env = Environment::from_pairs(&[
        ("PATH", LAUNCHD_PATH),
        ("LAY_LOG", log.to_str().unwrap()),
    ]);
    hand_over(&[lay.to_string_lossy().to_string()], "w9", &env).unwrap();
    let group = wait_for(&log).trim().to_string();
    assert_ne!(
        group,
        std::process::id().to_string(),
        "the command must be in its own session"
    );
}

#[test]
fn a_command_on_the_path_is_found_and_one_that_is_not_is_refused() {
    let dir = TempDir::new();
    let bin = dir.dir("bin");
    script(&dir, "bin/lay", "#!/bin/sh\n");
    let path = format!("{}:{}", bin.to_string_lossy(), LAUNCHD_PATH);
    let env = Environment::from_pairs(&[("PATH", &path)]);
    assert_eq!(which(&env, "lay"), Some(bin.join("lay")));
    assert_eq!(which(&env, "nothing-here-at-all"), None);
}

#[test]
fn a_command_with_a_slash_is_never_looked_up_on_the_path() {
    let dir = TempDir::new();
    let bin = dir.dir("bin");
    script(&dir, "bin/lay", "#!/bin/sh\n");
    let path = format!("{}:{}", bin.to_string_lossy(), LAUNCHD_PATH);
    let env = Environment::from_pairs(&[("PATH", &path)]);
    assert_eq!(which(&env, "./lay"), None);
}
