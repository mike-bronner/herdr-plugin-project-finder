use std::ffi::OsString;

pub const FLAG: &str = "--version";

pub const DIALOG_FLAG: &str = "--dialog";

pub use herdr_plugin_kit::update::CHECK_FLAG;

#[derive(Debug, Clone, PartialEq)]
pub enum Request {
    Pick,
    Report,
    Dialog,
    CheckUpdate,
    Refuse(String),
}

pub fn requested(arguments: &[OsString]) -> Request {
    let shown: Vec<String> = arguments
        .iter()
        .map(|argument| argument.to_string_lossy().into_owned())
        .collect();
    match shown.as_slice() {
        [] => Request::Pick,
        [flag] if flag == FLAG => Request::Report,
        [flag] if flag == DIALOG_FLAG => Request::Dialog,
        [flag] if flag == CHECK_FLAG => Request::CheckUpdate,
        [flag, extra, ..] if [FLAG, DIALOG_FLAG, CHECK_FLAG].contains(&flag.as_str()) => {
            Request::Refuse(extra.clone())
        }
        [first, ..] => Request::Refuse(first.clone()),
    }
}
