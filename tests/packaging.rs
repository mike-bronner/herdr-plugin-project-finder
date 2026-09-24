mod support;

use std::path::PathBuf;
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

const SECTIONS: [&str; 3] = ["build", "startup", "panes"];

const DOUBLED_SECTIONS: [&str; 2] = ["build", "startup"];

fn declared_platforms() -> Vec<String> {
    manifest()["platforms"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect()
}

fn entries(section: &str) -> Vec<toml::Value> {
    manifest()[section]
        .as_array()
        .unwrap_or_else(|| panic!("herdr-plugin.toml declares no {}", section))
        .clone()
}

fn command_of(entry: &toml::Value) -> Vec<String> {
    entry["command"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect()
}

fn runs_on(entry: &toml::Value, platform: &str) -> bool {
    match entry.get("platforms") {
        Some(named) => named
            .as_array()
            .unwrap()
            .iter()
            .any(|v| v.as_str() == Some(platform)),
        None => true,
    }
}

fn commands_for(section: &str, platform: &str) -> Vec<Vec<String>> {
    entries(section)
        .iter()
        .filter(|entry| runs_on(entry, platform))
        .map(command_of)
        .collect()
}

fn command_for(section: &str, platform: &str) -> Vec<String> {
    let matched = commands_for(section, platform);
    assert_eq!(
        matched.len(),
        1,
        "{} has {} entries that run on {}; one platform must resolve to exactly one command",
        section,
        matched.len(),
        platform
    );
    matched[0].clone()
}

fn pane_command(id: &str) -> Vec<String> {
    let matched: Vec<toml::Value> = entries("panes")
        .into_iter()
        .filter(|entry| entry["id"].as_str() == Some(id))
        .collect();
    assert_eq!(
        matched.len(),
        1,
        "the manifest declares no single pane `{}`",
        id
    );
    command_of(&matched[0])
}

fn strings(of: &[&[&str]]) -> Vec<Vec<String>> {
    of.iter()
        .map(|command| command.iter().map(|a| a.to_string()).collect())
        .collect()
}

#[test]
fn every_declared_platform_resolves_each_entry_to_one_command() {
    for platform in declared_platforms() {
        for section in SECTIONS {
            let commands = commands_for(section, &platform);
            assert!(
                !commands.is_empty(),
                "{} on {} names no command",
                section,
                platform
            );
            for (at, command) in commands.iter().enumerate() {
                assert!(
                    !command.is_empty(),
                    "{} on {} names an empty command",
                    section,
                    platform
                );
                assert!(
                    !command.iter().any(|a| a.contains("target/")),
                    "a build-artifact path breaks the moment the profile changes: {:?}",
                    command
                );
                assert!(
                    !commands[..at].contains(command),
                    "{} runs {:?} twice on {}",
                    section,
                    command,
                    platform
                );
            }
        }
    }
}

#[test]
fn the_shell_entry_is_declared_before_the_powershell_one() {
    for section in DOUBLED_SECTIONS {
        let all: Vec<Vec<String>> = entries(section).iter().map(command_of).collect();
        let shells: Vec<usize> = (0..all.len()).filter(|&at| all[at][0] == "sh").collect();
        assert!(!shells.is_empty(), "{} declares no shell entry", section);
        for shell in shells {
            let mut twin = vec![
                "powershell".to_string(),
                "-File".to_string(),
                format!("{}.ps1", all[shell][1]),
            ];
            twin.extend(all[shell][2..].iter().cloned());
            let Some(powershell) = all.iter().position(|command| *command == twin) else {
                panic!(
                    "{} does not declare a PowerShell twin of {:?}",
                    section, all[shell]
                );
            };
            assert!(
            shell < powershell,
            "whether Herdr filters a build or a startup step by platform before it runs one is \
             unverified; if it ever took the first entry regardless, the tested platforms have \
             to be the ones that win: {} {:?}",
                section,
                all[shell]
            );
        }
    }
}

#[test]
fn the_manifest_declares_the_picker_and_the_dialog_under_distinct_ids() {
    let panes = entries("panes");
    let ids: Vec<&str> = panes.iter().filter_map(|p| p["id"].as_str()).collect();
    assert_eq!(
        ids,
        vec!["picker", herdr_plugin_kit::dialog::ENTRYPOINT],
        "measured against a live Herdr 0.9.0: a pane id has to be unique across every entry, \
         whatever `platforms` says, so a second entry sharing the id is refused with `manifest \
         unavailable: duplicate pane id` and the whole plugin falls back to the manifest Herdr \
         last cached — which is how 0.9.0 left the keybinding running a Python file that \
         release had deleted. So the picker is declared once, under the id the keybinding \
         names, and the update dialog under the id herdr-plugin-kit opens it by. {:?}",
        panes
    );
}

#[test]
fn the_panes_are_dispatched_through_the_shim_not_a_build_artifact() {
    for platform in declared_platforms() {
        assert_eq!(
            commands_for("panes", &platform),
            strings(&[
                &["sh", "bin/launcher"],
                &["sh", "bin/launcher", pick_project::version::DIALOG_FLAG],
            ]),
            "each pane answers for every declared platform, and Windows being handed `sh` is \
             the known cost of that: {}",
            platform
        );
    }
    assert_eq!(pane_command("picker"), vec!["sh", "bin/launcher"]);
    assert_eq!(
        pane_command(herdr_plugin_kit::dialog::ENTRYPOINT),
        vec!["sh", "bin/launcher", "--dialog"]
    );
}

#[test]
fn the_readme_binds_the_key_to_the_entrypoint_the_manifest_declares() {
    let declared = entries("panes")[0]["id"].as_str().unwrap().to_string();
    let readme = read_repo_file("README.md");
    let named: Vec<String> = readme
        .lines()
        .filter(|line| line.contains("--entrypoint"))
        .map(|line| {
            line.split_whitespace()
                .skip_while(|word| *word != "--entrypoint")
                .nth(1)
                .unwrap_or_else(|| {
                    panic!(
                        "a documented command names --entrypoint with no id: {}",
                        line
                    )
                })
                .trim_matches(|c: char| !c.is_alphanumeric() && c != '-' && c != '_')
                .to_string()
        })
        .collect();

    assert!(
        !named.is_empty(),
        "README.md documents no --entrypoint, so nothing holds the keybinding to the pane"
    );
    for entrypoint in named {
        assert_eq!(
            entrypoint, declared,
            "the documented keybinding opens an entrypoint the manifest does not declare, which \
             Herdr answers with a pane that never opens"
        );
    }
}

#[test]
fn the_manifest_declares_a_build_step_so_a_github_install_shows_one() {
    assert_eq!(
        command_for("build", "macos"),
        vec!["sh", "bin/build", "--install"]
    );
    assert_eq!(
        command_for("build", "windows"),
        vec!["powershell", "-File", "bin/build.ps1", "--install"]
    );
}

#[test]
fn the_manifest_rebuilds_at_server_start_so_a_linked_plugin_is_covered_too() {
    assert_eq!(command_for("startup", "macos"), vec!["sh", "bin/build"]);
    assert_eq!(command_for("startup", "linux"), vec!["sh", "bin/build"]);
    assert_eq!(
        command_for("startup", "windows"),
        vec!["powershell", "-File", "bin/build.ps1"]
    );
}

#[test]
fn the_startup_step_never_carries_the_install_flag_that_skips_asking_herdr() {
    for command in entries("startup").iter().map(command_of) {
        assert!(
            !command.iter().any(|a| a == "--install"),
            "the flag says this is a GitHub install by construction, which is false on a \
             server restart: {:?}",
            command
        );
    }
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
fn the_readme_pins_its_install_example_to_the_version_the_manifest_declares() {
    let parsed = manifest();
    let version = parsed["version"].as_str().unwrap();
    let readme = read_repo_file("README.md");
    let pinned: Vec<&str> = readme
        .lines()
        .filter(|line| line.contains("plugin install") && line.contains("--ref"))
        .map(|line| {
            line.split_whitespace()
                .skip_while(|word| *word != "--ref")
                .nth(1)
                .unwrap_or_else(|| panic!("an install example names --ref with no tag: {}", line))
        })
        .filter(|tag| *tag != "<tag>")
        .collect();

    assert!(
        !pinned.is_empty(),
        "README.md shows no --ref install example, so nothing holds the documented version to the manifest"
    );
    for tag in pinned {
        assert_eq!(
            tag, version,
            "the README pins an install to a tag the manifest does not declare; a release tag is the version verbatim, with no v prefix from 0.8.0 on"
        );
    }
}

#[test]
fn the_readme_names_the_command_an_accepted_update_really_runs() {
    let command = format!(
        "herdr {}",
        herdr_plugin_kit::update::install_arguments(
            "mike-bronner",
            "herdr-plugin-project-finder",
            "<tag>"
        )
        .join(" ")
    );
    assert!(
        read_repo_file("README.md")
            .lines()
            .any(|line| line.trim() == command),
        "README.md does not show the command the update offer runs: {}",
        command
    );
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
        "api.rs",
        "app.rs",
        "config.rs",
        "picker.rs",
        "ui.rs",
        "version.rs",
        "update.rs",
        "mod.rs",
    ] {
        assert!(
            found.contains(&name.to_string()),
            "{} was not scanned",
            name
        );
    }
}

fn cargo_metadata() -> serde_json::Value {
    let out = Command::new("cargo")
        .args(["metadata", "--no-deps", "--format-version", "1"])
        .current_dir(manifest_dir())
        .output()
        .expect("cannot run cargo metadata");
    assert!(
        out.status.success(),
        "cargo could not read this plugin's manifest: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    serde_json::from_slice(&out.stdout).expect("cargo metadata is not JSON")
}

fn kit_pins() -> Vec<(String, String)> {
    let metadata = cargo_metadata();
    let mut found = Vec::new();
    for package in metadata["packages"].as_array().unwrap() {
        for dependency in package["dependencies"].as_array().unwrap() {
            let name = dependency["name"].as_str().unwrap_or("");
            if !name.starts_with("herdr-plugin-kit") {
                continue;
            }
            let source = dependency["source"].as_str().unwrap_or("");
            let tag = source
                .split_once("?tag=")
                .unwrap_or_else(|| {
                    panic!(
                        "{} is not pinned to a tag; cargo reports {:?}",
                        name, source
                    )
                })
                .1
                .to_string();
            found.push((name.to_string(), tag));
        }
    }
    found
}

fn workflow(name: &str) -> String {
    read_repo_file(&format!(".github/workflows/{}", name))
}

fn called_kit_tag() -> String {
    workflow("release.yml")
        .lines()
        .map(str::trim)
        .find_map(|line| line.strip_prefix("uses:")?.trim().split_once('@'))
        .map(|(_, tag)| tag.to_string())
        .expect("release.yml calls no reusable workflow at a tag")
}

#[test]
fn every_kit_pin_in_the_repository_names_the_same_tag() {
    let pins = kit_pins();
    assert_eq!(
        pins.len(),
        2,
        "the crate and the build stamp are two separate pins, and both have to be here: {:?}",
        pins
    );

    let mut named: Vec<String> = pins.iter().map(|(_, tag)| tag.clone()).collect();
    named.push(called_kit_tag());
    named.dedup();
    assert_eq!(
        named.len(),
        1,
        "a crate pin and a CI pin that disagree check this plugin against one kit and build \
         it against another, and the kit's own kit-pins gate runs only at release: {:?} \
         against the workflow's {}",
        pins,
        called_kit_tag()
    );
}

#[test]
fn the_release_workflow_calls_the_kits_own_and_grants_the_write_it_needs() {
    let text = workflow("release.yml");
    assert!(
        text.contains("mike-bronner/herdr-plugin-kit/.github/workflows/plugin-release.yml@"),
        "the release is the kit's reusable workflow, not a copy of it: {}",
        text
    );
    assert!(
        text.matches("contents: write").count() >= 1,
        "a called workflow runs on the caller's permissions and cannot raise its own, so a \
         caller that omits this fails before it starts: {}",
        text
    );
}

#[test]
fn the_ci_workflow_runs_every_conformance_gate_against_a_full_clone() {
    let text = workflow("ci.yml");
    for gate in [
        "kit/templates/sync_bin.py . --check",
        "kit/tools/plugin_gate.py versions .",
        "kit/tools/plugin_gate.py pin-block .",
    ] {
        assert!(text.contains(gate), "ci.yml never runs `{}`", gate);
    }
    assert!(
        text.contains("fetch-depth: 0"),
        "the version gate reads tag history, and a shallow clone lets it pass by seeing no \
         releases at all"
    );
}

#[test]
fn the_ci_workflow_compiles_every_target_the_release_publishes() {
    let text = workflow("ci.yml");
    assert!(
        text.contains("kit/tools/plugin_gate.py matrix"),
        "the target table comes from the kit; a copy here could disagree with the one the \
         kit's own tests cover, and the two would disagree in silence"
    );
    assert!(
        text.contains("fromJSON(needs.kit-gates.outputs.matrix)"),
        "the build job has to take its targets from that table rather than listing its own"
    );
    assert!(
        text.contains("cargo build --locked --target"),
        "nobody here has Windows hardware, so this job is the only reader src/layout.rs's \
         Windows paths ever get"
    );
}

#[test]
fn the_manifest_names_only_shims_that_are_really_there() {
    let parsed = manifest();
    let mut named = 0;
    for section in ["build", "startup", "panes"] {
        for entry in parsed[section].as_array().unwrap() {
            for argument in entry["command"].as_array().unwrap() {
                let argument = argument.as_str().unwrap();
                if !argument.starts_with("bin/") {
                    continue;
                }
                named += 1;
                assert!(
                    manifest_dir().join(argument).is_file(),
                    "the manifest dispatches {} through {}, which is not there",
                    section,
                    argument
                );
            }
        }
    }
    assert!(named >= 3, "only {} shim commands were scanned", named);
    assert!(
        !manifest_dir().join("bin/pick-project").exists(),
        "the hand-written shim is replaced by the kit's bin/launcher, and a copy left behind \
         is one a stale keybinding can still reach"
    );
}

#[test]
fn git_really_ignores_the_developer_override() {
    let out = Command::new("git")
        .args(["check-ignore", "BUILD_FROM_SOURCE"])
        .current_dir(manifest_dir())
        .output()
        .expect("cannot run git check-ignore");
    assert!(
        out.status.success(),
        "committing the override turns every install of that release into a source build, and \
         says so only in a line of a server log nobody is watching"
    );
}

#[test]
fn the_crate_declares_no_toolchain_floor() {
    let parsed = read_repo_file("Cargo.toml").parse::<toml::Table>().unwrap();
    assert!(
        parsed["package"].get("rust-version").is_none(),
        "a floor is a promise to people who compile this, and nobody has to: the plugin \
         installs a published binary"
    );
}

#[test]
fn the_python_implementation_and_its_suite_are_gone() {
    assert!(!manifest_dir().join("tests/test_pick_project.py").exists());
    assert!(!read_repo_file("herdr-plugin.toml").contains("python3"));
}

#[test]
fn nothing_in_the_repo_still_names_fzf_as_a_dependency() {
    for name in ["herdr-plugin.toml", "Cargo.toml", "README.md"] {
        assert!(
            !read_repo_file(name).contains("fzf"),
            "{} still names fzf",
            name
        );
    }
}
