use std::ffi::OsString;

pub const FLAG: &str = "--version";

#[derive(Debug, Clone, PartialEq)]
pub enum Request {
    Pick,
    Report,
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
        [flag, extra, ..] if flag == FLAG => Request::Refuse(extra.clone()),
        [first, ..] => Request::Refuse(first.clone()),
    }
}
