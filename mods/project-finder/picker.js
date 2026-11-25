import { basename, elide, expandHome, humanAge, inside, labelled, worktreeLocations } from "./discover.js";

export const HOME_LABEL = "~";
export const LABEL_WIDTH = 34;
export const HELD_NOTE = "held by checked worktrees";
export const KIND_WORD = { repo: "repo", worktree: "worktree" };
export const KIND_CELL = { repo: "repo", worktree: "tree" };
const URGENCY = ["blocked", "done", "working", "idle"];
const SETTINGS = ["root", "agent"];

export function readSettings(modJson, appSettings, home) {
  const problems = [];
  let given = {};
  try {
    given = JSON.parse(modJson ?? "{}")?.settings;
    if (given === undefined) given = {};
  } catch (error) {
    problems.push(`mod.json could not be read: ${error.message}`);
  }
  if (given === null || typeof given !== "object" || Array.isArray(given)) {
    problems.push("settings must be an object");
    given = {};
  }
  for (const key of Object.keys(given)) if (!SETTINGS.includes(key)) problems.push(`${key} is not a setting`);

  let root = home;
  const rawRoot = given.root ?? "~";
  if (typeof rawRoot !== "string") problems.push("root must be a path");
  else {
    const expanded = expandHome(rawRoot.trim(), home);
    if (!expanded) root = home;
    else if (!expanded.startsWith("/")) problems.push("root must be an absolute path or start with ~");
    else root = expanded;
  }

  let agent = null;
  const rawAgent = given.agent ?? null;
  if (rawAgent !== null) {
    if (Array.isArray(rawAgent) && rawAgent.length && rawAgent.every((part) => typeof part === "string" && part !== "")) agent = rawAgent;
    else problems.push('agent must be null or a list of words, such as ["claude"]');
  }

  let directory;
  if (appSettings !== null && appSettings !== undefined) {
    try {
      const parsed = JSON.parse(appSettings);
      directory = parsed?.worktrees?.directory;
      if (directory === null) directory = undefined;
    } catch {
      directory = false;
    }
  } else if (appSettings === undefined) directory = false;

  return { root, agent, ...worktreeLocations(directory, home), problems };
}

export function placeOf(space, panes) {
  const tabs = new Set((space.tabs ?? []).map((tab) => tab.id));
  return panes.find((pane) => tabs.has(pane.tab) && pane.cwd)?.cwd || space.folder || null;
}

export const isHomeSpace = (space) => space.name === HOME_LABEL;

export function homeSpaceIds(spaces, panes, home) {
  const ids = new Set();
  for (const space of spaces) {
    const tabs = new Set((space.tabs ?? []).map((tab) => tab.id));
    if (isHomeSpace(space) || panes.some((pane) => tabs.has(pane.tab) && pane.cwd === home)) ids.add(space.id);
  }
  return ids;
}

export function openSpaces(spaces, panes, paths) {
  const out = [];
  for (const space of spaces) {
    if (space.machine || isHomeSpace(space)) continue;
    const at = placeOf(space, panes);
    if (!at) continue;
    const path = paths.filter((p) => inside(at, p)).sort((a, b) => b.length - a.length)[0];
    if (path) out.push({ id: space.id, path, status: space.status ?? null, active: Boolean(space.active) });
  }
  return out;
}

export function orderRows(rows, open, touched) {
  const head = [];
  for (const space of open) {
    const row = rows.find((r) => r.path === space.path);
    if (row && !head.includes(row)) head.push(row);
  }
  const rest = rows.filter((row) => !head.includes(row)).map((row) => ({ row, at: touched(row.path) }));
  rest.sort((a, b) => b.at - a.at);
  return { head, rest: rest.map((r) => r.row) };
}

const urgent = (statuses) => URGENCY.find((s) => statuses.includes(s)) ?? null;

export function buildEntries(found, open, now) {
  const byPath = new Map(found.map((f) => [f.path, f]));
  const rows = labelled(found.map((f) => f.path));
  const { head, rest } = orderRows(rows, open, (path) => byPath.get(path).touched);
  return [...head, ...rest].map(({ label, path }) => {
    const fact = byPath.get(path);
    const mine = open.filter((o) => o.path === path);
    return {
      label,
      path,
      repo: fact.repo,
      kind: fact.kind,
      age: humanAge(fact.touched, now),
      open: mine.length > 0,
      status: urgent(mine.map((o) => o.status)),
      selected: mine.length > 0,
    };
  });
}

export const haystack = (entry) => `${entry.label} ${KIND_WORD[entry.kind]} ${entry.age} ${entry.status ?? ""}`;

export function held(entries, at) {
  const row = entries[at];
  return Boolean(row?.open) && entries.some((e) => e.selected && e.repo === row.path);
}

export const heldCount = (entries) => entries.filter((_, at) => held(entries, at)).length;

function check(entries, at) {
  entries[at].selected = true;
  const owner = entries.findIndex((e) => e.open && e.path === entries[at].repo);
  if (owner >= 0) entries[owner].selected = true;
}

function uncheck(entries, at) {
  if (!held(entries, at)) entries[at].selected = false;
}

export function toggle(entries, at) {
  if (!entries[at]) return;
  if (entries[at].selected) uncheck(entries, at);
  else check(entries, at);
}

export function setAll(entries, indices, selected) {
  if (selected) return indices.forEach((at) => check(entries, at));
  for (const kind of ["worktree", "repo"]) {
    for (const at of indices) if (entries[at].kind === kind) uncheck(entries, at);
  }
}

const BOUNDARY = " /-_.";

function matchAtom(text, atom) {
  const sensitive = atom !== atom.toLowerCase();
  const hay = sensitive ? text : text.toLowerCase();
  const needle = [...(sensitive ? atom : atom.toLowerCase())];
  const chars = [...hay];
  let best = null;
  for (let start = 0; start < chars.length; start++) {
    if (chars[start] !== needle[0]) continue;
    const positions = [start];
    for (let i = start + 1, n = 1; n < needle.length && i < chars.length; i++) {
      if (chars[i] === needle[n]) {
        positions.push(i);
        n++;
      }
    }
    if (positions.length < needle.length) break;
    let score = 0;
    positions.forEach((at, n) => {
      score += 16;
      if (at === 0 || BOUNDARY.includes(chars[at - 1])) score += 8;
      if (n > 0) score += at === positions[n - 1] + 1 ? 8 : -Math.min(at - positions[n - 1] - 1, 8);
    });
    if (!best || score > best.score) best = { score, positions };
  }
  return best;
}

export function fuzzy(text, query) {
  const atoms = query.trim().split(/\s+/).filter(Boolean);
  let score = 0;
  const marks = new Set();
  for (const atom of atoms) {
    const found = matchAtom(text, atom);
    if (!found) return null;
    score += found.score;
    found.positions.forEach((at) => marks.add(at));
  }
  return { score, marks: [...marks].sort((a, b) => a - b) };
}

export function filtered(entries, query) {
  if (!query.trim()) return entries.map((_, index) => ({ index, marks: [] }));
  const scored = [];
  entries.forEach((entry, index) => {
    const found = fuzzy(haystack(entry), query);
    if (found) scored.push({ index, score: found.score, marks: found.marks.filter((at) => at < [...entry.label].length) });
  });
  scored.sort((a, b) => b.score - a.score || a.index - b.index);
  return scored.map(({ index, marks }) => ({ index, marks }));
}

export function rowCells(entry) {
  const label = elide(entry.label, LABEL_WIDTH);
  if (!entry.repo) return { repo: "", name: label };
  const name = basename(entry.repo);
  const room = LABEL_WIDTH - [...label].length;
  const whole = `${name}/`;
  if ([...whole].length <= room) return { repo: whole, name: label };
  if (room <= 0) return { repo: "", name: label };
  if (room === 1) return { repo: "…", name: label };
  return { repo: `${elide(name, room - 1)}/`, name: label };
}

export const ROW_HEIGHT = 28;
export const LIST_ROWS = 16;
export const EMPTY_LIST_HEIGHT = 44;
export const listHeight = (count) => (count ? Math.min(count, LIST_ROWS) * ROW_HEIGHT : EMPTY_LIST_HEIGHT);

export function nameMarks(entry, marks) {
  const length = [...entry.label].length;
  if (length <= LABEL_WIDTH) return marks;
  return [...new Set(marks.map((at) => Math.min(at, LABEL_WIDTH - 1)))];
}

export function plan(entries, open, homeIds) {
  const selected = new Set(entries.filter((e) => e.selected).map((e) => e.path));
  const kinds = new Map(entries.map((e) => [e.path, e.kind]));
  const closing = open.filter((o) => kinds.has(o.path) && !selected.has(o.path) && !homeIds.has(o.id));
  const close = [...closing.filter((o) => kinds.get(o.path) === "worktree"), ...closing.filter((o) => kinds.get(o.path) !== "worktree")].map((o) => o.id);
  const create = entries.filter((e) => e.selected && !open.some((o) => o.path === e.path)).map((e) => e.path);
  return { close, create };
}

export function pickFocus(created, closed, focused, surviving, home) {
  if (created.length) return created.at(-1);
  if (focused && closed.includes(focused)) return surviving[0] ?? home ?? null;
  return null;
}

export function applySelection({ entries, spaces, panes, home, agent }, run) {
  const open = openSpaces(spaces, panes, entries.map((e) => e.path));
  const steps = plan(entries, open, homeSpaceIds(spaces, panes, home));
  const closed = [];
  const refused = [];
  for (const id of steps.close) {
    try {
      run("space.close", { id, confirmed: true });
      closed.push(id);
    } catch (error) {
      refused.push(`${open.find((o) => o.id === id)?.path ?? id} did not close: ${error?.message ?? error}`);
    }
  }
  for (const [i, folder] of steps.create.entries()) {
    try {
      run("space.new", { folder, machine: "", ...(agent ? { argv: agent } : {}), focus: i === steps.create.length - 1 });
    } catch (error) {
      refused.push(`${folder} did not open: ${error?.message ?? error}`);
      return refused;
    }
  }
  const selected = new Set(entries.filter((e) => e.selected).map((e) => e.path));
  const surviving = open.filter((o) => selected.has(o.path) && !closed.includes(o.id)).map((o) => o.id);
  const target = pickFocus(steps.create, closed, spaces.find((s) => s.active)?.id, surviving, spaces.find(isHomeSpace)?.id);
  if (target && !steps.create.length) {
    try {
      run("space.select", { id: target });
    } catch {}
  }
  return refused;
}
