mod support;

use pick_project::config::HerdrConfig;
use pick_project::theme::{
    canonical_theme, herdr_rejects_theme, palette, parse_colour, resolve_theme, theme_keys, Colour,
    Theme, DOTS, NAMED_COLORS, PALETTES, PALETTE_ROLE_ORDER, STATUSES, STATUS_ROLE_ORDER,
    STATUS_WIDTH, SYMBOLS, THEME_ALIASES,
};
use pick_project::ui::ratatui_colour;
use ratatui::style::Color;
use support::*;

fn never(_: &str) -> bool {
    false
}

fn always(_: &str) -> bool {
    true
}

fn theme_of(pairs: &[(&str, &str)]) -> Theme {
    resolve_theme(&HerdrConfig::from_pairs(pairs), &never)
}

fn theme_rejecting(pairs: &[(&str, &str)]) -> Theme {
    resolve_theme(&HerdrConfig::from_pairs(pairs), &always)
}

fn colours(theme: &Theme) -> Vec<(String, Colour)> {
    STATUSES
        .iter()
        .map(|s| (s.to_string(), theme.colour(s)))
        .collect()
}

#[test]
fn an_exact_theme_name_resolves_to_itself() {
    assert_eq!(canonical_theme("gruvbox"), Some("gruvbox"));
}

#[test]
fn case_and_separators_are_folded() {
    for spelling in [
        "Tokyo_Night",
        "TOKYO NIGHT",
        "tokyo-night",
        "  tokyo night  ",
    ] {
        assert_eq!(
            canonical_theme(spelling),
            Some("tokyo-night"),
            "{}",
            spelling
        );
    }
}

#[test]
fn aliases_resolve_to_their_target() {
    assert_eq!(canonical_theme("onedark"), Some("one-dark"));
    assert_eq!(canonical_theme("gruvbox-dark"), Some("gruvbox"));
    assert_eq!(canonical_theme("dawn"), Some("rose-pine-dawn"));
}

#[test]
fn an_unknown_or_empty_theme_name_resolves_to_nothing() {
    assert_eq!(canonical_theme("monokai"), None);
    assert_eq!(canonical_theme(""), None);
    assert_eq!(canonical_theme("   "), None);
}

#[test]
fn every_alias_target_is_a_real_palette() {
    for (alias, target) in THEME_ALIASES {
        assert!(palette(target).is_some(), "{} points at {}", alias, target);
    }
}

#[test]
fn no_alias_shadows_a_real_palette_name() {
    for (alias, _) in THEME_ALIASES {
        assert!(palette(alias).is_none(), "{} is already a palette", alias);
    }
}

#[test]
fn every_palette_has_all_six_roles() {
    for (name, roles) in PALETTES {
        assert_eq!(roles.len(), PALETTE_ROLE_ORDER.len(), "{}", name);
    }
}

#[test]
fn the_palette_order_opens_with_the_status_roles_in_their_own_order() {
    assert_eq!(
        PALETTE_ROLE_ORDER[..STATUS_ROLE_ORDER.len()],
        STATUS_ROLE_ORDER
    );
}

#[test]
fn the_palette_table_carries_eighteen_themes() {
    assert_eq!(PALETTES.len(), 18);
}

#[test]
fn a_six_digit_hex_is_read_as_rgb() {
    assert_eq!(parse_colour("#8899aa"), Colour::Rgb(0x88, 0x99, 0xaa));
}

#[test]
fn a_three_digit_hex_expands_by_seventeen() {
    assert_eq!(parse_colour("#f0a"), Colour::Rgb(255, 0, 170));
}

#[test]
fn an_rgb_function_is_read_as_rgb() {
    assert_eq!(
        parse_colour("rgb(137, 180, 250)"),
        Colour::Rgb(137, 180, 250)
    );
}

#[test]
fn named_colours_map_to_their_terminal_index() {
    assert_eq!(parse_colour("yellow"), Colour::Indexed(3));
    assert_eq!(parse_colour("lightred"), Colour::Indexed(9));
    assert_eq!(parse_colour("gray"), Colour::Indexed(7));
    assert_eq!(parse_colour("white"), Colour::Indexed(15));
}

#[test]
fn colour_case_and_whitespace_are_folded() {
    assert_eq!(parse_colour("  LightRed "), Colour::Indexed(9));
}

#[test]
fn reset_aliases_are_the_default_foreground() {
    for value in ["reset", "default", "none", "transparent", "RESET"] {
        assert_eq!(parse_colour(value), Colour::Default, "{}", value);
    }
}

#[test]
fn an_unknown_colour_is_cyan_the_way_herdr_reads_one() {
    assert_eq!(parse_colour("chartreuse"), parse_colour("cyan"));
}

#[test]
fn malformed_hex_falls_through_to_the_named_default() {
    assert_eq!(parse_colour("#gg0011"), parse_colour("cyan"));
    assert_eq!(parse_colour("#12345"), parse_colour("cyan"));
}

#[test]
fn out_of_range_rgb_falls_through_to_the_named_default() {
    assert_eq!(parse_colour("rgb(300,0,0)"), parse_colour("cyan"));
    assert_eq!(parse_colour("rgb(1,2)"), parse_colour("cyan"));
    assert_eq!(parse_colour("rgb(a,b,c)"), parse_colour("cyan"));
}

#[test]
fn every_named_colour_is_a_terminal_index() {
    for (name, index) in NAMED_COLORS {
        assert_eq!(parse_colour(name), Colour::Indexed(index), "{}", name);
    }
}

#[test]
fn an_index_reaches_the_screen_as_a_terminal_index() {
    assert_eq!(ratatui_colour(Colour::Indexed(3)), Color::Indexed(3));
}

#[test]
fn an_rgb_triple_reaches_the_screen_as_truecolor() {
    assert_eq!(ratatui_colour(Colour::Rgb(1, 2, 3)), Color::Rgb(1, 2, 3));
}

#[test]
fn the_default_foreground_reaches_the_screen_as_a_reset() {
    assert_eq!(ratatui_colour(Colour::Default), Color::Reset);
}

#[test]
fn the_defaults_are_dots_on_catppuccin() {
    let theme = theme_of(&[]);
    assert_eq!(theme.icon("working"), "●");
    assert_eq!(theme.icon("idle"), "○");
    assert_eq!(theme.colour("working"), Colour::Rgb(249, 226, 175));
}

#[test]
fn the_symbols_style_changes_every_glyph_herdr_changes() {
    let theme = theme_of(&[("ui.status_indicators", "symbols")]);
    for (status, glyph) in SYMBOLS {
        assert_eq!(theme.icon(status), glyph, "{}", status);
    }
}

#[test]
fn an_unknown_indicator_style_falls_back_to_dots() {
    let theme = theme_of(&[("ui.status_indicators", "emoji")]);
    for (status, glyph) in DOTS {
        assert_eq!(theme.icon(status), glyph, "{}", status);
    }
}

#[test]
fn the_terminal_theme_yields_ansi_indexes_not_hexes() {
    let theme = theme_of(&[("theme.name", "terminal")]);
    assert_eq!(theme.colour("working"), Colour::Indexed(3));
    assert_eq!(theme.colour("blocked"), Colour::Indexed(9));
    assert_eq!(theme.colour("done"), Colour::Indexed(6));
    assert_eq!(theme.colour("idle"), Colour::Indexed(2));
    assert_eq!(theme.colour("unknown"), Colour::Indexed(7));
}

#[test]
fn each_status_reads_its_own_role() {
    let theme = theme_of(&[("theme.name", "gruvbox")]);
    assert_eq!(theme.colour("working"), Colour::Rgb(250, 189, 47));
    assert_eq!(theme.colour("blocked"), Colour::Rgb(251, 73, 52));
    assert_eq!(theme.colour("done"), Colour::Rgb(142, 192, 124));
    assert_eq!(theme.colour("idle"), Colour::Rgb(184, 187, 38));
    assert_eq!(theme.colour("unknown"), Colour::Rgb(146, 131, 116));
}

#[test]
fn a_name_herdr_also_rejects_uses_herdrs_own_default() {
    let typo = theme_rejecting(&[("theme.name", "monokai")]);
    assert_eq!(colours(&typo), colours(&theme_of(&[])));
}

#[test]
fn a_name_herdr_accepts_but_this_table_lacks_uses_the_terminal_palette() {
    let newer = theme_of(&[("theme.name", "brand-new-theme")]);
    assert_eq!(
        colours(&newer),
        colours(&theme_of(&[("theme.name", "terminal")]))
    );
    assert_ne!(colours(&newer), colours(&theme_of(&[])));
}

#[test]
fn an_unset_theme_name_never_asks_herdr() {
    let asked = std::cell::RefCell::new(Vec::new());
    resolve_theme(&HerdrConfig::default(), &|field| {
        asked.borrow_mut().push(field.to_string());
        false
    });
    assert!(asked.into_inner().is_empty());
}

#[test]
fn a_known_theme_name_never_asks_herdr() {
    let asked = std::cell::RefCell::new(Vec::new());
    resolve_theme(
        &HerdrConfig::from_pairs(&[("theme.name", "gruvbox")]),
        &|field| {
            asked.borrow_mut().push(field.to_string());
            false
        },
    );
    assert!(asked.into_inner().is_empty());
}

#[test]
fn the_probe_is_told_which_field_to_look_for() {
    let asked = std::cell::RefCell::new(Vec::new());
    let config =
        HerdrConfig::from_pairs(&[("theme.auto_switch", "true"), ("theme.dark_name", "nope")]);
    resolve_theme(&config, &|field| {
        asked.borrow_mut().push(field.to_string());
        false
    });
    assert_eq!(asked.into_inner(), vec!["theme.dark_name"]);
}

#[test]
fn a_custom_override_replaces_only_its_role() {
    let theme = theme_of(&[("theme.name", "terminal"), ("theme.custom.red", "#ff8800")]);
    assert_eq!(theme.colour("blocked"), Colour::Rgb(255, 136, 0));
    assert_eq!(theme.colour("working"), Colour::Indexed(3));
}

#[test]
fn every_status_role_is_overridable() {
    for (role, status) in [
        ("green", "idle"),
        ("yellow", "working"),
        ("red", "blocked"),
        ("teal", "done"),
        ("overlay0", "unknown"),
    ] {
        let key = format!("theme.custom.{}", role);
        let theme = theme_of(&[("theme.name", "terminal"), (&key, "#010203")]);
        assert_eq!(theme.colour(status), Colour::Rgb(1, 2, 3), "{}", role);
    }
}

#[test]
fn auto_switch_uses_the_dark_name() {
    let theme = theme_of(&[
        ("theme.name", "terminal"),
        ("theme.auto_switch", "true"),
        ("theme.dark_name", "gruvbox"),
    ]);
    assert_eq!(theme.colour("working"), Colour::Rgb(250, 189, 47));
}

#[test]
fn auto_switch_off_ignores_the_dark_name() {
    let theme = theme_of(&[
        ("theme.name", "terminal"),
        ("theme.auto_switch", "false"),
        ("theme.dark_name", "gruvbox"),
    ]);
    assert_eq!(theme.colour("working"), Colour::Indexed(3));
}

#[test]
fn mode_overrides_apply_only_under_auto_switch() {
    let off = theme_of(&[
        ("theme.name", "terminal"),
        ("theme.custom.dark.yellow", "#010203"),
    ]);
    let on = theme_of(&[
        ("theme.name", "terminal"),
        ("theme.custom.dark.yellow", "#010203"),
        ("theme.auto_switch", "true"),
    ]);
    assert_eq!(off.colour("working"), Colour::Indexed(3));
    assert_eq!(on.colour("working"), Colour::Rgb(1, 2, 3));
}

#[test]
fn a_mode_override_beats_the_unqualified_one() {
    let theme = theme_of(&[
        ("theme.name", "terminal"),
        ("theme.auto_switch", "true"),
        ("theme.custom.yellow", "#111111"),
        ("theme.custom.dark.yellow", "#222222"),
    ]);
    assert_eq!(theme.colour("working"), Colour::Rgb(0x22, 0x22, 0x22));
}

#[test]
fn a_real_boolean_turns_auto_switch_on_as_well_as_the_word() {
    let config = HerdrConfig::parse(
        "[theme]\nname = \"terminal\"\nauto_switch = true\ndark_name = \"gruvbox\"\n",
    );
    let theme = resolve_theme(&config, &never);
    assert_eq!(theme.colour("working"), Colour::Rgb(250, 189, 47));
}

#[test]
fn the_accent_starts_as_the_cool_slot_of_whatever_palette_is_in_force() {
    for (name, palette) in PALETTES {
        let theme = theme_of(&[("theme.name", name)]);
        assert_eq!(theme.accent, palette[3], "{}", name);
    }
}

#[test]
fn a_custom_accent_overrides_the_palette_and_is_read_as_a_colour() {
    let theme = theme_of(&[
        ("theme.name", "terminal"),
        ("theme.custom.accent", "lightblue"),
    ]);
    assert_eq!(theme.accent, Colour::Indexed(12));
}

#[test]
fn a_custom_accent_is_taken_over_a_custom_teal_because_they_are_separate_roles() {
    let theme = theme_of(&[
        ("theme.name", "terminal"),
        ("theme.custom.teal", "#ff0000"),
        ("theme.custom.accent", "lightblue"),
    ]);
    assert_eq!(theme.accent, Colour::Indexed(12));
    assert_eq!(theme.colour("done"), Colour::Rgb(255, 0, 0));
}

#[test]
fn a_custom_teal_alone_never_moves_the_accent() {
    let theme = theme_of(&[("theme.name", "terminal"), ("theme.custom.teal", "#ff0000")]);
    assert_eq!(theme.accent, Colour::Indexed(6));
}

#[test]
fn the_dark_accent_is_taken_only_when_the_theme_switches_automatically() {
    let off = theme_of(&[
        ("theme.name", "terminal"),
        ("theme.custom.dark.accent", "#010203"),
    ]);
    assert_eq!(off.accent, Colour::Indexed(6));
    let on = theme_of(&[
        ("theme.dark_name", "terminal"),
        ("theme.auto_switch", "true"),
        ("theme.custom.dark.accent", "#010203"),
    ]);
    assert_eq!(on.accent, Colour::Rgb(1, 2, 3));
}

#[test]
fn the_theme_keys_cover_every_key_the_resolver_reads() {
    let keys = theme_keys();
    assert!(keys.contains(&"theme.custom.accent".to_string()));
    assert!(keys.contains(&"theme.custom.dark.accent".to_string()));
    for role in STATUS_ROLE_ORDER {
        assert!(keys.contains(&format!("theme.custom.{}", role)));
        assert!(keys.contains(&format!("theme.custom.dark.{}", role)));
    }
    for key in [
        "ui.status_indicators",
        "theme.name",
        "theme.auto_switch",
        "theme.dark_name",
    ] {
        assert!(keys.contains(&key.to_string()), "{}", key);
    }
}

#[test]
fn a_status_cell_carries_the_glyph_and_the_word() {
    let theme = theme_of(&[("ui.status_indicators", "symbols")]);
    assert_eq!(theme.cell("working"), "◐ working   ");
}

#[test]
fn the_status_word_is_kept_so_the_filter_can_match_it() {
    assert!(theme_of(&[]).cell("idle").contains("idle"));
}

#[test]
fn a_status_cell_is_padded_to_the_column_width() {
    for status in STATUSES {
        assert_eq!(
            theme_of(&[]).cell(status).chars().count(),
            STATUS_WIDTH,
            "{}",
            status
        );
    }
}

#[test]
fn an_unrecognised_status_takes_the_unknown_glyph_and_colour() {
    let theme = theme_of(&[("theme.name", "terminal")]);
    assert!(theme.cell("brand-new").contains('·'));
    assert_eq!(theme.colour("brand-new"), theme.colour("unknown"));
}

#[test]
fn a_reset_colour_is_kept_distinct_from_a_missing_one() {
    let theme = theme_of(&[
        ("theme.name", "terminal"),
        ("theme.custom.overlay0", "reset"),
    ]);
    assert_eq!(theme.colour("unknown"), Colour::Default);
    assert_eq!(theme.colour("brand-new"), Colour::Default);
}

fn fake_herdr(dir: &TempDir, stdout: &str, stderr: &str) -> String {
    use std::os::unix::fs::PermissionsExt;
    let body = format!(
        "#!/bin/sh\nprintf '%s' '{}'\nprintf '%s' '{}' >&2\n",
        stdout, stderr
    );
    let path = dir.write("herdr", &body);
    let mut perms = std::fs::metadata(&path).unwrap().permissions();
    perms.set_mode(0o755);
    std::fs::set_permissions(&path, perms).unwrap();
    path.to_string_lossy().to_string()
}

const DIAGNOSTIC: &str =
    "config: issues found\nunknown theme name theme.name = \"monokai\"; using \"catppuccin\"";

#[test]
fn a_diagnostic_naming_the_field_is_a_rejection() {
    let dir = TempDir::new();
    let herdr = fake_herdr(&dir, DIAGNOSTIC, "");
    assert!(herdr_rejects_theme(Some(&herdr), "theme.name"));
}

#[test]
fn a_clean_check_is_not_a_rejection() {
    let dir = TempDir::new();
    let herdr = fake_herdr(&dir, "config: ok", "");
    assert!(!herdr_rejects_theme(Some(&herdr), "theme.name"));
}

#[test]
fn a_diagnostic_about_a_different_field_is_not_a_rejection() {
    let dir = TempDir::new();
    let herdr = fake_herdr(&dir, DIAGNOSTIC, "");
    assert!(!herdr_rejects_theme(Some(&herdr), "theme.dark_name"));
}

#[test]
fn the_probe_reads_stderr_too() {
    let dir = TempDir::new();
    let herdr = fake_herdr(&dir, "", DIAGNOSTIC);
    assert!(herdr_rejects_theme(Some(&herdr), "theme.name"));
}

#[test]
fn an_unrunnable_herdr_is_not_a_rejection() {
    assert!(!herdr_rejects_theme(Some("/x/no/such/herdr"), "theme.name"));
}

#[test]
fn no_herdr_at_all_is_not_a_rejection() {
    assert!(!herdr_rejects_theme(None, "theme.name"));
}

#[test]
fn a_theme_the_table_lacks_is_drawn_rather_than_refused() {
    let dir = TempDir::new();
    let herdr = fake_herdr(&dir, "config: ok", "");
    let config = HerdrConfig::from_pairs(&[("theme.name", "brand-new-theme")]);
    let theme = resolve_theme(&config, &|field| herdr_rejects_theme(Some(&herdr), field));
    assert_eq!(theme.colour("idle"), Colour::Indexed(2));
}
