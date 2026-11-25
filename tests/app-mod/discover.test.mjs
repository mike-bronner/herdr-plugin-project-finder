import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import {
  FIXED_WORKTREE_DIRS,
  bandRepos,
  comparePaths,
  discover,
  elide,
  factsScript,
  humanAge,
  labelled,
  normpath,
  parentRepo,
  shellQuote,
  tails,
  worktreeLocations,
} from "../../mods/project-finder/discover.js";

const POINTER = "/x/parent/.git/worktrees/wt";

const run = (command, args) =>
  new Promise((resolve, reject) =>
    execFile(command, args, { maxBuffer: 8 << 20 }, (error, stdout, stderr) => {
      if (error && typeof error.code !== "number") reject(error);
      else resolve({ code: error?.code ?? 0, stdout, stderr });
    }),
  );

const temps = [];
function tempDir() {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), "project-finder-")));
  temps.push(dir);
  return dir;
}
test.after(() => temps.forEach((dir) => rmSync(dir, { recursive: true, force: true })));

const makeRepo = (path) => (mkdirSync(join(path, ".git"), { recursive: true }), path);
function makeWorktree(path, gitdir) {
  mkdirSync(path, { recursive: true });
  writeFileSync(join(path, ".git"), `gitdir: ${gitdir}\n`);
  return path;
}
function touch(path, when) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, "x");
  utimesSync(path, when, when);
}

class Tree {
  constructor() {
    this.root = tempDir();
  }
  repo(rel) {
    return makeRepo(join(this.root, rel));
  }
  worktree(rel) {
    return makeWorktree(join(this.root, rel), POINTER);
  }
  at(rel) {
    return join(this.root, rel);
  }
}

const fixed = () => [...FIXED_WORKTREE_DIRS];
const find = async (tree, dirs, roots) => (await discover({ root: tree.root, dirs, roots }, run)).rows;
const paths = async (tree, dirs = fixed(), roots = []) => (await find(tree, dirs, roots)).map((r) => r.path);
const set = (list) => new Set(list);
const locationsFor = (setting) => worktreeLocations(setting, "/x/home");

test("a repo directly under the root is found", async () => {
  const tree = new Tree();
  const repo = tree.repo("myrepo");
  assert.deepEqual(await paths(tree), [repo]);
});

test("a repo inside a grouping dir is found", async () => {
  const tree = new Tree();
  const repo = tree.repo("group-a/myrepo");
  assert.deepEqual(await paths(tree), [repo]);
});

test("a herdr worktree three levels down is found", async () => {
  const tree = new Tree();
  const repo = tree.repo("myrepo");
  const wt = tree.worktree("worktrees/myrepo/feat-x");
  assert.deepEqual(set(await paths(tree)), set([repo, wt]));
});

test("a worktree found three levels down is tagged worktree", async () => {
  const tree = new Tree();
  tree.worktree("worktrees/myrepo/feat-x");
  assert.deepEqual((await find(tree, fixed(), [])).map((r) => r.kind), ["worktree"]);
});

test("a fourth level is not searched", async () => {
  const tree = new Tree();
  tree.repo("a/b/c/d");
  assert.deepEqual(await paths(tree), []);
});

test("a repo nested inside a repo is skipped", async () => {
  const tree = new Tree();
  const outer = tree.repo("myrepo");
  tree.repo("myrepo/vendor/pkg");
  const inner = tree.repo("group-a/repo");
  tree.repo("group-a/repo/sub");
  assert.deepEqual(set(await paths(tree)), set([outer, inner]));
});

test("a nested repo sorting ahead of its parent is still skipped", async () => {
  const tree = new Tree();
  const outer = tree.repo("a");
  tree.repo("a/-x");
  assert.deepEqual(await paths(tree), [outer]);
});

test("paths sort by the bytes of their names, as the Rust picker sorts them", () => {
  const [ascii, privateUse, emoji] = ["/r/a", "/r/\u{E000}", "/r/\u{1F600}"];
  assert.deepEqual([emoji, privateUse, ascii].sort(comparePaths), [ascii, privateUse, emoji]);
  assert.deepEqual(bandRepos("/r", [emoji, privateUse, ascii].map((p) => `${p}/.git`)), [ascii, privateUse, emoji]);
  assert.ok(comparePaths("/r/\u{FFFD}x", "/r/\u{10000}") < 0);
});

test("a sibling sharing a name prefix is not mistaken for nesting", async () => {
  const tree = new Tree();
  const one = tree.repo("myrepo");
  const two = tree.repo("myrepo_old");
  assert.deepEqual(set(await paths(tree)), set([one, two]));
});

test("a directory whose name begins with a dot is never searched", async () => {
  const tree = new Tree();
  tree.repo(".config/tool");
  tree.repo("group/.hidden/repo");
  assert.deepEqual(await paths(tree), []);
});

test("a worktree in herdr's own container is found", async () => {
  const tree = new Tree();
  const repo = tree.repo("myrepo");
  const wt = tree.worktree("myrepo/.worktrees/myrepo/feat-x");
  assert.deepEqual(set(await paths(tree)), set([repo, wt]));
});

test("a worktree in claude code's container is found", async () => {
  const tree = new Tree();
  const repo = tree.repo("myrepo");
  const wt = tree.worktree("myrepo/.claude/worktrees/slug-1a2b");
  assert.deepEqual(set(await paths(tree)), set([repo, wt]));
});

test("a worktree in the undotted container is found", async () => {
  const tree = new Tree();
  tree.repo("myrepo");
  const wt = tree.worktree("myrepo/worktrees/feat-x");
  assert.ok((await paths(tree)).includes(wt));
});

test("a nested worktree is found under a repo at any depth", async () => {
  const tree = new Tree();
  tree.repo("group/myrepo");
  const wt = tree.worktree("group/myrepo/.worktrees/myrepo/feat-x");
  assert.ok((await paths(tree)).includes(wt));
});

test("a nested worktree is tagged worktree", async () => {
  const tree = new Tree();
  tree.repo("myrepo");
  tree.worktree("myrepo/.worktrees/myrepo/feat-x");
  const nested = (await find(tree, fixed(), [])).filter((r) => r.path.includes(".worktrees")).map((r) => r.kind);
  assert.deepEqual(nested, ["worktree"]);
});

test("an ordinary repo inside a container is still skipped", async () => {
  const tree = new Tree();
  const repo = tree.repo("myrepo");
  tree.repo("myrepo/worktrees/vendored");
  assert.deepEqual(await paths(tree), [repo]);
});

test("a container under a repo that is itself nested is not searched", async () => {
  const tree = new Tree();
  const repo = tree.repo("myrepo");
  tree.repo("myrepo/vendor/pkg");
  tree.worktree("myrepo/vendor/pkg/.worktrees/pkg/feat-x");
  assert.deepEqual(await paths(tree), [repo]);
});

test("a container named by the app's setting is searched", async () => {
  const tree = new Tree();
  tree.repo("myrepo");
  const wt = tree.worktree("myrepo/trees/feat-x");
  const { dirs, roots } = locationsFor("trees");
  assert.ok((await paths(tree, dirs, roots)).includes(wt));
});

test("a worktree beside its repo is found and tagged worktree", async () => {
  const tree = new Tree();
  const repo = tree.repo("group-a/repo-one");
  const wt = tree.worktree("group-a/worktrees/repo-one/feat-x");
  const { dirs, roots } = locationsFor("../worktrees");
  const found = await find(tree, dirs, roots);
  assert.deepEqual(set(found.map((r) => r.path)), set([repo, wt]));
  assert.equal(found.find((r) => r.path === wt).kind, "worktree");
});

test("each repo resolves its own sibling container", async () => {
  const tree = new Tree();
  const one = tree.repo("group-a/repo-one");
  const two = tree.repo("group-b/repo-two");
  const x = tree.worktree("group-a/worktrees/repo-one/feat-x");
  const y = tree.worktree("group-b/worktrees/repo-two/feat-y");
  const { dirs, roots } = locationsFor("../worktrees");
  assert.deepEqual(set(await paths(tree, dirs, roots)), set([one, two, x, y]));
});

test("a sibling container the depth bands also reach lists once", async () => {
  const tree = new Tree();
  const repo = tree.repo("myrepo");
  const wt = tree.worktree("worktrees/myrepo/feat-x");
  const { dirs, roots } = locationsFor("../worktrees");
  const found = await discover({ root: tree.root, dirs, roots }, run);
  assert.deepEqual(found.rows.map((r) => r.path), [repo, wt]);
  assert.deepEqual([found.bands, found.containers], [2, 0]);
});

test("every worktree layout lists at once", async () => {
  const tree = new Tree();
  const one = tree.repo("group-a/repo-one");
  const two = tree.repo("repo-two");
  const a = tree.worktree("repo-two/.claude/worktrees/slug-1a2b");
  const b = tree.worktree("group-a/repo-one/.worktrees/repo-one/feat-old");
  const c = tree.worktree("worktrees/repo-two/feat-flat");
  const d = tree.worktree("group-a/worktrees/repo-one/feat-new");
  const { dirs, roots } = locationsFor("../worktrees");
  assert.deepEqual(set(await paths(tree, dirs, roots)), set([one, two, a, b, c, d]));
});

test("a bare parent container admits only worktrees", async () => {
  const tree = new Tree();
  const one = tree.repo("a/group/one");
  tree.repo("a/group/trees/vendored");
  const wt = tree.worktree("a/group/trees/feat-x");
  assert.deepEqual(set(await paths(tree, [...fixed(), ".."], [])), set([one, wt]));
});

test("an absolute worktree root outside the picker root is searched", async () => {
  const tree = new Tree();
  const flat = tempDir();
  const repo = tree.repo("myrepo");
  const wt = makeWorktree(join(flat, "myrepo/feat-x"), POINTER);
  assert.deepEqual(set(await paths(tree, fixed(), [flat])), set([repo, wt]));
});

test("a worktree reached twice is listed once", async () => {
  const tree = new Tree();
  const repo = tree.repo("myrepo");
  const wt = tree.worktree("worktrees/myrepo/feat-x");
  assert.deepEqual(await paths(tree, fixed(), [tree.at("worktrees")]), [repo, wt]);
});

test("each pass reports what it contributed, and the two account for every row", async () => {
  const tree = new Tree();
  tree.repo("myrepo");
  tree.worktree("worktrees/myrepo/feat-x");
  tree.worktree("myrepo/.worktrees/myrepo/feat-y");
  const found = await discover({ root: tree.root, dirs: fixed(), roots: [] }, run);
  assert.equal(found.rows.length, 3);
  assert.deepEqual([found.bands, found.containers], [2, 1]);
});

test("nothing found at all reports two zeroes", async () => {
  const tree = new Tree();
  const found = await discover({ root: tree.root, dirs: fixed(), roots: [] }, run);
  assert.deepEqual([found.rows.length, found.bands, found.containers], [0, 0, 0]);
});

test("a root that does not exist finds nothing rather than failing", async () => {
  const found = await discover({ root: join(tempDir(), "gone"), dirs: fixed(), roots: [] }, run);
  assert.deepEqual(found.rows, []);
});

test("a worktree reached through a symlinked container is found", async () => {
  const tree = new Tree();
  const repo = tree.repo("myrepo");
  const elsewhere = tempDir();
  makeWorktree(join(elsewhere, "group/repo-one/feat-x"), POINTER);
  symlinkSync(join(elsewhere, "group"), tree.at("worktrees"));
  const found = await paths(tree, fixed(), [tree.at("worktrees")]);
  assert.ok(found.includes(repo));
  assert.equal(found.length, 2, String(found));
  assert.equal(realpathSync(found[1]), join(elsewhere, "group/repo-one/feat-x"));
});

test("a directory dot git is a repo and a file dot git is a worktree", async () => {
  const tree = new Tree();
  tree.repo("proj");
  makeWorktree(tree.at("wt"), "/x/deleted/.git/worktrees/wt");
  const kinds = Object.fromEntries((await find(tree, fixed(), [])).map((r) => [r.path, r.kind]));
  assert.deepEqual(kinds, { [tree.at("proj")]: "repo", [tree.at("wt")]: "worktree" });
});

test("a worktree row names the repository its pointer leads to", async () => {
  const tree = new Tree();
  const repo = tree.repo("myrepo");
  makeWorktree(tree.at("myrepo/.worktrees/myrepo/feat-x"), `${repo}/.git/worktrees/feat-x`);
  makeWorktree(tree.at("rel"), "../myrepo/.git/worktrees/rel");
  const found = await find(tree, fixed(), []);
  assert.equal(found.find((r) => r.path === repo).repo, null);
  assert.equal(found.find((r) => r.path.endsWith("feat-x")).repo, repo);
  assert.equal(found.find((r) => r.path === tree.at("rel")).repo, repo);
});

test("the fixed containers are always searched, and an absent setting adds the app's default root", () => {
  assert.deepEqual(worktreeLocations(undefined, "/x/home"), { dirs: fixed(), roots: ["/x/home/.herdr/worktrees"] });
  assert.ok(FIXED_WORKTREE_DIRS.includes(".worktrees"));
  assert.ok(FIXED_WORKTREE_DIRS.includes(".claude/worktrees"));
});

test("a relative setting joins the containers once", () => {
  assert.deepEqual(locationsFor("trees"), { dirs: [...fixed(), "trees"], roots: [] });
  assert.deepEqual(locationsFor("./.worktrees").dirs, fixed());
  assert.deepEqual(locationsFor("../trees"), { dirs: [...fixed(), "../trees"], roots: [] });
});

test("an absolute or home-relative setting becomes a flat root", () => {
  assert.deepEqual(locationsFor("/srv/worktrees/"), { dirs: fixed(), roots: ["/srv/worktrees"] });
  assert.deepEqual(locationsFor("~/Developer/worktrees"), { dirs: fixed(), roots: ["/x/home/Developer/worktrees"] });
});

test("an empty, unreadable or non-string setting degrades to the fixed containers", () => {
  for (const setting of ["   ", false, ["a", "b"], 3]) assert.deepEqual(locationsFor(setting), { dirs: fixed(), roots: [] });
});

async function touchedWith(build, dirs = fixed()) {
  const dir = tempDir();
  const repo = makeRepo(join(dir, "myrepo"));
  build(repo);
  const found = await discover({ root: dir, dirs, roots: [] }, run);
  return found.rows.find((r) => r.path === repo).touched;
}

test("the newest working tree file is reported", async () => {
  assert.equal(await touchedWith((repo) => (touch(join(repo, "old.txt"), 1000), touch(join(repo, "src/new.txt"), 2000))), 2000);
});

test("files three levels down count and files four levels down do not", async () => {
  assert.equal(await touchedWith((repo) => (touch(join(repo, "own.txt"), 1000), touch(join(repo, "a/b/c.txt"), 3000), touch(join(repo, "a/b/c/d.txt"), 9000))), 3000);
});

test("a nested worktree's files do not count as the parent's touch", async () => {
  assert.equal(await touchedWith((repo) => (touch(join(repo, "own.txt"), 1000), touch(join(repo, "worktrees/feat-x/README.md"), 9000))), 1000);
});

test("herdr's own container and claude's container are not walked", async () => {
  assert.equal(await touchedWith((repo) => (touch(join(repo, "own.txt"), 1000), touch(join(repo, ".worktrees/myrepo/stamp"), 9000))), 1000);
  assert.equal(await touchedWith((repo) => (touch(join(repo, "own.txt"), 1000), touch(join(repo, ".claude/worktrees/stamp"), 9000))), 1000);
});

test("the prune is by path, so the rest of dot claude still counts", async () => {
  assert.equal(await touchedWith((repo) => (touch(join(repo, "own.txt"), 1000), touch(join(repo, ".claude/settings.json"), 9000))), 9000);
});

test("a configured container is pruned as well", async () => {
  assert.equal(await touchedWith((repo) => (touch(join(repo, "own.txt"), 1000), touch(join(repo, "trees/b/f.txt"), 9000)), ["trees"]), 1000);
});

test("node_modules, vendor and git directories are not walked, but files with those names count", async () => {
  assert.equal(await touchedWith((repo) => (touch(join(repo, "own.txt"), 1000), touch(join(repo, "node_modules/x/f"), 9000), touch(join(repo, "vendor/f"), 9000), touch(join(repo, ".git/objects/f"), 9000))), 1000);
  assert.equal(await touchedWith((repo) => (touch(join(repo, "own.txt"), 1000), touch(join(repo, "src/vendor"), 4000))), 4000);
});

test("a directory merely sharing a prefix is not pruned", async () => {
  assert.equal(await touchedWith((repo) => touch(join(repo, "worktrees-notes/f.txt"), 9000)), 9000);
});

test("the same container name somewhere else in the tree still counts", async () => {
  assert.equal(await touchedWith((repo) => touch(join(repo, "src/worktrees/f.txt"), 9000)), 9000);
});

test("a container path holding glob characters is pruned by its literal path", async () => {
  assert.equal(await touchedWith((repo) => (touch(join(repo, "own.txt"), 1000), touch(join(repo, "[t]*/f.txt"), 9000), touch(join(repo, "t/f.txt"), 2000)), ["[t]*"]), 2000);
});

test("the git index still counts", async () => {
  assert.equal(await touchedWith((repo) => (touch(join(repo, ".git/index"), 5000), touch(join(repo, "own.txt"), 1000))), 5000);
});

test("a repo with no files at all reports nothing", async () => {
  assert.equal(await touchedWith(() => {}), 0);
});

test("a path holding a quote or a space reaches the shell intact", async () => {
  const dir = tempDir();
  const repo = makeRepo(join(dir, "it's a repo"));
  touch(join(repo, "f.txt"), 7000);
  const found = await discover({ root: dir, dirs: fixed(), roots: [] }, run);
  assert.deepEqual(found.rows.map((r) => [r.path, r.touched]), [[repo, 7000]]);
  assert.equal(shellQuote("it's"), `'it'\\''s'`);
});

test("each row is walked exactly once", () => {
  const script = factsScript(["/a", "/b", "/c"], fixed());
  assert.deepEqual(script.split("\n").filter((l) => l.startsWith("row ")).map((l) => l.split(" ")[1]), ["'/a'", "'/b'", "'/c'"]);
});

test("a row the shell does not describe fails rather than passing unread", async () => {
  const tree = new Tree();
  tree.repo("myrepo");
  const silent = (command, args) => (command === "sh" ? Promise.resolve({ code: 0, stdout: "" }) : run(command, args));
  await assert.rejects(discover({ root: tree.root, dirs: fixed(), roots: [] }, silent), /Could not read/);
});

const derive = (pointer, checkout = "/x/code/feat-x") => parentRepo(checkout, pointer);

test("each worktree layout yields its parent", () => {
  assert.equal(derive("gitdir: /x/code/group-a/repo-one/.git/worktrees/feat-x\n"), "/x/code/group-a/repo-one");
  assert.equal(derive("gitdir: /x/code/group-b/repo-two/.git/worktrees/feat-y\n"), "/x/code/group-b/repo-two");
  assert.equal(derive("gitdir: /x/code/repo-three/.git/worktrees/slug-1a2b\n"), "/x/code/repo-three");
});

test("a relative pointer is resolved against the checkout", () => {
  assert.equal(derive("gitdir: ../myrepo/.git/worktrees/feat-x\n"), "/x/code/myrepo");
});

test("pointers that are not a linked worktree's yield none", () => {
  for (const pointer of [
    "gitdir: /x/super/.git/modules/sub\n",
    "gitdir: /x/parent/git/worktrees/wt\n",
    "gitdir: /x/parent/.git\n",
    "worktreedir: /x/parent/.git/worktrees/wt\n",
    "gitdir:   \n",
    "",
    null,
  ]) assert.equal(derive(pointer), null, String(pointer));
});

test("whitespace, extra lines and a trailing separator are tolerated", () => {
  assert.equal(derive("gitdir:  /x/parent/.git/worktrees/wt  \n"), "/x/parent");
  assert.equal(derive("gitdir: /x/parent/.git/worktrees/wt\n/x/other/.git/worktrees/wt2\n"), "/x/parent");
  assert.equal(derive("gitdir: /x/parent/.git/worktrees/wt/\n"), "/x/parent");
});

test("eliding cuts by character, keeps the front and never exceeds the width", () => {
  assert.equal(elide("proj", 10), "proj");
  assert.equal(elide("0123456789", 10), "0123456789");
  assert.equal(elide("0123456789x", 10), "012345678…");
  for (let n = 1; n < 60; n++) assert.ok([...elide("x".repeat(n), 10)].length <= 10);
  assert.ok(elide("group-a/a-project-with-a-very-long-name", 12).startsWith("group-a/"));
  assert.equal(elide("ααααα", 3), "αα…");
});

test("ages are reported in the largest unit that fits", () => {
  assert.equal(humanAge(0, 100), "-");
  assert.equal(humanAge(100, 100), "0m ago");
  assert.equal(humanAge(100, 100 + 3599), "59m ago");
  assert.equal(humanAge(100, 100 + 3600), "1h ago");
  assert.equal(humanAge(100, 100 + 86399), "23h ago");
  assert.equal(humanAge(100, 100 + 86400), "1d ago");
  assert.equal(humanAge(100, 100 + 604799), "6d ago");
  assert.equal(humanAge(100, 100 + 604800), "1w ago");
});

const labels = (list) => labelled(list).map((r) => r.label);

test("a duplicated basename is qualified by its parent", () => {
  assert.deepEqual(labels(["/x/group-a/api", "/x/group-b/api", "/x/solo"]), ["group-a/api", "group-b/api", "solo"]);
});

test("a parent that is shared too is qualified further until the labels differ", () => {
  assert.deepEqual(labels(["/x/alpha/worktrees/slug", "/x/beta/worktrees/slug", "/x/gamma/worktrees/slug"]), [
    "alpha/worktrees/slug",
    "beta/worktrees/slug",
    "gamma/worktrees/slug",
  ]);
});

test("only the paths that collide are qualified", () => {
  assert.deepEqual(labels(["/x/alpha/worktrees/slug", "/x/beta/worktrees/slug", "/x/api"]), ["alpha/worktrees/slug", "beta/worktrees/slug", "api"]);
});

test("a path one of the others ends with is still told apart", () => {
  assert.deepEqual(labels(["/x/alpha/api", "/alpha/api"]), ["x/alpha/api", "/alpha/api"]);
});

test("every label a run hands out is its own", () => {
  const listed = labels(["/x/alpha/worktrees/slug", "/x/beta/worktrees/slug", "/y/alpha/worktrees/slug", "/x/alpha/slug", "/x/slug"]);
  assert.equal(new Set(listed).size, listed.length, String(listed));
});

test("the tails of a path run from its name up to the whole of it", () => {
  assert.deepEqual(tails("/x/group-a/api"), ["api", "group-a/api", "x/group-a/api", "/x/group-a/api"]);
  assert.deepEqual(tails("group-a/api"), ["api", "group-a/api"]);
});

test("labelling keeps the paths in order", () => {
  assert.deepEqual(labelled(["/x/b", "/x/a"]), [{ label: "b", path: "/x/b" }, { label: "a", path: "/x/a" }]);
});

test("normalising a path collapses dots without touching the disk", () => {
  assert.equal(normpath("./.worktrees"), ".worktrees");
  assert.equal(normpath("../trees"), "../trees");
  assert.equal(normpath("/srv/worktrees/"), "/srv/worktrees");
  assert.equal(normpath("a//b/../c"), "a/c");
  assert.equal(normpath(".."), "..");
});
