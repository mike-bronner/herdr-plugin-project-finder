use std::path::PathBuf;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use nucleo::pattern::{CaseMatching, Normalization, Pattern};
use nucleo::{Matcher, Utf32Str};

use crate::discover::{elide, Kind};

pub const LABEL_WIDTH: usize = 34;
pub const KIND_WIDTH: usize = 4;
pub const AGE_WIDTH: usize = 9;

pub const PROMPT: &str = "filter projects > ";

pub const LEGEND: [&str; 2] = [
    "enter apply  ·  tab toggle  ·  ctrl-a all  ·  ctrl-d none  ·  esc cancel",
    "checked = open  ·  unchecked open projects are closed on enter",
];

pub const MARKER: &str = "✓ ";
pub const GUTTER: &str = "  ";

#[derive(Debug, Clone, PartialEq)]
pub struct Entry {
    pub label: String,
    pub path: PathBuf,
    pub repo: Option<String>,
    pub kind: Kind,
    pub age: String,
    pub status: Option<String>,
    pub selected: bool,
}

impl Entry {
    pub fn haystack(&self) -> String {
        format!(
            "{} {} {} {}",
            self.label,
            self.kind.word(),
            self.age,
            self.status.clone().unwrap_or_default()
        )
    }
}

pub fn pad(text: &str, width: usize) -> String {
    let counted = text.chars().count();
    if counted >= width {
        text.to_string()
    } else {
        format!("{}{}", text, " ".repeat(width - counted))
    }
}

pub fn heading() -> String {
    format!(
        "{} {} {} {}",
        pad("PROJECT", LABEL_WIDTH),
        pad("KIND", KIND_WIDTH),
        pad("TOUCHED", AGE_WIDTH),
        "AGENT STATUS"
    )
}

fn repo_prefix(entry: &Entry, taken: usize) -> String {
    let Some(name) = &entry.repo else {
        return String::new();
    };
    let room = LABEL_WIDTH.saturating_sub(taken);
    let whole = format!("{}/", name);
    if whole.chars().count() <= room {
        return whole;
    }
    match room {
        0 => String::new(),
        1 => "…".to_string(),
        _ => format!("{}/", elide(name, room - 1)),
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Cells {
    pub repo: String,
    pub name: String,
    pub kind: String,
    pub age: String,
}

pub fn row_cells(entry: &Entry) -> Cells {
    let label = elide(&entry.label, LABEL_WIDTH);
    let repo = repo_prefix(entry, label.chars().count());
    let padding =
        " ".repeat(LABEL_WIDTH.saturating_sub(repo.chars().count() + label.chars().count()));
    Cells {
        repo,
        name: format!("{}{} ", label, padding),
        kind: pad(entry.kind.cell(), KIND_WIDTH),
        age: format!(" {} ", pad(&entry.age, AGE_WIDTH)),
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Highlights {
    pub name: Vec<usize>,
    pub kind: Vec<usize>,
    pub age: Vec<usize>,
    pub status_word: Vec<usize>,
}

fn name_column(index: usize, label: usize) -> usize {
    if label <= LABEL_WIDTH {
        index
    } else {
        index.min(LABEL_WIDTH - 1)
    }
}

pub fn highlights_for(entry: &Entry, indices: &[u32]) -> Highlights {
    let label = entry.label.chars().count();
    let word = entry.kind.word().chars().count();
    let hidden = entry
        .kind
        .word()
        .strip_suffix(entry.kind.cell())
        .map(|cut| cut.chars().count());
    let age = entry.age.chars().count();
    let status = entry
        .status
        .as_deref()
        .map(|s| s.chars().count())
        .unwrap_or(0);

    let kind_at = label + 1;
    let age_at = kind_at + word + 1;
    let status_at = age_at + age + 1;

    let mut found = Highlights::default();
    for index in indices.iter().map(|i| *i as usize) {
        if index < label {
            found.name.push(name_column(index, label));
        } else if index >= kind_at && index < kind_at + word {
            if let Some(hidden) = hidden {
                let inner = index - kind_at;
                if inner >= hidden {
                    found.kind.push(inner - hidden);
                }
            }
        } else if index >= age_at && index < age_at + age {
            found.age.push(index - age_at + 1);
        } else if index >= status_at && index < status_at + status {
            found.status_word.push(index - status_at);
        }
    }
    for column in [
        &mut found.name,
        &mut found.kind,
        &mut found.age,
        &mut found.status_word,
    ] {
        column.sort_unstable();
        column.dedup();
    }
    found
}

pub fn runs(text: &str, marked: &[usize]) -> Vec<(String, bool)> {
    let mut out: Vec<(String, bool)> = Vec::new();
    for (index, letter) in text.chars().enumerate() {
        let hit = marked.contains(&index);
        match out.last_mut() {
            Some((run, same)) if *same == hit => run.push(letter),
            _ => out.push((letter.to_string(), hit)),
        }
    }
    out
}

pub fn row_prefix(entry: &Entry) -> String {
    let cells = row_cells(entry);
    format!("{}{}{}{}", cells.repo, cells.name, cells.kind, cells.age)
}

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Action {
    Redraw,
    Accept,
    Cancel,
}

pub struct Picker {
    pub entries: Vec<Entry>,
    pub query: String,
    pub matches: Vec<usize>,
    pub cursor: usize,
    highlights: Vec<Highlights>,
    matcher: Matcher,
}

impl Picker {
    pub fn new(entries: Vec<Entry>) -> Picker {
        let mut picker = Picker {
            entries,
            query: String::new(),
            matches: Vec::new(),
            cursor: 0,
            highlights: Vec::new(),
            matcher: Matcher::new(nucleo::Config::DEFAULT),
        };
        picker.refilter();
        picker
    }

    pub fn refilter(&mut self) {
        if self.query.trim().is_empty() {
            self.matches = (0..self.entries.len()).collect();
            self.highlights = vec![Highlights::default(); self.entries.len()];
            self.cursor = 0;
            return;
        }
        let pattern = Pattern::parse(&self.query, CaseMatching::Smart, Normalization::Smart);
        let mut scored: Vec<(u32, usize, Highlights)> = Vec::new();
        let mut buffer = Vec::new();
        let mut indices = Vec::new();
        for (at, entry) in self.entries.iter().enumerate() {
            let haystack = entry.haystack();
            buffer.clear();
            indices.clear();
            if let Some(score) = pattern.indices(
                Utf32Str::new(&haystack, &mut buffer),
                &mut self.matcher,
                &mut indices,
            ) {
                scored.push((score, at, highlights_for(entry, &indices)));
            }
        }
        scored.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(&b.1)));
        self.matches = scored.iter().map(|(_, at, _)| *at).collect();
        self.highlights = scored.into_iter().map(|(_, _, found)| found).collect();
        self.cursor = 0;
    }

    pub fn highlights(&self, row: usize) -> Option<&Highlights> {
        self.highlights.get(row)
    }

    pub fn highlighted(&self) -> Option<&Entry> {
        self.entries.get(*self.matches.get(self.cursor)?)
    }

    pub fn selected_paths(&self) -> Vec<PathBuf> {
        self.entries
            .iter()
            .filter(|e| e.selected)
            .map(|e| e.path.clone())
            .collect()
    }

    pub fn selected_count(&self) -> usize {
        self.entries.iter().filter(|e| e.selected).count()
    }

    fn move_by(&mut self, delta: isize) {
        if self.matches.is_empty() {
            self.cursor = 0;
            return;
        }
        let last = self.matches.len() - 1;
        let next = self.cursor as isize + delta;
        self.cursor = next.clamp(0, last as isize) as usize;
    }

    fn toggle(&mut self) {
        if let Some(at) = self.matches.get(self.cursor) {
            let at = *at;
            self.entries[at].selected = !self.entries[at].selected;
        }
    }

    fn set_all(&mut self, selected: bool) {
        for at in self.matches.clone() {
            self.entries[at].selected = selected;
        }
    }

    fn drop_word(&mut self) {
        let trimmed = self.query.trim_end_matches(' ');
        let cut = trimmed.rfind(' ').map(|at| at + 1).unwrap_or(0);
        self.query.truncate(cut);
    }

    pub fn on_key(&mut self, key: KeyEvent) -> Action {
        let control = key.modifiers.contains(KeyModifiers::CONTROL);
        match (key.code, control) {
            (KeyCode::Esc, _) => return Action::Cancel,
            (KeyCode::Char('c'), true) | (KeyCode::Char('g'), true) => return Action::Cancel,
            (KeyCode::Enter, _) => return Action::Accept,
            (KeyCode::Tab, _) => {
                self.toggle();
                self.move_by(1);
            }
            (KeyCode::BackTab, _) => {
                self.toggle();
                self.move_by(-1);
            }
            (KeyCode::Char('a'), true) => self.set_all(true),
            (KeyCode::Char('d'), true) => self.set_all(false),
            (KeyCode::Up, _) | (KeyCode::Char('p'), true) => self.move_by(-1),
            (KeyCode::Down, _) | (KeyCode::Char('n'), true) => self.move_by(1),
            (KeyCode::PageUp, _) => self.move_by(-10),
            (KeyCode::PageDown, _) => self.move_by(10),
            (KeyCode::Home, _) => self.cursor = 0,
            (KeyCode::End, _) => self.move_by(self.matches.len() as isize),
            (KeyCode::Char('u'), true) => {
                self.query.clear();
                self.refilter();
            }
            (KeyCode::Char('w'), true) => {
                self.drop_word();
                self.refilter();
            }
            (KeyCode::Backspace, _) => {
                self.query.pop();
                self.refilter();
            }
            (KeyCode::Char(c), false) => {
                self.query.push(c);
                self.refilter();
            }
            _ => {}
        }
        Action::Redraw
    }
}
