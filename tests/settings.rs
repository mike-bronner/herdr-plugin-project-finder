mod support;

use std::path::{Path, PathBuf};

use pick_project::config::{
    herdr_config_path, home_label, parse_env_file, parse_picker_config, read_env_file,
    read_picker_config, read_sources, resolve_root, resolve_settings, Environment, HerdrConfig,
    Settings, DEBUG_VAR, HOME_VAR, LAYOUT_VAR, PICKER_KEYS, ROOT_VAR,
};
use support::*;

fn pairs(text: &str) -> Vec<(String, String)> {
    parse_env_file(text)
}

fn value(text: &str, key: &str) -> Option<String> {
    pairs(text)
        .into_iter()
        .find(|(k, _)| k == key)
        .map(|(_, v)| v)
}

fn settings_from(
    dir: &TempDir,
    config: Option<&str>,
    env_file: Option<&str>,
    environ: &[(&str, &str)],
) -> Settings {
    let config_dir = dir.dir("plugin-config");
    if let Some(body) = config {
        std::fs::write(config_dir.join("config.toml"), body).unwrap();
    }
    if let Some(body) = env_file {
        std::fs::write(config_dir.join(".env"), body).unwrap();
    }
    let own_root = dir.dir("own");
    let mut all: Vec<(&str, &str)> = vec![("HOME", "/private/tmp")];
    let held = config_dir.to_string_lossy().to_string();
    all.push(("HERDR_PLUGIN_CONFIG_DIR", &held));
    all.extend(environ.iter().copied());
    let env = Environment::from_pairs(&all);
    let sources = read_sources(&env, &own_root);
    resolve_settings(&env, &sources)
}

#[test]
fn env_file_values_are_applied() {
    let got = pairs("HERDR_PICKER_ROOT=/x/code\nHERDR_PICKER_HOME=base\n");
    assert_eq!(
        got,
        vec![
            ("HERDR_PICKER_ROOT".to_string(), "/x/code".to_string()),
            ("HERDR_PICKER_HOME".to_string(), "base".to_string())
        ]
    );
}

#[test]
fn a_real_env_var_overrides_the_env_file() {
    let dir = TempDir::new();
    let settings = settings_from(
        &dir,
        None,
        Some("HERDR_PICKER_ROOT=/from/file\n"),
        &[(ROOT_VAR, "/from/env")],
    );
    assert_eq!(settings.root.as_deref(), Some("/from/env"));
}

#[test]
fn a_missing_env_file_is_a_noop() {
    let dir = TempDir::new();
    assert!(read_env_file(&dir.join("absent.env")).is_empty());
}

#[test]
fn an_unreadable_env_path_is_a_noop() {
    let dir = TempDir::new();
    assert!(read_env_file(dir.path()).is_empty());
}

#[test]
fn env_comments_and_blank_lines_are_skipped() {
    let got =
        pairs("# HERDR_PICKER_ROOT=/commented\n\n   # indented comment\nHERDR_PICKER_HOME=base\n");
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].1, "base");
}

#[test]
fn env_quotes_are_stripped_but_only_matching_pairs() {
    let text = "A=\"/x/one\"\nB='/x/two'\nC=\"/x/three\nD=\"\"\n";
    assert_eq!(value(text, "A").as_deref(), Some("/x/one"));
    assert_eq!(value(text, "B").as_deref(), Some("/x/two"));
    assert_eq!(value(text, "C").as_deref(), Some("\"/x/three"));
    assert_eq!(value(text, "D").as_deref(), Some(""));
}

#[test]
fn a_hash_inside_an_env_value_is_not_a_comment() {
    assert_eq!(value("A=/x/a#b\n", "A").as_deref(), Some("/x/a#b"));
}

#[test]
fn surrounding_whitespace_in_the_env_file_is_trimmed() {
    assert_eq!(value("  A = /x/a  \n", "A").as_deref(), Some("/x/a"));
}

#[test]
fn the_first_equals_wins_so_env_values_may_contain_one() {
    assert_eq!(value("A=k=v\n", "A").as_deref(), Some("k=v"));
}

#[test]
fn a_malformed_env_line_is_skipped_and_later_lines_still_apply() {
    let got = pairs("HERDR_PICKER_ROOT /x/typo\nHERDR_PICKER_HOME=base\n");
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].0, "HERDR_PICKER_HOME");
}

#[test]
fn an_empty_env_key_is_skipped() {
    let got = pairs("=/x/a\nA=/x/b\n");
    assert_eq!(got, vec![("A".to_string(), "/x/b".to_string())]);
}

#[test]
fn an_env_file_setting_the_layout_command_reads_is_carried_to_the_child() {
    let dir = TempDir::new();
    let settings = settings_from(&dir, None, Some("SOME_TOOL_RATIO=0.3\n"), &[]);
    let env = Environment::from_pairs(&[("PATH", "/usr/bin")]);
    let child = env.overlaid(&settings.env_file);
    assert_eq!(child.get("SOME_TOOL_RATIO"), Some("0.3"));
}

#[test]
fn all_four_settings_are_applied() {
    let dir = TempDir::new();
    let settings = settings_from(
        &dir,
        Some("[picker]\nroot = \"/x/code\"\nhome = \"base\"\ndebug = true\nlayout = \"/x/lay\"\n"),
        None,
        &[],
    );
    assert_eq!(settings.root.as_deref(), Some("/x/code"));
    assert_eq!(settings.home.as_deref(), Some("base"));
    assert!(settings.debug);
    assert_eq!(settings.layout.as_deref(), Some("/x/lay"));
}

#[test]
fn a_real_env_var_overrides_the_config_file() {
    let dir = TempDir::new();
    let settings = settings_from(
        &dir,
        Some("[picker]\nroot = \"/from/file\"\n"),
        None,
        &[(ROOT_VAR, "/from/env")],
    );
    assert_eq!(settings.root.as_deref(), Some("/from/env"));
}

#[test]
fn an_unknown_key_is_reported_and_the_rest_of_the_file_still_applies() {
    let dir = TempDir::new();
    let settings = settings_from(
        &dir,
        Some("[picker]\nroot = \"/x/code\"\nnonsense = \"boom\"\n"),
        None,
        &[],
    );
    assert_eq!(settings.root.as_deref(), Some("/x/code"));
    assert_eq!(settings.complaints.len(), 1, "{:?}", settings.complaints);
    assert!(
        settings.complaints[0].contains("nonsense"),
        "{:?}",
        settings.complaints
    );
}

#[test]
fn a_key_outside_the_picker_table_is_not_claimed() {
    let dir = TempDir::new();
    let settings = settings_from(
        &dir,
        Some("[worktrees]\nroot = \"/x/wrong\"\n\n[picker]\nroot = \"/x/right\"\n"),
        None,
        &[],
    );
    assert_eq!(settings.root.as_deref(), Some("/x/right"));
}

#[test]
fn a_missing_config_file_is_a_noop() {
    let dir = TempDir::new();
    let settings = settings_from(&dir, None, None, &[]);
    assert_eq!(settings.root, None);
    assert!(settings.complaints.is_empty());
}

#[test]
fn an_unreadable_config_path_is_a_noop() {
    let config = read_picker_config(TempDir::new().path());
    assert!(config.error.is_none());
    assert!(config.table.root.is_none());
}

#[test]
fn a_malformed_config_file_sets_nothing_and_says_so() {
    let dir = TempDir::new();
    let settings = settings_from(&dir, Some("[picker\nroot /x/code\n}{\n"), None, &[]);
    assert_eq!(settings.root, None);
    assert_eq!(settings.complaints.len(), 1);
    assert!(
        settings.complaints[0].contains("does not parse"),
        "{:?}",
        settings.complaints
    );
}

#[test]
fn one_malformed_line_now_voids_the_whole_config_file() {
    let dir = TempDir::new();
    let settings = settings_from(
        &dir,
        Some("[picker]\nroot /x/typo\nhome = \"base\"\n"),
        None,
        &[],
    );
    assert_eq!(
        settings.home, None,
        "the line after a syntax error must not survive"
    );
    assert_eq!(settings.complaints.len(), 1);
}

#[test]
fn a_duplicate_key_voids_the_file_rather_than_keeping_the_first() {
    let dir = TempDir::new();
    let settings = settings_from(
        &dir,
        Some("[picker]\nroot = \"/x/one\"\nroot = \"/x/two\"\n"),
        None,
        &[],
    );
    assert_eq!(settings.root, None);
    assert!(settings.complaints[0].contains("does not parse"));
}

#[test]
fn a_setting_of_the_wrong_type_voids_the_file() {
    let dir = TempDir::new();
    let settings = settings_from(&dir, Some("[picker]\nroot = [\"a\", \"b\"]\n"), None, &[]);
    assert_eq!(settings.root, None);
    assert_eq!(settings.complaints.len(), 1);
}

#[test]
fn debug_false_leaves_debugging_off() {
    let dir = TempDir::new();
    assert!(!settings_from(&dir, Some("[picker]\ndebug = false\n"), None, &[]).debug);
}

#[test]
fn debug_true_turns_debugging_on() {
    let dir = TempDir::new();
    assert!(settings_from(&dir, Some("[picker]\ndebug = true\n"), None, &[]).debug);
}

#[test]
fn only_the_word_true_turns_debugging_on() {
    let dir = TempDir::new();
    let settings = settings_from(&dir, Some("[picker]\ndebug = 1\n"), None, &[]);
    assert!(!settings.debug, "a non-boolean must not read as true");
}

#[test]
fn the_env_file_alone_still_works() {
    let dir = TempDir::new();
    let settings = settings_from(&dir, None, Some("HERDR_PICKER_ROOT=/from/env-file\n"), &[]);
    assert_eq!(settings.root.as_deref(), Some("/from/env-file"));
}

#[test]
fn config_toml_wins_over_the_env_file() {
    let dir = TempDir::new();
    let settings = settings_from(
        &dir,
        Some("[picker]\nroot = \"/from/toml\"\n"),
        Some("HERDR_PICKER_ROOT=/from/env-file\n"),
        &[],
    );
    assert_eq!(settings.root.as_deref(), Some("/from/toml"));
}

#[test]
fn the_env_file_still_supplies_what_the_toml_omits() {
    let dir = TempDir::new();
    let settings = settings_from(
        &dir,
        Some("[picker]\nroot = \"/from/toml\"\n"),
        Some("HERDR_PICKER_HOME=base\n"),
        &[],
    );
    assert_eq!(settings.root.as_deref(), Some("/from/toml"));
    assert_eq!(settings.home.as_deref(), Some("base"));
}

#[test]
fn a_real_env_var_beats_both_files() {
    let dir = TempDir::new();
    let settings = settings_from(
        &dir,
        Some("[picker]\nroot = \"/from/toml\"\n"),
        Some("HERDR_PICKER_ROOT=/from/env-file\n"),
        &[(ROOT_VAR, "/from/env")],
    );
    assert_eq!(settings.root.as_deref(), Some("/from/env"));
}

#[test]
fn debug_false_in_the_toml_beats_the_env_file_turning_it_on() {
    let dir = TempDir::new();
    let settings = settings_from(
        &dir,
        Some("[picker]\ndebug = false\n"),
        Some("HERDR_PICKER_DEBUG=1\n"),
        &[],
    );
    assert!(!settings.debug);
}

#[test]
fn a_real_debug_var_beats_the_toml_turning_it_off() {
    let dir = TempDir::new();
    let settings = settings_from(
        &dir,
        Some("[picker]\ndebug = false\n"),
        None,
        &[(DEBUG_VAR, "1")],
    );
    assert!(settings.debug);
}

#[test]
fn an_emptied_debug_var_reads_as_off() {
    let dir = TempDir::new();
    let settings = settings_from(
        &dir,
        Some("[picker]\ndebug = true\n"),
        None,
        &[(DEBUG_VAR, "")],
    );
    assert!(!settings.debug);
}

#[test]
fn a_malformed_toml_leaves_the_env_file_working() {
    let dir = TempDir::new();
    let settings = settings_from(
        &dir,
        Some("[picker\nroot /x/typo\n"),
        Some("HERDR_PICKER_ROOT=/from/env-file\n"),
        &[],
    );
    assert_eq!(settings.root.as_deref(), Some("/from/env-file"));
}

fn shipped() -> pick_project::config::PickerConfig {
    read_picker_config(&manifest_dir().join("defaults.toml"))
}

#[test]
fn the_shipped_defaults_supply_a_layout_setting() {
    let config = shipped();
    assert!(config.error.is_none(), "{:?}", config.error);
    assert!(config.table.layout.is_some());
}

#[test]
fn the_shipped_defaults_name_no_key_the_picker_lacks() {
    assert!(shipped().unknown.is_empty(), "{:?}", shipped().unknown);
}

#[test]
fn the_default_is_what_an_unconfigured_picker_runs() {
    let dir = TempDir::new();
    let config_dir = dir.dir("plugin-config");
    let env = Environment::from_pairs(&[
        ("HOME", "/private/tmp"),
        ("HERDR_PLUGIN_CONFIG_DIR", config_dir.to_str().unwrap()),
    ]);
    let sources = read_sources(&env, &manifest_dir());
    let settings = resolve_settings(&env, &sources);
    assert_eq!(settings.layout, shipped().table.layout);
}

#[test]
fn a_user_setting_beats_the_shipped_default() {
    let dir = TempDir::new();
    let config_dir = dir.dir("plugin-config");
    std::fs::write(
        config_dir.join("config.toml"),
        "[picker]\nlayout = \"/x/mine\"\n",
    )
    .unwrap();
    let env = Environment::from_pairs(&[
        ("HOME", "/private/tmp"),
        ("HERDR_PLUGIN_CONFIG_DIR", config_dir.to_str().unwrap()),
    ]);
    let sources = read_sources(&env, &manifest_dir());
    assert_eq!(
        resolve_settings(&env, &sources).layout.as_deref(),
        Some("/x/mine")
    );
}

#[test]
fn an_env_file_beats_the_shipped_default_too() {
    let dir = TempDir::new();
    let config_dir = dir.dir("plugin-config");
    std::fs::write(
        config_dir.join(".env"),
        "HERDR_PICKER_LAYOUT=/x/from-env-file\n",
    )
    .unwrap();
    let env = Environment::from_pairs(&[
        ("HOME", "/private/tmp"),
        ("HERDR_PLUGIN_CONFIG_DIR", config_dir.to_str().unwrap()),
    ]);
    let sources = read_sources(&env, &manifest_dir());
    assert_eq!(
        resolve_settings(&env, &sources).layout.as_deref(),
        Some("/x/from-env-file")
    );
}

#[test]
fn the_default_still_lays_a_workspace_out_the_way_it_used_to() {
    use pick_project::layout::{resolve_layout, WORKSPACE_TOKEN};
    let dir = TempDir::new();
    let tool = dir.dir("panes/bin");
    let command = tool.join("agent-layout");
    std::fs::write(&command, "#!/bin/sh\n").unwrap();
    make_executable(&command);
    let asked = std::cell::RefCell::new(Vec::new());
    let env = Environment::from_pairs(&[("PATH", LAUNCHD_PATH), ("HOME", "/private/tmp")]);
    let resolved = resolve_layout(
        true,
        shipped().table.layout.as_deref(),
        &env,
        &|plugin_id| {
            asked.borrow_mut().push(plugin_id.to_string());
            Some(dir.join("panes").to_string_lossy().to_string())
        },
    );
    assert_eq!(resolved.warning, None);
    assert_eq!(asked.into_inner(), vec!["mikebronner.agentic-panes-layout"]);
    assert_eq!(
        resolved.argv,
        Some(vec![
            command.to_string_lossy().to_string(),
            "--workspace".to_string(),
            WORKSPACE_TOKEN.to_string()
        ])
    );
}

#[test]
fn the_shipped_default_does_not_name_this_plugin() {
    assert!(!read_repo_file("defaults.toml").contains("project-finder"));
}

#[test]
fn the_readme_names_the_plugin_the_default_points_at() {
    assert!(read_repo_file("README.md").contains("agentic-panes-layout"));
}

#[test]
fn no_foreign_setting_is_copied_into_this_repo() {
    for name in ["defaults.toml", "README.md"] {
        for line in read_repo_file(name).lines() {
            let trimmed = line.trim_start().trim_start_matches("export ");
            assert!(
                !(trimmed.starts_with("AGENT_LAYOUT_") && trimmed.contains('=')),
                "{} carries a foreign setting: {}",
                name,
                line
            );
        }
    }
}

#[test]
fn the_source_names_no_plugin_executable_or_flag_of_anothers() {
    let source = repo_source();
    for token in [
        "agentic-panes-layout",
        "agent-layout",
        "AGENT_LAYOUT",
        "mikebronner.",
        "--agent-name",
        "--no-agent",
    ] {
        assert!(!source.contains(token), "the source names {}", token);
    }
}

#[test]
fn the_source_reads_only_its_own_settings() {
    let source = repo_source();
    let mut read: Vec<String> = Vec::new();
    let mut rest = source.as_str();
    while let Some(at) = rest.find("env.get(\"") {
        let after = &rest[at + "env.get(\"".len()..];
        let end = after.find('"').unwrap_or(0);
        read.push(after[..end].to_string());
        rest = &after[end..];
    }
    assert!(!read.is_empty());
    for name in read {
        assert!(
            name.starts_with("HERDR_PICKER_")
                || name.starts_with("HERDR_PLUGIN_")
                || name.starts_with("HERDR_CONFIG_")
                || name.starts_with("HERDR_BIN_")
                || name.starts_with("HERDR_SOCKET_")
                || name == "PATH"
                || name == "HOME",
            "the picker reads {}",
            name
        );
    }
}

#[test]
fn only_this_plugins_settings_have_a_home_in_its_config_toml() {
    for (_, var) in PICKER_KEYS {
        assert!(var.starts_with("HERDR_PICKER_"), "{}", var);
    }
}

#[test]
fn the_picker_table_header_is_documented() {
    assert!(read_repo_file("README.md").lines().any(|l| l == "[picker]"));
}

#[test]
fn every_picker_key_is_documented_exactly_once() {
    let readme = read_repo_file("README.md");
    for (key, _) in PICKER_KEYS {
        let bare = key.split_once('.').unwrap().1;
        let at = readme
            .lines()
            .filter(|l| l.starts_with(&format!("{} = ", bare)))
            .count();
        assert_eq!(at, 1, "{} is documented {} times", key, at);
    }
}

#[test]
fn every_picker_key_names_the_variable_it_stands_in_for() {
    let readme = read_repo_file("README.md");
    for (_, var) in PICKER_KEYS {
        assert!(readme.contains(var), "the README never names {}", var);
    }
}

fn root_of(env: &Environment, root: Option<&str>) -> PathBuf {
    let settings = Settings {
        root: root.map(str::to_string),
        home: None,
        debug: false,
        layout: None,
        complaints: Vec::new(),
        env_file: Vec::new(),
    };
    resolve_root(env, &settings)
}

#[test]
fn an_existing_configured_root_is_kept() {
    let dir = TempDir::new();
    let env = bare_env(&[]);
    assert_eq!(root_of(&env, dir.path().to_str()), dir.path());
}

#[test]
fn a_nonexistent_root_falls_back_to_home() {
    let dir = TempDir::new();
    let home = dir.path().to_string_lossy().to_string();
    let env = Environment::from_pairs(&[("HOME", &home)]);
    assert_eq!(root_of(&env, Some("/x/does/not/exist")), dir.path());
}

#[test]
fn an_unset_root_uses_the_home_folder() {
    let dir = TempDir::new();
    let home = dir.path().to_string_lossy().to_string();
    let env = Environment::from_pairs(&[("HOME", &home)]);
    assert_eq!(root_of(&env, None), dir.path());
}

#[test]
fn an_empty_root_is_treated_as_unset() {
    let dir = TempDir::new();
    let home = dir.path().to_string_lossy().to_string();
    let env = Environment::from_pairs(&[("HOME", &home)]);
    assert_eq!(root_of(&env, Some("")), dir.path());
}

#[test]
fn a_tilde_in_the_root_is_expanded() {
    let dir = TempDir::new();
    let sub = dir.dir("Code");
    let home = dir.path().to_string_lossy().to_string();
    let env = Environment::from_pairs(&[("HOME", &home)]);
    assert_eq!(root_of(&env, Some("~/Code")), sub);
}

#[test]
fn an_expanded_tilde_that_is_not_a_directory_falls_back() {
    let dir = TempDir::new();
    dir.dir("Code");
    let home = dir.path().to_string_lossy().to_string();
    let env = Environment::from_pairs(&[("HOME", &home)]);
    assert_eq!(root_of(&env, Some("~/Nope")), dir.path());
}

#[test]
fn the_home_label_defaults_to_a_tilde() {
    let settings = Settings {
        root: None,
        home: None,
        debug: false,
        layout: None,
        complaints: Vec::new(),
        env_file: Vec::new(),
    };
    assert_eq!(home_label(&settings), "~");
}

fn plugin_config_dir(dir: &TempDir, with_config: bool) -> (PathBuf, PathBuf) {
    let nested = dir.dir("plugins/config/x.y");
    let root = dir.join("config.toml");
    if with_config {
        std::fs::write(&root, "").unwrap();
    }
    (nested, root)
}

#[test]
fn the_documented_config_override_wins() {
    let env = bare_env(&[("HERDR_CONFIG_PATH", "/x/other.toml")]);
    assert_eq!(herdr_config_path(&env), Path::new("/x/other.toml"));
}

#[test]
fn the_config_override_beats_the_plugin_config_dir() {
    let dir = TempDir::new();
    let (nested, _) = plugin_config_dir(&dir, true);
    let env = bare_env(&[
        ("HERDR_CONFIG_PATH", "/x/other.toml"),
        ("HERDR_PLUGIN_CONFIG_DIR", nested.to_str().unwrap()),
    ]);
    assert_eq!(herdr_config_path(&env), Path::new("/x/other.toml"));
}

#[test]
fn the_config_override_is_tilde_expanded() {
    let env = Environment::from_pairs(&[("HOME", "/x/home"), ("HERDR_CONFIG_PATH", "~/c.toml")]);
    assert_eq!(herdr_config_path(&env), Path::new("/x/home/c.toml"));
}

#[test]
fn the_config_is_derived_from_the_plugin_config_dir() {
    let dir = TempDir::new();
    let (nested, expected) = plugin_config_dir(&dir, true);
    let env = bare_env(&[("HERDR_PLUGIN_CONFIG_DIR", nested.to_str().unwrap())]);
    assert_eq!(herdr_config_path(&env), expected);
}

#[test]
fn a_trailing_separator_does_not_shift_the_derivation() {
    let dir = TempDir::new();
    let (nested, expected) = plugin_config_dir(&dir, true);
    let with_slash = format!("{}/", nested.to_string_lossy());
    let env = bare_env(&[("HERDR_PLUGIN_CONFIG_DIR", &with_slash)]);
    assert_eq!(herdr_config_path(&env), expected);
}

#[test]
fn a_derivation_that_finds_no_file_falls_back_to_the_default() {
    let dir = TempDir::new();
    let (nested, _) = plugin_config_dir(&dir, false);
    let env = Environment::from_pairs(&[
        ("HOME", "/x/home"),
        ("HERDR_PLUGIN_CONFIG_DIR", nested.to_str().unwrap()),
    ]);
    assert_eq!(
        herdr_config_path(&env),
        Path::new("/x/home/.config/herdr/config.toml")
    );
}

#[test]
fn nothing_set_uses_the_documented_default() {
    let env = Environment::from_pairs(&[("HOME", "/x/home")]);
    assert_eq!(
        herdr_config_path(&env),
        Path::new("/x/home/.config/herdr/config.toml")
    );
}

#[test]
fn a_scalar_is_read_under_its_table() {
    let config = HerdrConfig::parse("[theme]\nname = \"dracula\"\n");
    assert_eq!(config.string("theme.name"), Some("dracula"));
}

#[test]
fn the_same_key_in_two_tables_does_not_collide() {
    let config = HerdrConfig::parse("[ui]\nname = \"wrong\"\n\n[theme]\nname = \"nord\"\n");
    assert_eq!(config.string("theme.name"), Some("nord"));
    assert_eq!(config.string("ui.name"), Some("wrong"));
}

#[test]
fn a_nested_table_header_is_kept_whole() {
    let config = HerdrConfig::parse("[theme.custom]\nred = \"#ff0000\"\n");
    assert_eq!(config.string("theme.custom.red"), Some("#ff0000"));
}

#[test]
fn a_key_before_any_table_header_is_not_claimed_by_one() {
    let config = HerdrConfig::parse("name = \"root\"\n[theme]\nname = \"nord\"\n");
    assert_eq!(config.string("theme.name"), Some("nord"));
    assert_eq!(config.string("name"), Some("root"));
}

#[test]
fn comments_and_blank_lines_in_herdrs_config_are_skipped() {
    let config =
        HerdrConfig::parse("# [theme]\n# name = \"commented\"\n\n[theme]\nname = \"nord\"\n");
    assert_eq!(config.string("theme.name"), Some("nord"));
}

#[test]
fn a_trailing_comment_is_not_part_of_the_value() {
    let config = HerdrConfig::parse("[theme.custom]\nred = \"#ff8800\"  # accent\n");
    assert_eq!(config.string("theme.custom.red"), Some("#ff8800"));
}

#[test]
fn single_quotes_work_too() {
    let config = HerdrConfig::parse("[theme]\nname = 'nord'\n");
    assert_eq!(config.string("theme.name"), Some("nord"));
}

#[test]
fn an_array_is_not_read_as_a_string() {
    let config = HerdrConfig::parse("[theme]\nname = [\"a\", \"b\"]\n");
    assert_eq!(config.string("theme.name"), None);
    assert!(
        config.has("theme.name"),
        "the key is still present, just not a string"
    );
}

#[test]
fn a_missing_herdr_config_is_empty_rather_than_an_error() {
    let dir = TempDir::new();
    assert!(!HerdrConfig::read(&dir.join("absent.toml")).has("theme.name"));
}

#[test]
fn an_unreadable_herdr_config_path_is_empty() {
    let dir = TempDir::new();
    assert!(!HerdrConfig::read(dir.path()).has("theme.name"));
}

#[test]
fn a_malformed_herdr_config_voids_every_key_in_it() {
    let config = HerdrConfig::parse("[theme]\nname = \"nord\"\n[broken\n");
    assert_eq!(config.string("theme.name"), None);
}

#[test]
fn a_braced_string_is_still_a_string() {
    let config = parse_picker_config("[picker]\nlayout = \"{plugin:x.y}/bin/lay {workspace}\"\n");
    assert_eq!(
        config.table.layout.as_deref(),
        Some("{plugin:x.y}/bin/lay {workspace}")
    );
}

#[test]
fn a_boolean_is_read_as_a_boolean() {
    let config = HerdrConfig::parse("[theme]\nauto_switch = true\n");
    assert_eq!(config.boolean("theme.auto_switch"), Some(true));
    assert_eq!(config.string("theme.auto_switch"), None);
}

fn make_executable(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    let mut perms = std::fs::metadata(path).unwrap().permissions();
    perms.set_mode(0o755);
    std::fs::set_permissions(path, perms).unwrap();
}

#[test]
fn the_layout_and_home_variables_keep_their_documented_names() {
    assert_eq!(LAYOUT_VAR, "HERDR_PICKER_LAYOUT");
    assert_eq!(HOME_VAR, "HERDR_PICKER_HOME");
}
