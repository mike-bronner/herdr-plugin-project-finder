use std::collections::HashMap;
use std::path::{Path, PathBuf};

use crossterm::event::{self, Event, KeyEventKind};
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, List, ListItem, ListState, Paragraph, Wrap};
use ratatui::Frame;

use crate::config::Environment;
use crate::discover::Kind;
use crate::layout::which;
use crate::picker::{heading, row_cells, Action, Entry, Picker, GUTTER, LEGEND, MARKER, PROMPT};
use crate::theme::{Colour, Theme};

pub fn ratatui_colour(colour: Colour) -> Color {
    match colour {
        Colour::Default => Color::Reset,
        Colour::Indexed(index) => Color::Indexed(index),
        Colour::Rgb(r, g, b) => Color::Rgb(r, g, b),
    }
}

pub struct Previews {
    cache: HashMap<PathBuf, String>,
    git: Option<PathBuf>,
    lister: Option<PathBuf>,
}

impl Previews {
    pub fn new(env: &Environment) -> Previews {
        Previews {
            cache: HashMap::new(),
            git: which(env, "git"),
            lister: which(env, "ls"),
        }
    }

    pub fn of(&mut self, label: &str, path: &Path) -> String {
        if let Some(cached) = self.cache.get(path) {
            return cached.clone();
        }
        let body = self.body(path);
        let text = format!("{}\n\n{}", label, body);
        self.cache.insert(path.to_path_buf(), text.clone());
        text
    }

    fn body(&self, path: &Path) -> String {
        if let Some(git) = &self.git {
            let run = std::process::Command::new(git)
                .args(["-C"])
                .arg(path)
                .args(["log", "--oneline", "-8"])
                .output();
            if let Ok(out) = run {
                if out.status.success() {
                    return String::from_utf8_lossy(&out.stdout).to_string();
                }
            }
        }
        if let Some(lister) = &self.lister {
            if let Ok(out) = std::process::Command::new(lister)
                .arg("-1")
                .arg(path)
                .output()
            {
                return String::from_utf8_lossy(&out.stdout).to_string();
            }
        }
        String::new()
    }
}

pub fn run(
    entries: Vec<Entry>,
    theme: &Theme,
    env: &Environment,
) -> std::io::Result<Option<Vec<PathBuf>>> {
    let mut picker = Picker::new(entries);
    let mut previews = Previews::new(env);
    let mut terminal = ratatui::try_init()?;

    let outcome = loop {
        let preview = match picker.highlighted() {
            Some(entry) => previews.of(&entry.label, &entry.path.clone()),
            None => String::new(),
        };
        terminal.draw(|frame| draw(frame, &picker, theme, &preview))?;

        match event::read()? {
            Event::Key(key) if key.kind != KeyEventKind::Release => match picker.on_key(key) {
                Action::Accept => break Some(picker.selected_paths()),
                Action::Cancel => break None,
                Action::Redraw => {}
            },
            _ => {}
        }
    };

    ratatui::try_restore()?;
    Ok(outcome)
}

pub fn draw(frame: &mut Frame, picker: &Picker, theme: &Theme, preview: &str) {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),
            Constraint::Length(LEGEND.len() as u16),
            Constraint::Length(1),
            Constraint::Length(1),
            Constraint::Min(1),
        ])
        .split(frame.area());

    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled(PROMPT, Style::default().fg(Color::Indexed(4))),
            Span::raw(&picker.query),
        ])),
        rows[0],
    );
    frame.render_widget(
        Paragraph::new(
            LEGEND
                .iter()
                .map(|l| Line::from(Span::styled(*l, Style::default().fg(Color::Indexed(8)))))
                .collect::<Vec<Line>>(),
        ),
        rows[1],
    );
    frame.render_widget(
        Paragraph::new(Line::from(Span::styled(
            format!("  {}", heading()),
            Style::default().add_modifier(Modifier::BOLD),
        ))),
        rows[2],
    );
    frame.render_widget(
        Paragraph::new(Line::from(Span::styled(
            format!(
                "  {}/{} · {} checked",
                picker.matches.len(),
                picker.entries.len(),
                picker.selected_count()
            ),
            Style::default().fg(Color::Indexed(8)),
        ))),
        rows[3],
    );

    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(48), Constraint::Percentage(52)])
        .split(rows[4]);

    draw_list(frame, picker, theme, body[0]);
    frame.render_widget(
        Paragraph::new(preview)
            .wrap(Wrap { trim: false })
            .block(Block::default().borders(Borders::LEFT)),
        body[1],
    );
}

fn draw_list(frame: &mut Frame, picker: &Picker, theme: &Theme, area: Rect) {
    let items: Vec<ListItem> = picker
        .matches
        .iter()
        .map(|at| {
            let entry = &picker.entries[*at];
            let marker = if entry.selected { MARKER } else { GUTTER };
            let cells = row_cells(entry);
            let kind = match entry.kind {
                Kind::Worktree => Style::default().fg(ratatui_colour(theme.accent)),
                Kind::Repo => Style::default(),
            };
            let mut spans = vec![
                Span::styled(marker, Style::default().fg(Color::Indexed(2))),
                Span::styled(cells.repo, Style::default().add_modifier(Modifier::DIM)),
                Span::raw(cells.name),
                Span::styled(cells.kind, kind),
                Span::raw(cells.age),
            ];
            if let Some(status) = &entry.status {
                spans.push(Span::styled(
                    theme.cell(status),
                    Style::default().fg(ratatui_colour(theme.colour(status))),
                ));
            }
            ListItem::new(Line::from(spans))
        })
        .collect();

    let mut state = ListState::default();
    if !picker.matches.is_empty() {
        state.select(Some(picker.cursor));
    }
    frame.render_stateful_widget(
        List::new(items).highlight_style(Style::default().add_modifier(Modifier::REVERSED)),
        area,
        &mut state,
    );
}
