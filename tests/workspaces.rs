mod support;

use serde_json::json;

use pick_project::api::{self, Workspace};
use pick_project::app::open_project;
use pick_project::plan::{pick_focus, plan};
use support::*;

fn ids(of: &[String]) -> Vec<&str> {
    of.iter().map(String::as_str).collect()
}

#[test]
fn the_home_workspace_is_split_out_and_never_listed_among_the_others() {
    let stub = Stub::start(Script::default().open(vec![
        workspace("~", "w1"),
        workspace("a", "w2"),
        workspace("b", "w3"),
    ]));
    let (home, others) = api::open_workspaces(&stub.client(), "~");
    assert_eq!(home.unwrap().workspace_id, "w1");
    assert_eq!(labels(&others), vec!["a", "b"]);
}

#[test]
fn no_home_open_leaves_every_workspace_in_the_list() {
    let stub = Stub::start(Script::default().open(vec![workspace("a", "w2")]));
    let (home, others) = api::open_workspaces(&stub.client(), "~");
    assert!(home.is_none());
    assert_eq!(others.len(), 1);
}

#[test]
fn a_home_label_of_your_own_is_the_one_that_is_held_back() {
    let stub =
        Stub::start(Script::default().open(vec![workspace("~", "w1"), workspace("base", "w2")]));
    let (home, others) = api::open_workspaces(&stub.client(), "base");
    assert_eq!(home.unwrap().workspace_id, "w2");
    assert_eq!(labels(&others), vec!["~"]);
}

#[test]
fn an_unreachable_server_lists_nothing_rather_than_failing() {
    let stub = Stub::start(Script::default().failing("workspace.list", "server_not_running"));
    assert_eq!(
        api::open_workspaces(&stub.client(), "~"),
        (None, Vec::new())
    );
}

#[test]
fn a_workspace_row_with_no_id_is_dropped_rather_than_guessed_at() {
    let stub =
        Stub::start(Script::default().open(vec![json!({"label": "a"}), workspace("b", "w2")]));
    let (_, others) = api::open_workspaces(&stub.client(), "~");
    assert_eq!(labels(&others), vec!["b"]);
}

#[test]
fn a_workspace_with_no_agent_status_reads_as_unknown() {
    let stub =
        Stub::start(Script::default().open(vec![json!({"workspace_id": "w1", "label": "a"})]));
    let (_, others) = api::open_workspaces(&stub.client(), "~");
    assert_eq!(others[0].agent_status, "unknown");
}

#[test]
fn deselected_workspaces_close_new_ones_are_created_and_the_rest_are_untouched() {
    let open = vec![open("a", "w1"), open("b", "w2")];
    let steps = plan(&["b".to_string(), "c".to_string()], &open);
    assert_eq!(labels(&steps.to_close), vec!["a"]);
    assert_eq!(steps.to_create, vec!["c"]);
}

#[test]
fn an_empty_selection_closes_everything() {
    let open = vec![open("a", "w1"), open("stray", "w2")];
    let steps = plan(&[], &open);
    assert_eq!(labels(&steps.to_close), vec!["a", "stray"]);
    assert!(steps.to_create.is_empty());
}

#[test]
fn an_unchanged_selection_is_a_noop() {
    let steps = plan(&["a".to_string()], &[open("a", "w1")]);
    assert!(steps.to_close.is_empty());
    assert!(steps.to_create.is_empty());
}

#[test]
fn a_created_workspace_takes_the_focus() {
    let created = vec!["n1".to_string(), "n2".to_string()];
    let closed = vec!["w1".to_string()];
    let surviving = vec!["w9".to_string()];
    assert_eq!(
        pick_focus(&created, &closed, Some("w1"), &surviving, Some("h")),
        Some("n2".to_string())
    );
}

#[test]
fn closing_the_focused_workspace_falls_back_to_the_first_survivor() {
    let closed = vec!["w1".to_string()];
    let surviving = vec!["w5".to_string(), "w6".to_string()];
    assert_eq!(
        pick_focus(&[], &closed, Some("w1"), &surviving, Some("h")),
        Some("w5".to_string())
    );
}

#[test]
fn closing_the_focused_workspace_with_nothing_left_goes_home() {
    let closed = vec!["w1".to_string()];
    assert_eq!(
        pick_focus(&[], &closed, Some("w1"), &[], Some("h")),
        Some("h".to_string())
    );
}

#[test]
fn closing_the_focused_workspace_with_no_home_open_focuses_nothing() {
    let closed = vec!["w1".to_string()];
    assert_eq!(pick_focus(&[], &closed, Some("w1"), &[], None), None);
}

#[test]
fn an_unchanged_selection_leaves_the_focus_alone() {
    let closed = vec!["w2".to_string()];
    let surviving = vec!["w1".to_string()];
    assert_eq!(
        pick_focus(&[], &closed, Some("w1"), &surviving, Some("h")),
        None
    );
}

fn worktree_checkout(dir: &TempDir) -> (std::path::PathBuf, std::path::PathBuf) {
    let repo = make_repo(&dir.join("myrepo"));
    let checkout = dir.join("myrepo/.worktrees/myrepo/feat-x");
    let gitdir = format!("{}/.git/worktrees/feat-x", repo.to_string_lossy());
    make_worktree(&checkout, &gitdir);
    (repo, checkout)
}

#[test]
fn a_worktree_is_opened_against_its_parent_repo() {
    let dir = TempDir::new();
    let (repo, checkout) = worktree_checkout(&dir);
    let stub = Stub::start(Script::default());
    open_project(&stub.client(), "feat-x", &checkout);
    assert_eq!(
        stub.params_for("worktree.open"),
        vec![json!({"cwd": repo.to_string_lossy(),
                    "path": checkout.to_string_lossy(),
                    "label": "feat-x", "focus": false})]
    );
}

#[test]
fn opening_a_worktree_never_falls_through_to_workspace_create() {
    let dir = TempDir::new();
    let (_, checkout) = worktree_checkout(&dir);
    let stub = Stub::start(Script::default());
    open_project(&stub.client(), "feat-x", &checkout);
    assert_eq!(stub.changing(), vec!["worktree.open"]);
}

#[test]
fn the_opened_workspace_id_is_what_comes_back() {
    let dir = TempDir::new();
    let (_, checkout) = worktree_checkout(&dir);
    let stub = Stub::start(Script::default());
    assert_eq!(
        open_project(&stub.client(), "feat-x", &checkout),
        Some("new1".to_string())
    );
}

#[test]
fn a_plain_repo_names_itself_as_the_repo_it_belongs_to() {
    let dir = TempDir::new();
    let repo = make_repo(&dir.join("myrepo"));
    let stub = Stub::start(Script::default());
    open_project(&stub.client(), "myrepo", &repo);
    assert_eq!(
        stub.params_for("worktree.open"),
        vec![json!({"cwd": repo.to_string_lossy(),
                    "path": repo.to_string_lossy(),
                    "label": "myrepo", "focus": false})]
    );
}

#[test]
fn opening_a_plain_repo_never_falls_through_to_workspace_create() {
    let dir = TempDir::new();
    let repo = make_repo(&dir.join("myrepo"));
    let stub = Stub::start(Script::default());
    open_project(&stub.client(), "myrepo", &repo);
    assert_eq!(stub.changing(), vec!["worktree.open"]);
}

#[test]
fn a_row_that_is_no_git_checkout_uses_workspace_create() {
    let dir = TempDir::new();
    let plain = dir.dir("notes");
    let stub = Stub::start(Script::default());
    open_project(&stub.client(), "notes", &plain);
    assert_eq!(stub.changing(), vec!["workspace.create"]);
    assert_eq!(
        stub.params_for("workspace.create"),
        vec![json!({"cwd": plain.to_string_lossy(), "label": "notes", "focus": false})]
    );
}

#[test]
fn a_worktree_whose_pointer_has_the_wrong_shape_uses_workspace_create() {
    let dir = TempDir::new();
    let checkout = make_worktree(&dir.join("odd"), "/x/super/.git/modules/sub");
    let stub = Stub::start(Script::default());
    open_project(&stub.client(), "odd", &checkout);
    assert_eq!(stub.changing(), vec!["workspace.create"]);
}

#[test]
fn a_refused_worktree_open_falls_back_to_workspace_create() {
    let dir = TempDir::new();
    let (repo, checkout) = worktree_checkout(&dir);
    let stub = Stub::start(Script::default().failing("worktree.open", "unsupported_method"));
    let got = open_project(&stub.client(), "feat-x", &checkout);
    assert_eq!(stub.changing(), vec!["worktree.open", "workspace.create"]);
    assert_eq!(got, Some("new1".to_string()));
    assert_eq!(
        stub.params_for("workspace.create")[0]["cwd"],
        json!(checkout.to_string_lossy())
    );
    assert_ne!(
        stub.params_for("workspace.create")[0]["cwd"],
        json!(repo.to_string_lossy()),
        "the fallback opens the checkout, not its parent"
    );
}

#[test]
fn a_refused_worktree_open_on_a_plain_repo_falls_back_to_workspace_create() {
    let dir = TempDir::new();
    let repo = make_repo(&dir.join("myrepo"));
    let stub = Stub::start(Script::default().failing("worktree.open", "unsupported_method"));
    open_project(&stub.client(), "myrepo", &repo);
    assert_eq!(
        stub.params_for("workspace.create"),
        vec![json!({"cwd": repo.to_string_lossy(), "label": "myrepo", "focus": false})]
    );
}

#[test]
fn both_commands_failing_yields_nothing_so_the_caller_can_refuse() {
    let dir = TempDir::new();
    let (_, checkout) = worktree_checkout(&dir);
    let stub = Stub::start(
        Script::default()
            .failing("worktree.open", "boom")
            .failing("workspace.create", "boom"),
    );
    assert_eq!(open_project(&stub.client(), "feat-x", &checkout), None);
}

#[test]
fn nothing_is_ever_focused_as_it_is_opened() {
    let dir = TempDir::new();
    let (_, checkout) = worktree_checkout(&dir);
    let plain = dir.dir("notes");
    let stub = Stub::start(Script::default());
    open_project(&stub.client(), "feat-x", &checkout);
    open_project(&stub.client(), "notes", &plain);
    for method in ["worktree.open", "workspace.create"] {
        for params in stub.params_for(method) {
            assert_eq!(
                params["focus"],
                json!(false),
                "{} focused a workspace",
                method
            );
        }
    }
}

#[test]
fn the_plugin_is_asked_for_by_the_id_it_was_given() {
    let stub = Stub::start(Script::default().plugins(vec![json!({"plugin_root": "/x/tool"})]));
    api::plugin_root(&stub.client(), "some.plugin");
    assert_eq!(
        stub.params_for("plugin.list"),
        vec![json!({"plugin_id": "some.plugin"})]
    );
}

#[test]
fn the_reported_plugin_root_is_returned() {
    let stub = Stub::start(Script::default().plugins(vec![json!({"plugin_root": "/x/tool"})]));
    assert_eq!(
        api::plugin_root(&stub.client(), "some.plugin"),
        Some("/x/tool".to_string())
    );
}

#[test]
fn no_plugin_row_yields_no_root() {
    let stub = Stub::start(Script::default());
    assert_eq!(api::plugin_root(&stub.client(), "some.plugin"), None);
}

#[test]
fn a_plugin_row_with_no_root_yields_no_root() {
    for row in [
        json!({"plugin_root": ""}),
        json!({}),
        json!({"plugin_root": null}),
    ] {
        let stub = Stub::start(Script::default().plugins(vec![row.clone()]));
        assert_eq!(
            api::plugin_root(&stub.client(), "some.plugin"),
            None,
            "{}",
            row
        );
    }
}

#[test]
fn an_unreachable_server_yields_no_plugin_root_rather_than_failing() {
    let stub = Stub::start(Script::default().failing("plugin.list", "server_not_running"));
    assert_eq!(api::plugin_root(&stub.client(), "some.plugin"), None);
}

#[test]
fn a_disabled_plugin_is_still_resolved() {
    let stub = Stub::start(
        Script::default().plugins(vec![json!({"plugin_root": "/x/tool", "enabled": false})]),
    );
    assert_eq!(
        api::plugin_root(&stub.client(), "some.plugin"),
        Some("/x/tool".to_string())
    );
}

#[test]
fn the_first_plugin_row_carrying_a_root_wins() {
    let stub = Stub::start(Script::default().plugins(vec![
        json!({"plugin_root": ""}),
        json!({"plugin_root": "/x/second"}),
    ]));
    assert_eq!(
        api::plugin_root(&stub.client(), "some.plugin"),
        Some("/x/second".to_string())
    );
}

#[test]
fn closing_a_workspace_names_it_by_id() {
    let stub = Stub::start(Script::default());
    api::workspace_close(&stub.client(), "w7").unwrap();
    assert_eq!(
        stub.params_for("workspace.close"),
        vec![json!({"workspace_id": "w7"})]
    );
}

#[test]
fn focusing_a_workspace_names_it_by_id() {
    let stub = Stub::start(Script::default());
    api::workspace_focus(&stub.client(), "w7").unwrap();
    assert_eq!(
        stub.params_for("workspace.focus"),
        vec![json!({"workspace_id": "w7"})]
    );
}

#[test]
fn a_refused_call_carries_the_servers_own_code() {
    let stub = Stub::start(Script::default().failing("workspace.close", "workspace_not_found"));
    let err = api::workspace_close(&stub.client(), "w7").unwrap_err();
    assert_eq!(err.code(), Some("workspace_not_found"));
}

#[test]
fn a_socket_that_is_not_there_is_a_transport_error_not_an_api_one() {
    let client = pick_project::api::Client::new("/private/tmp/pick-project-absent.sock".into());
    let err = api::workspace_close(&client, "w7").unwrap_err();
    assert_eq!(err.code(), None);
}

#[test]
fn a_notification_names_the_picker_and_carries_the_body() {
    let stub = Stub::start(Script::default());
    api::notify(&stub.client(), "the layout will not run");
    assert_eq!(
        stub.params_for("notification.show"),
        vec![json!({"title": "project finder", "body": "the layout will not run"})]
    );
}

#[test]
fn every_request_is_one_line_carrying_an_id_a_method_and_params() {
    let stub = Stub::start(Script::default());
    api::workspace_focus(&stub.client(), "w7").unwrap();
    let sent = stub.requests();
    assert_eq!(sent.len(), 1);
    assert!(sent[0].get("method").is_some());
    assert!(sent[0].get("params").is_some());
}

#[test]
fn a_label_with_a_space_survives_as_one_value() {
    let dir = TempDir::new();
    let plain = dir.dir("notes");
    let stub = Stub::start(Script::default());
    open_project(&stub.client(), "two words", &plain);
    assert_eq!(
        stub.params_for("workspace.create")[0]["label"],
        json!("two words")
    );
}

#[test]
fn workspaces_keep_the_order_the_server_listed_them_in() {
    let stub = Stub::start(Script::default().open(vec![
        workspace("c", "w3"),
        workspace("a", "w1"),
        workspace("b", "w2"),
    ]));
    let listed: Vec<Workspace> = api::workspaces(&stub.client());
    assert_eq!(
        ids(&listed
            .iter()
            .map(|w| w.workspace_id.clone())
            .collect::<Vec<_>>()),
        vec!["w3", "w1", "w2"]
    );
}

#[test]
fn a_focused_workspace_is_reported_as_focused() {
    let stub =
        Stub::start(Script::default().open(vec![workspace_with("a", "w1", true, "working")]));
    let listed = api::workspaces(&stub.client());
    assert!(listed[0].focused);
    assert_eq!(listed[0].agent_status, "working");
}
