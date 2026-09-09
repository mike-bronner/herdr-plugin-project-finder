use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::Deserialize;

pub const ROOT_VAR: &str = "HERDR_PICKER_ROOT";
pub const HOME_VAR: &str = "HERDR_PICKER_HOME";
pub const DEBUG_VAR: &str = "HERDR_PICKER_DEBUG";
pub const LAYOUT_VAR: &str = "HERDR_PICKER_LAYOUT";

pub const PICKER_KEYS: [(&str, &str); 4] = [
    ("picker.root", ROOT_VAR),
    ("picker.home", HOME_VAR),
    ("picker.debug", DEBUG_VAR),
    ("picker.layout", LAYOUT_VAR),
];

pub const FIXED_WORKTREE_DIRS: [&str; 3] = [".worktrees", ".claude/worktrees", "worktrees"];

pub const DEFAULT_HOME_LABEL: &str = "~";

#[derive(Debug, Clone, Default)]
pub struct Environment {
    vars: BTreeMap<String, String>,
}

impl Environment {
    pub fn from_process() -> Environment {
        Environment {
            vars: std::env::vars().collect(),
        }
    }

    pub fn from_pairs(pairs: &[(&str, &str)]) -> Environment {
        Environment {
            vars: pairs
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
        }
    }

    pub fn get(&self, key: &str) -> Option<&str> {
        self.vars.get(key).map(String::as_str)
    }

    pub fn home(&self) -> PathBuf {
        PathBuf::from(self.get("HOME").unwrap_or("/"))
    }

    pub fn expanduser(&self, value: &str) -> String {
        if value == "~" {
            return self.home().to_string_lossy().to_string();
        }
        match value.strip_prefix("~/") {
            Some(rest) => self.home().join(rest).to_string_lossy().to_string(),
            None => value.to_string(),
        }
    }

    pub fn without_plugin_vars(&self) -> Vec<(String, String)> {
        self.vars
            .iter()
            .filter(|(k, _)| k.as_str() != "HERDR_PLUGIN_ROOT" && k.as_str() != "HERDR_PLUGIN_CONFIG_DIR")
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect()
    }

    pub fn overridden(&self, key: &str, value: &str) -> Environment {
        let mut vars = self.vars.clone();
        vars.insert(key.to_string(), value.to_string());
        Environment { vars }
    }

    pub fn overlaid(&self, pairs: &[(String, String)]) -> Environment {
        let mut vars = self.vars.clone();
        for (key, value) in pairs {
            vars.entry(key.clone()).or_insert_with(|| value.clone());
        }
        Environment { vars }
    }
}

pub fn parse_env_file(text: &str) -> Vec<(String, String)> {
    let mut pairs = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') || !line.contains('=') {
            continue;
        }
        let (key, value) = line.split_once('=').unwrap();
        let key = key.trim();
        let mut value = value.trim().to_string();
        let bytes: Vec<char> = value.chars().collect();
        if bytes.len() >= 2
            && bytes[0] == bytes[bytes.len() - 1]
            && (bytes[0] == '"' || bytes[0] == '\'')
        {
            value = bytes[1..bytes.len() - 1].iter().collect();
        }
        if !key.is_empty() {
            pairs.push((key.to_string(), value));
        }
    }
    pairs
}

pub fn read_env_file(path: &Path) -> Vec<(String, String)> {
    match std::fs::read_to_string(path) {
        Ok(text) => parse_env_file(&text),
        Err(_) => Vec::new(),
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct PickerTable {
    pub root: Option<String>,
    pub home: Option<String>,
    pub debug: Option<bool>,
    pub layout: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
struct PickerFile {
    picker: Option<PickerTable>,
}

#[derive(Debug, Clone, Default)]
pub struct PickerConfig {
    pub table: PickerTable,
    pub unknown: Vec<String>,
    pub error: Option<String>,
}

pub fn parse_picker_config(text: &str) -> PickerConfig {
    let mut unknown = Vec::new();
    let deserializer = toml::Deserializer::new(text);
    let parsed: Result<PickerFile, toml::de::Error> =
        serde_ignored::deserialize(deserializer, |path| unknown.push(path.to_string()));
    match parsed {
        Ok(file) => PickerConfig {
            table: file.picker.unwrap_or_default(),
            unknown,
            error: None,
        },
        Err(e) => PickerConfig {
            table: PickerTable::default(),
            unknown: Vec::new(),
            error: Some(first_line(&e.to_string())),
        },
    }
}

fn first_line(text: &str) -> String {
    text.lines().next().unwrap_or("").trim().to_string()
}

pub fn read_picker_config(path: &Path) -> PickerConfig {
    match std::fs::read_to_string(path) {
        Ok(text) => parse_picker_config(&text),
        Err(_) => PickerConfig::default(),
    }
}

#[derive(Debug, Clone)]
pub struct Settings {
    pub root: Option<String>,
    pub home: Option<String>,
    pub debug: bool,
    pub layout: Option<String>,
    pub complaints: Vec<String>,
    pub env_file: Vec<(String, String)>,
}

pub struct Sources {
    pub config: PickerConfig,
    pub env_file: Vec<(String, String)>,
    pub defaults: PickerConfig,
}

pub fn read_sources(env: &Environment, own_root: &Path) -> Sources {
    let dir = env.get("HERDR_PLUGIN_CONFIG_DIR").map(PathBuf::from);
    Sources {
        config: dir
            .as_ref()
            .map(|d| read_picker_config(&d.join("config.toml")))
            .unwrap_or_default(),
        env_file: dir
            .as_ref()
            .map(|d| read_env_file(&d.join(".env")))
            .unwrap_or_default(),
        defaults: read_picker_config(&own_root.join("defaults.toml")),
    }
}

pub fn resolve_settings(env: &Environment, sources: &Sources) -> Settings {
    let mut complaints = Vec::new();
    for (which, config) in [("config.toml", &sources.config), ("defaults.toml", &sources.defaults)] {
        if let Some(error) = &config.error {
            complaints.push(format!(
                "{} does not parse, so none of it applies: {}",
                which, error
            ));
        }
        for key in &config.unknown {
            complaints.push(format!("{} names {}, which the picker has no setting for", which, key));
        }
    }

    let from_env_file = |name: &str| {
        sources
            .env_file
            .iter()
            .find(|(k, _)| k == name)
            .map(|(_, v)| v.clone())
    };
    let string_of = |var: &str,
                     pick: fn(&PickerTable) -> Option<String>|
     -> Option<String> {
        env.get(var)
            .map(str::to_string)
            .or_else(|| pick(&sources.config.table))
            .or_else(|| from_env_file(var))
            .or_else(|| pick(&sources.defaults.table))
    };

    let debug = match env.get(DEBUG_VAR) {
        Some(value) => !value.is_empty(),
        None => sources
            .config
            .table
            .debug
            .or_else(|| from_env_file(DEBUG_VAR).map(|v| !v.is_empty()))
            .or(sources.defaults.table.debug)
            .unwrap_or(false),
    };

    Settings {
        root: string_of(ROOT_VAR, |t| t.root.clone()),
        home: string_of(HOME_VAR, |t| t.home.clone()),
        debug,
        layout: string_of(LAYOUT_VAR, |t| t.layout.clone()),
        complaints,
        env_file: sources.env_file.clone(),
    }
}

pub fn home_label(settings: &Settings) -> String {
    settings
        .home
        .clone()
        .unwrap_or_else(|| DEFAULT_HOME_LABEL.to_string())
}

pub fn resolve_root(env: &Environment, settings: &Settings) -> PathBuf {
    let home = env.home();
    let named = settings.root.as_deref().unwrap_or("");
    if named.is_empty() {
        return home;
    }
    let expanded = PathBuf::from(env.expanduser(named));
    if expanded.is_dir() {
        expanded
    } else {
        home
    }
}

pub fn herdr_config_path(env: &Environment) -> PathBuf {
    if let Some(override_path) = env.get("HERDR_CONFIG_PATH").filter(|v| !v.is_empty()) {
        return PathBuf::from(env.expanduser(override_path));
    }
    if let Some(dir) = env.get("HERDR_PLUGIN_CONFIG_DIR").filter(|v| !v.is_empty()) {
        let trimmed = dir.trim_end_matches('/');
        let root = Path::new(trimmed).ancestors().nth(3);
        if let Some(root) = root {
            let candidate = root.join("config.toml");
            if candidate.is_file() {
                return candidate;
            }
        }
    }
    env.home().join(".config/herdr/config.toml")
}

#[derive(Debug, Clone, Default)]
pub struct HerdrConfig {
    values: BTreeMap<String, toml::Value>,
}

impl HerdrConfig {
    pub fn parse(text: &str) -> HerdrConfig {
        let mut values = BTreeMap::new();
        if let Ok(table) = text.parse::<toml::Table>() {
            flatten("", &toml::Value::Table(table), &mut values);
        }
        HerdrConfig { values }
    }

    pub fn read(path: &Path) -> HerdrConfig {
        match std::fs::read_to_string(path) {
            Ok(text) => HerdrConfig::parse(&text),
            Err(_) => HerdrConfig::default(),
        }
    }

    pub fn from_pairs(pairs: &[(&str, &str)]) -> HerdrConfig {
        HerdrConfig {
            values: pairs
                .iter()
                .map(|(k, v)| (k.to_string(), toml::Value::String(v.to_string())))
                .collect(),
        }
    }

    pub fn string(&self, key: &str) -> Option<&str> {
        self.values.get(key)?.as_str()
    }

    pub fn boolean(&self, key: &str) -> Option<bool> {
        match self.values.get(key)? {
            toml::Value::Boolean(b) => Some(*b),
            toml::Value::String(s) => Some(s.trim().eq_ignore_ascii_case("true")),
            _ => None,
        }
    }

    pub fn has(&self, key: &str) -> bool {
        self.values.contains_key(key)
    }
}

fn flatten(prefix: &str, value: &toml::Value, out: &mut BTreeMap<String, toml::Value>) {
    match value {
        toml::Value::Table(table) => {
            for (key, child) in table {
                let path = if prefix.is_empty() {
                    key.clone()
                } else {
                    format!("{}.{}", prefix, key)
                };
                flatten(&path, child, out);
            }
        }
        other => {
            if !prefix.is_empty() {
                out.insert(prefix.to_string(), other.clone());
            }
        }
    }
}

pub fn normpath(path: &str) -> String {
    if path.is_empty() {
        return ".".to_string();
    }
    let absolute = path.starts_with('/');
    let mut parts: Vec<&str> = Vec::new();
    for part in path.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                let climbable = matches!(parts.last(), Some(last) if *last != "..");
                if climbable {
                    parts.pop();
                } else if !absolute {
                    parts.push("..");
                }
            }
            other => parts.push(other),
        }
    }
    let joined = parts.join("/");
    match (absolute, joined.is_empty()) {
        (true, _) => format!("/{}", joined),
        (false, true) => ".".to_string(),
        (false, false) => joined,
    }
}

pub fn worktree_locations(config: &HerdrConfig, env: &Environment) -> (Vec<String>, Vec<String>) {
    let mut dirs: Vec<String> = FIXED_WORKTREE_DIRS.iter().map(|d| d.to_string()).collect();
    let mut roots: Vec<String> = Vec::new();

    let raw = config.string("worktrees.directory").unwrap_or("");
    let setting = env.expanduser(raw.trim());
    if setting.starts_with('/') {
        roots.push(normpath(&setting));
    } else if !setting.is_empty() {
        let norm = normpath(&setting);
        if !dirs.contains(&norm) {
            dirs.push(norm);
        }
    }
    (dirs, roots)
}
