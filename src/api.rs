use std::collections::HashMap;

use herdr_plugin_kit::api::generated::{
    EmptyParams, NotificationShowAnswer, NotificationShowParams, OkAnswer, PluginListAnswer,
    PluginListParams, RequestMethod, WorkspaceCloseParams, WorkspaceCreateParams,
    WorkspaceCreatedAnswer, WorkspaceInfo, WorkspaceInfoAnswer, WorkspaceListAnswer,
    WorkspaceTarget, WorktreeOpenParams, WorktreeOpenedAnswer,
};

pub use herdr_plugin_kit::api::client::{CallError, Client, Socket};

pub const PLUGIN_ID: &str = "mikebronner.project-finder";

pub const NOTIFICATION_TITLE: &str = "project finder";

pub fn is_linked_worktree(workspace: &WorkspaceInfo) -> bool {
    workspace
        .worktree
        .as_ref()
        .is_some_and(|worktree| worktree.is_linked_worktree)
}

pub fn workspaces(client: &Client) -> Result<Vec<WorkspaceInfo>, CallError> {
    client
        .call::<WorkspaceListAnswer>(RequestMethod::WorkspaceList(EmptyParams(
            serde_json::Map::new(),
        )))
        .map(|answer| answer.workspaces)
}

pub fn open_workspaces(
    client: &Client,
    home_label: &str,
) -> Result<(Option<WorkspaceInfo>, Vec<WorkspaceInfo>), CallError> {
    let mut listed = workspaces(client)?;
    match listed.iter().position(|w| w.label == home_label) {
        Some(at) => Ok((Some(listed.remove(at)), listed)),
        None => Ok((None, listed)),
    }
}

pub fn worktree_open(
    client: &Client,
    cwd: &str,
    path: &str,
    label: &str,
) -> Result<String, CallError> {
    client
        .call::<WorktreeOpenedAnswer>(RequestMethod::WorktreeOpen(WorktreeOpenParams {
            branch: None,
            cwd: Some(cwd.to_string()),
            focus: false,
            label: Some(label.to_string()),
            path: Some(path.to_string()),
            trust_repository: None,
            workspace_id: None,
        }))
        .map(|answer| answer.workspace.workspace_id)
}

pub fn workspace_create(client: &Client, cwd: &str, label: &str) -> Result<String, CallError> {
    client
        .call::<WorkspaceCreatedAnswer>(RequestMethod::WorkspaceCreate(WorkspaceCreateParams {
            cwd: Some(cwd.to_string()),
            env: HashMap::new(),
            focus: false,
            label: Some(label.to_string()),
            source_workspace_id: None,
        }))
        .map(|answer| answer.workspace.workspace_id)
}

pub fn workspace_close(client: &Client, workspace_id: &str) -> Result<(), CallError> {
    client
        .call::<OkAnswer>(RequestMethod::WorkspaceClose(WorkspaceCloseParams {
            close_group: None,
            workspace_id: workspace_id.to_string(),
        }))
        .map(|_| ())
}

pub fn workspace_focus(client: &Client, workspace_id: &str) -> Result<(), CallError> {
    client
        .call::<WorkspaceInfoAnswer>(RequestMethod::WorkspaceFocus(WorkspaceTarget {
            workspace_id: workspace_id.to_string(),
        }))
        .map(|_| ())
}

pub fn plugin_root(client: &Client, plugin_id: &str) -> Option<String> {
    client
        .call::<PluginListAnswer>(RequestMethod::PluginList(PluginListParams {
            plugin_id: Some(plugin_id.to_string()),
        }))
        .ok()?
        .plugins
        .into_iter()
        .find(|plugin| !plugin.plugin_root.is_empty())
        .map(|plugin| plugin.plugin_root)
}

pub fn notify(client: &Client, body: &str) {
    let _ = client.call::<NotificationShowAnswer>(RequestMethod::NotificationShow(
        NotificationShowParams {
            body: Some(body.to_string()),
            position: None,
            sound: None,
            title: NOTIFICATION_TITLE.to_string(),
        },
    ));
}
