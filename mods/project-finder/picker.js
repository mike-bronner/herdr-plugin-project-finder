import { basename, discover, elide, expandHome, humanAge, inside, labelled, worktreeLocations } from "./discover.js";

export const HOME_LABEL = "~";
export const LABEL_WIDTH = 34;
export const HELD_NOTE = "held by checked worktrees";
export const KIND_WORD = { repo: "repo", worktree: "worktree" };
export const KIND_CELL = { repo: "repo", worktree: "tree" };
const URGENCY = ["blocked", "done", "working", "idle"];
const SETTINGS = ["root", "machineRoots", "agent"];
export const MACHINE_ROOTS_PROBLEM = 'machineRoots must map machine names to folders, such as {"devbox": "~/Developer"}';
export const MACHINE_WIDTH = 12;
export const HOME_SCRIPT = 'printf %s "$HOME"';
const SEARCH_TIMEOUT = 120_000;

const machineOf = (item) => item.machine ?? null;
export const sameProject = (a, b) => a.path === b.path && machineOf(a) === machineOf(b);
export const projectKey = (item) => (machineOf(item) ? `${item.machine}:${item.path}` : item.path);

function readRoot(raw, name, home, problems) {
  if (typeof raw !== "string") {
    problems.push(`${name} must be a path`);
    return null;
  }
  const expanded = expandHome(raw.trim(), home);
  if (!expanded) return home;
  if (expanded.startsWith("/")) return expanded;
  problems.push(`${name} must be an absolute path or start with ~`);
  return null;
}

export function readSettings(modJson, appSettings, home, machine = null) {
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

  let root = readRoot(given.root ?? "~", "root", home, problems) ?? home;
  const machineRoots = given.machineRoots ?? {};
  if (typeof machineRoots !== "object" || Array.isArray(machineRoots)) problems.push(MACHINE_ROOTS_PROBLEM);
  else {
    for (const [name, raw] of Object.entries(machineRoots)) {
      const own = readRoot(raw, `machineRoots.${name}`, home, problems);
      if (own && machine && name === machine) root = own;
    }
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

export function homeSpaceIds(spaces, panes, home, homes = {}) {
  const ids = new Set();
  for (const space of spaces) {
    const tabs = new Set((space.tabs ?? []).map((tab) => tab.id));
    const own = machineOf(space) ? homes[space.machine] : home;
    if (isHomeSpace(space) || (own && panes.some((pane) => tabs.has(pane.tab) && pane.cwd === own))) ids.add(space.id);
  }
  return ids;
}

export function openSpaces(spaces, panes, projects) {
  const places = projects.map((p) => (typeof p === "string" ? { path: p, machine: null } : p));
  const out = [];
  for (const space of spaces) {
    if (isHomeSpace(space)) continue;
    const at = placeOf(space, panes);
    if (!at) continue;
    const machine = machineOf(space);
    const place = places.filter((p) => machineOf(p) === machine && inside(at, p.path)).sort((a, b) => b.path.length - a.path.length)[0];
    if (place) out.push({ id: space.id, path: place.path, machine, status: space.status ?? null, active: Boolean(space.active) });
  }
  return out;
}

export function orderRows(rows, open, touched) {
  const head = [];
  for (const space of open) {
    const row = rows.find((r) => sameProject(r, space));
    if (row && !head.includes(row)) head.push(row);
  }
  const rest = rows.filter((row) => !head.includes(row)).map((row) => ({ row, at: touched(row.path, machineOf(row)) }));
  rest.sort((a, b) => b.at - a.at);
  return { head, rest: rest.map((r) => r.row) };
}

const urgent = (statuses) => URGENCY.find((s) => statuses.includes(s)) ?? null;

export function labelledPerMachine(found) {
  const labels = new Map();
  for (const machine of new Set(found.map(machineOf))) {
    const mine = found.filter((f) => machineOf(f) === machine);
    labelled(mine.map((f) => f.path)).forEach(({ label }, at) => labels.set(projectKey(mine[at]), label));
  }
  return found.map((f) => ({ label: labels.get(projectKey(f)), path: f.path, machine: machineOf(f) }));
}

export function buildEntries(found, open, now) {
  const byKey = new Map(found.map((f) => [projectKey(f), f]));
  const rows = labelledPerMachine(found);
  const { head, rest } = orderRows(rows, open, (path, machine) => byKey.get(projectKey({ path, machine })).touched);
  return [...head, ...rest].map(({ label, path, machine }) => {
    const fact = byKey.get(projectKey({ path, machine }));
    const mine = open.filter((o) => sameProject(o, { path, machine }));
    return {
      label,
      path,
      machine,
      repo: fact.repo,
      kind: fact.kind,
      age: humanAge(fact.touched, now),
      open: mine.length > 0,
      status: urgent(mine.map((o) => o.status)),
      selected: mine.length > 0,
    };
  });
}

export const haystack = (entry) => `${entry.label} ${KIND_WORD[entry.kind]} ${entry.age} ${entry.status ?? ""}${entry.machine ? ` ${entry.machine}` : ""}`;

export const machineCell = (entry) => elide(entry.machine ?? "", MACHINE_WIDTH);

export function held(entries, at) {
  const row = entries[at];
  return Boolean(row?.open) && entries.some((e) => e.selected && e.repo === row.path && machineOf(e) === machineOf(row));
}

export const heldCount = (entries) => entries.filter((_, at) => held(entries, at)).length;

function check(entries, at) {
  entries[at].selected = true;
  const owner = entries.findIndex((e) => e.open && e.path === entries[at].repo && machineOf(e) === machineOf(entries[at]));
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
  const selected = new Set(entries.filter((e) => e.selected).map(projectKey));
  const kinds = new Map(entries.map((e) => [projectKey(e), e.kind]));
  const closing = open.filter((o) => kinds.has(projectKey(o)) && !selected.has(projectKey(o)) && !homeIds.has(o.id));
  const isTree = (o) => kinds.get(projectKey(o)) === "worktree";
  const close = [...closing.filter(isTree), ...closing.filter((o) => !isTree(o))].map((o) => o.id);
  const create = entries.filter((e) => e.selected && !open.some((o) => sameProject(o, e))).map(projectKey);
  return { close, create };
}

export function pickFocus(created, closed, focused, surviving, home) {
  if (created.length) return created.at(-1);
  if (focused && closed.includes(focused)) return surviving[0] ?? home ?? null;
  return null;
}

export function applySelection({ entries, spaces, panes, home, homes = {}, agent }, run) {
  const open = openSpaces(spaces, panes, entries);
  const steps = plan(entries, open, homeSpaceIds(spaces, panes, home, homes));
  const byKey = new Map(entries.map((e) => [projectKey(e), e]));
  const closed = [];
  const refused = [];
  for (const id of steps.close) {
    try {
      run("space.close", { id, confirmed: true });
      closed.push(id);
    } catch (error) {
      const space = open.find((o) => o.id === id);
      refused.push(`${space ? projectKey(space) : id} did not close: ${error?.message ?? error}`);
    }
  }
  for (const [i, key] of steps.create.entries()) {
    const { path, machine } = byKey.get(key);
    try {
      run("space.new", { folder: path, machine: machine ?? "", ...(agent ? { argv: agent } : {}), focus: i === steps.create.length - 1 });
    } catch (error) {
      refused.push(`${key} did not open: ${error?.message ?? error}`);
      return refused;
    }
  }
  const selected = new Set(entries.filter((e) => e.selected).map(projectKey));
  const surviving = open.filter((o) => selected.has(projectKey(o)) && !closed.includes(o.id)).map((o) => o.id);
  const target = pickFocus(steps.create, closed, spaces.find((s) => s.active)?.id, surviving, spaces.find(isHomeSpace)?.id);
  if (target && !steps.create.length) {
    try {
      run("space.select", { id: target });
    } catch {}
  }
  return refused;
}

async function homeOn(machine, exec) {
  const result = await exec("sh", ["-c", HOME_SCRIPT], { on: machine });
  const found = result?.code === 0 ? (result.stdout ?? "").trim() : "";
  if (!found.startsWith("/")) throw new Error("its home folder could not be read");
  return found;
}

async function searchMachine(machine, { modJson, appSettings, home }, exec) {
  const where = machine ? { on: machine } : {};
  const own = machine ? await homeOn(machine, exec) : home;
  const settings = readSettings(modJson, appSettings, own, machine);
  const isDir = await exec("test", ["-d", settings.root], where).then((r) => r.code === 0, () => false);
  const root = isDir ? settings.root : own;
  const { rows } = await discover({ root, dirs: settings.dirs, roots: settings.roots }, (command, args) => exec(command, args, { timeout: SEARCH_TIMEOUT, ...where }));
  return { machine, home: own, root, rows: rows.map((row) => ({ ...row, machine })) };
}

export const skipNote = (machine, why) => `Skipped ${machine ?? "this computer"}: ${why}`;

export function searchPlan({ machines, modJson, appSettings, home }) {
  const { agent, problems } = readSettings(modJson, appSettings, home);
  const notes = [];
  const targets = [null];
  for (const { name, layout } of machines) {
    if (!name) continue;
    if (layout === "ready") targets.push(name);
    else notes.push(skipNote(name, layout === "loading" ? "still connecting" : "unavailable"));
  }
  return { agent, problems, notes, targets };
}

export async function discoverAll({ machines, modJson, appSettings, home }, exec, onResult = () => {}) {
  const { agent, problems, notes, targets } = searchPlan({ machines, modJson, appSettings, home });
  const results = await Promise.all(
    targets.map((machine) =>
      searchMachine(machine, { modJson, appSettings, home }, exec)
        .catch((error) => ({ machine, failed: String(error?.message ?? error) }))
        .then((result) => {
          onResult(result);
          return result;
        }),
    ),
  );
  const rows = [];
  const homes = {};
  const searched = [];
  for (const result of results) {
    if (result.failed !== undefined) {
      notes.push(skipNote(result.machine, result.failed));
      continue;
    }
    if (result.machine) homes[result.machine] = result.home;
    searched.push(projectKey({ path: result.root, machine: result.machine }));
    rows.push(...result.rows);
  }
  return { rows, homes, searched, notes, agent, problems };
}
