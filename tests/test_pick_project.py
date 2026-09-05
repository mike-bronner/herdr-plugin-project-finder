"""Unit tests for bin/pick-project's pure selection logic.

Run: python3 -m unittest discover tests
"""
import importlib.machinery, importlib.util, io, os, sys, tempfile, unittest
from unittest import mock

# Must be set before the loader below runs. A .pyc is treated as valid while
# the source's (mtime truncated to whole seconds, byte size) is unchanged, so
# editing the script to a same-size version inside one second makes this suite
# silently execute the PREVIOUS code. macOS system python3 hides the evidence:
# it sets sys.pycache_prefix to ~/Library/Caches/com.apple.python, so the cache
# lives outside the repo and `find . -name '*.pyc'` reports nothing. Writing no
# cache at all removes the failure mode; recompiling costs about a millisecond.
# A cache written before this line existed is still read until the source mtime
# next advances, so delete any stale one once under sys.pycache_prefix.
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "pick-project")
loader = importlib.machinery.SourceFileLoader("pick_project", SCRIPT)
spec = importlib.util.spec_from_loader("pick_project", loader)
pp = importlib.util.module_from_spec(spec)
loader.exec_module(pp)


def ws(label, wid, focused=False, status="idle"):
    return {"label": label, "workspace_id": wid, "focused": focused,
            "agent_status": status}


class OpenWorkspaces(unittest.TestCase):
    def test_home_is_split_out_and_never_in_others(self):
        listing = {"workspaces": [ws("~", "w1"), ws("a", "w2"), ws("b", "w3")]}
        with mock.patch.object(pp, "herdr", return_value=listing):
            home, others = pp.open_workspaces()
        self.assertEqual(home["workspace_id"], "w1")
        self.assertEqual([w["label"] for w in others], ["a", "b"])

    def test_no_home_open(self):
        with mock.patch.object(pp, "herdr", return_value={"workspaces": [ws("a", "w2")]}):
            home, others = pp.open_workspaces()
        self.assertIsNone(home)
        self.assertEqual(len(others), 1)

    def test_server_unreachable_is_empty(self):
        with mock.patch.object(pp, "herdr", return_value=None):
            self.assertEqual(pp.open_workspaces(), (None, []))


class OrderRows(unittest.TestCase):
    def test_open_first_in_sidebar_order_then_by_touch(self):
        labelled = [("a", "/a"), ("b", "/b"), ("c", "/c"), ("d", "/d")]
        open_ws = [ws("c", "w1"), ws("a", "w2"), ws("ghost", "w3")]
        touched = {"/a": 30, "/b": 10, "/c": 40, "/d": 20}
        with mock.patch.object(pp, "touched_at", side_effect=lambda p: touched[p]):
            head, rest = pp.order_rows(labelled, open_ws)
        # Sidebar order, ghost skipped. Note /a is newer than /c yet stays
        # second: open rows are never re-sorted by touch time.
        self.assertEqual(head, [("c", "/c", 40), ("a", "/a", 30)])
        self.assertEqual(rest, [("d", "/d", 20), ("b", "/b", 10)])   # newest first

    def test_nothing_open(self):
        with mock.patch.object(pp, "touched_at", return_value=0):
            head, rest = pp.order_rows([("a", "/a")], [])
        self.assertEqual(head, [])
        self.assertEqual(rest, [("a", "/a", 0)])

    def test_equal_touch_times_keep_input_order(self):
        # The sort key is the timestamp alone, so ties fall back to the order
        # repos() yielded. A key including the label or path would reorder them.
        labelled = [("b", "/b"), ("a", "/a")]
        with mock.patch.object(pp, "touched_at", return_value=7):
            _, rest = pp.order_rows(labelled, [])
        self.assertEqual([l for l, _, _ in rest], ["b", "a"])

    def test_open_rows_carry_their_touch_time(self):
        # An open row's timestamp is not needed for the ordering, only for the
        # TOUCHED column. Returning 0 or None here would blank that column for
        # exactly the projects the user is most likely looking at.
        with mock.patch.object(pp, "touched_at", return_value=99):
            head, _ = pp.order_rows([("a", "/a")], [ws("a", "w1")])
        self.assertEqual(head, [("a", "/a", 99)])


class PreselectBind(unittest.TestCase):
    def test_zero_open_adds_no_bind(self):
        self.assertEqual(pp.preselect_bind(0), [])

    def test_marks_first_n_then_returns_cursor_to_top(self):
        self.assertEqual(pp.preselect_bind(2),
                         ["--bind", "load:pos(1)+select+pos(2)+select+pos(1)"])


class ParseSelection(unittest.TestCase):
    def test_escape_is_cancel_even_with_stray_output(self):
        self.assertIsNone(pp.parse_selection(130, "a  1m ago \t/x/a\n"))

    def test_sentinel_means_deliberately_empty(self):
        self.assertEqual(pp.parse_selection(0, f"{pp.EMPTY}\n"), [])

    def test_sentinel_wins_over_cursor_line(self):
        # fzf still prints the highlighted row when nothing is selected.
        out = f"{pp.EMPTY}\nzed-laravel   3m ago  \t/x/zed-laravel\n"
        self.assertEqual(pp.parse_selection(0, out), [])

    def test_paths_extracted(self):
        out = "a  1m ago \t/x/a\nb  2m ago \t/x/b\n"
        self.assertEqual(pp.parse_selection(0, out), ["/x/a", "/x/b"])

    def test_trailing_label_field_is_not_glued_onto_the_path(self):
        # Rows carry a third field (the untruncated label) after the path. A
        # maxsplit=1 split would yield "/x/a\ta-very-long-name" as the path.
        out = "a-very-long-na…  repo  1m ago \t/x/a\ta-very-long-name\n"
        self.assertEqual(pp.parse_selection(0, out), ["/x/a"])

    def test_no_match_without_sentinel_is_cancel(self):
        self.assertIsNone(pp.parse_selection(1, ""))

    def test_heading_line_is_never_a_path(self):
        # --header-lines keeps fzf from returning it, but a line whose path
        # field is empty must never become a workspace even if one slips out.
        self.assertIsNone(pp.parse_selection(0, pp.HEADING + "\n"))

    def test_heading_alongside_real_rows_is_dropped(self):
        out = pp.HEADING + "\na  1m ago \t/x/a\n"
        self.assertEqual(pp.parse_selection(0, out), ["/x/a"])


class Plan(unittest.TestCase):
    def test_deselected_closed_new_created_kept_untouched(self):
        open_ws = [ws("a", "w1"), ws("b", "w2")]
        to_close, to_create = pp.plan(["b", "c"], open_ws)
        self.assertEqual([w["label"] for w in to_close], ["a"])
        self.assertEqual(to_create, ["c"])

    def test_empty_selection_closes_everything(self):
        open_ws = [ws("a", "w1"), ws("stray", "w2")]
        to_close, to_create = pp.plan([], open_ws)
        self.assertEqual([w["label"] for w in to_close], ["a", "stray"])
        self.assertEqual(to_create, [])

    def test_unchanged_selection_is_a_noop(self):
        open_ws = [ws("a", "w1")]
        self.assertEqual(pp.plan(["a"], open_ws), ([], []))


class PickFocus(unittest.TestCase):
    def test_created_wins(self):
        self.assertEqual(pp.pick_focus(["n1", "n2"], ["w1"], "w1", ["w9"], "h"), "n2")

    def test_focused_closed_falls_back_to_first_surviving(self):
        self.assertEqual(pp.pick_focus([], ["w1"], "w1", ["w5", "w6"], "h"), "w5")

    def test_focused_closed_nothing_left_goes_home(self):
        self.assertEqual(pp.pick_focus([], ["w1"], "w1", [], "h"), "h")

    def test_unchanged_leaves_focus_alone(self):
        self.assertIsNone(pp.pick_focus([], ["w2"], "w1", ["w1"], "h"))


class LiveAgentNames(unittest.TestCase):
    """Which of Herdr's listed agents hold a name a new `agent start` collides with.

    `name` is optional on AgentInfo. Herdr OMITS the key for an agent it merely
    detected rather than one started under a name, so most entries in a real
    listing carry no name at all.
    """

    def names(self, *agents):
        """live_agent_names() over an `agent list` carrying these agents."""
        with mock.patch.object(pp, "herdr", return_value={"agents": list(agents)}):
            return pp.live_agent_names()

    def test_named_agents_are_collected(self):
        self.assertEqual(self.names({"name": "alpha"}, {"name": "beta"}),
                         {"alpha", "beta"})

    def test_an_agent_with_no_name_key_contributes_nothing(self):
        # Reading the field with a bare .get() puts None in the set. None is
        # not a name and no generated name equals it, so the set would only
        # LOOK like it deduplicates while never matching anything.
        self.assertEqual(self.names({"pane_id": "w1:p1"}), set())

    def test_an_explicitly_null_name_contributes_nothing(self):
        # The 0.8.2 schema types name as ["string", "null"], so a null is legal
        # on the wire even though the serializer drops the key instead.
        self.assertEqual(self.names({"name": None}), set())

    def test_named_and_unnamed_agents_mix_in_one_listing(self):
        # The real shape: agents this picker started keep their names, agents
        # typed into a pane by hand or brought back by resume_agents_on_restore
        # do not, and both are listed together.
        self.assertEqual(
            self.names({"name": "alpha"}, {"pane_id": "w2:p1"}, {"name": "beta"}),
            {"alpha", "beta"})

    def test_an_unreachable_server_yields_no_names_rather_than_raising(self):
        # herdr() answers None on any failure. Failing OPEN here is deliberate:
        # the cost is one `agent start` possibly refused, against a picker that
        # opens nothing at all because it could not resolve a dedupe.
        with mock.patch.object(pp, "herdr", return_value=None):
            self.assertEqual(pp.live_agent_names(), set())

    def test_a_listing_with_no_agents_key_yields_no_names(self):
        with mock.patch.object(pp, "herdr", return_value={}):
            self.assertEqual(pp.live_agent_names(), set())

    def test_the_names_come_from_the_agent_list_command(self):
        # Not `workspace list`: an open workspace says nothing about whether a
        # live agent holds the name derived from its label.
        with mock.patch.object(pp, "herdr", return_value={"agents": []}) as h:
            pp.live_agent_names()
        self.assertEqual([c.args for c in h.call_args_list], [("agent", "list")])


class AgentName(unittest.TestCase):
    """A label turned into a Herdr agent name, and the dedupe against live ones.

    Herdr refuses `agent start` under a name a live agent already holds (error
    agent_name_taken). create_workspace() fires that command detached, so the
    refusal is never read: the workspace opens and the pane simply never gets
    its Claude. Every case here is what stops that happening.
    """

    def test_a_plain_label_passes_through_lowercased(self):
        self.assertEqual(pp.agent_name("MyRepo", set()), "myrepo")

    def test_illegal_characters_become_hyphens(self):
        # A duplicated basename is labelled "<parent>/<name>", and a dot is
        # legal in a directory name but not in an agent name.
        self.assertEqual(pp.agent_name("forks/my.repo", set()), "forks-my-repo")

    def test_leading_and_trailing_hyphens_are_stripped(self):
        self.assertEqual(pp.agent_name(".hidden.", set()), "hidden")

    def test_a_label_with_no_legal_characters_falls_back_to_agent(self):
        # The strip can empty the name outright, and the first-character test
        # below indexes it — without the fallback this raises instead of
        # opening the workspace.
        self.assertEqual(pp.agent_name("...", set()), "agent")

    def test_a_name_starting_with_a_digit_is_prefixed(self):
        # Herdr's grammar is [a-z][a-z0-9_-]{0,31}: the first character must be
        # a letter, and "2fa-service" is a perfectly ordinary directory name.
        self.assertEqual(pp.agent_name("2fa-service", set()), "a2fa-service")

    def test_an_underscore_start_is_prefixed_too(self):
        # "_" is legal in the tail but not in the first position, so testing
        # only the digit case would leave this one through.
        self.assertEqual(pp.agent_name("_private", set()), "a_private")

    def test_a_long_label_is_cut_to_the_32_character_limit(self):
        # A real Herdr worktree label, and the name the picker gave it.
        name = pp.agent_name("extract-greek-lemmas-from-lexicon", set())
        self.assertEqual(name, "extract-greek-lemmas-from-lexico")
        self.assertEqual(len(name), 32)

    def test_a_taken_name_gains_a_numeric_suffix(self):
        self.assertEqual(pp.agent_name("myrepo", {"myrepo"}), "myrepo-2")

    def test_the_suffix_counts_up_until_it_finds_a_free_name(self):
        taken = {"myrepo", "myrepo-2", "myrepo-3"}
        self.assertEqual(pp.agent_name("myrepo", taken), "myrepo-4")

    def test_a_suffixed_name_still_fits_the_32_character_limit(self):
        # The suffix replaces the tail rather than extending it. Appending
        # would make the deduped name the one thing Herdr rejects on length.
        taken = {pp.agent_name("a" * 40, set())}
        for _ in range(9):
            self.assertEqual(len(pp.agent_name("a" * 40, taken)), 32)

    def test_a_two_digit_suffix_takes_the_extra_character_it_needs(self):
        # The cut is 32 minus the suffix LENGTH, not a fixed 30, so the tenth
        # collision has to give up one more character than the ninth.
        taken = {"a" * 32} | {"a" * 30 + f"-{i}" for i in range(2, 10)}
        self.assertEqual(pp.agent_name("a" * 40, taken), "a" * 29 + "-10")

    def test_the_chosen_name_is_reserved_in_the_taken_set(self):
        # One picker run creates several workspaces from one set, and each
        # agent start is detached, so a new name does not reach `agent list`
        # before the next create reads it. Reserving here is the only thing
        # keeping two labels that normalise together apart.
        taken = set()
        first = pp.agent_name("my.repo", taken)
        second = pp.agent_name("my-repo", taken)
        self.assertEqual((first, second), ("my-repo", "my-repo-2"))

    def test_labels_differing_only_past_the_cut_are_still_distinguished(self):
        # The collision the picker actually meets: Herdr's worktree labels are
        # long and share their repo-name prefix, so they truncate together.
        taken = set()
        a = pp.agent_name("bible-models-update-for-migrations-step-one", taken)
        b = pp.agent_name("bible-models-update-for-migrations-step-two", taken)
        self.assertEqual(a, "bible-models-update-for-migratio")
        self.assertEqual(b, "bible-models-update-for-migrat-2")


class CreateWorkspace(unittest.TestCase):
    RES = {"workspace": {"workspace_id": "w9"}, "tab": {"tab_id": "w9:t1"},
           "root_pane": {"pane_id": "p1"}}

    def run_create(self, res):
        with mock.patch.object(pp, "herdr", return_value=res) as h, \
             mock.patch.object(pp.subprocess, "Popen"), \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            wid = pp.create_workspace("proj", "/x/proj", set())
        return wid, [c.args for c in h.call_args_list]

    def agent_start_argv(self, label, live):
        """The detached `herdr agent start` argv this fires for `label`."""
        with mock.patch.object(pp, "herdr", return_value=self.RES), \
             mock.patch.object(pp.subprocess, "Popen") as popen, \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            pp.create_workspace(label, "/x/proj", live)
        return popen.call_args.args[0]

    def test_first_tab_is_renamed_agent(self):
        wid, calls = self.run_create(self.RES)
        self.assertEqual(wid, "w9")
        self.assertIn(("tab", "rename", "w9:t1", "agent"), calls)
        # Rename happens before the split so the tab is named as it appears.
        self.assertLess(calls.index(("tab", "rename", "w9:t1", "agent")),
                        next(i for i, c in enumerate(calls) if c[:2] == ("pane", "split")))

    def test_no_tab_id_skips_rename_but_still_builds_layout(self):
        res = {k: v for k, v in self.RES.items() if k != "tab"}
        wid, calls = self.run_create(res)
        self.assertEqual(wid, "w9")
        self.assertFalse(any(c[:2] == ("tab", "rename") for c in calls))
        self.assertTrue(any(c[:2] == ("pane", "split") for c in calls))

    def test_create_failure_dies(self):
        with self.assertRaises(SystemExit):
            self.run_create(None)

    def test_the_agent_is_started_under_the_deduped_name(self):
        # The dedupe is decorative unless the deduped name is the one that
        # reaches `agent start`; the raw label would collide instead.
        argv = self.agent_start_argv("my-proj", {"my-proj"})
        self.assertEqual(argv[1:4], ["agent", "start", "my-proj-2"])

    def test_the_name_is_reserved_so_the_next_workspace_cannot_reuse_it(self):
        # Two creates out of one live set, which is how main() runs a
        # multi-select. Nothing else keeps the second off the first's name.
        live = set()
        self.assertEqual(self.agent_start_argv("my-proj", live)[3], "my-proj")
        self.assertEqual(self.agent_start_argv("my-proj", live)[3], "my-proj-2")


class LoadEnv(unittest.TestCase):
    def load(self, body, env=None):
        """Write `body` to a .env, load it over `env`, return the resulting environ."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            with mock.patch.dict(os.environ, env or {}, clear=True):
                pp.load_env(path)
                return dict(os.environ)

    def test_file_values_are_applied(self):
        env = self.load("HERDR_PICKER_ROOT=/x/code\nHERDR_PICKER_HOME=base\n")
        self.assertEqual(env["HERDR_PICKER_ROOT"], "/x/code")
        self.assertEqual(env["HERDR_PICKER_HOME"], "base")

    def test_real_env_overrides_the_file(self):
        env = self.load("HERDR_PICKER_ROOT=/from/file\n",
                        {"HERDR_PICKER_ROOT": "/from/env"})
        self.assertEqual(env["HERDR_PICKER_ROOT"], "/from/env")

    def test_missing_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {}, clear=True):
                pp.load_env(os.path.join(d, ".env"))   # never created
                self.assertEqual(dict(os.environ), {})

    def test_unreadable_path_is_a_noop(self):
        # A directory where a file is expected: OSError, not a crash.
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {}, clear=True):
                pp.load_env(d)
                self.assertEqual(dict(os.environ), {})

    def test_comments_and_blank_lines_are_skipped(self):
        # Asserts the whole environ: a commented line that still contains "="
        # would otherwise land as the junk key "# HERDR_PICKER_ROOT", which a
        # bare assertNotIn("HERDR_PICKER_ROOT") would not catch.
        env = self.load("# HERDR_PICKER_ROOT=/commented\n\n"
                        "   # indented comment\n"
                        "HERDR_PICKER_HOME=base\n")
        self.assertEqual(env, {"HERDR_PICKER_HOME": "base"})

    def test_quotes_are_stripped_but_only_matching_pairs(self):
        env = self.load('A="/x/one"\nB=\'/x/two\'\nC="/x/three\nD=""\n')
        self.assertEqual(env["A"], "/x/one")
        self.assertEqual(env["B"], "/x/two")
        self.assertEqual(env["C"], '"/x/three')   # unbalanced: left alone
        self.assertEqual(env["D"], "")

    def test_hash_inside_a_value_is_not_a_comment(self):
        self.assertEqual(self.load("A=/x/a#b\n")["A"], "/x/a#b")

    def test_surrounding_whitespace_is_trimmed(self):
        self.assertEqual(self.load("  A = /x/a  \n")["A"], "/x/a")

    def test_first_equals_wins_so_values_may_contain_one(self):
        self.assertEqual(self.load("A=k=v\n")["A"], "k=v")

    def test_malformed_line_is_skipped_and_later_lines_still_apply(self):
        # Whole-environ again: without the "=" check the typo'd line becomes the
        # key "HERDR_PICKER_ROOT /x/typo" rather than being dropped.
        env = self.load("HERDR_PICKER_ROOT /x/typo\nHERDR_PICKER_HOME=base\n")
        self.assertEqual(env, {"HERDR_PICKER_HOME": "base"})

    def test_empty_key_is_skipped(self):
        self.assertEqual(self.load("=/x/a\nA=/x/b\n"), {"A": "/x/b"})


class ResolveRoot(unittest.TestCase):
    def test_existing_configured_root_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"HERDR_PICKER_ROOT": d}, clear=True):
                self.assertEqual(pp.resolve_root(), d)

    def test_nonexistent_root_falls_back_to_home(self):
        with mock.patch.dict(os.environ, {"HERDR_PICKER_ROOT": "/x/does/not/exist"},
                             clear=True):
            self.assertEqual(pp.resolve_root(), os.path.expanduser("~"))

    def test_unset_uses_the_home_folder(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pp.resolve_root(), os.path.expanduser("~"))

    def test_empty_root_is_treated_as_unset(self):
        with mock.patch.dict(os.environ, {"HERDR_PICKER_ROOT": ""}, clear=True):
            self.assertEqual(pp.resolve_root(), os.path.expanduser("~"))

    def test_tilde_is_expanded(self):
        # .env values are never shell-expanded, so "~/x" arrives literally.
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "Code")
            os.mkdir(sub)
            with mock.patch.dict(os.environ, {"HOME": d, "HERDR_PICKER_ROOT": "~/Code"},
                                 clear=True):
                self.assertEqual(pp.resolve_root(), sub)

    def test_unexpanded_tilde_would_not_be_a_directory_and_falls_back(self):
        # Guards the expansion above: without it "~/Code" is a relative path
        # that does not exist, so the result would be home, not the real dir.
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "Code"))
            with mock.patch.dict(os.environ, {"HOME": d, "HERDR_PICKER_ROOT": "~/Nope"},
                                 clear=True):
                self.assertEqual(pp.resolve_root(), d)


class Repos(unittest.TestCase):
    """Discovery under the root: how deep it reaches, and what it skips.

    repos() reads the module-level DEV, which is bound at import, so each case
    builds a scratch root and patches DEV at it.
    """

    def discover(self, repos=(), worktrees=()):
        """(root, repos()) for a scratch root holding these projects.

        Both arguments are paths relative to the root, at any depth. A `repos`
        entry gets a .git DIRECTORY and a `worktrees` entry gets a .git FILE
        with a gitdir: pointer, which is the difference kind() reads, so a
        discovered path can be handed straight to it.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for rel in repos:
            os.makedirs(os.path.join(tmp.name, rel, ".git"))
        for rel in worktrees:
            os.makedirs(os.path.join(tmp.name, rel))
            with open(os.path.join(tmp.name, rel, ".git"), "w", encoding="utf-8") as f:
                f.write("gitdir: /x/parent/.git/worktrees/wt\n")
        with mock.patch.object(pp, "DEV", tmp.name):
            return tmp.name, pp.repos()

    def test_a_repo_directly_under_the_root_is_found(self):
        root, found = self.discover(repos=["myrepo"])
        self.assertEqual(found, [os.path.join(root, "myrepo")])

    def test_a_repo_inside_a_grouping_dir_is_found(self):
        root, found = self.discover(repos=["forks/myrepo"])
        self.assertEqual(found, [os.path.join(root, "forks", "myrepo")])

    def test_a_herdr_worktree_three_levels_down_is_found(self):
        # Herdr creates worktrees at <worktrees.directory>/<repo>/<branch-slug>,
        # which is three levels under the root on the default layout. Two levels
        # of globbing never reached them.
        root, found = self.discover(repos=["myrepo"],
                                    worktrees=["worktrees/myrepo/feat-x"])
        self.assertEqual(sorted(found),
                         sorted([os.path.join(root, "myrepo"),
                                 os.path.join(root, "worktrees", "myrepo", "feat-x")]))

    def test_a_worktree_found_three_levels_down_is_tagged_worktree(self):
        # The KIND column is the point of finding them, so the classification
        # is pinned on a path discovery actually produced, not a handmade one.
        root, found = self.discover(worktrees=["worktrees/myrepo/feat-x"])
        self.assertEqual([pp.kind(p) for p in found], ["worktree"])

    def test_a_fourth_level_is_not_searched(self):
        # Bounds the depth, so gaining a fifth stat pass stays a deliberate act.
        _, found = self.discover(repos=["a/b/c/d"])
        self.assertEqual(found, [])

    def test_a_repo_nested_inside_a_repo_is_skipped(self):
        # The third level puts a submodule or a vendored checkout in reach for
        # the first time, under a parent at either of the shallower depths.
        # Shallowest-first globbing is what has the parent already in `out`.
        root, found = self.discover(repos=["myrepo", "myrepo/vendor/pkg",
                                           "forks/repo", "forks/repo/sub"])
        self.assertEqual(sorted(found),
                         sorted([os.path.join(root, "myrepo"),
                                 os.path.join(root, "forks", "repo")]))

    def test_a_nested_repo_sorting_ahead_of_its_parent_is_still_skipped(self):
        # Pins what the depth banding buys: the skip needs the parent in `out`
        # before anything inside it, and globbing depth by depth is what
        # guarantees that. One flat recursive glob would not, however it is
        # sorted, because "a/-x" sorts ahead of its own parent "a" and would
        # leak through. Same ASCII boundary as the prefix-sibling case below:
        # any name starting below "/" (0x2F) sorts against you.
        root, found = self.discover(repos=["a", "a/-x"])
        self.assertEqual(found, [os.path.join(root, "a")])

    def test_a_sibling_sharing_a_name_prefix_is_not_mistaken_for_nesting(self):
        # The skip compares against parent + os.sep, so "myrepo_old" must not
        # read as living inside "myrepo". The suffix has to sort AFTER "/" for
        # this to bite: the shorter name must already be in `out` when the
        # longer one is tested, and "myrepo-fork" would sort ahead of it.
        root, found = self.discover(repos=["myrepo", "myrepo_old"])
        self.assertEqual(sorted(found),
                         sorted([os.path.join(root, "myrepo"),
                                 os.path.join(root, "myrepo_old")]))


class Kind(unittest.TestCase):
    """Repo vs. linked worktree, decided by the shape of .git."""

    def kind_of(self, make):
        """Build a project dir, run `make` on its .git path, classify it."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "proj")
            os.mkdir(p)
            make(os.path.join(p, ".git"))
            return pp.kind(p)

    def test_directory_dot_git_is_a_repo(self):
        self.assertEqual(self.kind_of(os.mkdir), "repo")

    def test_file_dot_git_is_a_worktree(self):
        def write_pointer(g):
            with open(g, "w", encoding="utf-8") as f:
                f.write("gitdir: /x/parent/.git/worktrees/proj\n")
        self.assertEqual(self.kind_of(write_pointer), "worktree")

    def test_worktree_with_a_deleted_parent_still_classifies(self):
        # `git -C` fails outright on these, which is why kind() only stats.
        def dangling(g):
            with open(g, "w", encoding="utf-8") as f:
                f.write("gitdir: /x/deleted/.git/worktrees/proj\n")
        self.assertEqual(self.kind_of(dangling), "worktree")

    def test_absent_dot_git_reads_as_a_repo(self):
        # repos() only ever yields paths that have a .git, so this is
        # unreachable today. Pinned so a future caller sees the fallback.
        self.assertEqual(self.kind_of(lambda g: None), "repo")


class Elide(unittest.TestCase):
    """Long labels are cut so they cannot shift the columns after them."""

    def test_short_text_is_untouched(self):
        self.assertEqual(pp.elide("proj", 10), "proj")

    def test_text_exactly_at_the_width_is_untouched(self):
        # Off-by-one guard: cutting here would spend a column on an ellipsis
        # that hides nothing.
        self.assertEqual(pp.elide("0123456789", 10), "0123456789")

    def test_longer_text_is_cut_to_the_width_and_marked(self):
        self.assertEqual(pp.elide("0123456789x", 10), "012345678\u2026")

    def test_result_never_exceeds_the_width(self):
        for n in range(1, 60):
            self.assertLessEqual(len(pp.elide("x" * n, 10)), 10)

    def test_the_front_is_kept_not_the_tail(self):
        # Dupe labels carry a "parent/" prefix that makes them unique, so the
        # head is the one part that must survive.
        self.assertTrue(pp.elide("Sites/very-long-project", 12).startswith("Sites/"))


class Row(unittest.TestCase):
    """The row format and the label width must stay in step."""

    def test_label_field_is_padded_to_label_width(self):
        # elide() trims to LABEL_WIDTH, so a ROW whose first field is padded to
        # some other width would either clip early or still let rows overflow.
        # Field 1 spans columns 0..W-1, a separating space sits at W, so the
        # second field starts at W+1.
        row = pp.ROW.format("x", "KIND", "", "", "", "")
        self.assertEqual(row.index("KIND"), pp.LABEL_WIDTH + 1)

    def test_a_max_width_label_does_not_shift_later_columns(self):
        short = pp.ROW.format(pp.elide("x", pp.LABEL_WIDTH),
                              "repo", "3m ago", "", "/x", "x")
        long = pp.ROW.format(pp.elide("y" * 200, pp.LABEL_WIDTH),
                             "repo", "3m ago", "", "/y", "y" * 200)
        self.assertEqual(short.index("repo"), long.index("repo"))
        self.assertEqual(short.index("3m ago"), long.index("3m ago"))

    def test_hidden_fields_are_path_then_untruncated_label(self):
        fields = pp.ROW.format("proj\u2026", "repo", "3m ago", "",
                               "/x/project-long", "project-long").split("\t")
        self.assertEqual(fields[1], "/x/project-long")
        self.assertEqual(fields[2], "project-long")


class FzfInstaller(unittest.TestCase):
    """Which installer to use here, and whether it is ours to run."""

    def which(self, *present):
        return mock.patch.object(pp.shutil, "which",
                                 side_effect=lambda b: f"/bin/{b}" if b in present else None)

    def test_homebrew_is_preferred_and_runnable(self):
        with self.which("brew", "apt-get"):
            self.assertEqual(pp.fzf_installer(), (["brew", "install", "fzf"], True))

    def test_apt_is_found_but_not_runnable(self):
        # sudo cannot prompt sensibly from a popup pane, so we only advise.
        with self.which("apt-get"):
            argv, runnable = pp.fzf_installer()
        self.assertIn("apt-get", argv)
        self.assertEqual(argv[0], "sudo")
        self.assertFalse(runnable)

    def test_dnf_and_pacman_are_recognised_but_not_runnable(self):
        for mgr in ("dnf", "pacman"):
            with self.subTest(mgr=mgr), self.which(mgr):
                argv, runnable = pp.fzf_installer()
                self.assertIn(mgr, argv)
                self.assertFalse(runnable)

    def test_no_package_manager_yields_no_command(self):
        with self.which():
            self.assertEqual(pp.fzf_installer(), (None, False))

    def test_advice_names_the_command_when_there_is_one(self):
        self.assertIn("brew install fzf", pp.install_advice(["brew", "install", "fzf"]))

    def test_advice_falls_back_to_the_upstream_url(self):
        self.assertIn("github.com/junegunn/fzf", pp.install_advice(None))


class CheckDeps(unittest.TestCase):
    """The --check-deps preflight the manifest's [[build]] step runs."""

    def run_check(self, fzf, brew=False):
        def which(b):
            return "/bin/fzf" if (b == "fzf" and fzf) else ("/bin/brew" if (b == "brew" and brew) else None)
        out = io.StringIO()
        with mock.patch.object(pp.shutil, "which", side_effect=which):
            return pp.check_deps(out), out.getvalue()

    def test_present_fzf_exits_zero(self):
        code, text = self.run_check(fzf=True)
        self.assertEqual(code, 0)
        self.assertIn("/bin/fzf", text)

    def test_missing_fzf_exits_non_zero(self):
        # A zero exit would let an unusable plugin install cleanly.
        code, _ = self.run_check(fzf=False)
        self.assertEqual(code, 1)

    def test_missing_fzf_reports_how_to_install_it(self):
        _, text = self.run_check(fzf=False, brew=True)
        self.assertIn("brew install fzf", text)

    def test_missing_fzf_with_no_package_manager_still_advises(self):
        _, text = self.run_check(fzf=False)
        self.assertIn("github.com/junegunn/fzf", text)

    def test_it_never_prompts(self):
        # A build step has no terminal; a prompt here would hang the install.
        with mock.patch("builtins.input", side_effect=AssertionError("prompted")):
            self.run_check(fzf=False)


class EnsureFzf(unittest.TestCase):
    """First-run install offer. Anything short of a working fzf must die()."""

    def run_ensure(self, answer, fzf_after=False, brew=True, rc=0):
        calls = {"which": 0}

        def which(b):
            if b == "brew":
                return "/bin/brew" if brew else None
            if b == "fzf":
                calls["which"] += 1
                return "/bin/fzf" if (calls["which"] > 1 and fzf_after) else None
            return None

        run = mock.Mock(return_value=mock.Mock(returncode=rc))
        with mock.patch.object(pp.shutil, "which", side_effect=which), \
             mock.patch.object(pp.subprocess, "run", run), \
             mock.patch.object(pp.sys, "stderr", io.StringIO()), \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            try:
                return pp.ensure_fzf(ask=lambda _: answer), run
            except SystemExit:
                return None, run

    def test_already_installed_is_a_noop(self):
        with mock.patch.object(pp.shutil, "which", return_value="/bin/fzf"), \
             mock.patch.object(pp.subprocess, "run",
                               side_effect=AssertionError("installed anyway")):
            self.assertTrue(pp.ensure_fzf())

    def test_yes_installs_and_continues(self):
        ok, run = self.run_ensure("y", fzf_after=True)
        self.assertTrue(ok)
        self.assertEqual(run.call_args.args[0], ["brew", "install", "fzf"])

    def test_yes_spelled_out_and_padded_is_accepted(self):
        self.assertTrue(self.run_ensure("  YES  ", fzf_after=True)[0])

    def test_declining_dies_without_installing(self):
        ok, run = self.run_ensure("n")
        self.assertIsNone(ok)
        run.assert_not_called()

    def test_empty_answer_is_a_decline(self):
        # The prompt is [y/N]: bare Enter must not install anything.
        ok, run = self.run_ensure("")
        self.assertIsNone(ok)
        run.assert_not_called()

    def test_a_failed_install_dies_rather_than_starting_the_picker(self):
        self.assertIsNone(self.run_ensure("y", fzf_after=True, rc=1)[0])

    def test_an_install_that_reports_success_but_produces_no_fzf_dies(self):
        self.assertIsNone(self.run_ensure("y", fzf_after=False, rc=0)[0])

    def test_a_sudo_installer_is_never_run_only_advised(self):
        # `input` is asserted un-called rather than made to raise: ensure_fzf()
        # catches Exception around the prompt and treats it as a decline, so a
        # raising stub would be swallowed and this would pass on a regression.
        # The die() message is checked too, since the not-runnable branch and
        # the declined branch both end in SystemExit but say different things.
        def which(b):
            return "/bin/apt-get" if b == "apt-get" else None
        run = mock.Mock()
        with mock.patch.object(pp.shutil, "which", side_effect=which), \
             mock.patch.object(pp.subprocess, "run", run), \
             mock.patch.object(pp, "die", side_effect=SystemExit) as died, \
             mock.patch("builtins.input") as asked:
            with self.assertRaises(SystemExit):
                pp.ensure_fzf()
        asked.assert_not_called()
        run.assert_not_called()
        msg = died.call_args.args[0]
        self.assertIn("not installed", msg)
        self.assertIn("apt-get", msg)

    def test_ask_is_resolved_at_call_time_not_bound_at_definition(self):
        # Guards the `ask = ask or input` line. A signature default would
        # capture the real builtin, and a regression that reaches the prompt
        # would block the suite on stdin instead of failing it.
        with mock.patch.object(pp.shutil, "which", return_value=None), \
             mock.patch.object(pp.sys, "stderr", io.StringIO()), \
             mock.patch("builtins.input", return_value="n") as patched, \
             mock.patch.object(pp, "fzf_installer",
                               return_value=(["brew", "install", "fzf"], True)), \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                pp.ensure_fzf()
        patched.assert_called_once()


class BytecodeCache(unittest.TestCase):
    """Guards the sys.dont_write_bytecode line at the top of this module."""

    def test_bytecode_writing_stays_disabled(self):
        # Removal guard, not a behavior test. Without the flag, a same-size
        # edit within one second makes this suite run the previous version of
        # bin/pick-project and report failures against code that is correct.
        self.assertTrue(sys.dont_write_bytecode)

    def test_no_cache_was_written_for_the_script(self):
        # The real behavior. The cache path is mirrored under pycache_prefix
        # when set (macOS system python), or a sibling __pycache__ otherwise.
        stem = os.path.splitext(os.path.basename(SCRIPT))[0]
        if sys.pycache_prefix:
            d = sys.pycache_prefix + os.path.dirname(os.path.abspath(SCRIPT))
        else:
            d = os.path.join(os.path.dirname(os.path.abspath(SCRIPT)), "__pycache__")
        stale = [f for f in (os.listdir(d) if os.path.isdir(d) else [])
                 if f.startswith(stem) and f.endswith(".pyc")]
        self.assertEqual(stale, [], f"stale bytecode in {d}: {stale}")


class BuildLines(unittest.TestCase):
    """Assembling one fzf input line per row.

    kind() hits the filesystem, so it is stubbed per path: these tests are about
    which value lands in which field, not about how it is derived.

    touched_at() is stubbed to RAISE. The touch time reaches build_lines on the
    row, computed once by order_rows; a build_lines that reaches for the
    filesystem instead walks every working tree a second time. Every test in
    this class therefore doubles as the guard on that.
    """

    def build(self, rows, status=None, kinds=None, touched=None):
        """`rows` are (label, path) pairs here; the touch time is attached from
        `touched` (keyed by path, default 0) to keep the fixtures readable."""
        triples = [(l, p, (touched or {}).get(p, 0)) for l, p in rows]
        with mock.patch.object(pp, "kind",
                               side_effect=lambda p: (kinds or {}).get(p, "repo")), \
             mock.patch.object(pp, "touched_at",
                               side_effect=AssertionError("build_lines walked the tree")):
            return pp.build_lines(triples, status or {}, 100)

    def fields(self, rows, **kw):
        return [l.split("\t") for l in self.build(rows, **kw)]

    def test_one_line_per_row_in_the_order_given(self):
        lines = self.build([("b", "/x/b"), ("a", "/x/a")])
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("b"))
        self.assertTrue(lines[1].startswith("a"))

    def test_no_rows_is_no_lines(self):
        self.assertEqual(self.build([]), [])

    def test_kind_comes_from_kind_not_a_constant(self):
        # The whole point of the extraction: a hardcoded "repo" here would make
        # every worktree invisible, and nothing else in the suite would notice.
        visible = [f[0] for f in self.fields([("w", "/x/w"), ("r", "/x/r")],
                                             kinds={"/x/w": "worktree"})]
        self.assertIn("worktree", visible[0])
        self.assertIn("repo", visible[1])
        self.assertNotIn("worktree", visible[1])

    def test_age_comes_from_the_row_touch_time(self):
        # now=100 and touched=40 is 60s, which human_age renders as "1m ago".
        self.assertIn("1m ago",
                      self.fields([("a", "/x/a")], touched={"/x/a": 40})[0][0])

    def test_the_touch_time_is_read_per_row_not_once_for_all(self):
        # Guards against the third element being read from the first row and
        # reused: two rows with different times must render different ages.
        # now=100, so 40 is "1m ago" and 99 is "0m ago".
        ages = [f[0] for f in self.fields([("a", "/x/a"), ("b", "/x/b")],
                                          touched={"/x/a": 40, "/x/b": 99})]
        self.assertIn("1m ago", ages[0])
        self.assertIn("0m ago", ages[1])

    def test_hidden_fields_are_path_then_untruncated_label(self):
        f = self.fields([("proj", "/x/proj")])[0]
        self.assertEqual(f[1], "/x/proj")
        self.assertEqual(f[2], "proj")

    def test_long_label_is_elided_on_screen_but_whole_in_field_3(self):
        long = "z" * (pp.LABEL_WIDTH + 20)
        f = self.fields([(long, "/x/z")])[0]
        self.assertTrue(f[0].startswith("z" * (pp.LABEL_WIDTH - 1) + "…"))
        self.assertEqual(f[2], long)

    def test_a_long_label_does_not_shift_the_columns(self):
        rows = [("short", "/x/s"), ("y" * 200, "/x/y")]
        visible = [f[0] for f in self.fields(rows)]
        self.assertEqual(len(visible[0]), len(visible[1]))
        self.assertEqual(visible[0].index("repo"), visible[1].index("repo"))

    def test_open_rows_get_their_rendered_status_cell(self):
        # `status` holds cells status_cell() already rendered, so build_lines
        # places the string verbatim and adds no marker of its own.
        f = self.fields([("a", "/x/a")], status={"a": "● idle"})[0]
        self.assertIn("● idle", f[0])

    def test_rows_that_are_not_open_get_a_blank_status(self):
        f = self.fields([("a", "/x/a"), ("b", "/x/b")], status={"a": "● idle"})
        self.assertIn("● idle", f[0][0])
        self.assertNotIn("●", f[1][0])

    def test_status_is_matched_on_the_full_label_not_the_elided_one(self):
        # The elided label is what gets displayed, but `status` is keyed by the
        # real workspace label, so a truncated name must still find its agent.
        long = "w" * (pp.LABEL_WIDTH + 20)
        f = self.fields([(long, "/x/w")], status={long: "● busy"})[0]
        self.assertIn("● busy", f[0])


class TouchTimeIsComputedOncePerRun(unittest.TestCase):
    """The startup cost this pair of functions is arranged to avoid.

    touched_at() walks each repo's working tree two levels deep and dominates
    the wait before the picker paints. order_rows() computes it and build_lines()
    consumes it, so the whole run must walk every repo exactly once. Measured on
    27 repos: the second walk cost 65 ms of a 194 ms startup.
    """

    def walk_counts(self, labelled, open_ws):
        """Every path touched_at() is called on, across the real call sequence."""
        calls = []

        def counted(path):
            calls.append(path)
            return 0

        with mock.patch.object(pp, "touched_at", side_effect=counted), \
             mock.patch.object(pp, "kind", return_value="repo"):
            head, rest = pp.order_rows(labelled, open_ws)
            pp.build_lines(head + rest, {}, 100)
        return calls

    def test_each_repo_is_walked_exactly_once(self):
        labelled = [("a", "/x/a"), ("b", "/x/b"), ("c", "/x/c")]
        calls = self.walk_counts(labelled, [ws("a", "w1")])
        # sorted() rather than a set: a set hides a repeat, which is the defect.
        self.assertEqual(sorted(calls), ["/x/a", "/x/b", "/x/c"])

    def test_open_repos_are_not_walked_twice(self):
        # The open rows are the specific regression: they skip the sort's walk,
        # so a build_lines that recomputes shows up here first.
        calls = self.walk_counts([("a", "/x/a")], [ws("a", "w1")])
        self.assertEqual(calls, ["/x/a"])

    def test_a_repo_with_no_open_workspace_is_walked_once(self):
        calls = self.walk_counts([("a", "/x/a")], [])
        self.assertEqual(calls, ["/x/a"])


class ReadToml(unittest.TestCase):
    """The TOML subset reader. Only scalars under [table] headers."""

    WANTED = frozenset(["ui.status_indicators", "theme.name", "theme.custom.red"])

    def read(self, body, wanted=None):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.toml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            return pp.read_toml(path, wanted or self.WANTED)

    def test_scalar_under_its_table(self):
        self.assertEqual(self.read('[theme]\nname = "dracula"\n'),
                         {"theme.name": "dracula"})

    def test_the_same_key_in_two_tables_does_not_collide(self):
        # Keys are qualified by table: a bare-key match would let [ui] name
        # answer for [theme] name.
        got = self.read('[ui]\nname = "wrong"\n\n[theme]\nname = "nord"\n')
        self.assertEqual(got, {"theme.name": "nord"})

    def test_nested_table_headers_are_kept_whole(self):
        self.assertEqual(self.read('[theme.custom]\nred = "#ff0000"\n'),
                         {"theme.custom.red": "#ff0000"})

    def test_keys_before_any_table_header_are_not_claimed(self):
        # A root-level key belongs to table "", so it must not answer for a
        # qualified name. onboarding = false sits above [ui] in a real config.
        self.assertEqual(self.read('name = "root"\n[theme]\nname = "nord"\n'),
                         {"theme.name": "nord"})

    def test_unwanted_keys_are_ignored(self):
        self.assertEqual(self.read('[theme]\nauto_switch = true\n'), {})

    def test_comments_and_blank_lines_are_skipped(self):
        self.assertEqual(self.read('# [theme]\n# name = "commented"\n\n'
                                   '[theme]\nname = "nord"\n'),
                         {"theme.name": "nord"})

    def test_trailing_comment_is_stripped_from_a_bare_value(self):
        self.assertEqual(self.read('[ui]\nstatus_indicators = symbols # why\n'),
                         {"ui.status_indicators": "symbols"})

    def test_a_quoted_value_ends_at_its_closing_quote(self):
        # A "#" inside quotes is a hex colour, not a comment. Stripping from the
        # first "#" would leave the empty string and silently drop the override.
        self.assertEqual(self.read('[theme.custom]\nred = "#ff8800"  # accent\n'),
                         {"theme.custom.red": "#ff8800"})

    def test_single_quotes_work_too(self):
        self.assertEqual(self.read("[theme]\nname = 'nord'\n"),
                         {"theme.name": "nord"})

    def test_arrays_and_inline_tables_are_skipped_not_half_parsed(self):
        got = self.read('[theme]\nname = ["a", "b"]\n', frozenset(["theme.name"]))
        self.assertEqual(got, {})

    def test_first_value_wins_on_a_duplicate_key(self):
        self.assertEqual(self.read('[theme]\nname = "nord"\nname = "dracula"\n'),
                         {"theme.name": "nord"})

    def test_a_missing_file_is_empty_not_an_error(self):
        # Herdr's config is optional; the picker must still open without one.
        self.assertEqual(pp.read_toml("/x/does/not/exist.toml", self.WANTED), {})

    def test_an_unreadable_path_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pp.read_toml(d, self.WANTED), {})


class HerdrConfigPath(unittest.TestCase):
    """Which config.toml the picker reads Herdr's theme out of."""

    def test_the_documented_override_wins(self):
        with mock.patch.dict(os.environ, {"HERDR_CONFIG_PATH": "/x/other.toml"},
                             clear=True):
            self.assertEqual(pp.herdr_config_path(), "/x/other.toml")

    def test_the_override_beats_the_plugin_config_dir(self):
        # The server is reading whatever HERDR_CONFIG_PATH names, so deriving a
        # different path from the plugin dir would read a file nobody is using.
        with tempfile.TemporaryDirectory() as d:
            plugin_dir = os.path.join(d, "plugins", "config", "x.y")
            os.makedirs(plugin_dir)
            open(os.path.join(d, "config.toml"), "w").close()
            with mock.patch.dict(os.environ,
                                 {"HERDR_CONFIG_PATH": "/x/other.toml",
                                  "HERDR_PLUGIN_CONFIG_DIR": plugin_dir}, clear=True):
                self.assertEqual(pp.herdr_config_path(), "/x/other.toml")

    def test_the_override_is_tilde_expanded(self):
        with mock.patch.dict(os.environ, {"HERDR_CONFIG_PATH": "~/c.toml"}, clear=True):
            self.assertEqual(pp.herdr_config_path(),
                             os.path.expanduser("~/c.toml"))

    def test_derived_from_the_plugin_config_dir(self):
        with tempfile.TemporaryDirectory() as d:
            plugin_dir = os.path.join(d, "plugins", "config", "x.y")
            os.makedirs(plugin_dir)
            expected = os.path.join(d, "config.toml")
            open(expected, "w").close()
            with mock.patch.dict(os.environ,
                                 {"HERDR_PLUGIN_CONFIG_DIR": plugin_dir}, clear=True):
                self.assertEqual(pp.herdr_config_path(), expected)

    def test_a_trailing_separator_does_not_shift_the_derivation(self):
        with tempfile.TemporaryDirectory() as d:
            plugin_dir = os.path.join(d, "plugins", "config", "x.y")
            os.makedirs(plugin_dir)
            expected = os.path.join(d, "config.toml")
            open(expected, "w").close()
            with mock.patch.dict(os.environ,
                                 {"HERDR_PLUGIN_CONFIG_DIR": plugin_dir + os.sep},
                                 clear=True):
                self.assertEqual(pp.herdr_config_path(), expected)

    def test_a_derivation_that_finds_no_file_falls_back_to_the_default(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"HERDR_PLUGIN_CONFIG_DIR": d},
                                 clear=True):
                self.assertEqual(pp.herdr_config_path(),
                                 os.path.expanduser("~/.config/herdr/config.toml"))

    def test_nothing_set_uses_the_documented_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pp.herdr_config_path(),
                             os.path.expanduser("~/.config/herdr/config.toml"))


class HerdrRejectsTheme(unittest.TestCase):
    """Reading Herdr's own verdict on a theme name out of `config check`."""

    DIAGNOSTIC = ('config: issues found\nunknown theme name theme.name = '
                  '"monokai"; using "catppuccin"; valid themes: catppuccin, terminal\n')

    def probe(self, field="theme.name", stdout="", stderr="", boom=False):
        run = mock.Mock(side_effect=OSError) if boom else mock.Mock(
            return_value=mock.Mock(stdout=stdout, stderr=stderr))
        with mock.patch.object(pp.subprocess, "run", run):
            return pp.herdr_rejects_theme(field)

    def test_a_diagnostic_naming_the_field_is_a_rejection(self):
        self.assertTrue(self.probe(stdout=self.DIAGNOSTIC))

    def test_a_clean_check_is_not_a_rejection(self):
        self.assertFalse(self.probe(stdout="config: ok\n"))

    def test_a_diagnostic_about_a_different_field_is_not_a_rejection(self):
        # theme.name and theme.dark_name are diagnosed separately. Matching on
        # "unknown theme name" alone would let one answer for the other.
        self.assertFalse(self.probe(field="theme.dark_name",
                                    stdout=self.DIAGNOSTIC))

    def test_stderr_is_read_too(self):
        self.assertTrue(self.probe(stderr=self.DIAGNOSTIC))

    def test_an_unrunnable_herdr_is_not_a_rejection(self):
        # Fail towards UNKNOWN_THEME: a picker that could not ask must not
        # claim the name was a typo and draw catppuccin over a real theme.
        self.assertFalse(self.probe(boom=True))


class CanonicalTheme(unittest.TestCase):
    def test_exact_name(self):
        self.assertEqual(pp.canonical_theme("gruvbox"), "gruvbox")

    def test_case_and_separators_are_folded(self):
        for spelling in ("Tokyo_Night", "TOKYO NIGHT", "tokyo-night"):
            with self.subTest(spelling=spelling):
                self.assertEqual(pp.canonical_theme(spelling), "tokyo-night")

    def test_aliases_resolve(self):
        self.assertEqual(pp.canonical_theme("onedark"), "one-dark")
        self.assertEqual(pp.canonical_theme("gruvbox-dark"), "gruvbox")
        self.assertEqual(pp.canonical_theme("dawn"), "rose-pine-dawn")

    def test_unknown_and_empty_are_none(self):
        self.assertIsNone(pp.canonical_theme("monokai"))
        self.assertIsNone(pp.canonical_theme(""))
        self.assertIsNone(pp.canonical_theme(None))

    def test_every_alias_target_is_a_real_palette(self):
        # A typo in THEME_ALIASES would otherwise make that alias fall back to
        # the default theme silently.
        for alias, target in pp.THEME_ALIASES.items():
            with self.subTest(alias=alias):
                self.assertIn(target, pp.PALETTES)

    def test_every_palette_has_all_five_roles(self):
        for name, roles in pp.PALETTES.items():
            with self.subTest(theme=name):
                self.assertEqual(len(roles), len(pp.STATUS_ROLE_ORDER))


class ParseColor(unittest.TestCase):
    """Port of Herdr's parse_color. Ints are 256-colour indexes, tuples are Rgb."""

    def test_six_digit_hex(self):
        self.assertEqual(pp.parse_color("#8899aa"), (0x88, 0x99, 0xaa))

    def test_three_digit_hex_expands_by_seventeen(self):
        self.assertEqual(pp.parse_color("#f0a"), (255, 0, 170))

    def test_rgb_function(self):
        self.assertEqual(pp.parse_color("rgb(137, 180, 250)"), (137, 180, 250))

    def test_named_colors_map_to_their_crossterm_index(self):
        # ratatui Yellow -> crossterm DarkYellow -> 3, LightRed -> Red -> 9.
        self.assertEqual(pp.parse_color("yellow"), 3)
        self.assertEqual(pp.parse_color("lightred"), 9)
        self.assertEqual(pp.parse_color("gray"), 7)
        self.assertEqual(pp.parse_color("white"), 15)

    def test_case_and_whitespace_are_folded(self):
        self.assertEqual(pp.parse_color("  LightRed "), 9)

    def test_reset_aliases_are_the_default_foreground(self):
        for value in ("reset", "default", "none", "transparent"):
            with self.subTest(value=value):
                self.assertIsNone(pp.parse_color(value))

    def test_an_unknown_value_is_cyan_matching_herdr(self):
        # Herdr warns and defaults to cyan rather than failing. Diverging would
        # colour a typo differently from the sidebar this is matching.
        self.assertEqual(pp.parse_color("chartreuse"), pp.parse_color("cyan"))

    def test_malformed_hex_falls_through_to_the_named_default(self):
        self.assertEqual(pp.parse_color("#gg0011"), pp.parse_color("cyan"))
        self.assertEqual(pp.parse_color("#12345"), pp.parse_color("cyan"))

    def test_out_of_range_rgb_falls_through(self):
        self.assertEqual(pp.parse_color("rgb(300,0,0)"), pp.parse_color("cyan"))


class Paint(unittest.TestCase):
    def test_an_index_uses_the_256_colour_form_ratatui_writes(self):
        self.assertEqual(pp.paint("x", 3), "\033[38;5;3mx\033[39m")

    def test_an_rgb_triple_uses_truecolor(self):
        self.assertEqual(pp.paint("x", (1, 2, 3)), "\033[38;2;1;2;3mx\033[39m")

    def test_none_is_the_default_foreground(self):
        self.assertEqual(pp.paint("x", None), "\033[39mx\033[39m")


class ResolveTheme(unittest.TestCase):
    """Reading icons and colours out of a Herdr config."""

    def resolve(self, rejects=False, **config):
        """`rejects` stands in for `herdr config check`: True means Herdr turns
        the configured theme name down too."""
        return pp.resolve_theme(config, rejects=lambda field: rejects)

    def test_defaults_are_dots_on_catppuccin(self):
        # Herdr's own defaults when [ui] and [theme] say nothing.
        icons, colours = self.resolve()
        self.assertEqual(icons["working"], "●")
        self.assertEqual(icons["idle"], "○")
        self.assertEqual(colours["working"], (249, 226, 175))   # catppuccin yellow

    def test_symbols_style_changes_every_glyph_herdr_changes(self):
        icons, _ = self.resolve(**{"ui.status_indicators": "symbols"})
        self.assertEqual(icons["working"], "◐")
        self.assertEqual(icons["blocked"], "×")
        self.assertEqual(icons["done"], "✓")
        self.assertEqual(icons["idle"], "○")
        self.assertEqual(icons["unknown"], "·")

    def test_an_unknown_indicator_style_falls_back_to_dots(self):
        icons, _ = self.resolve(**{"ui.status_indicators": "emoji"})
        self.assertEqual(icons, pp.STATUS_ICONS["dots"])

    def test_the_terminal_theme_yields_ansi_indexes_not_hexes(self):
        # The whole point of theme = "terminal": indexes 0-15 resolve through
        # the host terminal profile, exactly as Herdr's own rendering does.
        _, colours = self.resolve(**{"theme.name": "terminal"})
        self.assertEqual(colours, {"working": 3, "blocked": 9, "done": 6,
                                   "idle": 2, "unknown": 7})

    def test_each_status_reads_its_own_role(self):
        # Guards the STATUS_ROLES wiring: a role swap would still produce five
        # colours, just the wrong ones on the wrong statuses.
        _, colours = self.resolve(**{"theme.name": "gruvbox"})
        self.assertEqual(colours["working"], (250, 189, 47))   # yellow
        self.assertEqual(colours["blocked"], (251, 73, 52))    # red
        self.assertEqual(colours["done"], (142, 192, 124))     # teal
        self.assertEqual(colours["idle"], (184, 187, 38))      # green
        self.assertEqual(colours["unknown"], (146, 131, 116))  # overlay0

    def test_a_name_herdr_also_rejects_uses_herdrs_own_default(self):
        # A typo. Herdr's diagnostic says it falls back to catppuccin, so
        # matching that is exactly right.
        _, typo = self.resolve(rejects=True, **{"theme.name": "monokai"})
        _, default = self.resolve()
        self.assertEqual(typo, default)

    def test_a_name_herdr_accepts_but_this_table_lacks_uses_the_terminal_palette(self):
        # Herdr gained a theme since PALETTES was copied. Its colours cannot be
        # read from anywhere at runtime, so fall back to the terminal palette
        # rather than confidently drawing catppuccin's.
        _, newer = self.resolve(rejects=False, **{"theme.name": "brand-new-theme"})
        _, terminal = self.resolve(**{"theme.name": "terminal"})
        _, default = self.resolve()
        self.assertEqual(newer, terminal)
        self.assertNotEqual(newer, default)

    def test_an_unset_name_never_asks_herdr(self):
        # Unset is not unknown: Herdr documents catppuccin as the default, so
        # spending a subprocess to confirm it would be waste on the normal path.
        asked = []
        pp.resolve_theme({}, rejects=lambda f: asked.append(f) or False)
        self.assertEqual(asked, [])

    def test_a_known_name_never_asks_herdr(self):
        asked = []
        pp.resolve_theme({"theme.name": "gruvbox"},
                         rejects=lambda f: asked.append(f) or False)
        self.assertEqual(asked, [])

    def test_the_probe_is_told_which_field_to_look_for(self):
        # Herdr diagnoses theme.name and theme.dark_name separately, so probing
        # the wrong field would read as "accepted" and mask a typo.
        asked = []
        pp.resolve_theme({"theme.auto_switch": "true", "theme.dark_name": "nope"},
                         rejects=lambda f: asked.append(f) or False)
        self.assertEqual(asked, ["theme.dark_name"])

    def test_a_custom_override_replaces_only_its_role(self):
        _, colours = self.resolve(**{"theme.name": "terminal",
                                     "theme.custom.red": "#ff8800"})
        self.assertEqual(colours["blocked"], (255, 136, 0))   # red role
        self.assertEqual(colours["working"], 3)               # yellow untouched

    def test_every_status_role_is_overridable(self):
        # with_overrides() covers all five, so a role missing from the override
        # loop would silently ignore the user's setting.
        for role, status in (("green", "idle"), ("yellow", "working"),
                             ("red", "blocked"), ("teal", "done"),
                             ("overlay0", "unknown")):
            with self.subTest(role=role):
                _, colours = self.resolve(**{"theme.name": "terminal",
                                             f"theme.custom.{role}": "#010203"})
                self.assertEqual(colours[status], (1, 2, 3))

    def test_auto_switch_uses_dark_name(self):
        _, colours = self.resolve(**{"theme.name": "terminal",
                                     "theme.auto_switch": "true",
                                     "theme.dark_name": "gruvbox"})
        self.assertEqual(colours["working"], (250, 189, 47))   # gruvbox yellow

    def test_auto_switch_off_ignores_dark_name(self):
        _, colours = self.resolve(**{"theme.name": "terminal",
                                     "theme.auto_switch": "false",
                                     "theme.dark_name": "gruvbox"})
        self.assertEqual(colours["working"], 3)                # terminal yellow

    def test_mode_overrides_apply_only_under_auto_switch(self):
        keys = {"theme.name": "terminal", "theme.custom.dark.yellow": "#010203"}
        _, off = self.resolve(**keys)
        _, on = self.resolve(**dict(keys, **{"theme.auto_switch": "true"}))
        self.assertEqual(off["working"], 3)             # ignored while off
        self.assertEqual(on["working"], (1, 2, 3))      # applied while on

    def test_a_mode_override_beats_the_unqualified_one(self):
        _, colours = self.resolve(**{"theme.name": "terminal",
                                     "theme.auto_switch": "true",
                                     "theme.custom.yellow": "#111111",
                                     "theme.custom.dark.yellow": "#222222"})
        self.assertEqual(colours["working"], (0x22, 0x22, 0x22))

    def test_theme_keys_covers_every_key_resolve_theme_reads(self):
        # THEME_KEYS is the filter read_toml applies, so a key absent from it is
        # unreachable no matter how the resolver is written.
        for role in pp.STATUS_ROLE_ORDER:
            self.assertIn(f"theme.custom.{role}", pp.THEME_KEYS)
            self.assertIn(f"theme.custom.dark.{role}", pp.THEME_KEYS)
        for key in ("ui.status_indicators", "theme.name", "theme.auto_switch",
                    "theme.dark_name"):
            self.assertIn(key, pp.THEME_KEYS)


class StatusCell(unittest.TestCase):
    ICONS = {"working": "◐", "idle": "○", "unknown": "·"}
    COLOURS = {"working": 3, "idle": 2, "unknown": 7}

    def cell(self, status, colours=None):
        return pp.status_cell(status, self.ICONS, colours or self.COLOURS)

    def escape(self, status, colours=None):
        """The opening SGR escape of a rendered cell."""
        return self.cell(status, colours).split("m", 1)[0] + "m"

    def test_glyph_word_and_colour_are_all_present(self):
        self.assertEqual(self.cell("working"),
                         "\033[38;5;3m" + "◐ working".ljust(pp.STATUS_WIDTH) + "\033[39m")

    def test_the_status_word_is_kept_so_the_filter_can_match_it(self):
        # fzf searches the visible text; dropping the word to match Herdr's
        # glyph-only sidebar would make typing "idle" match nothing.
        self.assertIn("idle", self.cell("idle"))

    def test_visible_text_is_padded_not_the_escaped_string(self):
        # Padding after the escapes would count them and pad by ~9 too few.
        import re as _re
        visible = _re.sub(r"\033\[[0-9;]*m", "", self.cell("idle"))
        self.assertEqual(len(visible), pp.STATUS_WIDTH)

    def test_an_unrecognised_status_takes_the_unknown_glyph_and_colour(self):
        # A status Herdr adds later must still render, not raise KeyError. A
        # colours.get() fallback would hand it the DEFAULT foreground instead of
        # the unknown role's colour, so both halves are asserted.
        self.assertIn("·", self.cell("brand-new"))
        self.assertEqual(self.escape("brand-new"), self.escape("unknown"))

    def test_a_reset_colour_is_kept_distinct_from_a_missing_one(self):
        # parse_color returns None for "reset", which is a real colour choice.
        # It must render SGR 39 rather than being treated as an absent status.
        reset = {"working": 3, "idle": 2, "unknown": None}
        self.assertEqual(self.escape("unknown", reset), "\033[39m")


class Heading(unittest.TestCase):
    """The column heading fzf consumes via --header-lines=1."""

    def test_is_exactly_one_line(self):
        # --header-lines=1 consumes one line; a second would become a project.
        self.assertNotIn("\n", pp.HEADING)

    def test_hidden_fields_are_empty(self):
        # Keeps the heading from parsing as a selectable repo path.
        self.assertEqual(pp.HEADING.split("\t")[1:], ["", ""])

    def test_columns_line_up_with_a_row(self):
        # Guards against the heading being rewritten as a hand-padded literal.
        # Match the full label, not "STATUS": that substring also occurs inside
        # "AGENT STATUS" and would report the wrong column offset.
        row = pp.ROW.format("proj", "worktree", "3m ago", "\u25cf idle",
                            "/x/proj", "proj")
        self.assertEqual(pp.HEADING.index("KIND"), row.index("worktree"))
        self.assertEqual(pp.HEADING.index("TOUCHED"), row.index("3m ago"))
        self.assertEqual(pp.HEADING.index("AGENT STATUS"), row.index("\u25cf idle"))

    def test_kind_column_fits_its_widest_value(self):
        # "worktree" is 8 chars; a narrower column would shove TOUCHED right on
        # worktree rows only, which the fixed-width test above would not see.
        row = pp.ROW.format("proj", "worktree", "3m ago", "", "/x/proj", "proj")
        self.assertEqual(row.index("3m ago"),
                         pp.ROW.format("proj", "repo", "3m ago", "", "/x", "proj")
                           .index("3m ago"))

    def test_labels_are_in_column_order(self):
        visible = pp.HEADING.split("\t")[0]
        self.assertLess(visible.index("PROJECT"), visible.index("KIND"))
        self.assertLess(visible.index("KIND"), visible.index("TOUCHED"))
        self.assertLess(visible.index("TOUCHED"), visible.index("AGENT STATUS"))


class KeyBindings(unittest.TestCase):
    """What the picker binds, and what the legend claims it binds."""

    def test_space_is_not_bound(self):
        # Space is fzf's AND separator between query terms. Binding it toggles
        # rows while you type a multi-word filter, which silently changes the
        # selection and can close a workspace on enter.
        self.assertNotIn("space:", pp.KEYS)

    def test_select_all_and_none_stay_bound(self):
        self.assertIn("ctrl-a:select-all", pp.KEYS)
        self.assertIn("ctrl-d:deselect-all", pp.KEYS)

    def test_legend_agrees_with_the_bindings_about_space(self):
        # Doc-drift guard: re-binding space without updating the legend, or
        # advertising it without binding it, both fail here.
        self.assertEqual("space" in pp.KEYS, "space" in pp.HEADER)

    def test_legend_names_every_bound_key(self):
        for key in ("ctrl-a", "ctrl-d"):
            self.assertIn(key, pp.HEADER)


if __name__ == "__main__":
    unittest.main()
