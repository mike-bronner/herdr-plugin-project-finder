mod support;

use std::path::PathBuf;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use pick_project::discover::Kind;
use pick_project::picker::{
    heading, row_cells, row_prefix, Action, Entry, Picker, AGE_WIDTH, GUTTER, KIND_WIDTH,
    LABEL_WIDTH, LEGEND, MARKER, PROMPT,
};
use support::TempDir;

fn entry(label: &str, kind: Kind, age: &str, status: Option<&str>, selected: bool) -> Entry {
    Entry {
        label: label.to_string(),
        path: PathBuf::from(format!("/x/{}", label)),
        repo: None,
        kind,
        age: age.to_string(),
        status: status.map(str::to_string),
        selected,
    }
}

fn worktree_of(repo: &str, label: &str) -> Entry {
    Entry {
        repo: Some(repo.to_string()),
        ..entry(label, Kind::Worktree, "3m ago", None, false)
    }
}

fn plain(labels: &[&str]) -> Vec<Entry> {
    labels
        .iter()
        .map(|l| entry(l, Kind::Repo, "1m ago", None, false))
        .collect()
}

fn key(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

fn control(c: char) -> KeyEvent {
    KeyEvent::new(KeyCode::Char(c), KeyModifiers::CONTROL)
}

fn type_in(picker: &mut Picker, text: &str) {
    for c in text.chars() {
        picker.on_key(key(KeyCode::Char(c)));
    }
}

fn at(text: &str, needle: &str) -> Option<usize> {
    text.find(needle).map(|byte| text[..byte].chars().count())
}

fn shown(picker: &Picker) -> Vec<String> {
    picker
        .matches
        .iter()
        .map(|at| picker.entries[*at].label.clone())
        .collect()
}

fn checked(picker: &Picker) -> Vec<String> {
    picker
        .entries
        .iter()
        .filter(|e| e.selected)
        .map(|e| e.label.clone())
        .collect()
}

#[test]
fn every_row_is_listed_before_a_word_is_typed() {
    let picker = Picker::new(plain(&["a", "b", "c"]));
    assert_eq!(shown(&picker), vec!["a", "b", "c"]);
    assert_eq!(picker.cursor, 0);
}

#[test]
fn no_rows_is_no_matches_and_nothing_highlighted() {
    let picker = Picker::new(Vec::new());
    assert!(picker.matches.is_empty());
    assert!(picker.highlighted().is_none());
}

#[test]
fn the_rows_handed_in_as_open_start_checked_and_the_rest_do_not() {
    let entries = vec![
        entry("open-one", Kind::Repo, "1m ago", Some("idle"), true),
        entry("closed-one", Kind::Repo, "2m ago", None, false),
    ];
    let picker = Picker::new(entries);
    assert_eq!(checked(&picker), vec!["open-one"]);
}

#[test]
fn the_cursor_starts_at_the_top_however_many_rows_are_checked() {
    let entries = vec![
        entry("open-one", Kind::Repo, "1m ago", Some("idle"), true),
        entry("open-two", Kind::Repo, "1m ago", Some("idle"), true),
        entry("closed", Kind::Repo, "2m ago", None, false),
    ];
    let picker = Picker::new(entries);
    assert_eq!(picker.cursor, 0);
    assert_eq!(picker.highlighted().unwrap().label, "open-one");
}

#[test]
fn enter_accepts_what_is_checked() {
    let entries = vec![
        entry("a", Kind::Repo, "1m ago", None, true),
        entry("b", Kind::Repo, "1m ago", None, false),
    ];
    let mut picker = Picker::new(entries);
    assert_eq!(picker.on_key(key(KeyCode::Enter)), Action::Accept);
    assert_eq!(picker.selected_paths(), vec![PathBuf::from("/x/a")]);
}

#[test]
fn enter_with_nothing_checked_accepts_an_empty_selection() {
    let mut picker = Picker::new(plain(&["a", "b"]));
    assert_eq!(picker.on_key(key(KeyCode::Enter)), Action::Accept);
    assert!(picker.selected_paths().is_empty());
}

#[test]
fn enter_never_takes_the_highlighted_row_as_a_selection_of_its_own() {
    let mut picker = Picker::new(plain(&["a", "b"]));
    picker.on_key(key(KeyCode::Down));
    picker.on_key(key(KeyCode::Enter));
    assert!(
        picker.selected_paths().is_empty(),
        "moving the cursor must not check a row"
    );
}

#[test]
fn escape_cancels() {
    let mut picker = Picker::new(plain(&["a"]));
    assert_eq!(picker.on_key(key(KeyCode::Esc)), Action::Cancel);
}

#[test]
fn control_c_cancels_too() {
    let mut picker = Picker::new(plain(&["a"]));
    assert_eq!(picker.on_key(control('c')), Action::Cancel);
}

#[test]
fn cancelling_leaves_what_was_checked_alone_because_nothing_is_read_from_it() {
    let entries = vec![entry("a", Kind::Repo, "1m ago", None, true)];
    let mut picker = Picker::new(entries);
    picker.on_key(key(KeyCode::Tab));
    assert_eq!(picker.on_key(key(KeyCode::Esc)), Action::Cancel);
}

#[test]
fn tab_toggles_the_highlighted_row_and_moves_down() {
    let mut picker = Picker::new(plain(&["a", "b"]));
    picker.on_key(key(KeyCode::Tab));
    assert_eq!(checked(&picker), vec!["a"]);
    assert_eq!(picker.highlighted().unwrap().label, "b");
}

#[test]
fn tab_on_a_checked_row_unchecks_it() {
    let entries = vec![entry("a", Kind::Repo, "1m ago", None, true)];
    let mut picker = Picker::new(entries);
    picker.on_key(key(KeyCode::Tab));
    assert!(checked(&picker).is_empty());
}

#[test]
fn shift_tab_toggles_and_moves_up() {
    let mut picker = Picker::new(plain(&["a", "b"]));
    picker.on_key(key(KeyCode::Down));
    picker.on_key(key(KeyCode::BackTab));
    assert_eq!(checked(&picker), vec!["b"]);
    assert_eq!(picker.highlighted().unwrap().label, "a");
}

#[test]
fn tab_at_the_last_row_checks_it_and_stays_put() {
    let mut picker = Picker::new(plain(&["a", "b"]));
    picker.on_key(key(KeyCode::Down));
    picker.on_key(key(KeyCode::Tab));
    assert_eq!(checked(&picker), vec!["b"]);
    assert_eq!(picker.cursor, 1);
}

#[test]
fn control_a_checks_every_row_and_control_d_clears_them() {
    let mut picker = Picker::new(plain(&["a", "b", "c"]));
    picker.on_key(control('a'));
    assert_eq!(checked(&picker), vec!["a", "b", "c"]);
    picker.on_key(control('d'));
    assert!(checked(&picker).is_empty());
}

#[test]
fn control_a_reaches_only_the_rows_the_filter_left() {
    let mut picker = Picker::new(plain(&["alpha", "beta"]));
    type_in(&mut picker, "alpha");
    picker.on_key(control('a'));
    assert_eq!(checked(&picker), vec!["alpha"]);
}

#[test]
fn control_d_leaves_a_checked_row_the_filter_hides() {
    let entries = vec![
        entry("alpha", Kind::Repo, "1m ago", None, true),
        entry("beta", Kind::Repo, "1m ago", None, true),
    ];
    let mut picker = Picker::new(entries);
    type_in(&mut picker, "alpha");
    picker.on_key(control('d'));
    assert_eq!(checked(&picker), vec!["beta"]);
}

#[test]
fn space_types_into_the_filter_rather_than_toggling_a_row() {
    let mut picker = Picker::new(plain(&["a b"]));
    picker.on_key(key(KeyCode::Char(' ')));
    assert_eq!(picker.query, " ");
    assert!(checked(&picker).is_empty());
}

#[test]
fn a_two_word_query_narrows_by_both_words() {
    let entries = vec![
        entry("web-api", Kind::Repo, "1m ago", None, false),
        entry("web-site", Kind::Repo, "1m ago", None, false),
    ];
    let mut picker = Picker::new(entries);
    type_in(&mut picker, "web api");
    assert_eq!(shown(&picker), vec!["web-api"]);
}

#[test]
fn backspace_widens_the_filter_again() {
    let mut picker = Picker::new(plain(&["alpha", "beta"]));
    type_in(&mut picker, "alph");
    assert_eq!(shown(&picker), vec!["alpha"]);
    for _ in 0..4 {
        picker.on_key(key(KeyCode::Backspace));
    }
    assert_eq!(shown(&picker), vec!["alpha", "beta"]);
}

#[test]
fn control_u_clears_the_whole_filter() {
    let mut picker = Picker::new(plain(&["alpha", "beta"]));
    type_in(&mut picker, "alph");
    picker.on_key(control('u'));
    assert_eq!(picker.query, "");
    assert_eq!(shown(&picker), vec!["alpha", "beta"]);
}

#[test]
fn control_w_drops_the_last_word_of_the_filter() {
    let mut picker = Picker::new(plain(&["alpha"]));
    type_in(&mut picker, "one two");
    picker.on_key(control('w'));
    assert_eq!(picker.query, "one ");
}

#[test]
fn the_filter_matches_the_kind_word_so_typing_worktree_narrows_to_worktrees() {
    let entries = vec![
        entry("one", Kind::Worktree, "1m ago", None, false),
        entry("two", Kind::Repo, "1m ago", None, false),
    ];
    let mut picker = Picker::new(entries);
    type_in(&mut picker, "worktree");
    assert_eq!(shown(&picker), vec!["one"]);
}

#[test]
fn the_filter_matches_the_status_word_so_typing_blocked_narrows_to_blocked_agents() {
    let entries = vec![
        entry("one", Kind::Repo, "1m ago", Some("blocked"), true),
        entry("two", Kind::Repo, "1m ago", Some("idle"), true),
    ];
    let mut picker = Picker::new(entries);
    type_in(&mut picker, "blocked");
    assert_eq!(shown(&picker), vec!["one"]);
}

#[test]
fn the_filter_matches_past_the_ellipsis_the_screen_shows() {
    let long = format!("{}-needle", "z".repeat(LABEL_WIDTH));
    let entries = vec![
        entry(&long, Kind::Repo, "1m ago", None, false),
        entry("other", Kind::Repo, "1m ago", None, false),
    ];
    let mut picker = Picker::new(entries);
    type_in(&mut picker, "needle");
    assert_eq!(shown(&picker), vec![long]);
}

#[test]
fn a_query_that_matches_nothing_leaves_an_empty_list_rather_than_everything() {
    let mut picker = Picker::new(plain(&["alpha"]));
    type_in(&mut picker, "zzzzzzq");
    assert!(shown(&picker).is_empty());
    assert!(picker.highlighted().is_none());
}

#[test]
fn accepting_an_empty_list_still_returns_what_was_checked_elsewhere() {
    let entries = vec![entry("alpha", Kind::Repo, "1m ago", None, true)];
    let mut picker = Picker::new(entries);
    type_in(&mut picker, "zzzzzzq");
    assert_eq!(picker.on_key(key(KeyCode::Enter)), Action::Accept);
    assert_eq!(picker.selected_paths(), vec![PathBuf::from("/x/alpha")]);
}

#[test]
fn typing_returns_the_cursor_to_the_top_of_the_new_list() {
    let mut picker = Picker::new(plain(&["alpha", "alpine", "beta"]));
    picker.on_key(key(KeyCode::Down));
    picker.on_key(key(KeyCode::Down));
    type_in(&mut picker, "alp");
    assert_eq!(picker.cursor, 0);
}

#[test]
fn the_cursor_never_leaves_the_list() {
    let mut picker = Picker::new(plain(&["a", "b"]));
    for _ in 0..5 {
        picker.on_key(key(KeyCode::Up));
    }
    assert_eq!(picker.cursor, 0);
    for _ in 0..5 {
        picker.on_key(key(KeyCode::Down));
    }
    assert_eq!(picker.cursor, 1);
}

#[test]
fn control_p_and_control_n_move_like_the_arrows() {
    let mut picker = Picker::new(plain(&["a", "b"]));
    picker.on_key(control('n'));
    assert_eq!(picker.cursor, 1);
    picker.on_key(control('p'));
    assert_eq!(picker.cursor, 0);
}

#[test]
fn page_keys_move_further_than_one_row() {
    let labels: Vec<String> = (0..30).map(|n| format!("r{:02}", n)).collect();
    let borrowed: Vec<&str> = labels.iter().map(String::as_str).collect();
    let mut picker = Picker::new(plain(&borrowed));
    picker.on_key(key(KeyCode::PageDown));
    assert_eq!(picker.cursor, 10);
    picker.on_key(key(KeyCode::PageUp));
    assert_eq!(picker.cursor, 0);
}

#[test]
fn home_and_end_reach_both_ends() {
    let mut picker = Picker::new(plain(&["a", "b", "c"]));
    picker.on_key(key(KeyCode::End));
    assert_eq!(picker.cursor, 2);
    picker.on_key(key(KeyCode::Home));
    assert_eq!(picker.cursor, 0);
}

#[test]
fn a_checked_row_survives_a_filter_that_hides_it() {
    let mut picker = Picker::new(plain(&["alpha", "beta"]));
    picker.on_key(key(KeyCode::Tab));
    type_in(&mut picker, "beta");
    assert_eq!(shown(&picker), vec!["beta"]);
    assert_eq!(checked(&picker), vec!["alpha"]);
}

#[test]
fn selected_paths_come_back_in_the_order_the_rows_were_listed() {
    let entries = vec![
        entry("a", Kind::Repo, "1m ago", None, true),
        entry("b", Kind::Repo, "1m ago", None, false),
        entry("c", Kind::Repo, "1m ago", None, true),
    ];
    let picker = Picker::new(entries);
    assert_eq!(
        picker.selected_paths(),
        vec![PathBuf::from("/x/a"), PathBuf::from("/x/c")]
    );
}

#[test]
fn the_checked_count_is_what_the_screen_reports() {
    let mut picker = Picker::new(plain(&["a", "b"]));
    assert_eq!(picker.selected_count(), 0);
    picker.on_key(control('a'));
    assert_eq!(picker.selected_count(), 2);
}

#[test]
fn the_heading_labels_are_in_column_order() {
    let text = heading();
    let project = at(&text, "PROJECT").unwrap();
    let kind = at(&text, "KIND").unwrap();
    let touched = at(&text, "TOUCHED").unwrap();
    let status = at(&text, "AGENT STATUS").unwrap();
    assert!(project < kind);
    assert!(kind < touched);
    assert!(touched < status);
}

#[test]
fn the_heading_is_exactly_one_line() {
    assert!(!heading().contains('\n'));
}

#[test]
fn the_heading_columns_line_up_with_a_row() {
    let row = row_prefix(&entry("proj", Kind::Worktree, "3m ago", None, false));
    assert_eq!(at(&heading(), "KIND"), at(&row, "tree"));
    assert_eq!(at(&heading(), "TOUCHED"), at(&row, "3m ago"));
    assert_eq!(heading().chars().count(), row.chars().count() + "AGENT STATUS".len());
}

#[test]
fn the_kind_column_fits_its_widest_value() {
    let wide = row_prefix(&entry("proj", Kind::Worktree, "3m ago", None, false));
    let narrow = row_prefix(&entry("proj", Kind::Repo, "3m ago", None, false));
    assert_eq!(at(&wide, "3m ago"), at(&narrow, "3m ago"));
    assert_eq!(Kind::Worktree.cell().chars().count(), KIND_WIDTH);
    assert_eq!(Kind::Repo.cell().chars().count(), KIND_WIDTH);
}

#[test]
fn the_kind_column_spends_four_characters_and_no_more() {
    assert_eq!(KIND_WIDTH, 4);
    assert_eq!(Kind::Worktree.cell(), "tree");
    assert_eq!(Kind::Repo.cell(), "repo");
    assert_eq!(at(&heading(), "TOUCHED"), Some(LABEL_WIDTH + 1 + KIND_WIDTH + 1));
}

#[test]
fn a_long_label_is_elided_so_it_cannot_shift_the_columns() {
    let long = "y".repeat(200);
    let short = row_prefix(&entry("x", Kind::Repo, "3m ago", None, false));
    let wide = row_prefix(&entry(&long, Kind::Repo, "3m ago", None, false));
    assert_eq!(short.chars().count(), wide.chars().count());
    assert_eq!(at(&short, "repo"), at(&wide, "repo"));
    assert!(wide.starts_with(&format!("{}…", "y".repeat(LABEL_WIDTH - 1))));
}

#[test]
fn a_label_exactly_at_the_width_is_not_elided() {
    let exact = "y".repeat(LABEL_WIDTH);
    let row = row_prefix(&entry(&exact, Kind::Repo, "3m ago", None, false));
    assert!(row.starts_with(&exact));
    assert!(!row.contains('…'));
}

#[test]
fn the_age_column_is_wide_enough_for_the_longest_age() {
    assert!("59m ago".len() <= AGE_WIDTH);
    assert!("52w ago".len() <= AGE_WIDTH);
}

#[test]
fn the_marker_and_the_gutter_are_the_same_width_so_rows_stay_aligned() {
    assert_eq!(MARKER.chars().count(), GUTTER.chars().count());
}

#[test]
fn the_legend_names_every_key_that_is_bound() {
    let text = LEGEND.join(" ");
    for name in ["enter", "tab", "ctrl-a", "ctrl-d", "esc"] {
        assert!(text.contains(name), "the legend never names {}", name);
    }
}

#[test]
fn the_legend_does_not_promise_space_toggles_a_row() {
    let text = LEGEND.join(" ");
    assert!(!text.contains("space"), "{}", text);
}

#[test]
fn the_legend_says_what_checking_a_row_means() {
    let text = LEGEND.join(" ");
    assert!(text.contains("checked = open"), "{}", text);
    assert!(text.contains("closed on enter"), "{}", text);
}

#[test]
fn the_prompt_says_what_typing_does() {
    assert!(PROMPT.contains("filter"));
}

#[test]
fn a_row_carries_its_own_age_rather_than_one_for_the_whole_list() {
    let first = row_prefix(&entry("a", Kind::Repo, "1m ago", None, false));
    let second = row_prefix(&entry("b", Kind::Repo, "9h ago", None, false));
    assert!(first.contains("1m ago"));
    assert!(second.contains("9h ago"));
}

#[test]
fn a_row_carries_its_own_kind_rather_than_a_constant() {
    assert!(row_prefix(&entry("w", Kind::Worktree, "1m ago", None, false)).contains("tree"));
    let repo = row_prefix(&entry("r", Kind::Repo, "1m ago", None, false));
    assert!(repo.contains("repo"));
    assert!(!repo.contains("tree"));
}

#[test]
fn the_haystack_keeps_the_whole_word_worktree_even_though_the_column_says_tree() {
    let row = entry("w", Kind::Worktree, "1m ago", None, false);
    assert!(row.haystack().contains("worktree"), "{}", row.haystack());
    assert!(!row_prefix(&row).contains("worktree"), "{}", row_prefix(&row));
}

#[test]
fn a_worktree_row_says_which_repository_it_belongs_to() {
    let row = row_prefix(&worktree_of("tru-data", "feat-x"));
    assert!(row.starts_with("tru-data/feat-x"), "{}", row);
}

#[test]
fn the_repository_is_a_prefix_of_its_own_and_the_branch_stands_apart_from_it() {
    let cells = row_cells(&worktree_of("tru-data", "feat-x"));
    assert_eq!(cells.repo, "tru-data/");
    assert!(cells.name.starts_with("feat-x"), "{}", cells.name);
    assert!(!cells.name.contains("tru-data"), "{}", cells.name);
}

#[test]
fn a_repository_row_carries_no_prefix_at_all() {
    let cells = row_cells(&entry("alpha", Kind::Repo, "3m ago", None, false));
    assert_eq!(cells.repo, "");
    assert!(cells.name.starts_with("alpha"), "{}", cells.name);
}

#[test]
fn a_prefixed_row_is_exactly_as_wide_as_an_unprefixed_one() {
    let plain = row_prefix(&entry("feat-x", Kind::Worktree, "3m ago", None, false));
    let prefixed = row_prefix(&worktree_of("tru-data", "feat-x"));
    let long = row_prefix(&worktree_of("laravel-model-caching", "fix-cache-gaps"));
    assert_eq!(plain.chars().count(), prefixed.chars().count());
    assert_eq!(plain.chars().count(), long.chars().count());
}

#[test]
fn a_prefix_too_long_for_the_row_is_cut_at_its_end_and_the_branch_survives_whole() {
    let row = row_prefix(&worktree_of("laravel-model-caching", "fix-cache-gaps"));
    assert!(row.starts_with("laravel-model-cach…/fix-cache-gaps"), "{}", row);
    assert_eq!(at(&row, "3m ago"), at(&row_prefix(&worktree_of("x", "y")), "3m ago"));
}

#[test]
fn a_cut_prefix_keeps_the_separator_after_the_mark_so_the_branch_still_stands_apart() {
    let cells = row_cells(&worktree_of("laravel-model-caching", "fix-cache-gaps"));
    assert!(cells.repo.ends_with("…/"), "{}", cells.repo);
    assert_eq!(cells.repo.matches('…').count(), 1, "{}", cells.repo);
    assert!(cells.name.starts_with("fix-cache-gaps"), "{}", cells.name);
}

#[test]
fn a_cut_prefix_keeps_the_head_of_the_repository_name_rather_than_its_tail() {
    let cells = row_cells(&worktree_of("laravel-model-caching", "fix-cache-gaps"));
    assert!(cells.repo.starts_with("laravel-model-"), "{}", cells.repo);
    assert!(!cells.repo.contains("caching"), "{}", cells.repo);
}

#[test]
fn a_prefix_with_one_column_to_spend_is_the_mark_alone() {
    let branch = "b".repeat(LABEL_WIDTH - 1);
    let cells = row_cells(&worktree_of("tru-data", &branch));
    assert_eq!(cells.repo, "…");
    assert!(cells.name.starts_with(&branch), "{}", cells.name);
}

#[test]
fn a_multibyte_repository_name_is_cut_by_character_not_by_byte() {
    let branch = "b".repeat(LABEL_WIDTH - 4);
    let cells = row_cells(&worktree_of("ααααα", &branch));
    assert_eq!(cells.repo, "αα…/");
}

#[test]
fn the_kind_cell_stands_on_its_own_so_it_can_be_styled_apart_from_the_row() {
    let tree = row_cells(&worktree_of("tru-data", "feat-x"));
    let repo = row_cells(&entry("alpha", Kind::Repo, "3m ago", None, false));
    assert_eq!(tree.kind, "tree");
    assert_eq!(repo.kind, "repo");
    assert!(!tree.name.contains("tree"), "{}", tree.name);
    assert!(!tree.age.contains("tree"), "{}", tree.age);
}

#[test]
fn a_branch_that_fills_the_label_leaves_the_prefix_no_room_rather_than_losing_a_character() {
    let branch = "b".repeat(LABEL_WIDTH);
    let row = row_prefix(&worktree_of("tru-data", &branch));
    assert!(row.starts_with(&branch), "{}", row);
    assert!(!row.contains('…'), "{}", row);
}

#[test]
fn a_branch_longer_than_the_label_is_still_cut_from_the_right() {
    let branch = "b".repeat(LABEL_WIDTH + 10);
    let row = row_prefix(&worktree_of("tru-data", &branch));
    assert!(row.starts_with(&format!("{}…", "b".repeat(LABEL_WIDTH - 1))), "{}", row);
}

#[test]
fn the_repository_prefix_is_never_matched_on_because_it_is_only_drawn() {
    let rows = vec![
        worktree_of("tru-data", "feat-x"),
        entry("other", Kind::Repo, "1m ago", None, false),
    ];
    assert!(!rows[0].haystack().contains("tru-data"), "{}", rows[0].haystack());
    let mut picker = Picker::new(rows);
    type_in(&mut picker, "tru-data");
    assert!(shown(&picker).is_empty(), "{:?}", shown(&picker));
}

#[test]
fn a_row_that_is_not_open_carries_no_status_at_all() {
    let closed = entry("a", Kind::Repo, "1m ago", None, false);
    assert!(closed.status.is_none());
    assert!(!closed.haystack().contains("idle"));
}

#[test]
fn a_status_is_matched_on_the_full_label_not_the_elided_one() {
    let long = "w".repeat(LABEL_WIDTH + 20);
    let open = entry(&long, Kind::Repo, "1m ago", Some("blocked"), true);
    assert!(open.haystack().starts_with(&long));
    assert!(open.haystack().contains("blocked"));
}

fn rendered(picker: &Picker, preview: &str, width: u16, height: u16) -> Vec<String> {
    use pick_project::config::HerdrConfig;
    use pick_project::theme::resolve_theme;
    use ratatui::backend::TestBackend;
    use ratatui::Terminal;

    let theme = resolve_theme(&HerdrConfig::default(), &|_| false);
    let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
    terminal
        .draw(|frame| pick_project::ui::draw(frame, picker, &theme, preview))
        .unwrap();
    let buffer = terminal.backend().buffer().clone();
    (0..height)
        .map(|y| {
            (0..width)
                .map(|x| buffer[(x, y)].symbol().to_string())
                .collect::<String>()
                .trim_end()
                .to_string()
        })
        .collect()
}

#[test]
fn the_screen_shows_the_prompt_the_legend_and_the_heading() {
    let screen = rendered(&Picker::new(plain(&["alpha"])), "", 160, 12);
    assert_eq!(screen[0], PROMPT.trim_end(), "{:?}", screen);
    assert_eq!(screen[1], LEGEND[0]);
    assert_eq!(screen[2], LEGEND[1]);
    assert!(screen[3].contains("PROJECT"), "{:?}", screen);
    assert!(screen[3].contains("AGENT STATUS"), "{:?}", screen);
}

#[test]
fn the_screen_shows_what_was_typed() {
    let mut picker = Picker::new(plain(&["alpha", "beta"]));
    type_in(&mut picker, "alph");
    let screen = rendered(&picker, "", 160, 12);
    assert_eq!(screen[0], format!("{}alph", PROMPT));
}

#[test]
fn a_checked_row_is_drawn_with_the_marker_and_an_unchecked_one_with_the_gutter() {
    let entries = vec![
        entry("alpha", Kind::Repo, "1m ago", Some("working"), true),
        entry("beta", Kind::Worktree, "2h ago", None, false),
    ];
    let screen = rendered(&Picker::new(entries), "", 160, 12);
    let rows: Vec<&String> = screen.iter().skip(5).take(2).collect();
    assert!(rows[0].starts_with(MARKER), "{:?}", rows);
    assert!(rows[1].starts_with(GUTTER) && !rows[1].starts_with(MARKER), "{:?}", rows);
}

#[test]
fn an_open_row_is_drawn_with_its_glyph_and_status_word() {
    let entries = vec![entry("alpha", Kind::Repo, "1m ago", Some("working"), true)];
    let screen = rendered(&Picker::new(entries), "", 160, 12);
    let row = screen.iter().find(|l| l.contains("alpha")).unwrap();
    assert!(row.contains("● working"), "{:?}", row);
    assert!(row.contains("repo"), "{:?}", row);
    assert!(row.contains("1m ago"), "{:?}", row);
}

#[test]
fn a_row_that_is_not_open_is_drawn_with_no_status_at_all() {
    let screen = rendered(&Picker::new(plain(&["beta"])), "", 160, 12);
    let row = screen.iter().find(|l| l.contains("beta")).unwrap();
    assert!(!row.contains('●'), "{:?}", row);
    assert!(!row.contains("idle"), "{:?}", row);
}

fn drawn_styles(picker: &Picker, width: u16, row: u16) -> Vec<(String, ratatui::style::Style)> {
    use pick_project::config::HerdrConfig;
    use pick_project::theme::resolve_theme;
    use ratatui::backend::TestBackend;
    use ratatui::Terminal;

    let theme = resolve_theme(&HerdrConfig::default(), &|_| false);
    let mut terminal = Terminal::new(TestBackend::new(width, row + 1)).unwrap();
    terminal
        .draw(|frame| pick_project::ui::draw(frame, picker, &theme, ""))
        .unwrap();
    let buffer = terminal.backend().buffer().clone();
    (0..width)
        .map(|x| {
            let cell = &buffer[(x, row)];
            (cell.symbol().to_string(), cell.style())
        })
        .collect()
}

#[test]
fn the_drawn_row_leads_with_the_repository_and_dims_it_against_the_branch() {
    use ratatui::style::Modifier;

    let picker = Picker::new(vec![worktree_of("tru-data", "feat-x")]);
    let cells = drawn_styles(&picker, 160, 5);
    let text: String = cells.iter().map(|(s, _)| s.as_str()).collect();
    assert!(text.trim_start().starts_with("tru-data/feat-x"), "{}", text);
    let prefix_at = text.find("tru-data").unwrap();
    let branch_at = text.find("feat-x").unwrap();
    assert!(cells[prefix_at].1.add_modifier.contains(Modifier::DIM));
    assert!(cells[prefix_at + 8].1.add_modifier.contains(Modifier::DIM));
    assert!(!cells[branch_at].1.add_modifier.contains(Modifier::DIM));
}

#[test]
fn the_repository_prefix_is_dimmed_by_the_modifier_rather_than_by_a_palette_colour() {
    let picker = Picker::new(vec![worktree_of("tru-data", "feat-x")]);
    let cells = drawn_styles(&picker, 160, 5);
    let text: String = cells.iter().map(|(s, _)| s.as_str()).collect();
    let prefix_at = text.find("tru-data").unwrap();
    assert_eq!(cells[prefix_at].1.fg, Some(ratatui::style::Color::Reset));
}

#[test]
fn a_worktree_kind_cell_is_drawn_in_the_accent_and_a_repository_one_is_left_alone() {
    use pick_project::config::HerdrConfig;
    use pick_project::theme::resolve_theme;
    use pick_project::ui::ratatui_colour;

    let theme = resolve_theme(&HerdrConfig::default(), &|_| false);
    let accent = ratatui_colour(theme.accent);
    let tree = drawn_styles(&Picker::new(vec![worktree_of("tru-data", "feat-x")]), 160, 5);
    let text: String = tree.iter().map(|(s, _)| s.as_str()).collect();
    let at = text.find("tree").unwrap();
    assert_eq!(tree[at].1.fg, Some(accent));

    let repo = drawn_styles(&Picker::new(plain(&["alpha"])), 160, 5);
    let text: String = repo.iter().map(|(s, _)| s.as_str()).collect();
    let at = text.find("repo").unwrap();
    assert_eq!(repo[at].1.fg, Some(ratatui::style::Color::Reset));
    assert_ne!(repo[at].1.fg, Some(accent));
}

#[test]
fn the_preview_is_drawn_beside_the_list_rather_than_over_it() {
    let screen = rendered(&Picker::new(plain(&["alpha"])), "PREVIEW-TEXT", 160, 12);
    let row = screen.iter().find(|l| l.contains("PREVIEW-TEXT")).unwrap();
    let at = row.find("PREVIEW-TEXT").unwrap();
    assert!(at > 76, "the preview must sit in the right-hand pane: {}", at);
    assert!(screen.iter().any(|l| l.contains("alpha")), "{:?}", screen);
}

#[test]
fn the_row_count_and_the_checked_count_are_on_screen() {
    let mut picker = Picker::new(plain(&["alpha", "beta", "gamma"]));
    picker.on_key(control('a'));
    let screen = rendered(&picker, "", 160, 12);
    assert!(screen[4].contains("3/3"), "{:?}", screen[4]);
    assert!(screen[4].contains("3 checked"), "{:?}", screen[4]);
}

#[test]
fn a_list_longer_than_the_screen_scrolls_to_keep_the_cursor_visible() {
    let labels: Vec<String> = (0..40).map(|n| format!("row{:02}", n)).collect();
    let borrowed: Vec<&str> = labels.iter().map(String::as_str).collect();
    let mut picker = Picker::new(plain(&borrowed));
    for _ in 0..39 {
        picker.on_key(key(KeyCode::Down));
    }
    let screen = rendered(&picker, "", 160, 12);
    assert!(
        screen.iter().any(|l| l.contains("row39")),
        "the highlighted row must be on screen: {:?}",
        screen
    );
    assert!(!screen.iter().any(|l| l.contains("row00")), "{:?}", screen);
}

#[test]
fn a_narrow_screen_cuts_the_row_short_rather_than_wrapping_it() {
    let entries = vec![entry("alpha", Kind::Repo, "1m ago", Some("working"), true)];
    let screen = rendered(&Picker::new(entries), "", 100, 12);
    let row = screen.iter().find(|l| l.contains("alpha")).unwrap();
    assert!(!row.contains("working"), "a narrow list pane cuts the row: {}", row);
    assert!(
        !screen.iter().skip(6).any(|l| l.trim_start().starts_with("working")),
        "a cut row must not wrap onto the next one: {:?}",
        screen
    );
}

#[test]
fn a_narrow_screen_still_draws_without_panicking() {
    for width in [20u16, 40, 200] {
        for height in [6u16, 12, 60] {
            rendered(&Picker::new(plain(&["alpha", "beta"])), "text", width, height);
        }
    }
}

#[test]
fn an_empty_list_still_draws_the_heading_and_the_prompt() {
    let mut picker = Picker::new(plain(&["alpha"]));
    type_in(&mut picker, "zzzzzq");
    let screen = rendered(&picker, "", 160, 12);
    assert!(screen[3].contains("PROJECT"), "{:?}", screen);
    assert!(screen[4].contains("0/1"), "{:?}", screen[4]);
}

fn preview_env() -> pick_project::config::Environment {
    pick_project::config::Environment::from_pairs(&[(
        "PATH",
        "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
    )])
}

#[test]
fn the_preview_leads_with_the_full_project_name() {
    let dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let mut previews = pick_project::ui::Previews::new(&preview_env());
    let text = previews.of("a-very-long-project-name", &dir);
    assert!(
        text.starts_with("a-very-long-project-name\n\n"),
        "{}",
        &text[..text.len().min(60)]
    );
}

#[test]
fn the_preview_of_a_git_checkout_is_its_recent_log() {
    let dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let mut previews = pick_project::ui::Previews::new(&preview_env());
    let text = previews.of("project-finder", &dir);
    let body: Vec<&str> = text.lines().skip(2).filter(|l| !l.is_empty()).collect();
    assert!(!body.is_empty(), "{}", text);
    assert!(body.len() <= 8, "at most eight commits: {}", body.len());
    assert!(
        body[0].split_whitespace().next().unwrap().len() >= 7,
        "each line starts with a short hash: {}",
        body[0]
    );
}

#[test]
fn the_preview_of_a_directory_that_is_no_checkout_lists_it_instead() {
    let dir = TempDir::new();
    dir.write("one.txt", "x");
    dir.write("two.txt", "x");
    let mut previews = pick_project::ui::Previews::new(&preview_env());
    let text = previews.of("plain", dir.path());
    assert!(text.contains("one.txt"), "{}", text);
    assert!(text.contains("two.txt"), "{}", text);
}

#[test]
fn a_preview_is_read_once_and_remembered() {
    let dir = TempDir::new();
    dir.write("first.txt", "x");
    let mut previews = pick_project::ui::Previews::new(&preview_env());
    let before = previews.of("plain", dir.path());
    dir.write("second.txt", "x");
    assert_eq!(
        previews.of("plain", dir.path()),
        before,
        "a cached preview must not be read again on every keystroke"
    );
}

#[test]
fn a_directory_that_is_not_there_previews_as_its_name_alone() {
    let mut previews = pick_project::ui::Previews::new(&preview_env());
    let text = previews.of("gone", std::path::Path::new("/x/no/such/place"));
    assert_eq!(text.trim(), "gone");
}
