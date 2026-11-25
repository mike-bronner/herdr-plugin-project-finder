import assert from "node:assert/strict";
import test from "node:test";
import { FIXED_WORKTREE_DIRS } from "../../mods/project-finder/discover.js";
import {
  EMPTY_LIST_HEIGHT,
  HELD_NOTE,
  LABEL_WIDTH,
  LIST_ROWS,
  ROW_HEIGHT,
  applySelection,
  buildEntries,
  filtered,
  fuzzy,
  haystack,
  held,
  heldCount,
  homeSpaceIds,
  listHeight,
  nameMarks,
  openSpaces,
  orderRows,
  pickFocus,
  plan,
  readSettings,
  rowCells,
  setAll,
  toggle,
} from "../../mods/project-finder/picker.js";

const HOME = "/Users/someone";

const entry = (label, more = {}) => ({ label, path: `/x/${label}`, repo: null, kind: "repo", age: "3m ago", open: false, status: null, selected: false, ...more });
const openRepo = (label) => entry(label, { open: true, selected: true, status: "idle" });
const worktreeOf = (repo, branch, more = {}) => entry(branch, { kind: "worktree", repo: `/x/${repo}`, path: `/x/${repo}-wt/${branch}`, ...more });
const checked = (entries) => entries.filter((e) => e.selected).map((e) => e.label);

const space = (id, folder, more = {}) => ({ id, name: folder.split("/").at(-1), folder, machine: null, status: null, active: false, tabs: [{ id: `${id}-tab` }], ...more });
const pane = (spaceId, cwd) => ({ pane: `${spaceId}-p`, tab: `${spaceId}-tab`, cwd });

test("open rows come first in sidebar order, then the rest by touch", () => {
  const rows = ["/a", "/b", "/c", "/d"].map((path) => ({ label: path.slice(1), path }));
  const touched = { "/a": 30, "/b": 10, "/c": 40, "/d": 20 };
  const { head, rest } = orderRows(rows, [{ path: "/c" }, { path: "/a" }, { path: "/ghost" }, { path: "/c" }], (p) => touched[p]);
  assert.deepEqual(head.map((r) => r.label), ["c", "a"]);
  assert.deepEqual(rest.map((r) => r.label), ["d", "b"]);
});

test("nothing open leaves every row in the tail, and equal touch times keep the input order", () => {
  const rows = [{ label: "b", path: "/b" }, { label: "a", path: "/a" }];
  const { head, rest } = orderRows(rows, [], () => 7);
  assert.deepEqual(head, []);
  assert.deepEqual(rest.map((r) => r.label), ["b", "a"]);
});

test("a space matches the deepest listed folder its first terminal is in", () => {
  const paths = ["/x/repo", "/x/repo/.worktrees/repo/feat-x", "/x/other"];
  const spaces = [space("s1", "/x/repo"), space("s2", "/x/repo"), space("s3", "/elsewhere")];
  const panes = [pane("s1", "/x/repo/src"), pane("s2", "/x/repo/.worktrees/repo/feat-x/lib"), pane("s3", "/tmp")];
  assert.deepEqual(openSpaces(spaces, panes, paths).map((o) => [o.id, o.path]), [
    ["s1", "/x/repo"],
    ["s2", "/x/repo/.worktrees/repo/feat-x"],
  ]);
});

test("a space with no terminal falls back to the folder it was made in", () => {
  assert.deepEqual(openSpaces([space("s1", "/x/repo")], [], ["/x/repo"]).map((o) => o.path), ["/x/repo"]);
});

test("a folder that merely shares a prefix does not match", () => {
  assert.deepEqual(openSpaces([space("s1", "/x/repo-old")], [], ["/x/repo"]), []);
});

test("the home space and spaces on other machines are never matched", () => {
  const spaces = [space("h", HOME, { name: "~" }), space("r", "/x/repo", { machine: "vps" })];
  assert.deepEqual(openSpaces(spaces, [pane("h", "/x/repo")], ["/x/repo"]), []);
});

test("the home space and any space with a terminal at home are held back from closing", () => {
  const spaces = [space("h", HOME, { name: "~" }), space("p", "/x/repo"), space("q", "/x/other")];
  const panes = [pane("p", "/x/repo"), { pane: "p2", tab: "p-tab", cwd: HOME }, pane("q", "/x/other")];
  assert.deepEqual([...homeSpaceIds(spaces, panes, HOME)], ["h", "p"]);
});

test("entries pre-check open rows, carry their most urgent status and age, and label by tails", () => {
  const found = [
    { path: "/x/group-a/api", kind: "repo", repo: null, touched: 100 },
    { path: "/x/group-b/api", kind: "repo", repo: null, touched: 900 },
    { path: "/x/solo", kind: "repo", repo: null, touched: 0 },
  ];
  const open = [
    { id: "s1", path: "/x/solo", status: "working" },
    { id: "s2", path: "/x/solo", status: "blocked" },
  ];
  const entries = buildEntries(found, open, 1000);
  assert.deepEqual(entries.map((e) => [e.label, e.selected, e.status, e.age]), [
    ["solo", true, "blocked", "-"],
    ["group-b/api", false, null, "1m ago"],
    ["group-a/api", false, null, "15m ago"],
  ]);
});

test("the filter searches the name, the whole kind word, the age and the status word", () => {
  const tree = entry("w", { kind: "worktree", status: "blocked" });
  assert.equal(haystack(tree), "w worktree 3m ago blocked");
  const entries = [entry("alpha"), tree, entry("beta", { age: "2d ago" })];
  assert.deepEqual(filtered(entries, "worktree").map((r) => r.index), [1]);
  assert.deepEqual(filtered(entries, "blocked").map((r) => r.index), [1]);
  assert.deepEqual(filtered(entries, "2d").map((r) => r.index), [2]);
});

test("an empty filter keeps every row in order and marks nothing", () => {
  assert.deepEqual(filtered([entry("a"), entry("b")], "  "), [{ index: 0, marks: [] }, { index: 1, marks: [] }]);
});

test("a fuzzy filter matches letters in order, every word must match, and space is part of the filter", () => {
  const entries = [entry("project-finder"), entry("finder"), entry("profile")];
  assert.deepEqual(filtered(entries, "pfd").map((r) => r.index), [0]);
  assert.deepEqual(filtered(entries, "fin pro").map((r) => r.index), [0]);
  assert.deepEqual(filtered(entries, "xyz"), []);
});

test("a tighter match ranks first, and ties keep the list order", () => {
  const entries = [entry("a-p-i-x"), entry("api"), entry("api")];
  assert.deepEqual(filtered(entries, "api").map((r) => r.index), [1, 2, 0]);
});

test("lowercase matches any case and an uppercase letter makes the match exact", () => {
  assert.ok(fuzzy("Herdr", "herdr"));
  assert.equal(fuzzy("herdr", "Herdr"), null);
});

test("the marks name the label letters that matched and nothing past the label", () => {
  const [row] = filtered([entry("abc", { status: "idle" })], "ac idle");
  assert.deepEqual(row.marks, [0, 2]);
});

test("a mark past the cut of a long label lands on the ellipsis", () => {
  const long = entry("x".repeat(LABEL_WIDTH + 5));
  assert.deepEqual(nameMarks(long, [0, LABEL_WIDTH + 2]), [0, LABEL_WIDTH - 1]);
});

test("the list is as tall as the rows it shows", () => {
  assert.equal(listHeight(1), ROW_HEIGHT);
  assert.equal(listHeight(5), 5 * ROW_HEIGHT);
});

test("the list stops growing at its row cap", () => {
  assert.equal(listHeight(LIST_ROWS), LIST_ROWS * ROW_HEIGHT);
  assert.equal(listHeight(LIST_ROWS + 1), LIST_ROWS * ROW_HEIGHT);
  assert.equal(listHeight(168), LIST_ROWS * ROW_HEIGHT);
});

test("an empty filter result keeps room for its message", () => {
  assert.equal(listHeight(0), EMPTY_LIST_HEIGHT);
  assert.ok(EMPTY_LIST_HEIGHT > ROW_HEIGHT);
});

test("a worktree row names its repository as a prefix and a repository row carries none", () => {
  assert.deepEqual(rowCells(worktreeOf("sky-mill", "feat-x")), { repo: "sky-mill/", name: "feat-x" });
  assert.deepEqual(rowCells(entry("alpha")), { repo: "", name: "alpha" });
});

test("a prefix too long for the row is cut at its end and the branch survives whole", () => {
  const cells = rowCells(worktreeOf("copper-kettle-brewing", "fix-cache-gaps"));
  assert.equal(cells.repo, "copper-kettle-brew…/");
  assert.equal(cells.name, "fix-cache-gaps");
  assert.equal([...cells.repo, ...cells.name].length, LABEL_WIDTH);
});

test("a prefix with one column to spend is the mark alone, and none is dropped", () => {
  assert.equal(rowCells(worktreeOf("sky-mill", "b".repeat(LABEL_WIDTH - 1))).repo, "…");
  assert.equal(rowCells(worktreeOf("sky-mill", "b".repeat(LABEL_WIDTH))).repo, "");
  assert.equal(rowCells(worktreeOf("ααααα", "b".repeat(LABEL_WIDTH - 4))).repo, "αα…/");
});

test("checking a worktree checks the open repository it belongs to, which is then held", () => {
  const entries = [openRepo("alpha"), worktreeOf("alpha", "feat-x")];
  entries[0].selected = false;
  toggle(entries, 1);
  assert.deepEqual(checked(entries), ["alpha", "feat-x"]);
  assert.ok(held(entries, 0));
  assert.equal(heldCount(entries), 1);
});

test("a held repository will not uncheck until its last checked worktree lets go", () => {
  const entries = [openRepo("alpha"), worktreeOf("alpha", "feat-x", { selected: true }), worktreeOf("alpha", "feat-y", { selected: true })];
  toggle(entries, 0);
  assert.deepEqual(checked(entries), ["alpha", "feat-x", "feat-y"]);
  toggle(entries, 1);
  assert.ok(held(entries, 0), "feat-y still holds it");
  toggle(entries, 2);
  assert.ok(!held(entries, 0));
  toggle(entries, 0);
  assert.deepEqual(checked(entries), []);
});

test("a repository that is not open is never held, and checking its worktree never checks it", () => {
  const entries = [entry("alpha"), worktreeOf("alpha", "feat-x")];
  toggle(entries, 1);
  assert.ok(!held(entries, 0));
  assert.deepEqual(checked(entries), ["feat-x"]);
});

test("a worktree of another repository never holds this one", () => {
  const entries = [openRepo("alpha"), worktreeOf("beta", "feat-y", { selected: true })];
  assert.ok(!held(entries, 0));
  toggle(entries, 0);
  assert.deepEqual(checked(entries), ["feat-y"]);
});

test("ctrl-a checks the open repository of a worktree the filter hides", () => {
  const entries = [openRepo("alpha"), worktreeOf("alpha", "feat-x")];
  entries[0].selected = false;
  setAll(entries, [1], true);
  assert.deepEqual(checked(entries), ["alpha", "feat-x"]);
});

test("ctrl-d clears a repository and its worktrees together, worktrees first", () => {
  const entries = [openRepo("alpha"), worktreeOf("alpha", "feat-x", { selected: true, open: true })];
  setAll(entries, [0, 1], false);
  assert.deepEqual(checked(entries), []);
});

test("ctrl-d leaves a repository checked when the filter hides a worktree of its own", () => {
  const entries = [openRepo("alpha"), worktreeOf("alpha", "feat-x", { selected: true })];
  setAll(entries, [0], false);
  assert.deepEqual(checked(entries), ["alpha", "feat-x"]);
  assert.equal(HELD_NOTE, "held by checked worktrees");
});

const rows = () => [
  entry("alpha", { selected: false }),
  worktreeOf("alpha", "feat-x", { selected: false }),
  entry("beta", { selected: true }),
  entry("gamma", { selected: true }),
];

test("enter closes unchecked open rows, worktrees before their repository, and opens newly checked ones", () => {
  const entries = rows();
  const open = [
    { id: "s-alpha", path: "/x/alpha" },
    { id: "s-wt", path: "/x/alpha-wt/feat-x" },
    { id: "s-beta", path: "/x/beta" },
    { id: "s-other", path: "/elsewhere" },
  ];
  assert.deepEqual(plan(entries, open, new Set()), { close: ["s-wt", "s-alpha"], create: ["/x/gamma"] });
});

test("every space on an unchecked row closes, and a space nobody listed is left alone", () => {
  const entries = [entry("alpha")];
  const open = [{ id: "a1", path: "/x/alpha" }, { id: "a2", path: "/x/alpha" }, { id: "z", path: "/x/zeta" }];
  assert.deepEqual(plan(entries, open, new Set()).close, ["a1", "a2"]);
});

test("the home space is never closed, even when its row is unchecked", () => {
  const entries = [entry("alpha")];
  assert.deepEqual(plan(entries, [{ id: "h", path: "/x/alpha" }], new Set(["h"])).close, []);
});

test("an unchanged selection changes nothing", () => {
  const entries = [entry("alpha", { selected: true, open: true })];
  assert.deepEqual(plan(entries, [{ id: "a", path: "/x/alpha" }], new Set()), { close: [], create: [] });
});

test("a created space takes the focus", () => {
  assert.equal(pickFocus(["n1", "n2"], ["w1"], "w1", ["w9"], "h"), "n2");
});

test("closing the focused space falls back to the first survivor, then home, then nothing", () => {
  assert.equal(pickFocus([], ["w1"], "w1", ["w5", "w6"], "h"), "w5");
  assert.equal(pickFocus([], ["w1"], "w1", [], "h"), "h");
  assert.equal(pickFocus([], ["w1"], "w1", [], undefined), null);
});

test("an unchanged focus is left alone", () => {
  assert.equal(pickFocus([], ["w2"], "w1", ["w1"], "h"), null);
});

function recorder(refuse = []) {
  const calls = [];
  const run = (command, args) => {
    calls.push([command, args]);
    if (refuse.includes(args.id ?? args.folder)) throw new Error("busy");
  };
  return { calls, run };
}

test("enter starts the agent in every new space, and only the last one takes the focus", () => {
  const { calls, run } = recorder();
  const entries = [entry("alpha", { selected: true }), entry("beta", { selected: true }), entry("gamma", { selected: true })];
  applySelection({ entries, spaces: [], panes: [], home: HOME, agent: ["claude", "--continue"] }, run);
  assert.deepEqual(calls, [
    ["space.new", { folder: "/x/alpha", machine: "", argv: ["claude", "--continue"], focus: false }],
    ["space.new", { folder: "/x/beta", machine: "", argv: ["claude", "--continue"], focus: false }],
    ["space.new", { folder: "/x/gamma", machine: "", argv: ["claude", "--continue"], focus: true }],
  ]);
});

test("enter with no agent opens plain shells that carry no argv", () => {
  const { calls, run } = recorder();
  applySelection({ entries: [entry("alpha", { selected: true })], spaces: [], panes: [], home: HOME, agent: null }, run);
  assert.deepEqual(calls, [["space.new", { folder: "/x/alpha", machine: "", focus: true }]]);
  assert.equal("argv" in calls[0][1], false);
});

test("enter closes worktrees before their repository, confirmed, and then opens new spaces", () => {
  const { calls, run } = recorder();
  const entries = [entry("alpha"), worktreeOf("alpha", "feat-x"), entry("beta", { selected: true })];
  const spaces = [space("s-alpha", "/x/alpha", { active: true }), space("s-wt", "/x/alpha-wt/feat-x")];
  applySelection({ entries, spaces, panes: [], home: HOME, agent: null }, run);
  assert.deepEqual(calls, [
    ["space.close", { id: "s-wt", confirmed: true }],
    ["space.close", { id: "s-alpha", confirmed: true }],
    ["space.new", { folder: "/x/beta", machine: "", focus: true }],
  ]);
});

test("enter that opens nothing and closes the shown space selects the first space that stayed", () => {
  const { calls, run } = recorder();
  const entries = [entry("alpha"), entry("beta", { selected: true }), entry("gamma", { selected: true })];
  const spaces = [space("h", HOME, { name: "~" }), space("s-alpha", "/x/alpha", { active: true }), space("s-beta", "/x/beta"), space("s-gamma", "/x/gamma")];
  applySelection({ entries, spaces, panes: [], home: HOME, agent: null }, run);
  assert.deepEqual(calls, [
    ["space.close", { id: "s-alpha", confirmed: true }],
    ["space.select", { id: "s-beta" }],
  ]);
});

test("enter that leaves no survivor selects home, and a shown space that stays is not reselected", () => {
  const home = recorder();
  const spaces = [space("h", HOME, { name: "~" }), space("s-alpha", "/x/alpha", { active: true })];
  applySelection({ entries: [entry("alpha")], spaces, panes: [], home: HOME, agent: null }, home.run);
  assert.deepEqual(home.calls.at(-1), ["space.select", { id: "h" }]);
  const stays = recorder();
  const kept = [space("s-alpha", "/x/alpha"), space("s-beta", "/x/beta", { active: true })];
  applySelection({ entries: [entry("alpha"), entry("beta", { selected: true })], spaces: kept, panes: [], home: HOME, agent: null }, stays.run);
  assert.deepEqual(stays.calls, [["space.close", { id: "s-alpha", confirmed: true }]]);
});

test("a close that fails is reported, the rest still run, and the space it left is not counted as closed", () => {
  const { calls, run } = recorder(["s-alpha"]);
  const entries = [entry("alpha"), entry("beta")];
  const spaces = [space("h", HOME, { name: "~" }), space("s-alpha", "/x/alpha", { active: true }), space("s-beta", "/x/beta")];
  const refused = applySelection({ entries, spaces, panes: [], home: HOME, agent: null }, run);
  assert.deepEqual(refused, ["/x/alpha did not close: busy"]);
  assert.deepEqual(calls, [
    ["space.close", { id: "s-alpha", confirmed: true }],
    ["space.close", { id: "s-beta", confirmed: true }],
  ]);
});

test("a select that fails is ignored, and the failed closes before it are still returned", () => {
  const { calls, run } = recorder(["s-alpha", "h"]);
  const entries = [entry("alpha"), entry("beta")];
  const spaces = [space("h", HOME, { name: "~" }), space("s-alpha", "/x/alpha"), space("s-beta", "/x/beta", { active: true })];
  const refused = applySelection({ entries, spaces, panes: [], home: HOME, agent: null }, run);
  assert.deepEqual(refused, ["/x/alpha did not close: busy"]);
  assert.deepEqual(calls, [
    ["space.close", { id: "s-alpha", confirmed: true }],
    ["space.close", { id: "s-beta", confirmed: true }],
    ["space.select", { id: "h" }],
  ]);
});

test("a space that fails to open is reported with the failed closes, stops the rest, and nothing is selected after it", () => {
  const { calls, run } = recorder(["s-alpha", "/x/gamma"]);
  const entries = [entry("alpha"), entry("beta"), entry("gamma", { selected: true }), entry("delta", { selected: true })];
  const spaces = [space("h", HOME, { name: "~" }), space("s-alpha", "/x/alpha", { active: true }), space("s-beta", "/x/beta")];
  const refused = applySelection({ entries, spaces, panes: [], home: HOME, agent: null }, run);
  assert.deepEqual(refused, ["/x/alpha did not close: busy", "/x/gamma did not open: busy"]);
  assert.deepEqual(calls, [
    ["space.close", { id: "s-alpha", confirmed: true }],
    ["space.close", { id: "s-beta", confirmed: true }],
    ["space.new", { folder: "/x/gamma", machine: "", focus: false }],
  ]);
});

test("enter never closes a space with a terminal at home, even when its first terminal's row is unchecked", () => {
  const { calls, run } = recorder();
  const entries = [entry("alpha"), entry("beta")];
  const spaces = [space("s-alpha", "/x/alpha", { active: true, tabs: [{ id: "s-alpha-tab" }, { id: "s-alpha-home" }] }), space("s-beta", "/x/beta")];
  const panes = [pane("s-alpha", "/x/alpha"), { pane: "s-alpha-h", tab: "s-alpha-home", cwd: HOME }, pane("s-beta", "/x/beta")];
  assert.deepEqual(openSpaces(spaces, panes, entries.map((e) => e.path)).map((o) => o.id), ["s-alpha", "s-beta"]);
  applySelection({ entries, spaces, panes, home: HOME, agent: null }, run);
  assert.deepEqual(calls, [["space.close", { id: "s-beta", confirmed: true }]]);
});

test("enter after checking a worktree of an open repository opens the worktree and never closes the held repository", () => {
  const found = [
    { path: "/x/alpha", kind: "repo", repo: null, touched: 900 },
    { path: "/x/alpha/.worktrees/feat-x", kind: "worktree", repo: "/x/alpha", touched: 800 },
  ];
  const spaces = [space("s-alpha", "/x/alpha", { active: true })];
  const entries = buildEntries(found, openSpaces(spaces, [], found.map((f) => f.path)), 1000);
  const repo = entries.findIndex((e) => e.path === "/x/alpha");
  const tree = entries.findIndex((e) => e.kind === "worktree");
  toggle(entries, tree);
  toggle(entries, repo);
  assert.equal(entries[repo].selected, true);
  const { calls, run } = recorder();
  applySelection({ entries, spaces, panes: [], home: HOME, agent: null }, run);
  assert.deepEqual(calls, [["space.new", { folder: "/x/alpha/.worktrees/feat-x", machine: "", focus: true }]]);
});

const modJson = (settings) => JSON.stringify({ place: "none", settings });

test("the defaults search home with a plain shell and the app's worktree folder", () => {
  const s = readSettings(modJson({ root: "~", agent: null }), "{}", HOME);
  assert.deepEqual(s, { root: HOME, agent: null, dirs: [...FIXED_WORKTREE_DIRS], roots: [`${HOME}/.herdr/worktrees`], problems: [] });
  assert.equal(readSettings(null, null, HOME).root, HOME);
});

test("an empty or blank root searches home and reports nothing", () => {
  for (const root of ["", "   "]) {
    const s = readSettings(modJson({ root }), "{}", HOME);
    assert.deepEqual([s.root, s.problems], [HOME, []], JSON.stringify(root));
  }
});

test("an agent list starts that agent and a root under home is expanded", () => {
  const s = readSettings(modJson({ root: "~/Developer", agent: ["claude", "--continue"] }), "{}", HOME);
  assert.equal(s.root, `${HOME}/Developer`);
  assert.deepEqual(s.agent, ["claude", "--continue"]);
});

test("a malformed agent, root or key is reported and never used", () => {
  for (const agent of ["claude", [], [""], [1], {}]) {
    const s = readSettings(modJson({ agent }), "{}", HOME);
    assert.equal(s.agent, null);
    assert.equal(s.problems.length, 1, JSON.stringify(agent));
  }
  assert.deepEqual(readSettings(modJson({ root: "relative/dir" }), "{}", HOME).problems, ["root must be an absolute path or start with ~"]);
  assert.deepEqual(readSettings(modJson({ root: 3 }), "{}", HOME).problems, ["root must be a path"]);
  assert.deepEqual(readSettings(modJson({ roots: "/x" }), "{}", HOME).problems, ["roots is not a setting"]);
  assert.equal(readSettings("{not json", "{}", HOME).problems.length, 1);
});

test("settings that are null, a list or a word are reported and the defaults are used", () => {
  for (const settings of [null, ["claude"], "claude"]) {
    const s = readSettings(modJson(settings), "{}", HOME);
    assert.deepEqual([s.root, s.agent, s.problems], [HOME, null, ["settings must be an object"]], JSON.stringify(settings));
  }
});

test("an absent settings.json still searches the app's default worktree folder", () => {
  const s = readSettings(null, null, HOME);
  assert.deepEqual([s.dirs, s.roots, s.problems], [[...FIXED_WORKTREE_DIRS], [`${HOME}/.herdr/worktrees`], []]);
});

test("the app's worktree directory is read from settings.json", () => {
  const s = readSettings(null, JSON.stringify({ worktrees: { directory: "~/Developer/worktrees" } }), HOME);
  assert.deepEqual(s.roots, [`${HOME}/Developer/worktrees`]);
});

test("a null worktree directory in settings.json keeps the app's default worktree folder", () => {
  const s = readSettings(null, JSON.stringify({ worktrees: { directory: null } }), HOME);
  assert.deepEqual([s.roots, s.problems], [[`${HOME}/.herdr/worktrees`], []]);
});

test("an unreadable or malformed settings.json leaves only the fixed containers", () => {
  for (const text of [undefined, "{broken"]) {
    const s = readSettings(null, text, HOME);
    assert.deepEqual([s.dirs, s.roots, s.problems], [[...FIXED_WORKTREE_DIRS], [], []]);
  }
});
