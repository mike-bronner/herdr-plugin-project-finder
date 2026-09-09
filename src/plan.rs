use crate::api::Workspace;

#[derive(Debug, PartialEq)]
pub struct Plan {
    pub to_close: Vec<Workspace>,
    pub to_create: Vec<String>,
}

pub fn plan(selected: &[String], open_ws: &[Workspace]) -> Plan {
    let to_close = open_ws
        .iter()
        .filter(|w| !selected.contains(&w.label))
        .cloned()
        .collect();
    let to_create = selected
        .iter()
        .filter(|label| !open_ws.iter().any(|w| w.label == **label))
        .cloned()
        .collect();
    Plan {
        to_close,
        to_create,
    }
}

pub fn pick_focus(
    created: &[String],
    closed: &[String],
    focused: Option<&str>,
    surviving: &[String],
    home: Option<&str>,
) -> Option<String> {
    if let Some(last) = created.last() {
        return Some(last.clone());
    }
    let focus_was_closed = focused.is_some_and(|f| closed.iter().any(|c| c == f));
    if focus_was_closed {
        return surviving
            .first()
            .cloned()
            .or_else(|| home.map(str::to_string));
    }
    None
}
