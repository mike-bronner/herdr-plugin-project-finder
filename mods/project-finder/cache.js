import { buildEntries, filtered, projectKey } from "./picker.js";

export const CACHE_KEY = "projects";
export const CACHE_VERSION = 1;
const KINDS = ["repo", "worktree"];

const isText = (value) => typeof value === "string" && value !== "";
const isFolder = (value) => typeof value === "string" && value.startsWith("/");

const isRow = (row) =>
  row !== null && typeof row === "object" && isFolder(row.path) && KINDS.includes(row.kind) && Number.isFinite(row.touched) && (row.repo === null || isFolder(row.repo));

const isRecord = (record) =>
  record !== null &&
  typeof record === "object" &&
  (record.machine === null || isText(record.machine)) &&
  isFolder(record.home) &&
  isFolder(record.root) &&
  Array.isArray(record.rows) &&
  record.rows.every(isRow);

const fact = ({ path, repo, kind, touched }) => ({ path, repo, kind, touched });

export function readCache(text, machines) {
  if (typeof text !== "string") return null;
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (parsed?.version !== CACHE_VERSION || !Array.isArray(parsed.machines) || !parsed.machines.every(isRecord)) return null;
  const names = parsed.machines.map((record) => record.machine);
  if (new Set(names).size !== names.length) return null;
  const listed = new Set(machines.map((m) => m.name).filter(Boolean));
  const kept = parsed.machines
    .filter((record) => record.machine === null || listed.has(record.machine))
    .map(({ machine, home, root, rows }) => ({ machine, home, root, rows: rows.map((row) => ({ ...fact(row), machine })) }));
  return kept.length ? kept : null;
}

export const cacheText = (records) =>
  JSON.stringify({ version: CACHE_VERSION, machines: records.map(({ machine, home, root, rows }) => ({ machine, home, root, rows: rows.map(fact) })) });

export function mergeResult(records, result) {
  if (result.failed !== undefined) return records;
  const fresh = { machine: result.machine, home: result.home, root: result.root, rows: result.rows };
  const at = records.findIndex((record) => record.machine === result.machine);
  return at < 0 ? [...records, fresh] : records.map((record, i) => (i === at ? fresh : record));
}

export function cachedView(records, machines) {
  const order = [null, ...machines.map((m) => m.name)];
  const sorted = [...records].sort((a, b) => order.indexOf(a.machine) - order.indexOf(b.machine));
  return {
    rows: sorted.flatMap((record) => record.rows),
    homes: Object.fromEntries(sorted.filter((record) => record.machine).map((record) => [record.machine, record.home])),
  };
}

export function refreshed({ entries, shown, at }, rows, open, now, query) {
  const fresh = buildEntries(rows, open, now);
  const chosen = new Map((entries ?? []).filter((entry) => entry.selected !== entry.open).map((entry) => [projectKey(entry), entry.selected]));
  for (const entry of fresh) if (chosen.has(projectKey(entry))) entry.selected = chosen.get(projectKey(entry));
  const nextShown = filtered(fresh, query);
  const current = entries && shown[at] ? projectKey(entries[shown[at].index]) : null;
  const same = nextShown.findIndex((row) => projectKey(fresh[row.index]) === current);
  return { entries: fresh, shown: nextShown, at: same >= 0 ? same : Math.max(0, Math.min(at, nextShown.length - 1)) };
}
