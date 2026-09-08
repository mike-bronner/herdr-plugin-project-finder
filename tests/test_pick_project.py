"""Unit tests for bin/pick-project's pure selection logic.

Run: python3 -m unittest discover tests
"""
import importlib.machinery, importlib.util, io, os, re, sys, tempfile, time
import unittest
from unittest import mock

# ManifestIsValidToml at the foot of this file parses herdr-plugin.toml for
# real, which needs tomllib — added in Python 3.11. /usr/bin/python3 is 3.9 on
# this machine, and the plugin targets it deliberately, because Herdr's server
# runs under launchd with a minimal PATH that /opt/homebrew is not on. So the
# check has to be skippable.
#
# A skip that reads as a pass would be worse than no check at all, hence the
# banner: it is printed once, at import, on stderr, so no green run can be
# mistaken for a checked manifest.
try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

NO_TOML = ("Python %d.%d.%d has no tomllib, which arrived in 3.11"
           % sys.version_info[:3])

if tomllib is None:
    print("\n%(bar)s\n"
          "!! herdr-plugin.toml WAS NOT CHECKED: %(why)s.\n"
          "!! The manifest parse test is SKIPPED, not passed. Herdr re-reads\n"
          "!! that file at dispatch time, so one syntax error in it stops this\n"
          "!! plugin dispatching, silently and with nothing surfaced. A green\n"
          "!! run below does NOT say the manifest is valid TOML.\n"
          "!! To really check it, re-run under a 3.11+ interpreter:\n"
          "!!     python3.11 -m unittest discover tests\n"
          "%(bar)s\n" % {"bar": "!" * 70, "why": NO_TOML}, file=sys.stderr)

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
MANIFEST = os.path.join(HERE, "..", "herdr-plugin.toml")
loader = importlib.machinery.SourceFileLoader("pick_project", SCRIPT)
spec = importlib.util.spec_from_loader("pick_project", loader)
pp = importlib.util.module_from_spec(spec)
loader.exec_module(pp)


def ws(label, wid, focused=False, status="idle"):
    return {"label": label, "workspace_id": wid, "focused": focused,
            "agent_status": status}


def make_project(path, worktree=False):
    """Create a git project at `path`, and return it.

    A repo gets a .git DIRECTORY, a worktree gets a .git FILE holding a
    gitdir: pointer. That is the difference row_kind() reads, so a project built
    here can be handed straight to it.
    """
    os.makedirs(path, exist_ok=True)
    git = os.path.join(path, ".git")
    if worktree:
        with open(git, "w", encoding="utf-8") as f:
            f.write("gitdir: /x/parent/.git/worktrees/wt\n")
    else:
        os.makedirs(git, exist_ok=True)
    return path


def write_config(directory, body):
    """A config.toml holding `body` in `directory`, returned as a path."""
    path = os.path.join(directory, "config.toml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


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
        out = f"{pp.EMPTY}\ncursor-row   3m ago  \t/x/cursor-row\n"
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
        self.assertEqual(pp.agent_name("group-a/my.repo", set()), "group-a-my-repo")

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
        # A worktree label one character past the limit, and the name the
        # picker gave it.
        name = pp.agent_name("label-that-exceeds-the-name-limit", set())
        self.assertEqual(name, "label-that-exceeds-the-name-limi")
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
        a = pp.agent_name("same-first-thirty-two-characters-one", taken)
        b = pp.agent_name("same-first-thirty-two-characters-two", taken)
        self.assertEqual(a, "same-first-thirty-two-characters")
        self.assertEqual(b, "same-first-thirty-two-characte-2")


class CreateWorkspaceHarness:
    """Shared by the two classes below. A mixin rather than a base TestCase,
    which would run every inherited test a second time."""

    RES = {"workspace": {"workspace_id": "w9"}, "tab": {"tab_id": "w9:t1"},
           "root_pane": {"pane_id": "p1"}}

    @staticmethod
    def fake_herdr(res, splits=("p2", "p3")):
        """A `herdr` stand-in answering each `pane split` with a NEW pane id.

        Every split has to answer with its own id. Which pane the SECOND split
        names is the whole of the layout — `--ratio` sizes the pane named in
        `--pane` — and one shared return value would make the agent pane and the
        tool pane the same string, hiding exactly the mix-up these tests exist
        to catch. A split past the end of `splits` answers None, which is how a
        failed split is spelt.
        """
        ids = iter(splits)

        def call(*args):
            if args[:2] == ("pane", "split"):
                new = next(ids, None)
                return {"pane": {"pane_id": new}} if new else None
            return res

        return call

    def run_create(self, res, splits=("p2", "p3")):
        with mock.patch.object(pp, "herdr",
                               side_effect=self.fake_herdr(res, splits)) as h, \
             mock.patch.object(pp.subprocess, "Popen"), \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            wid = pp.create_workspace("proj", "/x/proj", set())
        return wid, [c.args for c in h.call_args_list]

    def splits(self, calls):
        return [c for c in calls if c[:2] == ("pane", "split")]

    def renames(self, calls):
        return [c for c in calls if c[:2] == ("pane", "rename")]

    def agent_start_argv(self, label, live):
        """The detached `herdr agent start` argv this fires for `label`."""
        with mock.patch.object(pp, "herdr", side_effect=self.fake_herdr(self.RES)), \
             mock.patch.object(pp.subprocess, "Popen") as popen, \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            pp.create_workspace(label, "/x/proj", live)
        return popen.call_args.args[0]

    def create_watching_the_agent_pane(self, splits=("p2", "p3")):
        """(the herdr calls, the pane `agent start` was aimed at) from ONE create.

        Both halves out of the same run on purpose: the focus test compares
        them, and taking them from two creates would compare two workspaces.
        """
        with mock.patch.object(pp, "herdr",
                               side_effect=self.fake_herdr(self.RES, splits)) as h, \
             mock.patch.object(pp.subprocess, "Popen") as popen, \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            pp.create_workspace("proj", "/x/proj", set())
        argv = popen.call_args.args[0]
        return [c.args for c in h.call_args_list], argv[argv.index("--pane") + 1]

    def focused_pane(self, calls, splits=("p2", "p3"), root="p1"):
        """Which pane the cursor is on once the layout is built.

        A workspace arrives focused on the single pane it arrives with, and a
        split moves the cursor onto the pane it CREATES only when `--focus` is
        passed. `--no-focus` and no flag at all both leave the cursor where it
        already is. That rule was measured, not read: see split_pane(), whose
        docstring carries the isolated-server observation it came from.

        `splits` is the id the fake answers each split with, in order, because
        the created id is not derivable from the call itself.
        """
        focus = root
        for call, created in zip(self.splits(calls), splits):
            if "--focus" in call:
                focus = created
        return focus


class CreateWorkspace(CreateWorkspaceHarness, unittest.TestCase):
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

    def test_a_worktree_row_gets_the_same_layout_as_a_repo_row(self):
        # The picker stays solely responsible for the three-pane layout on
        # every row: Herdr emits worktree.opened for this call, and the local
        # agent-layout plugin subscribes to worktree.created alone.
        with mock.patch.object(pp, "parent_repo", return_value="/x/myrepo"), \
             mock.patch.object(pp, "herdr",
                               side_effect=self.fake_herdr(self.RES)) as h, \
             mock.patch.object(pp.subprocess, "Popen") as popen, \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            wid = pp.create_workspace("feat-x", "/x/wt/feat-x", set())
        calls = [c.args for c in h.call_args_list]
        self.assertEqual(wid, "w9")
        self.assertEqual(calls[0][:2], ("worktree", "open"))
        self.assertIn(("tab", "rename", "w9:t1", "agent"), calls)
        self.assertEqual(len(self.splits(calls)), 2)
        self.assertEqual(len(self.renames(calls)), 3)
        popen.assert_called_once()


class AgentLayout(CreateWorkspaceHarness, unittest.TestCase):
    """The three panes create_workspace() builds, and how they are sized.

    The sizing is entirely in the ORDER of the two splits, because `--ratio` is
    the share kept by the pane named in `--pane` rather than the one the split
    creates. Nothing about a wrong order raises: the tab still ends up with
    three panes, sized the wrong way round. So the pane ids in these assertions
    are the point of them, not incidental detail.
    """

    def test_one_pane_becomes_three(self):
        _, calls = self.run_create(self.RES)
        self.assertEqual(len(self.splits(calls)), 2)

    def test_the_agent_pane_is_split_first_and_keeps_half_the_width(self):
        # --pane p1 is the root pane the workspace arrived with, so the agent
        # keeps 0.5 and the column beside it gets the other half. Direction and
        # ratio are DIRECTION and RATIO at their defaults, pinned as settings
        # by LayoutSettings below and as read-at-the-call by the last test in
        # this class.
        _, calls = self.run_create(self.RES)
        self.assertEqual(self.splits(calls)[0],
                         ("pane", "split", "--pane", "p1", "--direction", "right",
                          "--ratio", "0.5", "--cwd", "/x/proj", "--no-focus"))

    def test_the_second_split_names_the_tool_pane(self):
        # p2 is what the first split returned. Naming p1 here would build a
        # third column beside the agent; naming the pane the second split
        # CREATES would leave the tool pane 0.4 of the column instead of 0.6.
        _, calls = self.run_create(self.RES)
        self.assertEqual(self.splits(calls)[1],
                         ("pane", "split", "--pane", "p2", "--direction", "down",
                          "--ratio", "0.6", "--cwd", "/x/proj", "--no-focus"))

    def test_a_new_workspace_opens_with_the_cursor_on_the_agent(self):
        # The wanted outcome, rather than the --no-focus flags. Those flags are
        # only the default said out loud — a split moves the cursor when
        # --focus is passed and not otherwise, measured rather than assumed
        # (split_pane()). So the cursor lands on the agent for one reason:
        # the agent is started in the pane the workspace arrived on. Start it
        # in a pane a split created, or add --focus to either split, and the
        # user opens a project looking at lazygit or at a bare shell — both of
        # which satisfy every other assertion in this class. Comparing the
        # focused pane against the pane the agent is really started in is what
        # stops the two drifting apart.
        calls, agent_pane = self.create_watching_the_agent_pane()
        self.assertEqual(agent_pane, "p1")
        self.assertEqual(self.focused_pane(calls), agent_pane)

    def test_the_focus_model_follows_a_split_that_asks_for_the_focus(self):
        # A canary on focused_pane() itself. No real split passes --focus, so
        # the branch that MOVES the cursor never runs in a green suite: a model
        # that ignored the flag would answer "p1" to everything and pass the
        # test above whatever the picker did.
        stealing = [("pane", "split", "--pane", "p1", "--direction", "right",
                     "--ratio", "0.5", "--cwd", "/x/proj", "--focus"),
                    ("pane", "split", "--pane", "p2", "--direction", "down",
                     "--ratio", "0.6", "--cwd", "/x/proj", "--focus")]
        self.assertEqual(self.focused_pane(stealing), "p3")
        self.assertEqual(self.focused_pane(stealing[:1]), "p2")
        # --no-focus is not --focus by a prefix match: the picker's own calls
        # carry the longer flag, and reading it as the shorter one would make
        # the test above assert the exact opposite of the layout it guards.
        no_focus = [c[:-1] + ("--no-focus",) for c in stealing]
        self.assertEqual(self.focused_pane(no_focus), "p1")

    def test_every_split_opens_in_the_project(self):
        _, calls = self.run_create(self.RES)
        for call in self.splits(calls):
            self.assertEqual(call[call.index("--cwd") + 1], "/x/proj")

    def test_the_tool_command_runs_in_the_tool_pane(self):
        # p2, never p3: lazygit belongs above the bare shell, not in it.
        _, calls = self.run_create(self.RES)
        self.assertIn(("pane", "run", "p2", "lazygit"), calls)
        self.assertEqual(len([c for c in calls if c[:2] == ("pane", "run")]), 1)

    def test_all_three_panes_are_labelled(self):
        # p1 included. A pane's `label` and its `agent` are independent fields
        # on 0.8.2 and neither clears the other, so labelling the agent's own
        # pane is not wasted work left to the border to do.
        _, calls = self.run_create(self.RES)
        self.assertEqual(self.renames(calls),
                         [("pane", "rename", "p1", "agent"),
                          ("pane", "rename", "p2", "lazygit"),
                          ("pane", "rename", "p3", "shell")])

    def test_the_panes_are_laid_out_before_the_agent_is_started(self):
        # `agent start` is what takes the pane busy. Every rename and the tool
        # command land first, so none of them races it.
        manager = mock.Mock()
        with mock.patch.object(pp, "herdr",
                               side_effect=self.fake_herdr(self.RES)) as h, \
             mock.patch.object(pp.subprocess, "Popen") as popen, \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            manager.attach_mock(h, "herdr")
            manager.attach_mock(popen, "popen")
            pp.create_workspace("proj", "/x/proj", set())
        names = [c[0] for c in manager.mock_calls]
        self.assertEqual(names.count("popen"), 1)
        self.assertEqual(names[-1], "popen")

    def test_the_agent_still_starts_when_the_first_split_fails(self):
        # A workspace with one pane and an agent in it beats no workspace: the
        # picker is mid-way through a multi-select and must not abandon it.
        wid, calls = self.run_create(self.RES, splits=())
        self.assertEqual(wid, "w9")
        self.assertEqual(len(self.splits(calls)), 1)
        self.assertFalse([c for c in calls if c[:2] == ("pane", "run")])
        self.assertEqual(self.renames(calls), [("pane", "rename", "p1", "agent")])

    def test_a_failed_second_split_still_fills_and_labels_the_tool_pane(self):
        # Two panes exist and one of them is the tool pane, so lazygit and both
        # labels that CAN be written still are. Only the shell's is skipped.
        _, calls = self.run_create(self.RES, splits=("p2",))
        self.assertIn(("pane", "run", "p2", "lazygit"), calls)
        self.assertEqual(self.renames(calls),
                         [("pane", "rename", "p1", "agent"),
                          ("pane", "rename", "p2", "lazygit")])

    def test_no_root_pane_builds_nothing_and_starts_nothing(self):
        res = {k: v for k, v in self.RES.items() if k != "root_pane"}
        wid, calls = self.run_create(res)
        self.assertEqual(wid, "w9")
        self.assertFalse(self.splits(calls))
        self.assertFalse(self.renames(calls))

    def test_the_settings_are_what_reach_herdr(self):
        # Every value is read from its setting at the call, not baked into the
        # call site. Each default below would still pass a happy-path test.
        #
        # The two directions are swapped for each other rather than set to
        # "left" and "up": `herdr pane split --help` gives the flag exactly two
        # possible values, right and down, so a fixture outside that pair could
        # never happen in a real run.
        with mock.patch.multiple(pp, KIND="codex", TAB_NAME="work",
                                 DIRECTION="down", RATIO="0.3",
                                 TOOL_COMMAND="gitui --ps", TOOL_DIRECTION="right",
                                 TOOL_RATIO="0.75", AGENT_LABEL="claude",
                                 TOOL_LABEL="git", SHELL_LABEL="sh"):
            _, calls = self.run_create(self.RES)
            argv = self.agent_start_argv("proj", set())
        self.assertIn(("tab", "rename", "w9:t1", "work"), calls)
        self.assertEqual(argv[argv.index("--kind") + 1], "codex")
        self.assertEqual(self.splits(calls)[0][4:8],
                         ("--direction", "down", "--ratio", "0.3"))
        self.assertEqual(self.splits(calls)[1][4:8],
                         ("--direction", "right", "--ratio", "0.75"))
        self.assertIn(("pane", "run", "p2", "gitui --ps"), calls)
        self.assertEqual(self.renames(calls),
                         [("pane", "rename", "p1", "claude"),
                          ("pane", "rename", "p2", "git"),
                          ("pane", "rename", "p3", "sh")])


class LayoutSettings(unittest.TestCase):
    """The layout values are settings with documented defaults.

    The names are the sibling herdr-plugin-agentic-panes-layout's rather than
    this plugin's HERDR_PICKER_ prefix, because both plugins build the same
    three panes and one vocabulary for one layout is the point. This class
    pins that decision so a rename cannot happen in one plugin alone.
    """

    DEFAULTS = {"KIND": "claude", "TAB_NAME": "agent",
                "DIRECTION": "right", "RATIO": "0.5",
                "TOOL_COMMAND": "lazygit", "TOOL_DIRECTION": "down",
                "TOOL_RATIO": "0.6", "AGENT_LABEL": "agent",
                "TOOL_LABEL": "lazygit", "SHELL_LABEL": "shell"}

    README = os.path.join(HERE, "..", "README.md")

    def test_the_defaults_are_the_documented_ones(self):
        for name, value in self.DEFAULTS.items():
            self.assertEqual(getattr(pp, name), value, name)

    def test_the_readme_documents_every_setting_at_its_default(self):
        # Doc-drift guard, in the shape KeyBindings uses below: adding a
        # setting without documenting it, or changing a default without
        # correcting the README, both fail here.
        #
        # Scoped to the line naming the setting and the comment block directly
        # above it, never a whole-file search: "Default: lazygit" appears twice
        # in this file for two different settings, so a file-wide `in` would be
        # satisfied by the other one and pin nothing.
        with open(self.README, encoding="utf-8") as f:
            lines = [line.rstrip("\n") for line in f]
        for name, value in self.DEFAULTS.items():
            key = f"AGENT_LAYOUT_{name}"
            at = [i for i, line in enumerate(lines) if line.startswith(key + "=")]
            self.assertEqual(len(at), 1, key)
            self.assertEqual(lines[at[0]], f"{key}={value}", key)
            above = lines[max(0, at[0] - 3):at[0]]
            self.assertTrue(any(line == f"# Default: {value}" for line in above), key)

    def test_the_settings_are_read_under_the_sibling_plugins_names(self):
        # Read from the source: the constants are bound at import, so an
        # environment set now cannot be observed without re-executing the
        # module. The names are the contract with the other plugin.
        with open(pp.__file__, encoding="utf-8") as f:
            source = f.read()
        for name in self.DEFAULTS:
            self.assertIn(f'os.environ.get("AGENT_LAYOUT_{name}")', source, name)

    def test_nothing_is_read_that_this_class_does_not_cover(self):
        # The closing half of the guard above, and self-contained: a setting
        # added to the picker but not to DEFAULTS would otherwise slip past
        # every check in this class, including the README one. Reading the
        # source is the only way to see it — the constants are bound at import.
        with open(pp.__file__, encoding="utf-8") as f:
            source = f.read()
        read = set(re.findall(r'os\.environ\.get\("(AGENT_LAYOUT_\w+)"\)', source))
        self.assertEqual(read, {f"AGENT_LAYOUT_{n}" for n in self.DEFAULTS})

    def test_every_sibling_setting_but_the_recipe_is_read(self):
        # The other half of the vocabulary contract, and the reason there is no
        # list of ignored names here any more: a name that works in one plugin
        # and quietly does nothing in the other is the trap the shared prefix
        # exists to close. Adding one to the sibling and not to DEFAULTS above
        # fails here.
        #
        # _RECIPE is the single exception, and not an oversight: it points the
        # sibling's event hook at an executable that replaces the layout, which
        # is a feature this picker does not have rather than a value it lays
        # out with. Reading it would promise something nothing here honours.
        sibling = os.path.join(HERE, "..", "..",
                               "herdr-plugin-agentic-panes-layout", "README.md")
        if not os.path.isfile(sibling):
            # Loud for the same reason as the banner at the top of this file: a
            # skip that reads as a pass is worse than no check at all.
            print("\n%(bar)s\n"
                  "!! THE SHARED SETTING NAMES WERE NOT CHECKED: the sibling\n"
                  "!! plugin herdr-plugin-agentic-panes-layout is not checked\n"
                  "!! out beside this repo, so its README could not be read.\n"
                  "!! The test is SKIPPED, not passed. Clone the sibling beside\n"
                  "!! this repo to check that one vocabulary covers both.\n"
                  "%(bar)s\n" % {"bar": "!" * 70}, file=sys.stderr)
            self.skipTest("the sibling plugin is not checked out beside this one")
        with open(sibling, encoding="utf-8") as f:
            names = {line.split("=")[0] for line in f
                     if line.startswith("AGENT_LAYOUT_")}
        self.assertIn("AGENT_LAYOUT_RECIPE", names)  # the file was really read
        expected = {f"AGENT_LAYOUT_{name}" for name in self.DEFAULTS}
        self.assertEqual(names - {"AGENT_LAYOUT_RECIPE"}, expected)


class SplitPane(unittest.TestCase):
    """Reading the new pane's id out of a split, and failing closed without it."""

    def call(self, reply):
        with mock.patch.object(pp, "herdr", return_value=reply) as h:
            got = pp.split_pane("p1", "down", "0.6", "/x/proj")
        return got, h.call_args.args

    def test_the_new_pane_id_is_returned(self):
        got, _ = self.call({"pane": {"pane_id": "p2"}})
        self.assertEqual(got, "p2")

    def test_the_split_is_asked_for_exactly_as_given(self):
        _, args = self.call({"pane": {"pane_id": "p2"}})
        self.assertEqual(args, ("pane", "split", "--pane", "p1", "--direction",
                                "down", "--ratio", "0.6", "--cwd", "/x/proj",
                                "--no-focus"))

    def test_a_failed_command_yields_none(self):
        self.assertIsNone(self.call(None)[0])

    def test_a_reply_without_a_pane_yields_none(self):
        self.assertIsNone(self.call({})[0])

    def test_an_explicitly_null_pane_yields_none(self):
        # A null is not a missing key, and .get("pane", {}) would raise on it.
        self.assertIsNone(self.call({"pane": None})[0])

    def test_a_pane_without_an_id_yields_none(self):
        self.assertIsNone(self.call({"pane": {}})[0])


class OpenProject(unittest.TestCase):
    """Which herdr command opens a row, and what happens when it will not.

    A linked worktree has to go through `worktree open`. It is the only command
    that records which repo the checkout belongs to, and that record is what
    Herdr's sidebar groups by — a worktree opened with `workspace create`
    carries none and floats at top level as an unrelated project.
    """

    RES = {"workspace": {"workspace_id": "w9"}}

    def open(self, parent, fails=()):
        """(result, calls) for open_project() with parent_repo() answering `parent`.

        `fails` names commands, by their first two words, that herdr() answers
        None for; every other command answers RES.
        """
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            return None if args[:2] in fails else self.RES

        with mock.patch.object(pp, "parent_repo", return_value=parent), \
             mock.patch.object(pp, "herdr", side_effect=fake_herdr):
            return pp.open_project("feat-x", "/x/wt/feat-x"), calls

    def test_a_worktree_is_opened_against_its_parent_repo(self):
        # --cwd names the parent by PATH. The --workspace form would need the
        # parent's own workspace open, and opening one the user did not check
        # would break the picker's contract that the selection is the truth.
        _, calls = self.open("/x/myrepo")
        self.assertEqual(calls[0], ("worktree", "open", "--cwd", "/x/myrepo",
                                    "--path", "/x/wt/feat-x",
                                    "--label", "feat-x", "--no-focus"))

    def test_opening_a_worktree_never_falls_through_to_workspace_create(self):
        # Two commands for one row would open the project twice.
        _, calls = self.open("/x/myrepo")
        self.assertEqual([c[:2] for c in calls], [("worktree", "open")])

    def test_the_worktree_result_is_returned_unchanged(self):
        # create_workspace() reads tab and root_pane off whatever comes back,
        # so a result summarised or reshaped here would lose the layout.
        res, _ = self.open("/x/myrepo")
        self.assertIs(res, self.RES)

    def test_a_row_with_no_parent_uses_workspace_create(self):
        _, calls = self.open(None)
        self.assertEqual(calls, [("workspace", "create", "--cwd", "/x/wt/feat-x",
                                  "--label", "feat-x", "--no-focus")])

    def test_a_failed_worktree_open_falls_back_to_workspace_create(self):
        # A Herdr too old to carry the command. min_herdr_version is 0.8.0, and
        # the fallback opens the worktree exactly as every earlier version of
        # this picker did, so the floor does not have to rise.
        res, calls = self.open("/x/myrepo", fails={("worktree", "open")})
        self.assertEqual([c[:2] for c in calls],
                         [("worktree", "open"), ("workspace", "create")])
        self.assertIs(res, self.RES)

    def test_both_commands_failing_yields_none_so_the_caller_dies(self):
        # The fallback's own failure stays fatal, as it always has been.
        res, _ = self.open("/x/myrepo",
                           fails={("worktree", "open"), ("workspace", "create")})
        self.assertIsNone(res)

    def test_nothing_is_ever_focused(self):
        # Opening the picker must not move the user off what they are looking
        # at; main() picks the focus target itself, after every create.
        for parent in ("/x/myrepo", None):
            with self.subTest(parent=parent):
                _, calls = self.open(parent)
                self.assertIn("--no-focus", calls[0])
                self.assertNotIn("--focus", calls[0])

    # The two cases above again, over a real directory rather than a stubbed
    # parent_repo(), so the call site is pinned to the filesystem it reads.

    def real(self, worktree):
        """(repo, calls) for open_project() over a real project dir."""
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        repo = make_project(os.path.join(d.name, "myrepo"))
        checkout = os.path.join(repo, ".worktrees", "myrepo", "feat-x")
        os.makedirs(checkout)
        with open(os.path.join(checkout, ".git"), "w", encoding="utf-8") as f:
            f.write(f"gitdir: {repo}/.git/worktrees/feat-x\n")
        calls = []
        with mock.patch.object(pp, "herdr",
                               side_effect=lambda *a: calls.append(a) or self.RES):
            pp.open_project("feat-x", checkout if worktree else repo)
        return repo, calls

    def test_a_real_worktree_names_its_real_parent(self):
        repo, calls = self.real(worktree=True)
        self.assertEqual(calls[0][:4], ("worktree", "open", "--cwd", repo))

    def test_a_real_repo_still_uses_workspace_create(self):
        # Repo rows are untouched by this: they have no parent to name.
        repo, calls = self.real(worktree=False)
        self.assertEqual(calls, [("workspace", "create", "--cwd", repo,
                                  "--label", "feat-x", "--no-focus")])


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
    """Discovery under the root: how deep it reaches, what it skips, and what
    each of its two passes contributed.

    repos() reads the module-level DEV, which is bound at import, so each case
    builds a scratch root and patches DEV at it.
    """

    def discover(self, repos=(), worktrees=(), dirs=None, roots=None, counts=None):
        """(root, repos()) for a scratch root holding these projects.

        Both project arguments are paths relative to the root, at any depth.

        `counts` is the optional per-pass tally dict, forwarded only when one is
        given: the no-argument call is repos()' own default and every other case
        here keeps exercising it, so dropping the argument entirely would leave
        the default signature untested.

        The container settings are patched rather than inherited, since
        WORKTREE_DIRS is bound at import from the developer's own Herdr
        config: unpatched, every case here would pass or fail according to a
        file outside the repo. They default to the shipped names.

        A `roots` entry is taken as-is when absolute and resolved against the
        scratch root otherwise, so a flat root inside the root and one outside
        it are both expressible.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for rel in repos:
            make_project(os.path.join(tmp.name, rel))
        for rel in worktrees:
            make_project(os.path.join(tmp.name, rel), worktree=True)
        with mock.patch.object(pp, "DEV", tmp.name), \
             mock.patch.object(pp, "WORKTREE_DIRS",
                               list(pp.FIXED_WORKTREE_DIRS) if dirs is None else dirs), \
             mock.patch.object(pp, "WORKTREE_ROOTS",
                               [os.path.join(tmp.name, r) for r in roots or ()]):
            return tmp.name, (pp.repos() if counts is None else pp.repos(counts))

    def test_a_repo_directly_under_the_root_is_found(self):
        root, found = self.discover(repos=["myrepo"])
        self.assertEqual(found, [os.path.join(root, "myrepo")])

    def test_a_repo_inside_a_grouping_dir_is_found(self):
        root, found = self.discover(repos=["group-a/myrepo"])
        self.assertEqual(found, [os.path.join(root, "group-a", "myrepo")])

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
        self.assertEqual([pp.row_kind(p) for p in found], ["worktree"])

    def test_a_fourth_level_is_not_searched(self):
        # Bounds the depth, so gaining a fifth stat pass stays a deliberate act.
        _, found = self.discover(repos=["a/b/c/d"])
        self.assertEqual(found, [])

    def test_a_repo_nested_inside_a_repo_is_skipped(self):
        # The third level puts a submodule or a vendored checkout in reach for
        # the first time, under a parent at either of the shallower depths.
        # Shallowest-first globbing is what has the parent already in `out`.
        root, found = self.discover(repos=["myrepo", "myrepo/vendor/pkg",
                                           "group-a/repo", "group-a/repo/sub"])
        self.assertEqual(sorted(found),
                         sorted([os.path.join(root, "myrepo"),
                                 os.path.join(root, "group-a", "repo")]))

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

    def test_a_worktree_in_herdrs_own_container_is_found(self):
        # Herdr's layout while [worktrees] directory is ".worktrees": the
        # container inside the repo, then the repo's own name, then the branch
        # slug. Relative is not what nests it — "../worktrees" is relative too
        # and lands beside the repo instead, which the sibling case below pins.
        # Two things hid this one — the leading dot, which no wildcard matches,
        # and the nested skip.
        root, found = self.discover(
            repos=["myrepo"], worktrees=["myrepo/.worktrees/myrepo/feat-x"])
        self.assertEqual(
            sorted(found),
            sorted([os.path.join(root, "myrepo"),
                    os.path.join(root, "myrepo", ".worktrees", "myrepo", "feat-x")]))

    def test_a_worktree_in_claude_codes_container_is_found(self):
        # The other real layout, and it does NOT nest by repo name, so both
        # container depths have to be searched.
        root, found = self.discover(
            repos=["myrepo"], worktrees=["myrepo/.claude/worktrees/slug-1a2b"])
        self.assertEqual(
            sorted(found),
            sorted([os.path.join(root, "myrepo"),
                    os.path.join(root, "myrepo", ".claude", "worktrees", "slug-1a2b")]))

    def test_a_worktree_in_the_undotted_container_is_found(self):
        root, found = self.discover(repos=["myrepo"],
                                    worktrees=["myrepo/worktrees/feat-x"])
        self.assertIn(os.path.join(root, "myrepo", "worktrees", "feat-x"), found)

    def test_a_nested_worktree_is_found_under_a_repo_at_any_depth(self):
        # The container hangs off the repo, not off the root, so a repo two
        # levels down carries its worktrees five levels down. That is past
        # every depth band, which is the whole reason for the second pass.
        root, found = self.discover(
            repos=["group/myrepo"],
            worktrees=["group/myrepo/.worktrees/myrepo/feat-x"])
        self.assertIn(
            os.path.join(root, "group", "myrepo", ".worktrees", "myrepo", "feat-x"),
            found)

    def test_a_nested_worktree_is_tagged_worktree(self):
        # The KIND column is the point of finding them, pinned on a path
        # discovery actually produced rather than a handmade one.
        root, found = self.discover(
            repos=["myrepo"], worktrees=["myrepo/.worktrees/myrepo/feat-x"])
        nested = [p for p in found if ".worktrees" in p]
        self.assertEqual([pp.row_kind(p) for p in nested], ["worktree"])

    def test_an_ordinary_repo_inside_a_container_is_still_skipped(self):
        # The exemption is for linked worktrees, not for everything under a
        # container name. A submodule that happens to sit there is still the
        # parent's own content and must not become a second row.
        root, found = self.discover(repos=["myrepo", "myrepo/worktrees/vendored"])
        self.assertEqual(found, [os.path.join(root, "myrepo")])

    def test_a_container_under_a_repo_that_is_itself_nested_is_not_searched(self):
        # A vendored checkout is skipped, so its containers are never reached
        # either: the second pass runs over what the first pass kept.
        root, found = self.discover(
            repos=["myrepo", "myrepo/vendor/pkg"],
            worktrees=["myrepo/vendor/pkg/.worktrees/pkg/feat-x"])
        self.assertEqual(found, [os.path.join(root, "myrepo")])

    def test_a_container_named_by_herdrs_config_is_searched(self):
        # End to end: a real config.toml, read by the real read_toml() through
        # the real herdr_config_path(), naming a container none of the fixed
        # names cover.
        cfg = tempfile.TemporaryDirectory()
        self.addCleanup(cfg.cleanup)
        path = write_config(cfg.name, '[worktrees]\ndirectory = "trees"\n')
        with mock.patch.dict(os.environ, {"HERDR_CONFIG_PATH": path}):
            dirs, roots = pp.worktree_locations()
        root, found = self.discover(repos=["myrepo"],
                                    worktrees=["myrepo/trees/feat-x"],
                                    dirs=dirs, roots=roots)
        self.assertIn(os.path.join(root, "myrepo", "trees", "feat-x"), found)

    # A container BESIDE the repo, which is what a "../worktrees" setting
    # produces. Herdr moved to it so that nothing walking a repository can
    # descend into a checkout: .git/info/exclude stops git, and nothing else.
    # A repo at <root>/<group>/<name> then keeps its worktrees at
    # <root>/<group>/worktrees/<name>/<slug> — four levels down, past every
    # depth band, and inside no repository at all.
    #
    # The container pass reaches it with no new mechanism: a container is a
    # relative path resolved against the repo root, and normpath collapsing the
    # ".." is the whole of the handling. These cases pin that, since nothing
    # else states it and an innocent-looking change to either the join or the
    # dirs list would silently drop the layout. What the ".." does change is
    # the COST — one container now serves every repo in a parent — so the last
    # case here counts globs rather than rows.

    def sibling_locations(self):
        """(dirs, roots) for a real config.toml holding "../worktrees"."""
        cfg = tempfile.TemporaryDirectory()
        self.addCleanup(cfg.cleanup)
        path = write_config(cfg.name, '[worktrees]\ndirectory = "../worktrees"\n')
        with mock.patch.dict(os.environ, {"HERDR_CONFIG_PATH": path}):
            return pp.worktree_locations()

    def sibling(self, **kwargs):
        """discover() with the sibling container read from a real config."""
        dirs, roots = self.sibling_locations()
        return self.discover(dirs=dirs, roots=roots, **kwargs)

    def test_a_worktree_beside_its_repo_is_found(self):
        # End to end, through the real read_toml() and herdr_config_path().
        root, found = self.sibling(
            repos=["group-a/repo-one"],
            worktrees=["group-a/worktrees/repo-one/feat-x"])
        self.assertEqual(
            sorted(found),
            sorted([os.path.join(root, "group-a", "repo-one"),
                    os.path.join(root, "group-a", "worktrees", "repo-one",
                                 "feat-x")]))

    def test_a_worktree_beside_its_repo_is_tagged_worktree(self):
        # The KIND column is the point of finding them, pinned on a path
        # discovery actually produced rather than a handmade one.
        root, found = self.sibling(
            repos=["group-a/repo-one"],
            worktrees=["group-a/worktrees/repo-one/feat-x"])
        beside = [p for p in found if os.sep + "worktrees" + os.sep in p]
        self.assertEqual([pp.row_kind(p) for p in beside], ["worktree"])

    def test_each_repo_resolves_its_own_sibling_container(self):
        # The ".." is resolved against the REPO, not against the root, so two
        # repos under different parents each get their own container. Resolving
        # against the root would find one of these two at most, and the fixture
        # is shaped so that neither container sits where the other repo looks.
        root, found = self.sibling(
            repos=["group-a/repo-one", "group-b/repo-two"],
            worktrees=["group-a/worktrees/repo-one/feat-x",
                       "group-b/worktrees/repo-two/feat-y"])
        self.assertEqual(
            sorted(found),
            sorted([os.path.join(root, "group-a", "repo-one"),
                    os.path.join(root, "group-b", "repo-two"),
                    os.path.join(root, "group-a", "worktrees", "repo-one",
                                 "feat-x"),
                    os.path.join(root, "group-b", "worktrees", "repo-two",
                                 "feat-y")]))

    def test_a_sibling_container_the_depth_bands_also_reach_lists_once(self):
        # A repo directly under the root resolves its sibling container to
        # <root>/worktrees, which puts the checkout three levels down and in
        # reach of the bands — an undotted container is not hidden from them
        # the way ".worktrees" and ".claude" are. So the path arrives twice and
        # must still produce one row, credited to the pass that found it.
        counts = {}
        dirs, roots = self.sibling_locations()
        root, found = self.discover(repos=["myrepo"],
                                    worktrees=["worktrees/myrepo/feat-x"],
                                    dirs=dirs, roots=roots, counts=counts)
        self.assertEqual(found, [os.path.join(root, "myrepo"),
                                 os.path.join(root, "worktrees", "myrepo",
                                              "feat-x")])
        self.assertEqual(counts, {"bands": 2, "containers": 0})

    def test_every_worktree_layout_lists_at_once(self):
        # The migration is not atomic: while Herdr's setting changes, worktrees
        # made under the old value stay where they are. All four shapes have to
        # list together — Claude Code's <repo>/.claude/worktrees/<slug>, which
        # carries one level fewer than the rest, Herdr's old nested
        # <repo>/.worktrees/<repo>/<slug>, Herdr's older flat
        # <root>/worktrees/<repo>/<slug>, and the new sibling
        # <parent>/worktrees/<repo>/<slug>.
        root, found = self.sibling(
            repos=["group-a/repo-one", "repo-two"],
            worktrees=["repo-two/.claude/worktrees/slug-1a2b",
                       "group-a/repo-one/.worktrees/repo-one/feat-old",
                       "worktrees/repo-two/feat-flat",
                       "group-a/worktrees/repo-one/feat-new"])
        self.assertEqual(
            sorted(found),
            sorted([os.path.join(root, "group-a", "repo-one"),
                    os.path.join(root, "repo-two"),
                    os.path.join(root, "repo-two", ".claude", "worktrees",
                                 "slug-1a2b"),
                    os.path.join(root, "group-a", "repo-one", ".worktrees",
                                 "repo-one", "feat-old"),
                    os.path.join(root, "worktrees", "repo-two", "feat-flat"),
                    os.path.join(root, "group-a", "worktrees", "repo-one",
                                 "feat-new")]))

    def test_a_bare_parent_container_admits_only_worktrees(self):
        # ".." is a legal value that resolves to the grouping dir itself, so it
        # aims the container globs at every sibling of the repo. What bounds
        # that is the row_kind() == "worktree" test, not the container name: the
        # ordinary repo here sits exactly where the worktree does, four levels
        # down and past the bands, and must not become a row.
        root, found = self.discover(
            repos=["a/group/one", "a/group/trees/vendored"],
            worktrees=["a/group/trees/feat-x"],
            dirs=list(pp.FIXED_WORKTREE_DIRS) + [".."])
        self.assertEqual(
            sorted(found),
            sorted([os.path.join(root, "a", "group", "one"),
                    os.path.join(root, "a", "group", "trees", "feat-x")]))

    def globbed(self, **kwargs):
        """((root, repos()), the patterns repos() handed to glob)."""
        patterns, real = [], pp.glob.glob
        with mock.patch.object(
                pp.glob, "glob",
                side_effect=lambda p, **kw: patterns.append(p) or real(p, **kw)):
            return self.discover(**kwargs), patterns

    def test_a_shared_sibling_container_is_globbed_once(self):
        # Every repo in a parent resolves "../worktrees" to the SAME directory,
        # so the bases list holds one copy per repo and the two container globs
        # would re-run over it once per repo. Counted rather than timed: the
        # saving is real work not done, and a timing assertion would be flaky.
        # On the real 85-repo root it is 340 bases against 262 distinct ones,
        # 28.9ms of discovery against 23.0ms.
        dirs, roots = self.sibling_locations()
        (root, found), patterns = self.globbed(
            repos=["group/one", "group/two", "group/three"],
            worktrees=["group/worktrees/one/feat-x"],
            dirs=dirs, roots=roots)
        shared = os.path.join(root, "group", "worktrees") + os.sep
        self.assertEqual([p for p in patterns if p.startswith(shared)],
                         [shared + os.path.join("*", ".git"),
                          shared + os.path.join("*", "*", ".git")])
        self.assertIn(os.path.join(root, "group", "worktrees", "one", "feat-x"),
                      found)

    def test_an_absolute_worktree_root_outside_the_picker_root_is_searched(self):
        # The old flat layout, and Herdr's shipped default. Nothing under the
        # picker root leads to it, so it is searched on its own.
        flat = tempfile.TemporaryDirectory()
        self.addCleanup(flat.cleanup)
        wt = make_project(os.path.join(flat.name, "myrepo", "feat-x"), worktree=True)
        root, found = self.discover(repos=["myrepo"], roots=[flat.name])
        self.assertEqual(sorted(found), sorted([os.path.join(root, "myrepo"), wt]))

    def test_a_worktree_reached_twice_is_listed_once(self):
        # A flat root that sits inside the picker root is found by the depth
        # bands as well, so the same path arrives twice. One row, not two.
        root, found = self.discover(repos=["myrepo"],
                                    worktrees=["worktrees/myrepo/feat-x"],
                                    roots=["worktrees"])
        self.assertEqual(found, [os.path.join(root, "myrepo"),
                                 os.path.join(root, "worktrees", "myrepo", "feat-x")])

    # The per-pass tally: the same two passes seen from the other side.

    def tally(self, **kwargs):
        """(repos(), counts) for a scratch root holding these projects."""
        counts = {}
        _, found = self.discover(counts=counts, **kwargs)
        return found, counts

    # Both passes contribute, and the band pass returns a worktree as well as a
    # repo: Herdr's flat <root>/worktrees/<repo>/<slug> is three levels down, so
    # the depth bands reach it. That is the case the reported wording has to
    # survive — "2 repos" would be a lie about this tree.
    MIXED_PASSES = {"repos": ["myrepo"],
                    "worktrees": ["worktrees/myrepo/feat-x",
                                  "myrepo/.worktrees/myrepo/feat-y"]}

    def test_each_pass_reports_what_it_contributed(self):
        found, counts = self.tally(**self.MIXED_PASSES)
        self.assertEqual(len(found), 3)
        self.assertEqual(counts, {"bands": 2, "containers": 1})

    def test_the_two_figures_account_for_every_row(self):
        # The debug line reports the row total beside the split, so a split that
        # does not add up to it would be visibly wrong.
        found, counts = self.tally(**self.MIXED_PASSES)
        self.assertEqual(counts["bands"] + counts["containers"], len(found))

    def test_a_pass_that_found_nothing_reports_zero(self):
        # Both keys are always present: debug_discovery() indexes them, so an
        # omitted key would raise instead of printing.
        found, counts = self.tally(repos=["myrepo"])
        self.assertEqual((len(found), counts), (1, {"bands": 1, "containers": 0}))

    def test_nothing_found_at_all_reports_two_zeroes(self):
        found, counts = self.tally()
        self.assertEqual((found, counts), ([], {"bands": 0, "containers": 0}))


class DebugDiscovery(unittest.TestCase):
    """The HERDR_PICKER_DEBUG line: silent by default, one line when asked for."""

    def report(self, flag=None, paths=("/x/a", "/x/b", "/x/c"),
               counts=None, elapsed=0.0264):
        """What one debug_discovery() call writes, as text.

        A `flag` of None leaves HERDR_PICKER_DEBUG unset. clear=True either way,
        so the developer's own environment cannot decide the outcome.
        """
        out = io.StringIO()
        env = {} if flag is None else {"HERDR_PICKER_DEBUG": flag}
        with mock.patch.dict(os.environ, env, clear=True):
            pp.debug_discovery(elapsed, list(paths),
                               counts or {"bands": 2, "containers": 1}, out)
        return out.getvalue()

    def test_unset_writes_nothing(self):
        # The default path for every normal run: a popup pane that writes
        # uninvited is the thing this gate exists to prevent.
        self.assertEqual(self.report(), "")

    def test_an_empty_value_writes_nothing(self):
        # What "HERDR_PICKER_DEBUG=" in the .env file leaves behind. It has to
        # read as off, the way an empty HERDR_PICKER_ROOT reads as unset.
        self.assertEqual(self.report(flag=""), "")

    def test_the_elapsed_seconds_are_reported_as_milliseconds(self):
        # 0.0264s is the measured figure from the real root, in the units the
        # repos() docstring quotes. A raw-seconds line would read "0.0ms".
        self.assertIn("26.4ms", self.report(flag="1"))

    def test_the_row_total_and_both_passes_are_reported(self):
        text = self.report(flag="1")
        self.assertIn("3 rows", text)
        self.assertIn("2 from depth bands", text)
        self.assertIn("1 from worktree containers", text)

    def test_the_row_total_is_the_rows_not_the_sum_of_the_passes(self):
        # Pins which of the two the figure comes from, so an inconsistency
        # between the list and the tally shows up instead of being smoothed.
        self.assertIn("3 rows", self.report(flag="1",
                                            counts={"bands": 1, "containers": 1}))

    def test_it_is_one_line(self):
        text = self.report(flag="1")
        self.assertEqual(text.count("\n"), 1)
        self.assertTrue(text.endswith("\n"), text)

    def test_it_names_the_picker(self):
        # The line can land in a shared terminal, so it says whose it is.
        self.assertTrue(self.report(flag="1").startswith("picker: "))

    def test_the_default_stream_is_stderr(self):
        # Where every other writer in the script goes. Not stdout: that is the
        # stream a caller piping the picker would be reading.
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"HERDR_PICKER_DEBUG": "1"}, clear=True), \
             mock.patch.object(pp.sys, "stderr", err):
            pp.debug_discovery(0.0264, ["/x/a"], {"bands": 1, "containers": 0})
        self.assertIn("picker: discovery", err.getvalue())


class DebugLineFromARealRun(unittest.TestCase):
    """What driving main() shows that a unit test of the line cannot: where the
    write lands in the sequence, and what the timer is wrapped around.

    fzf paints the popup over the whole pane, so anything written while it owns
    the screen garbles the list. The write has to be on the near side of that
    subprocess call.
    """

    def run_main(self, env, delay=0):
        """Every write and every subprocess call main() makes, in order.

        `delay` is seconds that discovery is made to take, so the reported figure
        can be held against a known floor.
        """
        events = []

        class Recorder(io.StringIO):
            def write(self, text):
                events.append(("write", text))
                return super().write(text)

        def fake_run(argv, **kwargs):
            events.append(("run", argv[0]))
            # 130 is Esc: parse_selection() reads it as cancelled, so main()
            # returns without touching a workspace.
            return mock.Mock(returncode=130, stdout="")

        def fake_repos(counts=None):
            time.sleep(delay)
            if counts is not None:
                counts.update({"bands": 2, "containers": 1})
            return ["/x/a", "/x/b", "/x/c"]

        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(pp, "HERDR", "/bin/herdr"), \
             mock.patch.object(pp, "ensure_fzf", return_value=True), \
             mock.patch.object(pp, "repos", side_effect=fake_repos), \
             mock.patch.object(pp, "open_workspaces", return_value=(None, [])), \
             mock.patch.object(pp, "resolve_theme", return_value=({}, {})), \
             mock.patch.object(pp, "order_rows", return_value=([], [])), \
             mock.patch.object(pp, "build_lines", return_value=[]), \
             mock.patch.object(pp.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(pp.sys, "stderr", Recorder()):
            pp.main()
        return events

    def test_the_line_is_written_before_fzf_starts(self):
        events = self.run_main({"HERDR_PICKER_DEBUG": "1"})
        self.assertEqual([kind for kind, _ in events], ["write", "run"])
        self.assertIn("picker: discovery", events[0][1])
        self.assertEqual(events[1][1], "fzf")

    def test_an_ordinary_run_writes_nothing_at_all(self):
        # The whole-run version of the gate: fzf still starts, and not one byte
        # reaches the pane before it.
        events = self.run_main({})
        self.assertEqual(events, [("run", "fzf")])

    def test_the_reported_time_is_the_time_discovery_took(self):
        # The central claim, and the one thing no assertion on the text can
        # show: the clock has to be started before repos() and read after it.
        # A timer that brackets anything else reports a figure near zero, so
        # discovery is made to take a known minimum and the figure held above
        # it. No upper bound: the scheduler owns that end.
        events = self.run_main({"HERDR_PICKER_DEBUG": "1"}, delay=0.02)
        reported = float(events[0][1].split("discovery ")[1].split("ms")[0])
        self.assertGreaterEqual(reported, 15.0, events[0][1])


class WorktreeLocations(unittest.TestCase):
    """Where worktrees are looked for: fixed names plus Herdr's own setting."""

    def locate(self, config):
        return pp.worktree_locations(config)

    def test_the_fixed_containers_are_always_searched(self):
        dirs, roots = self.locate({})
        self.assertEqual(dirs, list(pp.FIXED_WORKTREE_DIRS))
        self.assertEqual(roots, [])

    def test_both_real_layouts_are_covered_by_the_fixed_names(self):
        # Herdr's and Claude Code's, the two that exist on this machine.
        self.assertIn(".worktrees", pp.FIXED_WORKTREE_DIRS)
        self.assertIn(".claude/worktrees", pp.FIXED_WORKTREE_DIRS)

    def test_a_relative_setting_joins_the_containers(self):
        dirs, roots = self.locate({"worktrees.directory": "trees"})
        self.assertEqual(dirs, list(pp.FIXED_WORKTREE_DIRS) + ["trees"])
        self.assertEqual(roots, [])

    def test_a_relative_setting_already_covered_is_not_repeated(self):
        dirs, _ = self.locate({"worktrees.directory": "./.worktrees"})
        self.assertEqual(dirs, list(pp.FIXED_WORKTREE_DIRS))

    def test_a_setting_beside_the_repo_stays_relative(self):
        # Herdr resolves a relative value against the repo root, so "../x"
        # names a real directory beside the repo, not a nonsense one.
        dirs, roots = self.locate({"worktrees.directory": "../trees"})
        self.assertIn(os.path.join("..", "trees"), dirs)
        self.assertEqual(roots, [])

    def test_an_absolute_setting_becomes_a_flat_root(self):
        dirs, roots = self.locate({"worktrees.directory": "/srv/worktrees/"})
        self.assertEqual(dirs, list(pp.FIXED_WORKTREE_DIRS))
        self.assertEqual(roots, ["/srv/worktrees"])

    def test_herdrs_shipped_default_is_read_as_a_root_not_a_container(self):
        # ~/.herdr/worktrees is absolute once expanded, and .env values are
        # never shell-expanded, so the tilde has to be handled here.
        _, roots = self.locate({"worktrees.directory": "~/.herdr/worktrees"})
        self.assertEqual(roots, [os.path.expanduser("~/.herdr/worktrees")])

    def test_an_empty_setting_degrades_to_the_fixed_containers(self):
        dirs, roots = self.locate({"worktrees.directory": "   "})
        self.assertEqual(dirs, list(pp.FIXED_WORKTREE_DIRS))
        self.assertEqual(roots, [])

    def test_an_unreadable_config_degrades_rather_than_raising(self):
        # read_toml() answers {} for a missing file; nothing here may raise on
        # it, since the picker must still open.
        with mock.patch.dict(os.environ, {"HERDR_CONFIG_PATH": "/nope/config.toml"}):
            dirs, roots = pp.worktree_locations()
        self.assertEqual((dirs, roots), (list(pp.FIXED_WORKTREE_DIRS), []))

    def test_a_malformed_setting_line_degrades_rather_than_raising(self):
        # An array is skipped by read_toml() rather than half-parsed, so the
        # key reads as absent.
        with tempfile.TemporaryDirectory() as d:
            path = write_config(d, '[worktrees]\ndirectory = ["a", "b"]\n')
            with mock.patch.dict(os.environ, {"HERDR_CONFIG_PATH": path}):
                dirs, roots = pp.worktree_locations()
        self.assertEqual((dirs, roots), (list(pp.FIXED_WORKTREE_DIRS), []))


class TouchedAt(unittest.TestCase):
    """The TOUCHED column: newest of the git index and the working tree."""

    def touch(self, path, when):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("x")
        os.utime(path, (when, when))

    def touched(self, build, dirs=None):
        """touched_at() for a scratch repo `build` fills in."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = make_project(os.path.join(tmp.name, "myrepo"))
        build(repo)
        with mock.patch.object(pp, "WORKTREE_DIRS",
                               list(pp.FIXED_WORKTREE_DIRS) if dirs is None else dirs):
            return repo, pp.touched_at(repo)

    def test_the_newest_working_tree_file_is_reported(self):
        # The control the prune cases below are measured against.
        def build(repo):
            self.touch(os.path.join(repo, "old.txt"), 1000)
            self.touch(os.path.join(repo, "src", "new.txt"), 2000)
        self.assertEqual(self.touched(build)[1], 2000)

    def test_a_nested_worktrees_files_do_not_count_as_the_parents_touch(self):
        # The live defect: a container one level down puts the worktree's own
        # working tree at depth 2, which the walk reads. The worktree is its own
        # row now, so its work must not read as work on the parent.
        def build(repo):
            self.touch(os.path.join(repo, "own.txt"), 1000)
            self.touch(os.path.join(repo, "worktrees", "feat-x", "README.md"), 9000)
        self.assertEqual(self.touched(build)[1], 1000)

    def test_herdrs_own_container_is_not_walked(self):
        # Measured: under .worktrees/<repo>/<slug> a worktree's own files sit at
        # depth 3, which the walk never reached even before the prune. So what
        # is pinned here is the container being pruned outright — the deepest
        # level the walk does read inside it is ignored too.
        def build(repo):
            self.touch(os.path.join(repo, "own.txt"), 1000)
            self.touch(os.path.join(repo, ".worktrees", "myrepo", "stamp"), 9000)
        self.assertEqual(self.touched(build)[1], 1000)

    def test_the_claude_container_is_not_walked(self):
        # Same shape one level shallower: .claude/worktrees is itself the
        # deepest directory the walk reads, so its contents are the fixture.
        def build(repo):
            self.touch(os.path.join(repo, "own.txt"), 1000)
            self.touch(os.path.join(repo, ".claude", "worktrees", "stamp"), 9000)
        self.assertEqual(self.touched(build)[1], 1000)

    def test_the_prune_is_by_path_so_the_rest_of_dot_claude_still_counts(self):
        # ".claude" itself is ordinary repo content. Pruning by name would
        # silently stop settings edits counting as a touch.
        def build(repo):
            self.touch(os.path.join(repo, "own.txt"), 1000)
            self.touch(os.path.join(repo, ".claude", "settings.json"), 9000)
        self.assertEqual(self.touched(build)[1], 9000)

    def test_a_configured_container_is_pruned_as_well(self):
        def build(repo):
            self.touch(os.path.join(repo, "own.txt"), 1000)
            self.touch(os.path.join(repo, "trees", "b", "f.txt"), 9000)
        self.assertEqual(self.touched(build, dirs=["trees"])[1], 1000)

    def test_a_directory_merely_sharing_a_prefix_is_not_pruned(self):
        def build(repo):
            self.touch(os.path.join(repo, "worktrees-notes", "f.txt"), 9000)
        self.assertEqual(self.touched(build)[1], 9000)

    def test_the_same_name_somewhere_else_in_the_tree_still_counts(self):
        # The other half of "by path, not by name": a container is only a
        # container at the place repos() looks for it. A matching name deeper
        # in the tree is ordinary content, and pruning it would lose real work.
        def build(repo):
            self.touch(os.path.join(repo, "src", "worktrees", "f.txt"), 9000)
        self.assertEqual(self.touched(build)[1], 9000)

    def test_the_git_index_still_counts(self):
        def build(repo):
            self.touch(os.path.join(repo, ".git", "index"), 5000)
            self.touch(os.path.join(repo, "own.txt"), 1000)
        self.assertEqual(self.touched(build)[1], 5000)


class Kind(unittest.TestCase):
    """Repo vs. linked worktree, decided by the shape of .git."""

    def kind_of(self, make):
        """Build a project dir, run `make` on its .git path, classify it."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "proj")
            os.mkdir(p)
            make(os.path.join(p, ".git"))
            return pp.row_kind(p)

    def test_directory_dot_git_is_a_repo(self):
        self.assertEqual(self.kind_of(os.mkdir), "repo")

    def test_file_dot_git_is_a_worktree(self):
        def write_pointer(g):
            with open(g, "w", encoding="utf-8") as f:
                f.write("gitdir: /x/parent/.git/worktrees/proj\n")
        self.assertEqual(self.kind_of(write_pointer), "worktree")

    def test_worktree_with_a_deleted_parent_still_classifies(self):
        # `git -C` fails outright on these, which is why row_kind() only stats.
        def dangling(g):
            with open(g, "w", encoding="utf-8") as f:
                f.write("gitdir: /x/deleted/.git/worktrees/proj\n")
        self.assertEqual(self.kind_of(dangling), "worktree")

    def test_absent_dot_git_reads_as_a_repo(self):
        # repos() only ever yields paths that have a .git, so this is
        # unreachable today. Pinned so a future caller sees the fallback.
        self.assertEqual(self.kind_of(lambda g: None), "repo")


class ParentRepo(unittest.TestCase):
    """The repo a linked worktree belongs to, read out of its gitdir pointer.

    open_project() names that repo to Herdr so the sidebar can nest the
    checkout under it. A wrong answer nests it under an unrelated repo, so
    every shape that is not exactly git's must come back None.
    """

    def derive(self, pointer):
        """(checkout, parent_repo(checkout)) for a .git holding `pointer`."""
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        checkout = os.path.join(d.name, "feat-x")
        os.makedirs(checkout)
        with open(os.path.join(checkout, ".git"), "w", encoding="utf-8") as f:
            f.write(pointer)
        return checkout, pp.parent_repo(checkout)

    # The three pointer shapes a real machine writes, over placeholder paths.
    # All three layouts place the CHECKOUT differently and the admin path
    # identically, which is the whole reason one parse serves them all.
    # Herdr's sibling <parent>/worktrees/<repo>/<slug> gets no fixture of its
    # own for exactly that reason: its pointer is byte-identical in shape to
    # the flat one below, so a fourth copy would assert the same parse twice.

    def test_the_repository_nested_layout_yields_the_parent(self):
        # Herdr's, while [worktrees] directory is ".worktrees": the checkout
        # lives at <repo>/.worktrees/<repo>/<slug>.
        _, parent = self.derive(
            "gitdir: /x/code/group-a/repo-one/.git/worktrees/feat-x\n")
        self.assertEqual(parent, "/x/code/group-a/repo-one")

    def test_the_flat_layout_yields_the_parent(self):
        # The checkout sits under <root>/worktrees/<repo>/<slug>, nowhere near
        # its repo. These rows nest correctly in the sidebar today only because
        # Herdr opened them; through the picker they floated like the rest.
        _, parent = self.derive(
            "gitdir: /x/code/group-b/repo-two/.git/worktrees/feat-y\n")
        self.assertEqual(parent, "/x/code/group-b/repo-two")

    def test_claude_codes_layout_yields_the_parent(self):
        _, parent = self.derive(
            "gitdir: /x/code/repo-three/.git/worktrees/slug-1a2b\n")
        self.assertEqual(parent, "/x/code/repo-three")

    def test_a_relative_pointer_is_resolved_against_the_checkout(self):
        # `git worktree add --relative-paths`, and the worktree.useRelativePaths
        # setting, write one of these. Handed to Herdr as --cwd unresolved it
        # would name a directory relative to wherever the picker happens to run.
        checkout, parent = self.derive("gitdir: ../myrepo/.git/worktrees/feat-x\n")
        self.assertEqual(parent,
                         os.path.join(os.path.dirname(checkout), "myrepo"))

    def test_an_ordinary_repo_has_no_worktree_parent(self):
        # Its .git is a directory, so the read raises before any parsing. This
        # is what keeps repo rows on `workspace create` without a second stat.
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(pp.parent_repo(make_project(os.path.join(d, "r"))))

    def test_a_submodule_pointer_yields_none(self):
        # A submodule's .git is a FILE too, so row_kind() calls it a
        # worktree, but it points into .git/modules and has no worktree parent
        # to name.
        # Nesting it under the superproject would be a different claim entirely.
        self.assertIsNone(self.derive("gitdir: /x/super/.git/modules/sub\n")[1])

    def test_an_admin_dir_outside_dot_git_yields_none(self):
        # "worktrees" alone is not the shape: the component above it has to be
        # .git. A prefix or a single-name test would admit this.
        self.assertIsNone(self.derive("gitdir: /x/parent/git/worktrees/wt\n")[1])

    def test_an_admin_dir_with_no_worktrees_component_yields_none(self):
        self.assertIsNone(self.derive("gitdir: /x/parent/.git\n")[1])

    def test_a_line_whose_key_is_not_gitdir_yields_none(self):
        # The KEY is checked, not merely the value's shape. The fixture is
        # shaped to make that visible: an admin path that passes every shape
        # check below, behind a key that is not "gitdir". A .git file holding
        # anything else is malformed, and malformed must not name a parent.
        self.assertIsNone(
            self.derive("worktreedir: /x/parent/.git/worktrees/wt\n")[1])

    def test_a_gitdir_key_with_no_target_yields_none(self):
        self.assertIsNone(self.derive("gitdir:   \n")[1])

    def test_an_empty_dot_git_yields_none(self):
        self.assertIsNone(self.derive("")[1])

    def test_a_missing_dot_git_yields_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(pp.parent_repo(os.path.join(d, "gone")))

    def test_surrounding_whitespace_around_the_target_is_ignored(self):
        # Git pads neither end, but a file edited by hand may. Unstripped, the
        # padded path stops looking absolute and gets joined onto the checkout.
        _, parent = self.derive("gitdir:  /x/parent/.git/worktrees/wt  \n")
        self.assertEqual(parent, "/x/parent")

    def test_only_the_first_line_is_read(self):
        # The fixture is shaped to make the difference visible: read whole, the
        # two lines splice into one path that still passes every shape check
        # and yields "/x/parent/.git/worktrees/wt\n/x/other" as the repo to
        # nest under. A second line is not a shape git writes, so anything
        # carrying one is malformed and must not produce a parent by accident.
        _, parent = self.derive("gitdir: /x/parent/.git/worktrees/wt\n"
                                "/x/other/.git/worktrees/wt2\n")
        self.assertEqual(parent, "/x/parent")

    def test_a_trailing_separator_on_the_admin_path_is_tolerated(self):
        _, parent = self.derive("gitdir: /x/parent/.git/worktrees/wt/\n")
        self.assertEqual(parent, "/x/parent")


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
        cut = pp.elide("group-a/a-project-with-a-very-long-name", 12)
        self.assertTrue(cut.startswith("group-a/"))


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

    row_kind() hits the filesystem, so it is stubbed per path: these tests are
    about which value lands in which field, not about how it is derived.

    touched_at() is stubbed to RAISE. The touch time reaches build_lines on the
    row, computed once by order_rows; a build_lines that reaches for the
    filesystem instead walks every working tree a second time. Every test in
    this class therefore doubles as the guard on that.
    """

    def build(self, rows, status=None, kinds=None, touched=None):
        """`rows` are (label, path) pairs here; the touch time is attached from
        `touched` (keyed by path, default 0) to keep the fixtures readable."""
        triples = [(l, p, (touched or {}).get(p, 0)) for l, p in rows]
        with mock.patch.object(pp, "row_kind",
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
             mock.patch.object(pp, "row_kind", return_value="repo"):
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


class ManifestIsValidToml(unittest.TestCase):
    """The manifest has to PARSE. Nothing else here opens it at all.

    Measured on 0.8.2, 2026-09-08: Herdr re-reads herdr-plugin.toml from disk
    when it dispatches, rather than trusting the copy it cached in
    plugins.json. An edit takes effect on the very next dispatch — no re-link,
    no restart, no reload-config. This is the other half of that: a syntax
    error in the manifest stops every dispatch for this plugin, with no toast,
    no error and nothing surfaced. It simply goes quiet, and every route into
    this plugin runs through the one [[panes]] entry the manifest declares, so
    what goes quiet is the picker itself.

    read_toml() in the picker cannot answer this question and is deliberately
    not used here. It extracts a fixed set of wanted scalars out of Herdr's own
    config and ignores every line it does not recognise, so it returns happily
    on a file no TOML parser would accept. That tolerance is right for optional
    user config and wrong for a validity check.

    Skipped, loudly, where tomllib is unavailable: see the banner at the top of
    this file. A skip is not a pass — and this suite already skips one other
    test when the sibling plugin is not checked out beside it, so the skip
    COUNT in the summary cannot tell you which checks did not run. The banner
    is what distinguishes this one.
    """

    def setUp(self):
        if tomllib is None:
            self.skipTest("herdr-plugin.toml was NOT parsed: " + NO_TOML)

    def parse(self, path):
        """The manifest at `path`, read exactly as Herdr's loader would."""
        with open(path, "rb") as f:
            return tomllib.load(f)

    def test_the_manifest_parses(self):
        try:
            self.parse(MANIFEST)
        except tomllib.TOMLDecodeError as e:
            self.fail("herdr-plugin.toml is not valid TOML: %s" % e)

    def test_a_typo_in_the_manifest_is_really_caught(self):
        """A canary on the test above, which would pass for two very different
        reasons: the manifest is valid, or nothing is really parsing it.

        The fixture is the REAL manifest plus one unterminated string, which is
        what a typo looks like, written to a temporary directory. Corrupting
        the real file to prove the point would be the same class of mistake
        this test exists to catch.
        """
        with open(MANIFEST, "rb") as f:
            typo = f.read() + b'\nname = "unterminated\n'
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        broken = os.path.join(tmp.name, "herdr-plugin.toml")
        with open(broken, "wb") as f:
            f.write(typo)
        with self.assertRaises(tomllib.TOMLDecodeError):
            self.parse(broken)


if __name__ == "__main__":
    unittest.main()
