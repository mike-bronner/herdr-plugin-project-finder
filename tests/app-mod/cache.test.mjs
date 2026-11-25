import assert from "node:assert/strict";
import test from "node:test";
import { CACHE_VERSION, cacheText, cachedView, mergeResult, readCache, refreshed } from "../../mods/project-finder/cache.js";
import { openSpaces, toggle } from "../../mods/project-finder/picker.js";

const HOME = "/Users/someone";
const BOX = "/home/builder";
const NOW = 1_800_000_000;
const listed = [{ name: "devbox", layout: "ready" }];

const repo = (path, touched, machine = null) => ({ path, repo: null, kind: "repo", touched, machine });
const tree = (path, parent, touched, machine = null) => ({ path, repo: parent, kind: "worktree", touched, machine });
const local = (rows) => ({ machine: null, home: HOME, root: `${HOME}/code`, rows });
const devbox = (rows) => ({ machine: "devbox", home: BOX, root: `${BOX}/Developer`, rows });
const keys = (entries) => entries.map((e) => (e.machine ? `${e.machine}:${e.path}` : e.path));
const space = (id, folder, more = {}) => ({ id, name: folder.split("/").at(-1), folder, machine: null, status: null, active: false, tabs: [{ id: `${id}-tab` }], ...more });
const pane = (id, cwd) => ({ pane: `${id}-p`, tab: `${id}-tab`, cwd });

const shop = repo(`${HOME}/code/shop`, NOW - 60);
const ledger = repo(`${HOME}/code/ledger`, NOW - 600);
const infra = repo(`${BOX}/Developer/infra`, NOW - 120, "devbox");

test("a written cache reads back as the same records, with each row stamped with its machine", () => {
  const records = [local([shop, ledger]), devbox([infra, tree(`${BOX}/Developer/wt/fix`, `${BOX}/Developer/infra`, NOW, "devbox")])];
  assert.deepEqual(readCache(cacheText(records), listed), records);
});

test("the written cache carries its version and stores only the facts a row is rebuilt from", () => {
  const written = JSON.parse(cacheText([local([{ ...shop, label: "shop", selected: true }])]));
  assert.equal(written.version, CACHE_VERSION);
  assert.deepEqual(written.machines[0].rows, [{ path: shop.path, repo: null, kind: "repo", touched: shop.touched }]);
});

test("an absent, unparsable or old-format cache reads as none, so the popup does a first load", () => {
  const good = JSON.parse(cacheText([local([shop])]));
  for (const text of [null, undefined, "", "{not json", "null", "[]", JSON.stringify({ ...good, version: 0 }), JSON.stringify({ version: CACHE_VERSION, machines: {} })]) {
    assert.equal(readCache(text, listed), null, String(text));
  }
});

test("a cache with any malformed machine or row reads as none rather than in part", () => {
  const bad = [
    { ...local([shop]), home: "relative" },
    { ...local([shop]), root: 7 },
    { ...local([shop]), machine: "" },
    { ...local([shop]), rows: "shop" },
    local([{ ...shop, path: "code/shop" }]),
    local([{ ...shop, kind: "folder" }]),
    local([{ ...shop, touched: "yesterday" }]),
    local([{ ...shop, repo: 3 }]),
    local([null]),
    null,
  ];
  for (const record of bad) {
    const text = JSON.stringify({ version: CACHE_VERSION, machines: [devbox([infra]), record] });
    assert.equal(readCache(text, listed), null, JSON.stringify(record));
  }
});

test("a cache that names one machine twice reads as none", () => {
  assert.equal(readCache(cacheText([local([shop]), local([ledger])]), listed), null);
});

test("cached rows for a machine no longer listed are left out, and a listed machine keeps them whatever its state", () => {
  const text = cacheText([local([shop]), devbox([infra]), { ...devbox([]), machine: "retired" }]);
  assert.deepEqual(readCache(text, [{ name: "devbox", layout: "unavailable" }]).map((r) => r.machine), [null, "devbox"]);
  assert.deepEqual(readCache(text, []).map((r) => r.machine), [null]);
});

test("a cache left with no machine once unlisted ones are dropped reads as none", () => {
  assert.equal(readCache(cacheText([devbox([infra])]), []), null);
});

test("a finished search replaces only its own machine's rows, adding, dropping and re-dating projects", () => {
  const before = [local([shop, ledger]), devbox([infra])];
  const billing = repo(`${HOME}/code/billing`, NOW);
  const after = mergeResult(before, { machine: null, home: HOME, root: `${HOME}/code`, rows: [{ ...shop, touched: NOW }, billing] });
  assert.deepEqual(after, [local([{ ...shop, touched: NOW }, billing]), devbox([infra])]);
  assert.deepEqual(before[0].rows, [shop, ledger]);
});

test("a search for a machine with no cached rows adds that machine", () => {
  assert.deepEqual(mergeResult([local([shop])], { ...devbox([infra]) }), [local([shop]), devbox([infra])]);
});

test("a failed search keeps that machine's cached rows", () => {
  const before = [local([shop]), devbox([infra])];
  assert.equal(mergeResult(before, { machine: "devbox", failed: "devbox cannot be reached" }), before);
});

test("the view lists this computer first, then machines in their listed order, with each remote's home", () => {
  const other = { machine: "alpha", home: "/home/alpha", root: "/home/alpha", rows: [repo("/home/alpha/api", NOW, "alpha")] };
  const view = cachedView([devbox([infra]), other, local([shop])], [{ name: "alpha" }, { name: "devbox" }]);
  assert.deepEqual(keys(view.rows), [shop.path, "alpha:/home/alpha/api", `devbox:${infra.path}`]);
  assert.deepEqual(view.homes, { alpha: "/home/alpha", devbox: BOX });
});

const first = (rows, open = [], query = "") => refreshed({ entries: undefined, shown: [], at: 0 }, rows, open, NOW, query);

test("a first build checks the open rows and starts the cursor at the top", () => {
  const open = openSpaces([space("s1", ledger.path)], [pane("s1", ledger.path)], [shop, ledger]);
  const state = first([shop, ledger], open);
  assert.deepEqual(keys(state.entries), [ledger.path, shop.path]);
  assert.deepEqual(state.entries.map((e) => e.selected), [true, false]);
  assert.equal(state.at, 0);
});

test("rows the user checked or unchecked keep that choice across a refresh, and new rows start from their open state", () => {
  const open = openSpaces([space("s1", ledger.path), space("s2", `${HOME}/code/billing`)], [], [shop, ledger, repo(`${HOME}/code/billing`, 0)]);
  const state = first([shop, ledger], open);
  toggle(state.entries, state.entries.findIndex((e) => e.path === shop.path));
  toggle(state.entries, state.entries.findIndex((e) => e.path === ledger.path));
  const billing = repo(`${HOME}/code/billing`, NOW);
  const next = refreshed(state, [shop, ledger, billing], open, NOW, "");
  const selected = Object.fromEntries(next.entries.map((e) => [e.path, e.selected]));
  assert.deepEqual(selected, { [shop.path]: true, [ledger.path]: false, [billing.path]: true });
});

test("a refresh takes open state and agent status from the spaces given now, and an untouched row follows its open state", () => {
  const state = first([shop, ledger]);
  const open = openSpaces([space("s1", shop.path, { status: "working" })], [], [shop, ledger]);
  const next = refreshed(state, [shop, ledger], open, NOW, "");
  const row = next.entries.find((e) => e.path === shop.path);
  assert.deepEqual([row.open, row.status, row.selected], [true, "working", true]);
});

test("the filter text is applied to the refreshed rows", () => {
  const next = refreshed(first([shop, ledger], [], "ledg"), [shop, ledger, repo(`${HOME}/code/ledger-api`, NOW)], [], NOW, "ledg");
  assert.deepEqual(next.shown.map((r) => next.entries[r.index].path), [`${HOME}/code/ledger-api`, ledger.path]);
});

test("the cursor follows its row when the refresh moves it", () => {
  const state = { ...first([shop, ledger]), at: 1 };
  assert.equal(state.entries[state.shown[1].index].path, ledger.path);
  const next = refreshed(state, [shop, { ...ledger, touched: NOW }], [], NOW, "");
  assert.deepEqual([next.at, next.entries[next.shown[next.at].index].path], [0, ledger.path]);
});

test("with a filter on, the cursor follows the shown row it was on, not the entry at the same place", () => {
  const ledgerApi = repo(`${HOME}/code/ledger-api`, NOW - 300);
  const state = { ...first([shop, ledgerApi, ledger], [], "ledg"), at: 1 };
  assert.deepEqual([state.entries[state.shown[1].index].path, state.entries[1].path], [ledger.path, ledgerApi.path]);
  const next = refreshed(state, [shop, ledgerApi, { ...ledger, touched: NOW }], [], NOW, "ledg");
  assert.deepEqual([next.at, next.entries[next.shown[next.at].index].path], [0, ledger.path]);
});

test("the cursor keeps its place when its row is gone, and stays inside the shorter list", () => {
  const rows = [repo(`${HOME}/a`, NOW - 1), repo(`${HOME}/b`, NOW - 2), repo(`${HOME}/c`, NOW - 3)];
  const middle = refreshed({ ...first(rows), at: 1 }, [rows[0], rows[2]], [], NOW, "");
  assert.equal(middle.at, 1);
  const last = refreshed({ ...first(rows), at: 2 }, [rows[0]], [], NOW, "");
  assert.equal(last.at, 0);
  const none = refreshed({ ...first(rows), at: 2 }, [], [], NOW, "");
  assert.deepEqual([none.at, none.shown], [0, []]);
});

test("the same folder on two machines keeps a separate checked state per machine", () => {
  const twin = repo(shop.path, NOW, "devbox");
  const state = first([shop, twin]);
  toggle(state.entries, state.entries.findIndex((e) => e.machine === "devbox"));
  const next = refreshed(state, [shop, twin], [], NOW, "");
  assert.deepEqual(next.entries.map((e) => [e.machine, e.selected]), [["devbox", true], [null, false]]);
});

test("an open row the user unchecked stays unchecked, and one still open but gone from the spaces is checked no longer", () => {
  const open = openSpaces([space("s1", shop.path), space("s2", ledger.path)], [], [shop, ledger]);
  const state = first([shop, ledger], open);
  toggle(state.entries, state.entries.findIndex((e) => e.path === shop.path));
  const later = openSpaces([space("s1", shop.path)], [], [shop, ledger]);
  const next = refreshed(state, [shop, ledger], later, NOW, "");
  assert.deepEqual(next.entries.map((e) => [e.path, e.open, e.selected]), [[shop.path, true, false], [ledger.path, false, false]]);
});

test("a refresh that keeps a checked worktree also keeps its open repository checked", () => {
  const fix = tree(`${HOME}/wt/fix`, shop.path, NOW);
  const open = openSpaces([space("s1", shop.path)], [], [shop, fix]);
  const state = first([shop, fix], open);
  toggle(state.entries, state.entries.findIndex((e) => e.path === fix.path));
  const next = refreshed(state, [shop, fix], open, NOW, "");
  assert.deepEqual(next.entries.map((e) => [e.path, e.selected]), [[shop.path, true], [fix.path, true]]);
});
