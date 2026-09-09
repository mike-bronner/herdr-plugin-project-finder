use std::collections::{BTreeSet, HashSet};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::api::Workspace;
use crate::config::normpath;

pub const DEPTH_BANDS: [usize; 3] = [1, 2, 3];
pub const CONTAINER_BANDS: [usize; 2] = [1, 2];

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Counts {
    pub bands: usize,
    pub containers: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Repo,
    Worktree,
}

impl Kind {
    pub fn word(&self) -> &'static str {
        match self {
            Kind::Repo => "repo",
            Kind::Worktree => "worktree",
        }
    }
}

pub fn row_kind(path: &Path) -> Kind {
    if path.join(".git").is_file() {
        Kind::Worktree
    } else {
        Kind::Repo
    }
}

fn checkouts_at(base: &Path, depth: usize) -> Vec<PathBuf> {
    let mut level = vec![base.to_path_buf()];
    for _ in 0..depth {
        let mut next = Vec::new();
        for dir in &level {
            let Ok(entries) = std::fs::read_dir(dir) else {
                continue;
            };
            for entry in entries.flatten() {
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if name.starts_with('.') {
                    continue;
                }
                let path = entry.path();
                if path.is_dir() {
                    next.push(path);
                }
            }
        }
        level = next;
    }
    let mut found: Vec<PathBuf> = level
        .into_iter()
        .filter(|p| p.join(".git").exists())
        .collect();
    found.sort();
    found
}

pub fn repos(root: &Path, worktree_dirs: &[String], worktree_roots: &[String]) -> (Vec<PathBuf>, Counts) {
    let mut out: Vec<PathBuf> = Vec::new();

    for depth in DEPTH_BANDS {
        for path in checkouts_at(root, depth) {
            let nested = out.iter().any(|owner| path.starts_with(owner));
            if nested || out.contains(&path) {
                continue;
            }
            out.push(path);
        }
    }
    let banded = out.len();

    let mut bases: Vec<PathBuf> = Vec::new();
    let mut seen: HashSet<PathBuf> = HashSet::new();
    for repo in out.clone() {
        for dir in worktree_dirs {
            let joined = normpath(&repo.join(dir).to_string_lossy());
            let base = PathBuf::from(joined);
            if seen.insert(base.clone()) {
                bases.push(base);
            }
        }
    }
    bases.extend(worktree_roots.iter().map(PathBuf::from));

    for base in &bases {
        for depth in CONTAINER_BANDS {
            for path in checkouts_at(base, depth) {
                if !out.contains(&path) && row_kind(&path) == Kind::Worktree {
                    out.push(path);
                }
            }
        }
    }

    let counts = Counts {
        bands: banded,
        containers: out.len() - banded,
    };
    (out, counts)
}

pub fn parent_repo(path: &Path) -> Option<PathBuf> {
    let pointer = std::fs::read_to_string(path.join(".git")).ok()?;
    let first = pointer.split('\n').next().unwrap_or("");
    let (key, target) = match first.split_once(':') {
        Some((key, target)) => (key, target),
        None => (first, ""),
    };
    if key.trim() != "gitdir" || target.trim().is_empty() {
        return None;
    }
    let admin = PathBuf::from(normpath(
        &path.join(target.trim()).to_string_lossy(),
    ));
    let container = admin.parent()?;
    let dotgit = container.parent()?;
    if container.file_name()?.to_string_lossy() != "worktrees"
        || dotgit.file_name()?.to_string_lossy() != ".git"
    {
        return None;
    }
    dotgit.parent().map(Path::to_path_buf)
}

const PRUNED_NAMES: [&str; 3] = ["node_modules", "vendor", ".git"];

pub fn touched_at(path: &Path, worktree_dirs: &[String]) -> u64 {
    let mut newest = mtime(&path.join(".git").join("index")).unwrap_or(0);
    let trees: BTreeSet<PathBuf> = worktree_dirs
        .iter()
        .map(|d| PathBuf::from(normpath(&path.join(d).to_string_lossy())))
        .collect();

    let mut level = vec![path.to_path_buf()];
    for depth in 0..3usize {
        let mut next = Vec::new();
        for dir in &level {
            let Ok(entries) = std::fs::read_dir(dir) else {
                continue;
            };
            for entry in entries.flatten() {
                let child = entry.path();
                let is_dir = child.is_dir();
                if !is_dir {
                    if let Some(m) = mtime(&child) {
                        newest = newest.max(m);
                    }
                    continue;
                }
                if depth >= 2 {
                    continue;
                }
                let name = entry.file_name();
                let name = name.to_string_lossy().to_string();
                if PRUNED_NAMES.contains(&name.as_str()) || trees.contains(&child) {
                    continue;
                }
                next.push(child);
            }
        }
        level = next;
    }
    newest
}

fn mtime(path: &Path) -> Option<u64> {
    let modified = std::fs::metadata(path).ok()?.modified().ok()?;
    modified.duration_since(UNIX_EPOCH).ok().map(|d| d.as_secs())
}

pub fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

pub fn human_age(touched: u64, now: u64) -> String {
    if touched == 0 {
        return "-".to_string();
    }
    let elapsed = now.saturating_sub(touched);
    for (cutoff, divisor, unit) in [(3600, 60, "m"), (86400, 3600, "h"), (604800, 86400, "d")] {
        if elapsed < cutoff {
            return format!("{}{} ago", elapsed / divisor, unit);
        }
    }
    format!("{}w ago", elapsed / 604800)
}

pub fn elide(text: &str, width: usize) -> String {
    let chars: Vec<char> = text.chars().collect();
    if chars.len() <= width {
        return text.to_string();
    }
    let mut cut: String = chars[..width.saturating_sub(1)].iter().collect();
    cut.push('…');
    cut
}

pub fn label_for(path: &Path, duplicated: &HashSet<String>) -> String {
    let base = basename(path);
    if duplicated.contains(&base) {
        let parent = path.parent().map(basename).unwrap_or_default();
        format!("{}/{}", parent, base)
    } else {
        base
    }
}

fn basename(path: &Path) -> String {
    path.file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default()
}

pub fn duplicated_basenames(paths: &[PathBuf]) -> HashSet<String> {
    let mut seen: HashSet<String> = HashSet::new();
    let mut dupes: HashSet<String> = HashSet::new();
    for path in paths {
        let base = basename(path);
        if !seen.insert(base.clone()) {
            dupes.insert(base);
        }
    }
    dupes
}

pub fn labelled(paths: &[PathBuf]) -> Vec<(String, PathBuf)> {
    let dupes = duplicated_basenames(paths);
    paths
        .iter()
        .map(|p| (label_for(p, &dupes), p.clone()))
        .collect()
}

#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub label: String,
    pub path: PathBuf,
    pub touched: u64,
}

pub fn order_rows(
    labelled: &[(String, PathBuf)],
    open_ws: &[Workspace],
    touch: &mut dyn FnMut(&Path) -> u64,
) -> (Vec<Row>, Vec<Row>) {
    let mut head: Vec<Row> = Vec::new();
    for workspace in open_ws {
        if let Some((label, path)) = labelled.iter().find(|(l, _)| *l == workspace.label) {
            head.push(Row {
                label: label.clone(),
                path: path.clone(),
                touched: touch(path),
            });
        }
    }
    let listed: HashSet<String> = head.iter().map(|r| r.label.clone()).collect();
    let mut rest: Vec<Row> = labelled
        .iter()
        .filter(|(label, _)| !listed.contains(label))
        .map(|(label, path)| Row {
            label: label.clone(),
            path: path.clone(),
            touched: touch(path),
        })
        .collect();
    rest.sort_by_key(|row| std::cmp::Reverse(row.touched));
    (head, rest)
}

pub fn debug_line(elapsed_ms: f64, rows: usize, counts: Counts) -> String {
    format!(
        "picker: discovery {:.1}ms, {} rows ({} from depth bands, {} from worktree containers)\n",
        elapsed_ms, rows, counts.bands, counts.containers
    )
}
