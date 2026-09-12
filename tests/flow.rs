mod support;

use std::path::{Path, PathBuf};

use serde_json::json;

use pick_project::app::{self, Fatal, Outcome};
use pick_project::config::Environment;
use pick_project::picker::Entry;
use support::*;

struct World {
    tree: TempDir,
    own: TempDir,
    config_dir: PathBuf,
}

impl World {
    fn new() -> World {
        let tree = TempDir::new();
        let own = TempDir::new();
        let config_dir = own.dir("plugin-config");
        World {
            tree,
            own,
            config_dir,
        }
    }

    fn repo(&self, rel: &str) -> PathBuf {
        make_repo(&self.tree.join(rel))
    }

    fn configure(&self, body: &str) {
        std::fs::write(self.config_dir.join("config.toml"), body).unwrap();
    }

    fn env(&self, stub: &Stub, extra: &[(&str, &str)]) -> Environment {
        let root = self.tree.path().to_string_lossy().to_string();
        let config = self.config_dir.to_string_lossy().to_string();
        let mut pairs: Vec<(&str, &str)> = vec![
            ("HERDR_PICKER_ROOT", &root),
            ("HERDR_PLUGIN_CONFIG_DIR", &config),
        ];
        pairs.extend(extra.iter().copied());
        env_for(stub, self.tree.path(), &pairs)
    }

    fn own_root(&self) -> &Path {
        self.own.path()
    }
}

struct Run {
    outcome: Result<Outcome, Fatal>,
    stderr: String,
    listed: Vec<Entry>,
}

fn run_choosing(world: &World, stub: &Stub, pick: &[&str], extra: &[(&str, &str)]) -> Run {
    run_with(world, stub, extra, |entries| {
        Some(
            entries
                .iter()
                .filter(|e| pick.contains(&e.label.as_str()))
                .map(|e| e.path.clone())
                .collect(),
        )
    })
}

fn run_with(
    world: &World,
    stub: &Stub,
    extra: &[(&str, &str)],
    choose: impl Fn(&[Entry]) -> Option<Vec<PathBuf>>,
) -> Run {
    let env = world.env(stub, extra);
    let out = Recorder::new();
    let listed = std::cell::RefCell::new(Vec::new());
    let outcome = app::run(
        &env,
        world.own_root(),
        &mut out.clone(),
        &mut |entries, _, _| {
            *listed.borrow_mut() = entries.clone();
            Ok(choose(&entries))
        },
    );
    Run {
        outcome,
        stderr: out.text(),
        listed: listed.into_inner(),
    }
}

fn closed_ids(stub: &Stub) -> Vec<String> {
    stub.params_for("workspace.close")
        .iter()
        .filter_map(|p| p["workspace_id"].as_str().map(str::to_string))
        .collect()
}

fn created_labels(stub: &Stub) -> Vec<String> {
    stub.requests()
        .iter()
        .filter(|r| {
            matches!(
                r["method"].as_str(),
                Some("workspace.create") | Some("worktree.open")
            )
        })
        .filter_map(|r| r["params"]["label"].as_str().map(str::to_string))
        .collect()
}

#[test]
fn a_root_with_no_git_repositories_refuses_by_name() {
    let world = World::new();
    let stub = Stub::start(Script::default());
    let run = run_choosing(&world, &stub, &[], &[]);
    match run.outcome {
        Err(Fatal::NoRepos(message)) => {
            assert!(message.contains(&world.tree.path().to_string_lossy().to_string()))
        }
        other => panic!("{:?}", other),
    }
    assert!(stub.requests().is_empty(), "nothing is asked of Herdr");
}

#[test]
fn no_socket_to_reach_herdr_on_is_refused_before_anything_is_drawn() {
    let world = World::new();
    world.repo("myrepo");
    let stub = Stub::start(Script::default());
    let env = Environment::from_pairs(&[
        ("PATH", LAUNCHD_PATH),
        ("HOME", &world.tree.path().to_string_lossy()),
        ("HERDR_PICKER_ROOT", &world.tree.path().to_string_lossy()),
    ]);
    let mut out: Vec<u8> = Vec::new();
    let mut drawn = false;
    let outcome = app::run(&env, world.own_root(), &mut out, &mut |_, _, _| {
        drawn = true;
        Ok(None)
    });
    assert!(matches!(outcome, Err(Fatal::NoServer(_))), "{:?}", outcome);
    assert!(
        !drawn,
        "the picker must not draw itself with nowhere to send a pick"
    );
    let _ = stub;
}

#[test]
fn a_refused_workspace_list_is_fatal_and_the_picker_is_never_drawn() {
    let world = World::new();
    world.repo("alpha");
    let stub = Stub::start(Script::default().failing("workspace.list", "server_not_running"));
    let run = run_choosing(&world, &stub, &["alpha"], &[]);
    match run.outcome {
        Err(Fatal::NoServer(message)) => {
            assert!(message.contains("server_not_running"), "{}", message)
        }
        other => panic!("{:?}", other),
    }
    assert!(run.listed.is_empty(), "nothing was drawn");
    assert!(stub.changing().is_empty(), "nothing was opened or closed");
}

#[test]
fn a_workspace_list_refused_after_the_picker_closes_and_opens_nothing() {
    let world = World::new();
    world.repo("alpha");
    world.repo("beta");
    let stub = Stub::start(
        Script::default()
            .open(vec![workspace("alpha", "w1")])
            .failing_at("workspace.list", "server_not_running", 2),
    );
    let run = run_choosing(&world, &stub, &["beta"], &[]);
    match run.outcome {
        Err(Fatal::NoServer(message)) => {
            assert!(message.contains("server_not_running"), "{}", message)
        }
        other => panic!("{:?}", other),
    }
    assert_eq!(
        run.listed.len(),
        2,
        "the picker was drawn from the first listing"
    );
    assert!(
        stub.changing().is_empty(),
        "a selection acted on a list that never arrived would close alpha and open beta"
    );
}

#[test]
fn open_workspaces_are_listed_first_and_start_checked() {
    let world = World::new();
    world.repo("alpha");
    world.repo("beta");
    world.repo("gamma");
    let stub = Stub::start(Script::default().open(vec![workspace("gamma", "w3")]));
    let run = run_choosing(&world, &stub, &[], &[]);
    assert_eq!(run.listed[0].label, "gamma");
    assert!(run.listed[0].selected);
    assert!(run.listed[1..].iter().all(|e| !e.selected));
}

#[test]
fn an_open_workspace_carries_its_agent_status_onto_its_row() {
    let world = World::new();
    world.repo("alpha");
    let stub =
        Stub::start(Script::default().open(vec![workspace_with("alpha", "w1", false, "blocked")]));
    let run = run_choosing(&world, &stub, &["alpha"], &[]);
    assert_eq!(run.listed[0].status.as_deref(), Some("blocked"));
}

#[test]
fn a_row_that_is_not_open_carries_no_status() {
    let world = World::new();
    world.repo("alpha");
    let stub = Stub::start(Script::default());
    let run = run_choosing(&world, &stub, &[], &[]);
    assert_eq!(run.listed[0].status, None);
}

#[test]
fn the_home_workspace_is_never_listed_and_never_closed() {
    let world = World::new();
    world.repo("alpha");
    let stub = Stub::start(
        Script::default().open(vec![workspace("~", "home1"), workspace("alpha", "w1")]),
    );
    let run = run_choosing(&world, &stub, &[], &[]);
    assert!(run.listed.iter().all(|e| e.label != "~"));
    assert_eq!(closed_ids(&stub), vec!["w1"]);
}

#[test]
fn a_home_label_of_your_own_is_the_one_held_back() {
    let world = World::new();
    world.repo("alpha");
    let stub = Stub::start(
        Script::default().open(vec![workspace("base", "home1"), workspace("alpha", "w1")]),
    );
    world.configure("[picker]\nhome = \"base\"\n");
    let run = run_choosing(&world, &stub, &["alpha"], &[]);
    assert!(run.outcome.is_ok());
    assert!(closed_ids(&stub).is_empty());
}

#[test]
fn cancelling_changes_nothing_at_all() {
    let world = World::new();
    world.repo("alpha");
    world.repo("beta");
    let stub = Stub::start(Script::default().open(vec![workspace("alpha", "w1")]));
    let run = run_with(&world, &stub, &[], |_| None);
    assert_eq!(
        run.outcome.unwrap(),
        Outcome {
            cancelled: true,
            ..Outcome::default()
        }
    );
    assert!(stub.changing().is_empty(), "{:?}", stub.changing());
}

#[test]
fn an_empty_selection_closes_every_open_project() {
    let world = World::new();
    world.repo("alpha");
    world.repo("beta");
    let stub = Stub::start(Script::default().open(vec![
        workspace("~", "home1"),
        workspace("alpha", "w1"),
        workspace("beta", "w2"),
    ]));
    run_choosing(&world, &stub, &[], &[]);
    assert_eq!(closed_ids(&stub), vec!["w1", "w2"]);
    assert!(created_labels(&stub).is_empty());
}

#[test]
fn the_selection_becomes_the_truth() {
    let world = World::new();
    world.repo("alpha");
    world.repo("beta");
    world.repo("gamma");
    let stub = Stub::start(
        Script::default().open(vec![workspace("alpha", "w1"), workspace("beta", "w2")]),
    );
    run_choosing(&world, &stub, &["beta", "gamma"], &[]);
    assert_eq!(closed_ids(&stub), vec!["w1"]);
    assert_eq!(created_labels(&stub), vec!["gamma"]);
}

#[test]
fn an_unchanged_selection_touches_nothing() {
    let world = World::new();
    world.repo("alpha");
    let stub = Stub::start(Script::default().open(vec![workspace("alpha", "w1")]));
    run_choosing(&world, &stub, &["alpha"], &[]);
    assert!(closed_ids(&stub).is_empty());
    assert!(created_labels(&stub).is_empty());
}

#[test]
fn a_newly_opened_workspace_takes_the_focus() {
    let world = World::new();
    world.repo("alpha");
    let stub = Stub::start(Script::default());
    let run = run_choosing(&world, &stub, &["alpha"], &[]);
    assert_eq!(run.outcome.unwrap().focused.as_deref(), Some("new1"));
    assert_eq!(
        stub.params_for("workspace.focus"),
        vec![json!({"workspace_id": "new1"})]
    );
}

#[test]
fn closing_the_focused_workspace_moves_the_focus_home() {
    let world = World::new();
    world.repo("alpha");
    let stub = Stub::start(Script::default().open(vec![
        workspace("~", "home1"),
        workspace_with("alpha", "w1", true, "idle"),
    ]));
    let run = run_choosing(&world, &stub, &[], &[]);
    assert_eq!(run.outcome.unwrap().focused.as_deref(), Some("home1"));
}

#[test]
fn a_run_that_changes_nothing_focuses_nothing() {
    let world = World::new();
    world.repo("alpha");
    let stub =
        Stub::start(Script::default().open(vec![workspace_with("alpha", "w1", true, "idle")]));
    let run = run_choosing(&world, &stub, &["alpha"], &[]);
    assert_eq!(run.outcome.unwrap().focused, None);
    assert!(stub.params_for("workspace.focus").is_empty());
}

#[test]
fn a_workspace_that_will_not_open_at_all_refuses_by_path() {
    let world = World::new();
    let repo = world.repo("alpha");
    let stub = Stub::start(
        Script::default()
            .failing("worktree.open", "boom")
            .failing("workspace.create", "boom"),
    );
    let run = run_choosing(&world, &stub, &["alpha"], &[]);
    match run.outcome {
        Err(Fatal::NotOpened(message)) => {
            assert!(
                message.contains(&repo.to_string_lossy().to_string()),
                "{}",
                message
            )
        }
        other => panic!("{:?}", other),
    }
}

#[test]
fn a_close_the_server_refuses_is_not_counted_as_closed() {
    let world = World::new();
    world.repo("alpha");
    let stub = Stub::start(
        Script::default()
            .open(vec![workspace_with("alpha", "w1", true, "idle")])
            .failing("workspace.close", "workspace_not_found"),
    );
    let run = run_choosing(&world, &stub, &[], &[]);
    let outcome = run.outcome.unwrap();
    assert!(outcome.closed.is_empty());
    assert_eq!(outcome.focused, None, "a focus that survived must not move");
}

fn repo_with_worktrees(world: &World, repo: &str, branches: &[&str]) -> String {
    let root = world.repo(repo);
    let container = world.tree.join(&format!("{}/.worktrees/{}", repo, repo));
    for branch in branches {
        make_worktree(
            &container.join(branch),
            &format!("{}/.git/worktrees/{}", root.to_string_lossy(), branch),
        );
    }
    root.to_string_lossy().to_string()
}

#[test]
fn a_repository_and_its_worktrees_all_close_in_one_pass() {
    let world = World::new();
    let root = repo_with_worktrees(&world, "alpha", &["feat-x", "feat-y"]);
    let stub = Stub::start(Script::default().open(vec![
        workspace_in_repo("alpha", "w1", &root, false),
        workspace_in_repo("feat-x", "w2", &root, true),
        workspace_in_repo("feat-y", "w3", &root, true),
    ]));
    let run = run_choosing(&world, &stub, &[], &[]);
    assert_eq!(closed_ids(&stub), vec!["w2", "w3", "w1"]);
    assert_eq!(run.outcome.unwrap().closed, vec!["w2", "w3", "w1"]);
    assert_eq!(
        run.stderr, "",
        "nothing was refused, so nothing is reported"
    );
}

#[test]
fn a_repository_whose_worktree_stays_checked_closes_what_was_unchecked_and_reports_the_rest() {
    let world = World::new();
    let root = repo_with_worktrees(&world, "alpha", &["feat-x", "feat-y"]);
    let stub = Stub::start(Script::default().open(vec![
        workspace_in_repo("alpha", "w1", &root, false),
        workspace_in_repo("feat-x", "w2", &root, true),
        workspace_in_repo("feat-y", "w3", &root, true),
    ]));
    let run = run_choosing(&world, &stub, &["feat-y"], &[]);
    assert_eq!(run.outcome.unwrap().closed, vec!["w2"]);
    assert!(
        !closed_ids(&stub).contains(&"w3".to_string()),
        "a checked worktree is never asked to close"
    );
    assert!(run.stderr.contains("alpha did not close"), "{}", run.stderr);
    assert!(
        run.stderr.contains("workspace_group_close_required"),
        "{}",
        run.stderr
    );
    assert_eq!(stub.params_for("notification.show").len(), 1);
}

#[test]
fn a_worktree_closes_while_the_repository_it_belongs_to_stays_open() {
    let world = World::new();
    let root = repo_with_worktrees(&world, "alpha", &["feat-x"]);
    let stub = Stub::start(Script::default().open(vec![
        workspace_in_repo("alpha", "w1", &root, false),
        workspace_in_repo("feat-x", "w2", &root, true),
    ]));
    let run = run_choosing(&world, &stub, &["alpha"], &[]);
    assert_eq!(closed_ids(&stub), vec!["w2"]);
    assert_eq!(run.stderr, "");
}

#[test]
fn one_repositorys_open_worktree_never_holds_another_repository_open() {
    let world = World::new();
    let alpha = repo_with_worktrees(&world, "alpha", &["feat-x"]);
    let beta = repo_with_worktrees(&world, "beta", &["feat-y"]);
    let stub = Stub::start(Script::default().open(vec![
        workspace_in_repo("alpha", "w1", &alpha, false),
        workspace_in_repo("feat-x", "w2", &alpha, true),
        workspace_in_repo("beta", "w3", &beta, false),
        workspace_in_repo("feat-y", "w4", &beta, true),
    ]));
    let run = run_choosing(&world, &stub, &["feat-y"], &[]);
    assert_eq!(run.outcome.unwrap().closed, vec!["w2", "w1"]);
    assert!(run.stderr.contains("beta did not close"), "{}", run.stderr);
    assert!(
        !run.stderr.contains("alpha did not close"),
        "{}",
        run.stderr
    );
}

fn layout_script(world: &World, records: &Path) -> String {
    use std::os::unix::fs::PermissionsExt;
    let path = world.own.join("lay");
    std::fs::write(
        &path,
        format!(
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > '{0}'/$$.part && mv '{0}'/$$.part '{0}'/$$\n",
            records.to_string_lossy()
        ),
    )
    .unwrap();
    let mut perms = std::fs::metadata(&path).unwrap().permissions();
    perms.set_mode(0o755);
    std::fs::set_permissions(&path, perms).unwrap();
    path.to_string_lossy().to_string()
}

fn published(records: &Path) -> Vec<String> {
    std::fs::read_dir(records)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| path.extension().map(|e| e != "part").unwrap_or(true))
        .map(|path| std::fs::read_to_string(path).unwrap())
        .collect()
}

fn wait_for_runs(records: &Path, want: usize) -> Vec<String> {
    for _ in 0..300 {
        let found = published(records);
        if found.len() >= want {
            return found
                .iter()
                .flat_map(|text| text.lines().map(str::to_string))
                .collect();
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    panic!("fewer than {} layout commands published a record", want);
}

#[test]
fn every_workspace_the_picker_opens_is_handed_to_the_layout_command() {
    let world = World::new();
    world.repo("alpha");
    world.repo("beta");
    let records = world.own.dir("records");
    let lay = layout_script(&world, &records);
    world.configure(&format!(
        "[picker]\nlayout = \"{} --space {{workspace}}\"\n",
        lay
    ));
    let stub = Stub::start(Script::default());
    let run = run_choosing(&world, &stub, &["alpha", "beta"], &[]);
    assert!(run.outcome.is_ok());
    let mut lines = wait_for_runs(&records, 2);
    lines.sort();
    assert_eq!(lines, vec!["--space", "--space", "new1", "new2"]);
}

#[test]
fn the_layout_command_is_resolved_once_however_many_workspaces_open() {
    let world = World::new();
    world.repo("alpha");
    world.repo("beta");
    let records = world.own.dir("records");
    let lay = layout_script(&world, &records);
    world.configure("[picker]\nlayout = \"{plugin:some.plugin}/lay --space {workspace}\"\n");
    let _ = lay;
    let stub =
        Stub::start(Script::default().plugins(vec![json!({"plugin_root": world.own.path()})]));
    let run = run_choosing(&world, &stub, &["alpha", "beta"], &[]);
    assert!(run.outcome.is_ok());
    assert_eq!(
        stub.params_for("plugin.list").len(),
        1,
        "the checkout is asked for once per run, not once per workspace"
    );
    wait_for_runs(&records, 2);
}

#[test]
fn a_layout_that_will_not_resolve_warns_once_and_still_opens_every_workspace() {
    let world = World::new();
    world.repo("alpha");
    world.repo("beta");
    world.configure("[picker]\nlayout = \"{plugin:some.plugin}/lay --space {workspace}\"\n");
    let stub = Stub::start(Script::default());
    let run = run_choosing(&world, &stub, &["alpha", "beta"], &[]);
    assert!(run.outcome.is_ok());
    assert_eq!(created_labels(&stub), vec!["alpha", "beta"]);
    assert_eq!(
        stub.params_for("notification.show").len(),
        1,
        "one notice for the whole run"
    );
    assert!(run.stderr.contains("some.plugin"), "{}", run.stderr);
    assert!(
        run.stderr.contains("Herdr has no checkout for"),
        "{}",
        run.stderr
    );
}

#[test]
fn a_selection_that_creates_nothing_never_asks_about_the_layout() {
    let world = World::new();
    world.repo("alpha");
    world.configure("[picker]\nlayout = \"{plugin:some.plugin}/lay\"\n");
    let stub = Stub::start(Script::default().open(vec![workspace("alpha", "w1")]));
    let run = run_choosing(&world, &stub, &["alpha"], &[]);
    assert!(run.outcome.is_ok());
    assert!(stub.params_for("plugin.list").is_empty());
    assert!(stub.params_for("notification.show").is_empty());
    assert_eq!(run.stderr, "");
}

#[test]
fn no_layout_setting_still_opens_the_workspace_and_says_nothing() {
    let world = World::new();
    world.repo("alpha");
    let stub = Stub::start(Script::default());
    let run = run_choosing(&world, &stub, &["alpha"], &[]);
    assert_eq!(created_labels(&stub), vec!["alpha"]);
    assert_eq!(run.stderr, "");
    assert!(stub.params_for("notification.show").is_empty());
}

#[test]
fn the_debug_line_is_written_before_the_picker_is_drawn() {
    let world = World::new();
    world.repo("alpha");
    world.configure("[picker]\ndebug = true\n");
    let stub = Stub::start(Script::default());
    let env = world.env(&stub, &[]);
    let out = Recorder::new();
    let seen = std::cell::RefCell::new(String::new());
    let watcher = out.clone();
    let outcome = app::run(&env, world.own_root(), &mut out.clone(), &mut |_, _, _| {
        *seen.borrow_mut() = watcher.text();
        Ok(None)
    });
    assert!(outcome.is_ok());
    assert!(seen.into_inner().contains("picker: discovery"));
    assert!(out.text().contains("picker: discovery"));
}

#[test]
fn an_ordinary_run_writes_nothing_at_all() {
    let world = World::new();
    world.repo("alpha");
    let stub = Stub::start(Script::default());
    let run = run_with(&world, &stub, &[], |_| None);
    assert_eq!(run.stderr, "");
}

#[test]
fn the_debug_line_counts_both_passes_of_a_real_search() {
    let world = World::new();
    world.repo("alpha");
    make_worktree(
        &world.tree.join("alpha/.worktrees/alpha/feat-x"),
        "/x/parent/.git/worktrees/wt",
    );
    world.configure("[picker]\ndebug = true\n");
    let stub = Stub::start(Script::default());
    let run = run_with(&world, &stub, &[], |_| None);
    assert!(run.stderr.contains("2 rows"), "{}", run.stderr);
    assert!(run.stderr.contains("1 from depth bands"), "{}", run.stderr);
    assert!(
        run.stderr.contains("1 from worktree containers"),
        "{}",
        run.stderr
    );
}

#[test]
fn a_broken_config_file_is_reported_and_the_picker_still_opens() {
    let world = World::new();
    world.repo("alpha");
    world.configure("[picker\nroot /x/typo\n");
    let stub = Stub::start(Script::default());
    let run = run_choosing(&world, &stub, &["alpha"], &[]);
    assert!(run.outcome.is_ok(), "{:?}", run.outcome);
    assert!(run.stderr.contains("does not parse"), "{}", run.stderr);
    assert_eq!(created_labels(&stub), vec!["alpha"]);
}

#[test]
fn a_repo_and_a_worktree_are_both_listed_and_told_apart() {
    let world = World::new();
    let repo = world.repo("alpha");
    make_worktree(
        &world.tree.join("alpha/.worktrees/alpha/feat-x"),
        &format!("{}/.git/worktrees/feat-x", repo.to_string_lossy()),
    );
    let stub = Stub::start(Script::default());
    let run = run_choosing(&world, &stub, &[], &[]);
    let kinds: Vec<&str> = run.listed.iter().map(|e| e.kind.word()).collect();
    assert_eq!(kinds.len(), 2);
    assert!(kinds.contains(&"repo"));
    assert!(kinds.contains(&"worktree"));
}

#[test]
fn a_listed_worktree_carries_the_repository_it_belongs_to_and_a_repository_carries_none() {
    let world = World::new();
    let repo = world.repo("alpha");
    make_worktree(
        &world.tree.join("alpha/.worktrees/alpha/feat-x"),
        &format!("{}/.git/worktrees/feat-x", repo.to_string_lossy()),
    );
    let stub = Stub::start(Script::default());
    let run = run_choosing(&world, &stub, &[], &[]);
    let repos: Vec<Option<PathBuf>> = run.listed.iter().map(|e| e.repo.clone()).collect();
    assert!(repos.contains(&Some(repo.clone())), "{:?}", repos);
    assert!(repos.contains(&None), "{:?}", repos);
}

#[test]
fn a_worktree_is_opened_against_the_repo_it_belongs_to() {
    let world = World::new();
    let repo = world.repo("alpha");
    let checkout = make_worktree(
        &world.tree.join("alpha/.worktrees/alpha/feat-x"),
        &format!("{}/.git/worktrees/feat-x", repo.to_string_lossy()),
    );
    let stub = Stub::start(Script::default());
    run_choosing(&world, &stub, &["feat-x"], &[]);
    assert_eq!(
        stub.params_for("worktree.open"),
        vec![json!({"cwd": repo.to_string_lossy(),
                    "path": checkout.to_string_lossy(),
                    "label": "feat-x", "focus": false})]
    );
}

#[test]
fn two_projects_sharing_a_name_are_told_apart_by_their_parent() {
    let world = World::new();
    world.repo("group-a/api");
    world.repo("group-b/api");
    let stub = Stub::start(Script::default());
    let run = run_choosing(&world, &stub, &[], &[]);
    let mut labels: Vec<String> = run.listed.iter().map(|e| e.label.clone()).collect();
    labels.sort();
    assert_eq!(labels, vec!["group-a/api", "group-b/api"]);
}

#[test]
fn worktrees_sharing_a_name_under_containers_sharing_one_are_each_reachable() {
    let world = World::new();
    for repo in ["alpha", "beta"] {
        let root = world.repo(repo);
        make_worktree(
            &world.tree.join(&format!("{}/worktrees/slug", repo)),
            &format!("{}/.git/worktrees/slug", root.to_string_lossy()),
        );
    }
    let stub = Stub::start(Script::default());
    let run = run_with(&world, &stub, &[], |entries| {
        Some(entries.iter().map(|e| e.path.clone()).collect())
    });
    run.outcome.unwrap();

    let labels: Vec<String> = run.listed.iter().map(|e| e.label.clone()).collect();
    let named: std::collections::HashSet<&String> = labels.iter().collect();
    assert_eq!(
        named.len(),
        4,
        "every row is labelled its own: {:?}",
        labels
    );

    let opened: std::collections::HashSet<String> = stub
        .params_for("worktree.open")
        .iter()
        .filter_map(|p| p["path"].as_str().map(str::to_string))
        .collect();
    assert_eq!(
        opened.len(),
        4,
        "a label two rows share leaves one path unreachable: {:?}",
        opened
    );
}

#[test]
fn the_homebrew_directories_are_put_on_the_path_the_launchd_server_lacks() {
    let bare = Environment::from_pairs(&[("PATH", LAUNCHD_PATH)]);
    let widened = app::with_extra_path(bare);
    let path = widened.get("PATH").unwrap();
    for dir in app::EXTRA_PATH_DIRS {
        if Path::new(dir).is_dir() {
            assert!(
                path.split(':').any(|d| d == dir),
                "{} is missing from {}",
                dir,
                path
            );
        }
    }
    assert!(
        path.ends_with(LAUNCHD_PATH),
        "the original PATH is kept: {}",
        path
    );
}

#[test]
fn a_directory_already_on_the_path_is_not_added_twice() {
    let existing = app::EXTRA_PATH_DIRS
        .iter()
        .find(|d| Path::new(d).is_dir())
        .copied();
    let Some(existing) = existing else {
        return;
    };
    let start = format!("{}:{}", existing, LAUNCHD_PATH);
    let widened = app::with_extra_path(Environment::from_pairs(&[("PATH", &start)]));
    let path = widened.get("PATH").unwrap();
    assert_eq!(
        path.split(':').filter(|d| *d == existing).count(),
        1,
        "{}",
        path
    );
}

#[test]
fn the_herdr_binary_setting_beats_whatever_is_on_the_path() {
    let env = Environment::from_pairs(&[("PATH", LAUNCHD_PATH), ("HERDR_BIN_PATH", "/x/mine")]);
    assert_eq!(app::herdr_binary(&env).as_deref(), Some("/x/mine"));
}

#[test]
fn an_emptied_herdr_binary_setting_falls_back_to_the_path() {
    let env = Environment::from_pairs(&[("PATH", LAUNCHD_PATH), ("HERDR_BIN_PATH", "")]);
    assert_eq!(app::herdr_binary(&env), None);
}
