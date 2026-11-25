export const FIXED_WORKTREE_DIRS = [".worktrees", ".claude/worktrees", "worktrees"];
export const APP_WORKTREE_DEFAULT = "~/.herdr/worktrees";
export const PRUNED_NAMES = ["node_modules", "vendor", ".git"];

export function normpath(path) {
  if (!path) return ".";
  const absolute = path.startsWith("/");
  const parts = [];
  for (const part of path.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") {
      if (parts.length && parts.at(-1) !== "..") parts.pop();
      else if (!absolute) parts.push("..");
      continue;
    }
    parts.push(part);
  }
  const joined = parts.join("/");
  if (absolute) return `/${joined}`;
  return joined || ".";
}

export function expandHome(path, home) {
  if (path === "~") return home;
  if (path.startsWith("~/")) return home.replace(/\/+$/, "") + path.slice(1);
  return path;
}

export function worktreeLocations(directory, home) {
  const dirs = [...FIXED_WORKTREE_DIRS];
  const roots = [];
  const raw = directory === undefined ? APP_WORKTREE_DEFAULT : directory;
  if (typeof raw !== "string") return { dirs, roots };
  const setting = expandHome(raw.trim(), home);
  if (setting.startsWith("/")) roots.push(normpath(setting));
  else if (setting) {
    const norm = normpath(setting);
    if (!dirs.includes(norm)) dirs.push(norm);
  }
  return { dirs, roots };
}

const names = (path) => path.split("/").filter((part) => part && part !== "." && part !== "..");

function compareNames(a, b) {
  const [x, y] = [[...a], [...b]];
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    if (x[i] !== y[i]) return x[i].codePointAt(0) - y[i].codePointAt(0);
  }
  return x.length - y.length;
}

export function comparePaths(a, b) {
  const [x, y] = [names(a), names(b)];
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    if (x[i] !== y[i]) return compareNames(x[i], y[i]);
  }
  return x.length - y.length;
}

export const inside = (path, dir) => path === dir || path.startsWith(dir === "/" ? "/" : `${dir}/`);

function below(base, path) {
  if (!inside(path, base) || path === base) return null;
  return names(path.slice(base === "/" ? 1 : base.length + 1));
}

const dirname = (path) => path.slice(0, path.lastIndexOf("/")) || "/";

export function bandRepos(root, gitPaths) {
  const checkouts = gitPaths.map(dirname);
  const out = [];
  for (const depth of [1, 2, 3]) {
    const level = checkouts.filter((path) => below(root, path)?.length === depth).sort(comparePaths);
    for (const path of level) {
      if (out.some((owner) => inside(path, owner))) continue;
      out.push(path);
    }
  }
  return out;
}

export function containerBases(repos, dirs, roots) {
  const bases = [];
  for (const repo of repos) {
    for (const dir of dirs) {
      const base = normpath(`${repo}/${dir}`);
      if (!bases.includes(base)) bases.push(base);
    }
  }
  return [...bases, ...roots];
}

export function containerWorktrees(bases, worktrees, known) {
  const out = [];
  for (const base of bases) {
    for (const depth of [1, 2]) {
      const level = worktrees
        .filter((path) => {
          const rel = below(base, path);
          return rel?.length === depth && !rel.some((part) => part.startsWith("."));
        })
        .sort(comparePaths);
      for (const path of level) {
        if (!known.includes(path) && !out.includes(path)) out.push(path);
      }
    }
  }
  return out;
}

export function parentRepo(path, pointer) {
  if (typeof pointer !== "string") return null;
  const first = pointer.split("\n")[0];
  const colon = first.indexOf(":");
  const key = colon < 0 ? first : first.slice(0, colon);
  const target = colon < 0 ? "" : first.slice(colon + 1).trim();
  if (key.trim() !== "gitdir" || !target) return null;
  const admin = normpath(target.startsWith("/") ? target : `${path}/${target}`);
  const container = dirname(admin);
  const dotgit = dirname(container);
  if (container.split("/").at(-1) !== "worktrees" || dotgit.split("/").at(-1) !== ".git") return null;
  return dirname(dotgit);
}

export function shellQuote(text) {
  return `'${text.replace(/'/g, `'\\''`)}'`;
}

const globEscape = (text) => text.replace(/[\\*?[\]]/g, (c) => `\\${c}`);

export function factsScript(paths, dirs) {
  const head = [
    "if stat -L -c %Y / >/dev/null 2>&1; then fmt='-c %Y'; else fmt='-f %m'; fi",
    "row() {",
    '  p=$1; i=$2; shift 2',
    '  if [ -f "$p/.git" ]; then line=; IFS= read -r line < "$p/.git" || :; printf \'@%s\\ttree\\t%s\\n\' "$i" "$line"; else printf \'@%s\\trepo\\t\\n\' "$i"; fi',
    '  if [ -e "$p/.git/index" ]; then stat -L $fmt "$p/.git/index" 2>/dev/null; fi',
    `  find -L "$p" -mindepth 1 -maxdepth 3 -type d \\( ${PRUNED_NAMES.map((n) => `-name ${shellQuote(n)}`).join(" -o ")} "$@" \\) -prune -o -type f -exec stat -L $fmt {} + 2>/dev/null`,
    "}",
    "{",
  ];
  const rows = paths.map((path, i) => {
    const prunes = dirs
      .map((dir) => normpath(`${path}/${dir}`))
      .filter((tree) => tree !== path && inside(tree, path))
      .flatMap((tree) => ["-o", "-path", shellQuote(globEscape(tree))]);
    return `row ${shellQuote(path)} ${i} ${prunes.join(" ")}`.trimEnd();
  });
  const tail = [
    "} | awk -F '\\t' '",
    '/^@/ { if (n) print max "\\t" key; key = substr($0, 2); max = 0; n = 1; next }',
    "$1 + 0 > max { max = $1 + 0 }",
    'END { if (n) print max "\\t" key }',
    "'",
  ];
  return [...head, ...rows, ...tail].join("\n");
}

export function parseFacts(stdout) {
  const facts = new Map();
  for (const line of stdout.split("\n")) {
    const [touched, index, kind, ...rest] = line.split("\t");
    if (kind !== "repo" && kind !== "tree") continue;
    facts.set(Number(index), { touched: Number(touched) || 0, kind: kind === "tree" ? "worktree" : "repo", pointer: kind === "tree" ? rest.join("\t") : null });
  }
  return facts;
}

const lines = (result) => (result?.stdout ?? "").split("\n").filter(Boolean);

export async function discover({ root, dirs, roots }, run) {
  const top = normpath(root);
  const found = await run("find", ["-L", top, "-mindepth", "1", "-maxdepth", "4", "(", "-name", ".git", "-prune", "-print", ")", "-o", "(", "-name", ".*", "-prune", ")"]);
  const banded = bandRepos(top, lines(found));
  const bases = containerBases(banded, dirs, roots);
  const hits = bases.length
    ? lines(await run("find", ["-L", ...bases, "-mindepth", "1", "-maxdepth", "3", "(", "-name", ".git", "-prune", "-type", "f", "-print", ")", "-o", "(", "-name", ".*", "-prune", ")"]))
    : [];
  const paths = [...banded, ...containerWorktrees(bases, hits.map(dirname), banded)];
  if (!paths.length) return { rows: [], bands: 0, containers: 0 };
  const facts = parseFacts((await run("sh", ["-c", factsScript(paths, dirs)])).stdout ?? "");
  const rows = paths.map((path, i) => {
    const fact = facts.get(i);
    if (!fact) throw new Error(`Could not read ${path}.`);
    return { path, kind: fact.kind, repo: fact.kind === "worktree" ? parentRepo(path, fact.pointer) : null, touched: fact.touched };
  });
  return { rows, bands: banded.length, containers: paths.length - banded.length };
}

export function tails(path) {
  const parts = names(path);
  const ladder = parts.map((_, i) => parts.slice(parts.length - 1 - i).join("/"));
  if (ladder.at(-1) !== path) ladder.push(path);
  return ladder;
}

export function labelled(paths) {
  const ladders = paths.map(tails);
  return ladders.map((ladder, at) => {
    const label = ladder.find((tail, depth) => ladders.every((theirs, other) => other === at || theirs[depth] !== tail)) ?? ladder.at(-1) ?? "";
    return { label, path: paths[at] };
  });
}

export function humanAge(touched, now) {
  if (!touched) return "-";
  const elapsed = Math.max(0, now - touched);
  for (const [cutoff, divisor, unit] of [[3600, 60, "m"], [86400, 3600, "h"], [604800, 86400, "d"]]) {
    if (elapsed < cutoff) return `${Math.floor(elapsed / divisor)}${unit} ago`;
  }
  return `${Math.floor(elapsed / 604800)}w ago`;
}

export function elide(text, width) {
  const chars = [...text];
  if (chars.length <= width) return text;
  return `${chars.slice(0, Math.max(0, width - 1)).join("")}…`;
}

export const basename = (path) => names(path).at(-1) ?? "";
