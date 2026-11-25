import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { FIXED_WORKTREE_DIRS } from "../../mods/project-finder/discover.js";
import {
  HOME_SCRIPT,
  MACHINE_ROOTS_PROBLEM,
  MACHINE_WIDTH,
  applySelection,
  buildEntries,
  discoverAll,
  filtered,
  haystack,
  held,
  homeSpaceIds,
  labelledPerMachine,
  machineCell,
  openSpaces,
  plan,
  projectKey,
  readSettings,
  searchPlan,
  toggle,
} from "../../mods/project-finder/picker.js";

const HOME = "/Users/someone";
const BOX_HOME = "/home/builder";

const modJson = (settings) => JSON.stringify({ place: "none", settings });
const space = (id, folder, more = {}) => ({ id, name: folder.split("/").at(-1), folder, machine: null, status: null, active: false, tabs: [{ id: `${id}-tab` }], ...more });
const pane = (spaceId, cwd) => ({ pane: `${spaceId}-p`, tab: `${spaceId}-tab`, cwd });
const entry = (path, more = {}) => ({ label: path.split("/").at(-1), path, machine: null, repo: null, kind: "repo", age: "3m ago", open: false, status: null, selected: false, ...more });

test("the root setting is expanded against the home of the machine it is read for", () => {
  const s = readSettings(modJson({ root: "~/code" }), "{}", BOX_HOME, "devbox");
  assert.deepEqual([s.root, s.roots, s.problems], [`${BOX_HOME}/code`, [`${BOX_HOME}/.herdr/worktrees`], []]);
});

test("a machine root overrides the root on that machine only", () => {
  const json = modJson({ root: "~/code", machineRoots: { devbox: "~/Developer", other: "/srv/repos" } });
  assert.equal(readSettings(json, "{}", BOX_HOME, "devbox").root, `${BOX_HOME}/Developer`);
  assert.equal(readSettings(json, "{}", "/home/x", "other").root, "/srv/repos");
  assert.equal(readSettings(json, "{}", "/home/y", "third").root, "/home/y/code");
  assert.equal(readSettings(json, "{}", HOME).root, `${HOME}/code`);
  assert.deepEqual(readSettings(json, "{}", HOME).problems, []);
});

test("a blank machine root searches that machine's home", () => {
  const s = readSettings(modJson({ root: "/srv", machineRoots: { devbox: " " } }), "{}", BOX_HOME, "devbox");
  assert.deepEqual([s.root, s.problems], [BOX_HOME, []]);
});

test("a null machine root map is the same as none", () => {
  const s = readSettings(modJson({ machineRoots: null }), "{}", BOX_HOME, "devbox");
  assert.deepEqual([s.root, s.problems], [BOX_HOME, []]);
});

test("a machine root map that is not an object is reported, and the root setting is used", () => {
  for (const machineRoots of [["~/x"], "~/x", 3]) {
    const s = readSettings(modJson({ root: "~/code", machineRoots }), "{}", BOX_HOME, "devbox");
    assert.deepEqual([s.root, s.problems], [`${BOX_HOME}/code`, [MACHINE_ROOTS_PROBLEM]], JSON.stringify(machineRoots));
  }
});

test("a malformed machine root is reported on every machine and never used", () => {
  const json = modJson({ root: "~/code", machineRoots: { devbox: "relative/dir", spare: 7 } });
  const problems = ["machineRoots.devbox must be an absolute path or start with ~", "machineRoots.spare must be a path"];
  assert.deepEqual(readSettings(json, "{}", HOME).problems, problems);
  const s = readSettings(json, "{}", BOX_HOME, "devbox");
  assert.deepEqual([s.root, s.problems], [`${BOX_HOME}/code`, problems]);
});

test("the app's worktree directory is expanded against each machine's home", () => {
  const app = JSON.stringify({ worktrees: { directory: "~/Developer/worktrees" } });
  assert.deepEqual(readSettings(null, app, BOX_HOME, "devbox").roots, [`${BOX_HOME}/Developer/worktrees`]);
  assert.deepEqual(readSettings(null, app, HOME).roots, [`${HOME}/Developer/worktrees`]);
  assert.deepEqual(readSettings(null, app, BOX_HOME, "devbox").dirs, [...FIXED_WORKTREE_DIRS]);
});

test("a space matches a row only when its machine and folder both match", () => {
  const projects = [
    { path: "/srv/api", machine: null },
    { path: "/srv/api", machine: "devbox" },
    { path: "/srv/web", machine: "devbox" },
  ];
  const spaces = [
    space("local", "/srv/api"),
    space("devbox/1", "/srv/api", { machine: "devbox" }),
    space("spare/1", "/srv/web", { machine: "spare" }),
    space("devbox/2", "/srv/web/src", { machine: "devbox" }),
  ];
  assert.deepEqual(openSpaces(spaces, [], projects).map((o) => [o.id, o.machine, o.path]), [
    ["local", null, "/srv/api"],
    ["devbox/1", "devbox", "/srv/api"],
    ["devbox/2", "devbox", "/srv/web"],
  ]);
});

test("a remote space's first terminal places it among that machine's rows", () => {
  const projects = [{ path: "/srv/api", machine: "devbox" }, { path: "/srv/api/.worktrees/fix", machine: "devbox" }];
  const spaces = [space("devbox/1", "/srv/api", { machine: "devbox" })];
  assert.deepEqual(openSpaces(spaces, [pane("devbox/1", "/srv/api/.worktrees/fix/lib")], projects).map((o) => o.path), ["/srv/api/.worktrees/fix"]);
});

test("a remote home space is never matched", () => {
  const spaces = [space("devbox/h", BOX_HOME, { name: "~", machine: "devbox" })];
  assert.deepEqual(openSpaces(spaces, [pane("devbox/h", "/srv/api")], [{ path: "/srv/api", machine: "devbox" }]), []);
});

test("a space with a terminal at its own machine's home is held back, and another machine's home does not count", () => {
  const spaces = [
    space("devbox/1", "/srv/api", { machine: "devbox" }),
    space("devbox/2", "/srv/web", { machine: "devbox" }),
    space("spare/1", "/srv/api", { machine: "spare" }),
    space("local", "/srv/api"),
  ];
  const panes = [pane("devbox/1", BOX_HOME), pane("devbox/2", HOME), pane("spare/1", BOX_HOME), pane("local", BOX_HOME)];
  assert.deepEqual([...homeSpaceIds(spaces, panes, HOME, { devbox: BOX_HOME })], ["devbox/1"]);
});

test("a remote space named ~ is held back even when its machine's home is unknown", () => {
  assert.deepEqual([...homeSpaceIds([space("spare/h", "/x", { name: "~", machine: "spare" })], [], HOME)], ["spare/h"]);
});

test("labels are unique per machine, so the same folder on two machines keeps its short name", () => {
  const found = [
    { path: "/srv/group-a/api", machine: null },
    { path: "/srv/group-a/api", machine: "devbox" },
    { path: "/srv/group-b/api", machine: "devbox" },
  ];
  assert.deepEqual(labelledPerMachine(found).map((r) => [r.machine, r.label]), [
    [null, "api"],
    ["devbox", "group-a/api"],
    ["devbox", "group-b/api"],
  ]);
});

test("the same folder on two machines makes two rows, each open only by its own machine's spaces", () => {
  const found = [
    { path: "/srv/api", machine: null, kind: "repo", repo: null, touched: 900 },
    { path: "/srv/api", machine: "devbox", kind: "repo", repo: null, touched: 100 },
  ];
  const open = [{ id: "devbox/1", path: "/srv/api", machine: "devbox", status: "working" }];
  const entries = buildEntries(found, open, 1000);
  assert.deepEqual(entries.map((e) => [e.machine, e.label, e.open, e.selected, e.status]), [
    ["devbox", "api", true, true, "working"],
    [null, "api", false, false, null],
  ]);
});

test("rows from every machine are one list, open ones first, then by touch", () => {
  const found = [
    { path: "/a", machine: null, kind: "repo", repo: null, touched: 100 },
    { path: "/b", machine: "devbox", kind: "repo", repo: null, touched: 900 },
    { path: "/c", machine: "spare", kind: "repo", repo: null, touched: 500 },
  ];
  const entries = buildEntries(found, [{ id: "local", path: "/a", machine: null }], 1000);
  assert.deepEqual(entries.map((e) => projectKey(e)), ["/a", "devbox:/b", "spare:/c"]);
});

test("the machine cell is blank for this computer and names a remote, cut to its width", () => {
  assert.equal(machineCell(entry("/srv/api")), "");
  assert.equal(machineCell(entry("/srv/api", { machine: "devbox" })), "devbox");
  const long = "a-very-long-machine-name";
  assert.equal([...machineCell(entry("/srv/api", { machine: long }))].length, MACHINE_WIDTH);
  assert.ok(machineCell(entry("/srv/api", { machine: long })).endsWith("…"));
});

test("the filter matches machine names, and a local row has no machine words", () => {
  const entries = [entry("/srv/api"), entry("/srv/web", { machine: "devbox" })];
  assert.equal(haystack(entries[1]), "web repo 3m ago  devbox");
  assert.equal(haystack(entries[0]), "api repo 3m ago ");
  assert.deepEqual(filtered(entries, "devbox").map((r) => [r.index, r.marks]), [[1, []]]);
});

test("a worktree holds and checks only the open repository on its own machine", () => {
  const entries = [
    entry("/srv/api", { open: true, selected: false }),
    entry("/srv/api", { machine: "devbox", open: true, selected: false }),
    entry("/srv/api/.worktrees/fix", { machine: "devbox", kind: "worktree", repo: "/srv/api" }),
  ];
  toggle(entries, 2);
  assert.deepEqual(entries.map((e) => e.selected), [false, true, true]);
  assert.deepEqual([held(entries, 0), held(entries, 1)], [false, true]);
});

test("the plan closes and opens by machine and folder together", () => {
  const entries = [
    entry("/srv/api", { selected: true }),
    entry("/srv/api", { machine: "devbox" }),
    entry("/srv/web", { machine: "devbox", selected: true }),
  ];
  const open = [{ id: "local", path: "/srv/api", machine: null }, { id: "devbox/1", path: "/srv/api", machine: "devbox" }];
  assert.deepEqual(plan(entries, open, new Set()), { close: ["devbox/1"], create: ["devbox:/srv/web"] });
});

function recorder(refuse = []) {
  const calls = [];
  const run = (command, args) => {
    calls.push([command, args]);
    if (refuse.includes(args.id ?? args.folder)) throw new Error("busy");
  };
  return { calls, run };
}

test("enter closes remote worktrees before repositories on their machines, then opens each new space on its machine", () => {
  const { calls, run } = recorder();
  const entries = [
    entry("/srv/api", { machine: "devbox" }),
    entry("/srv/api/.worktrees/fix", { machine: "devbox", kind: "worktree", repo: "/srv/api" }),
    entry("/srv/api", { selected: true }),
    entry("/srv/web", { machine: "devbox", selected: true }),
    entry("/srv/docs", { selected: true }),
  ];
  const spaces = [
    space("local", "/srv/api", { active: true }),
    space("devbox/1", "/srv/api", { machine: "devbox" }),
    space("devbox/2", "/srv/api/.worktrees/fix", { machine: "devbox" }),
  ];
  applySelection({ entries, spaces, panes: [], home: HOME, homes: { devbox: BOX_HOME }, agent: ["claude"] }, run);
  assert.deepEqual(calls, [
    ["space.close", { id: "devbox/2", confirmed: true }],
    ["space.close", { id: "devbox/1", confirmed: true }],
    ["space.new", { folder: "/srv/web", machine: "devbox", argv: ["claude"], focus: false }],
    ["space.new", { folder: "/srv/docs", machine: "", argv: ["claude"], focus: true }],
  ]);
});

test("enter never closes a remote space with a terminal at that machine's home", () => {
  const { calls, run } = recorder();
  const entries = [entry("/srv/api", { machine: "devbox" }), entry("/srv/web", { machine: "devbox" })];
  const spaces = [
    space("devbox/1", "/srv/api", { machine: "devbox", tabs: [{ id: "devbox/1-tab" }, { id: "devbox/1-home" }] }),
    space("devbox/2", "/srv/web", { machine: "devbox" }),
  ];
  const panes = [pane("devbox/1", "/srv/api"), { pane: "devbox/1-h", tab: "devbox/1-home", cwd: BOX_HOME }];
  applySelection({ entries, spaces, panes, home: HOME, homes: { devbox: BOX_HOME }, agent: null }, run);
  assert.deepEqual(calls, [["space.close", { id: "devbox/2", confirmed: true }]]);
});

test("a remote close or open that fails is reported with its machine", () => {
  const { run } = recorder(["devbox/1", "/srv/web"]);
  const entries = [entry("/srv/api", { machine: "devbox" }), entry("/srv/web", { machine: "devbox", selected: true })];
  const spaces = [space("devbox/1", "/srv/api", { machine: "devbox" })];
  const refused = applySelection({ entries, spaces, panes: [], home: HOME, agent: null }, run);
  assert.deepEqual(refused, ["devbox:/srv/api did not close: busy", "devbox:/srv/web did not open: busy"]);
});

const runLocal = (command, args) =>
  new Promise((resolve, reject) =>
    execFile(command, args, { maxBuffer: 8 << 20 }, (error, stdout, stderr) => {
      if (error && typeof error.code !== "number") reject(error);
      else resolve({ code: error?.code ?? 0, stdout, stderr });
    }),
  );

const temps = [];
function tempDir() {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), "project-finder-remote-")));
  temps.push(dir);
  return dir;
}
test.after(() => temps.forEach((dir) => rmSync(dir, { recursive: true, force: true })));

const makeRepo = (path) => mkdirSync(join(path, ".git"), { recursive: true });

function machinesOnDisk() {
  const local = tempDir();
  const box = tempDir();
  makeRepo(join(local, "code", "shop"));
  makeRepo(join(box, "code", "ignored"));
  makeRepo(join(box, "Developer", "shop"));
  makeRepo(join(box, "Developer", "infra"));
  const tree = join(box, "Developer", "worktrees", "shop", "fix-cart");
  mkdirSync(tree, { recursive: true });
  writeFileSync(join(tree, ".git"), `gitdir: ${join(box, "Developer", "shop", ".git", "worktrees", "fix-cart")}\n`);
  return { local, box };
}

function fakeExec(homes, { refuse = [], homeFails = [] } = {}) {
  const calls = [];
  const exec = async (command, args, options = {}) => {
    calls.push({ command, on: options.on ?? null, timeout: options.timeout });
    if (refuse.includes(options.on)) throw new Error(`${options.on} cannot be reached`);
    if (command === "sh" && args[1] === HOME_SCRIPT) return { code: homeFails.includes(options.on) ? 1 : 0, stdout: homes[options.on] ?? "", stderr: "" };
    return runLocal(command, args);
  };
  return { calls, exec };
}

test("every ready machine is searched with its own home, root and worktree folder, and the rows carry their machine", async () => {
  const { local, box } = machinesOnDisk();
  const { calls, exec } = fakeExec({ devbox: box });
  const found = await discoverAll(
    {
      machines: [{ name: null, layout: "ready" }, { name: "devbox", layout: "ready" }],
      modJson: modJson({ root: "~/code", machineRoots: { devbox: "~/Developer" } }),
      appSettings: JSON.stringify({ worktrees: { directory: "~/Developer/worktrees" } }),
      home: local,
    },
    exec,
  );
  assert.deepEqual(found.rows.map((r) => [r.machine, r.path, r.kind, r.repo]), [
    [null, join(local, "code", "shop"), "repo", null],
    ["devbox", join(box, "Developer", "infra"), "repo", null],
    ["devbox", join(box, "Developer", "shop"), "repo", null],
    ["devbox", join(box, "Developer", "worktrees", "shop", "fix-cart"), "worktree", join(box, "Developer", "shop")],
  ]);
  assert.deepEqual(found.homes, { devbox: box });
  assert.deepEqual(found.searched, [join(local, "code"), `devbox:${join(box, "Developer")}`]);
  assert.deepEqual([found.notes, found.problems, found.agent], [[], [], null]);
  const remote = calls.filter((c) => c.on === "devbox");
  assert.deepEqual(remote.map((c) => c.command), ["sh", "test", "find", "find", "sh"]);
  assert.ok(remote.slice(2).every((c) => c.timeout === 120_000));
  assert.deepEqual(calls.filter((c) => c.on === null).map((c) => c.command), ["test", "find", "find", "sh"]);
});

test("a machine that is unavailable, still connecting or failing is skipped with a note, and the rest still load", async () => {
  const { local, box } = machinesOnDisk();
  const { calls, exec } = fakeExec({ devbox: box, nohome: "" }, { refuse: ["broken"] });
  const found = await discoverAll(
    {
      machines: [
        { name: null, layout: "ready" },
        { name: "offline", layout: "unavailable" },
        { name: "starting", layout: "loading" },
        { name: "broken", layout: "ready" },
        { name: "nohome", layout: "ready" },
        { name: "devbox", layout: "ready" },
      ],
      modJson: modJson({ root: "~/code" }),
      appSettings: "{}",
      home: local,
    },
    exec,
  );
  assert.deepEqual(found.notes, [
    "Skipped offline: unavailable",
    "Skipped starting: still connecting",
    "Skipped broken: broken cannot be reached",
    "Skipped nohome: its home folder could not be read",
  ]);
  assert.deepEqual(found.rows.map((r) => [r.machine, r.path]), [
    [null, join(local, "code", "shop")],
    ["devbox", join(box, "code", "ignored")],
  ]);
  assert.deepEqual(Object.keys(found.homes), ["devbox"]);
  assert.equal(calls.some((c) => c.on === "offline" || c.on === "starting"), false);
});

test("every ready machine starts its search at once, and rows and notes keep the machine order whatever order the searches end in", async () => {
  const { local, box } = machinesOnDisk();
  const other = tempDir();
  makeRepo(join(other, "code", "ledger"));
  const { exec } = fakeExec({ alpha: box, beta: other, gone: "/home/x" }, { refuse: ["lost"], homeFails: ["gone"] });
  const started = [];
  const finished = [];
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  const finishes = new Map(
    ["alpha", "beta"].map((name) => {
      let resolve;
      const done = new Promise((r) => (resolve = r));
      return [name, { done, resolve }];
    }),
  );
  const waitsFor = new Map([
    [null, "alpha"],
    ["alpha", "beta"],
  ]);
  const gated = async (command, args, options = {}) => {
    const machine = options.on ?? null;
    started.push(machine);
    const homeRead = command === "sh" && args[1] === HOME_SCRIPT;
    if (homeRead && (machine === "alpha" || machine === "gone")) await gate;
    const lastCall = command === "sh" && !homeRead;
    if (lastCall && waitsFor.has(machine)) await finishes.get(waitsFor.get(machine)).done;
    const result = await exec(command, args, options);
    if (lastCall) {
      finished.push(machine);
      finishes.get(machine)?.resolve();
    }
    return result;
  };
  const pending = discoverAll(
    {
      machines: ["alpha", "beta", "gone", "lost"].map((name) => ({ name, layout: "ready" })),
      modJson: modJson({ root: "~/code" }),
      appSettings: "{}",
      home: local,
    },
    gated,
  );
  assert.deepEqual(started, [null, "alpha", "beta", "gone", "lost"]);
  await new Promise((resolve) => setImmediate(resolve));
  release();
  const found = await pending;
  assert.deepEqual(finished, ["beta", "alpha", null]);
  assert.deepEqual(found.rows.map((r) => [r.machine, r.path]), [
    [null, join(local, "code", "shop")],
    ["alpha", join(box, "code", "ignored")],
    ["beta", join(other, "code", "ledger")],
  ]);
  assert.deepEqual(found.notes, ["Skipped gone: its home folder could not be read", "Skipped lost: lost cannot be reached"]);
  assert.deepEqual(found.homes, { alpha: box, beta: other });
});

test("a home read that exits non-zero skips that machine even when it printed a folder", async () => {
  const { local } = machinesOnDisk();
  const { calls, exec } = fakeExec({ gone: "/home/x" }, { homeFails: ["gone"] });
  const found = await discoverAll({ machines: [{ name: "gone", layout: "ready" }], modJson: modJson({ root: "~/code" }), appSettings: "{}", home: local }, exec);
  assert.deepEqual(found.notes, ["Skipped gone: its home folder could not be read"]);
  assert.deepEqual(found.homes, {});
  assert.deepEqual(found.rows.map((r) => r.machine), [null]);
  assert.deepEqual(calls.filter((c) => c.on === "gone").map((c) => c.command), ["sh"]);
});

test("a failing search on this computer is a note too, and the remote rows still load", async () => {
  const { box } = machinesOnDisk();
  const { exec } = fakeExec({ devbox: box });
  const failing = async (command, args, options = {}) => (options.on ? exec(command, args, options) : Promise.reject(new Error("find is missing")));
  const found = await discoverAll({ machines: [{ name: "devbox", layout: "ready" }], modJson: modJson({ root: "~/code" }), appSettings: "{}", home: "/nowhere" }, failing);
  assert.deepEqual(found.notes, ["Skipped this computer: find is missing"]);
  assert.deepEqual(found.rows.map((r) => r.machine), ["devbox"]);
  assert.deepEqual(found.searched, [`devbox:${join(box, "code")}`]);
});

test("a remote root that is not a folder falls back to that machine's home", async () => {
  const { local, box } = machinesOnDisk();
  const { exec } = fakeExec({ devbox: box });
  const found = await discoverAll(
    { machines: [{ name: "devbox", layout: "ready" }], modJson: modJson({ root: "~/code", machineRoots: { devbox: "~/missing" } }), appSettings: "{}", home: local },
    exec,
  );
  assert.deepEqual(found.searched, [join(local, "code"), `devbox:${box}`]);
});

test("each machine's result is reported as its search ends, before slower machines finish", async () => {
  const { local, box } = machinesOnDisk();
  const { exec } = fakeExec({ devbox: box }, { refuse: ["broken"] });
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  const slow = async (command, args, options = {}) => {
    if (options.on === "devbox") await gate;
    return exec(command, args, options);
  };
  const reported = [];
  let localReported;
  const localDone = new Promise((resolve) => (localReported = resolve));
  const pending = discoverAll(
    {
      machines: [{ name: "devbox", layout: "ready" }, { name: "broken", layout: "ready" }, { name: "offline", layout: "unavailable" }],
      modJson: modJson({ root: "~/code" }),
      appSettings: "{}",
      home: local,
    },
    slow,
    (result) => {
      reported.push(result);
      if (result.machine === null) localReported();
    },
  );
  await Promise.race([localDone, new Promise((resolve) => setTimeout(resolve, 5000))]);
  assert.deepEqual(reported.map((r) => [r.machine, r.failed ?? null]), [["broken", "broken cannot be reached"], [null, null]]);
  assert.deepEqual(reported[1].rows.map((r) => r.path), [join(local, "code", "shop")]);
  assert.deepEqual([reported[1].home, reported[1].root], [local, join(local, "code")]);
  release();
  await pending;
  assert.deepEqual(reported.map((r) => r.machine), ["broken", null, "devbox"]);
  assert.deepEqual(reported[2].rows.map((r) => [r.machine, r.path]), [["devbox", join(box, "code", "ignored")]]);
});

test("the search plan reads the settings and notes machines that are not ready before any search runs", () => {
  const found = searchPlan({
    machines: [{ name: null, layout: "ready" }, { name: "devbox", layout: "ready" }, { name: "starting", layout: "loading" }, { name: "offline", layout: "unavailable" }],
    modJson: modJson({ agent: ["claude"], colour: "red" }),
    appSettings: "{}",
    home: HOME,
  });
  assert.deepEqual(found, {
    agent: ["claude"],
    problems: ["colour is not a setting"],
    notes: ["Skipped starting: still connecting", "Skipped offline: unavailable"],
    targets: [null, "devbox"],
  });
});
