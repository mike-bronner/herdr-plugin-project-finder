use crate::config::HerdrConfig;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Colour {
    Default,
    Indexed(u8),
    Rgb(u8, u8, u8),
}

pub const STATUS_WIDTH: usize = 12;

pub const STATUSES: [&str; 5] = ["working", "blocked", "done", "idle", "unknown"];

pub const STATUS_ROLE_ORDER: [&str; 5] = ["green", "yellow", "red", "teal", "overlay0"];

pub const STATUS_ROLES: [(&str, &str); 5] = [
    ("working", "yellow"),
    ("blocked", "red"),
    ("done", "teal"),
    ("idle", "green"),
    ("unknown", "overlay0"),
];

pub const DOTS: [(&str, &str); 5] = [
    ("working", "●"),
    ("blocked", "●"),
    ("done", "●"),
    ("idle", "○"),
    ("unknown", "·"),
];

pub const SYMBOLS: [(&str, &str); 5] = [
    ("working", "◐"),
    ("blocked", "×"),
    ("done", "✓"),
    ("idle", "○"),
    ("unknown", "·"),
];

pub const DEFAULT_THEME: &str = "catppuccin";
pub const UNKNOWN_THEME: &str = "terminal";

const fn rgb(r: u8, g: u8, b: u8) -> Colour {
    Colour::Rgb(r, g, b)
}

pub const PALETTES: [(&str, [Colour; 5]); 18] = [
    ("catppuccin", [rgb(166, 227, 161), rgb(249, 226, 175), rgb(243, 139, 168), rgb(148, 226, 213), rgb(108, 112, 134)]),
    ("catppuccin-latte", [rgb(64, 160, 43), rgb(223, 142, 29), rgb(210, 15, 57), rgb(23, 146, 153), rgb(156, 160, 176)]),
    ("terminal", [Colour::Indexed(2), Colour::Indexed(3), Colour::Indexed(9), Colour::Indexed(6), Colour::Indexed(7)]),
    ("tokyo-night", [rgb(158, 206, 106), rgb(224, 175, 104), rgb(247, 118, 142), rgb(125, 207, 255), rgb(86, 95, 137)]),
    ("tokyo-night-day", [rgb(88, 117, 57), rgb(140, 108, 62), rgb(245, 42, 101), rgb(17, 140, 116), rgb(137, 144, 179)]),
    ("dracula", [rgb(80, 250, 123), rgb(241, 250, 140), rgb(255, 85, 85), rgb(139, 233, 253), rgb(98, 114, 164)]),
    ("nord", [rgb(163, 190, 140), rgb(235, 203, 139), rgb(191, 97, 106), rgb(143, 188, 187), rgb(76, 86, 106)]),
    ("gruvbox", [rgb(184, 187, 38), rgb(250, 189, 47), rgb(251, 73, 52), rgb(142, 192, 124), rgb(146, 131, 116)]),
    ("gruvbox-light", [rgb(121, 116, 14), rgb(181, 118, 20), rgb(157, 0, 6), rgb(66, 123, 88), rgb(146, 131, 116)]),
    ("one-dark", [rgb(152, 195, 121), rgb(229, 192, 123), rgb(224, 108, 117), rgb(86, 182, 194), rgb(92, 99, 112)]),
    ("one-light", [rgb(80, 161, 79), rgb(193, 132, 1), rgb(228, 86, 73), rgb(1, 132, 188), rgb(160, 161, 167)]),
    ("solarized", [rgb(133, 153, 0), rgb(181, 137, 0), rgb(220, 50, 47), rgb(42, 161, 152), rgb(88, 110, 117)]),
    ("solarized-light", [rgb(133, 153, 0), rgb(181, 137, 0), rgb(220, 50, 47), rgb(42, 161, 152), rgb(147, 161, 161)]),
    ("kanagawa", [rgb(118, 148, 106), rgb(192, 163, 110), rgb(195, 64, 67), rgb(127, 180, 202), rgb(114, 113, 105)]),
    ("kanagawa-lotus", [rgb(111, 137, 78), rgb(119, 113, 63), rgb(200, 64, 83), rgb(78, 140, 162), rgb(160, 156, 172)]),
    ("rose-pine", [rgb(49, 116, 143), rgb(246, 193, 119), rgb(235, 111, 146), rgb(156, 207, 216), rgb(110, 106, 134)]),
    ("rose-pine-dawn", [rgb(40, 105, 131), rgb(234, 157, 52), rgb(180, 99, 122), rgb(86, 148, 159), rgb(152, 147, 165)]),
    ("vesper", [rgb(153, 255, 228), rgb(255, 199, 153), rgb(255, 128, 128), rgb(102, 221, 204), rgb(92, 92, 92)]),
];

pub const THEME_ALIASES: [(&str, &str); 14] = [
    ("catppuccin-mocha", "catppuccin"),
    ("latte", "catppuccin-latte"),
    ("light", "catppuccin-latte"),
    ("tokyonight", "tokyo-night"),
    ("tokyo-day", "tokyo-night-day"),
    ("tokyonight-day", "tokyo-night-day"),
    ("gruvbox-dark", "gruvbox"),
    ("onedark", "one-dark"),
    ("onelight", "one-light"),
    ("solarized-dark", "solarized"),
    ("lotus", "kanagawa-lotus"),
    ("rosepine", "rose-pine"),
    ("rosepine-dawn", "rose-pine-dawn"),
    ("dawn", "rose-pine-dawn"),
];

pub const NAMED_COLORS: [(&str, u8); 19] = [
    ("black", 0), ("red", 1), ("green", 2), ("yellow", 3), ("blue", 4),
    ("magenta", 5), ("purple", 5), ("cyan", 6), ("gray", 7), ("grey", 7),
    ("darkgray", 8), ("darkgrey", 8), ("lightred", 9), ("lightgreen", 10),
    ("lightyellow", 11), ("lightblue", 12), ("lightmagenta", 13),
    ("lightcyan", 14), ("white", 15),
];

pub const RESET_ALIASES: [&str; 4] = ["reset", "default", "none", "transparent"];

pub fn palette(name: &str) -> Option<&'static [Colour; 5]> {
    PALETTES.iter().find(|(n, _)| *n == name).map(|(_, p)| p)
}

pub fn canonical_theme(name: &str) -> Option<&'static str> {
    let key: String = name
        .trim()
        .to_lowercase()
        .chars()
        .map(|c| if c == ' ' || c == '_' { '-' } else { c })
        .collect();
    let target: &str = THEME_ALIASES
        .iter()
        .find(|(alias, _)| *alias == key)
        .map(|(_, target)| *target)
        .unwrap_or(&key);
    PALETTES.iter().find(|(n, _)| *n == target).map(|(n, _)| *n)
}

fn named_colour(name: &str) -> Colour {
    NAMED_COLORS
        .iter()
        .find(|(n, _)| *n == name)
        .map(|(_, i)| Colour::Indexed(*i))
        .unwrap_or(Colour::Indexed(6))
}

pub fn parse_colour(value: &str) -> Colour {
    let s = value.trim().to_lowercase();
    if RESET_ALIASES.contains(&s.as_str()) {
        return Colour::Default;
    }
    if let Some(hex) = s.strip_prefix('#') {
        let digits: Vec<char> = hex.chars().collect();
        let hexy = digits.iter().all(|c| c.is_ascii_hexdigit());
        if hexy && digits.len() == 6 {
            let byte = |at: usize| u8::from_str_radix(&hex[at..at + 2], 16).unwrap_or(0);
            return Colour::Rgb(byte(0), byte(2), byte(4));
        }
        if hexy && digits.len() == 3 {
            let byte = |at: usize| u8::from_str_radix(&hex[at..at + 1], 16).unwrap_or(0) * 17;
            return Colour::Rgb(byte(0), byte(1), byte(2));
        }
    }
    if let Some(inner) = s.strip_prefix("rgb(").and_then(|r| r.strip_suffix(')')) {
        let parts: Vec<&str> = inner.split(',').map(str::trim).collect();
        let numbers: Option<Vec<u16>> = parts
            .iter()
            .map(|p| {
                if p.chars().all(|c| c.is_ascii_digit()) && !p.is_empty() {
                    p.parse::<u16>().ok()
                } else {
                    None
                }
            })
            .collect();
        if let Some(numbers) = numbers {
            if numbers.len() == 3 && numbers.iter().all(|n| *n < 256) {
                return Colour::Rgb(numbers[0] as u8, numbers[1] as u8, numbers[2] as u8);
            }
        }
    }
    named_colour(&s)
}

pub fn theme_keys() -> Vec<String> {
    let mut keys = vec![
        "ui.status_indicators".to_string(),
        "theme.name".to_string(),
        "theme.auto_switch".to_string(),
        "theme.dark_name".to_string(),
    ];
    for role in STATUS_ROLE_ORDER {
        keys.push(format!("theme.custom.{}", role));
        keys.push(format!("theme.custom.dark.{}", role));
    }
    keys
}

#[derive(Debug, Clone)]
pub struct Theme {
    pub icons: Vec<(String, String)>,
    pub colours: Vec<(String, Colour)>,
}

impl Theme {
    pub fn icon(&self, status: &str) -> &str {
        self.lookup_icon(status).unwrap_or_else(|| self.lookup_icon("unknown").unwrap_or("·"))
    }

    fn lookup_icon(&self, status: &str) -> Option<&str> {
        self.icons
            .iter()
            .find(|(s, _)| s == status)
            .map(|(_, g)| g.as_str())
    }

    pub fn colour(&self, status: &str) -> Colour {
        self.colours
            .iter()
            .find(|(s, _)| s == status)
            .map(|(_, c)| *c)
            .unwrap_or_else(|| {
                self.colours
                    .iter()
                    .find(|(s, _)| s == "unknown")
                    .map(|(_, c)| *c)
                    .unwrap_or(Colour::Default)
            })
    }

    pub fn cell(&self, status: &str) -> String {
        let text = format!("{} {}", self.icon(status), status);
        let width = text.chars().count();
        if width >= STATUS_WIDTH {
            text
        } else {
            format!("{}{}", text, " ".repeat(STATUS_WIDTH - width))
        }
    }
}

pub fn resolve_theme(config: &HerdrConfig, rejects: &dyn Fn(&str) -> bool) -> Theme {
    let style = config.string("ui.status_indicators").unwrap_or("dots");
    let glyphs = if style == "symbols" { SYMBOLS } else { DOTS };
    let icons = glyphs
        .iter()
        .map(|(s, g)| (s.to_string(), g.to_string()))
        .collect();

    let auto = config.boolean("theme.auto_switch").unwrap_or(false);
    let field = if auto { "theme.dark_name" } else { "theme.name" };
    let name = config.string(field).unwrap_or("");
    let key = match canonical_theme(name) {
        Some(key) => key,
        None if name.is_empty() => DEFAULT_THEME,
        None if rejects(field) => DEFAULT_THEME,
        None => UNKNOWN_THEME,
    };
    let base = palette(key).expect("every theme key names a palette");

    let mut roles: Vec<(&str, Colour)> = STATUS_ROLE_ORDER
        .iter()
        .zip(base.iter())
        .map(|(role, colour)| (*role, *colour))
        .collect();

    let mut tables = vec!["theme.custom"];
    if auto {
        tables.push("theme.custom.dark");
    }
    for (role, colour) in roles.iter_mut() {
        for table in &tables {
            if let Some(value) = config.string(&format!("{}.{}", table, role)) {
                *colour = parse_colour(value);
            }
        }
    }

    let colours = STATUS_ROLES
        .iter()
        .map(|(status, role)| {
            let colour = roles
                .iter()
                .find(|(r, _)| r == role)
                .map(|(_, c)| *c)
                .unwrap_or(Colour::Default);
            (status.to_string(), colour)
        })
        .collect();

    Theme { icons, colours }
}

pub fn herdr_rejects_theme(herdr: Option<&str>, field: &str) -> bool {
    let Some(herdr) = herdr else {
        return false;
    };
    let Ok(output) = std::process::Command::new(herdr)
        .args(["config", "check"])
        .output()
    else {
        return false;
    };
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    text.contains(&format!("unknown theme name {}", field))
}
