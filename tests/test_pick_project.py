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


class CreateWorkspaceHarness:
    """Shared by the two classes below. A mixin rather than a base TestCase,
    which would run every inherited test a second time."""

    # What open_project() answers with. The workspace id is the only field read
    # now: the tab id and the root pane id went out with the splits, and the
    # sibling asks Herdr for whatever it needs from the workspace id alone.
    RES = {"workspace": {"workspace_id": "w9"}}

    # A resolved layout_command(), which is an absolute path to the SIBLING
    # plugin's checkout. Its two parent directories are read by layout_env(),
    # so it is a real-looking path rather than a bare name.
    LAYOUT = "/x/panes/bin/agent-layout"

    def create(self, res=RES, layout=LAYOUT, label="proj", path="/x/proj"):
        """(workspace id, the herdr calls, the Popen mock) from ONE create.

        All three come out of one run on purpose: the handoff assertions hold
        the sibling's argv against the workspace id that same call returned, and
        taking them from two creates would compare two workspaces.
        """
        with mock.patch.object(pp, "herdr", return_value=res) as h, \
             mock.patch.object(pp.subprocess, "Popen") as popen, \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            wid = pp.create_workspace(label, path, layout)
        return wid, [c.args for c in h.call_args_list], popen

    def handoff(self, **kwargs):
        """The argv the sibling plugin is fired with, from one create."""
        return self.create(**kwargs)[2].call_args.args[0]


class CreateWorkspace(CreateWorkspaceHarness, unittest.TestCase):
    """Opening the workspace, and handing it to the sibling to lay out."""

    def test_the_opened_workspaces_id_is_returned(self):
        wid, calls, _ = self.create()
        self.assertEqual(wid, "w9")
        self.assertEqual(calls[0][:2], ("workspace", "create"))

    def test_create_failure_dies(self):
        # The one fatal step. Everything after it is best effort, because a
        # workspace that opened badly still beats abandoning the rest of a
        # multi-select.
        with self.assertRaises(SystemExit):
            self.create(res=None)

    def test_the_picker_builds_no_part_of_the_layout(self):
        # The whole point of the delegation, and the assertion that catches a
        # split, a rename or a `pane run` creeping back into this file: opening
        # the workspace is the ONLY thing this sends to Herdr.
        _, calls, _ = self.create()
        self.assertEqual(len(calls), 1)

    def test_no_agent_is_started_here_either(self):
        # `agent start` moved into the sibling with the panes. The detached
        # process this fires is the sibling itself, never herdr.
        argv = self.handoff()
        self.assertEqual(argv[0], self.LAYOUT)
        self.assertNotIn("start", argv)

    def test_the_workspace_is_opened_before_it_is_handed_over(self):
        # The sibling is given a workspace id, so there is nothing to hand over
        # until the create has answered. Ordering the two the other way round
        # is not a thing that raises — it is a NameError-free run against an id
        # that does not exist yet, reported only as a toast from the sibling.
        manager = mock.Mock()
        with mock.patch.object(pp, "herdr", return_value=self.RES) as h, \
             mock.patch.object(pp.subprocess, "Popen") as popen, \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            manager.attach_mock(h, "herdr")
            manager.attach_mock(popen, "popen")
            pp.create_workspace("proj", "/x/proj", self.LAYOUT)
        self.assertEqual([c[0] for c in manager.mock_calls], ["herdr", "popen"])

    def test_the_label_is_what_the_workspace_is_opened_under(self):
        # This file no longer derives the agent name, and the sibling derives it
        # from the workspace LABEL. So the label reaching Herdr unchanged is the
        # whole of the picker's remaining part in naming the agent: pass some
        # other string here and every agent comes up under the wrong name.
        _, calls, _ = self.create(label="my-proj")
        self.assertEqual(calls[0][calls[0].index("--label") + 1], "my-proj")

    def test_a_worktree_row_is_handed_over_exactly_like_a_repo_row(self):
        # A worktree is opened by a different command, and the handoff must not
        # notice. open_project() promises `worktree open` answers with the same
        # workspace the create does, and this is where that promise is spent.
        with mock.patch.object(pp, "parent_repo", return_value="/x/myrepo"), \
             mock.patch.object(pp, "herdr", return_value=self.RES) as h, \
             mock.patch.object(pp.subprocess, "Popen") as popen, \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            wid = pp.create_workspace("feat-x", "/x/wt/feat-x", self.LAYOUT)
        calls = [c.args for c in h.call_args_list]
        self.assertEqual(wid, "w9")
        self.assertEqual(calls[0][:2], ("worktree", "open"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(popen.call_args.args[0][:3],
                         [self.LAYOUT, "--workspace", "w9"])

    def test_no_sibling_still_opens_the_workspace_and_fires_nothing(self):
        # The absent-sibling path at this level: the sibling is a separate
        # install that nothing here declares a dependency on, so `layout` being
        # None is a legitimate state and not an error. A workspace with one
        # bare pane is still a workspace, and dying over the missing layout
        # would abandon the rest of a multi-select over a plugin the user never
        # asked for. The notice is the CALLER's job — see ResolveLayout.
        wid, calls, popen = self.create(layout=None)
        self.assertEqual(wid, "w9")
        self.assertEqual(len(calls), 1)
        popen.assert_not_called()


class LayoutHandoff(CreateWorkspaceHarness, unittest.TestCase):
    """The call that replaces the layout this file used to build.

    One flag and nothing else, run detached with a corrected environment. Each
    part of that is load-bearing and none of it raises when it is wrong: a
    missing --workspace lays out whichever workspace happens to be focused, an
    --agent-name added back takes the sibling off its own dedupe and collides
    two agents in one multi-select, and a synchronous call stalls the picker for
    as long as the agent takes to come up. All three still open the workspaces.
    """

    def test_the_sibling_is_run_with_the_workspace_and_nothing_else(self):
        self.assertEqual(self.handoff(), [self.LAYOUT, "--workspace", "w9"])

    def test_the_workspace_handed_over_is_the_one_that_was_opened(self):
        # Held against the create's own answer rather than against "w9", so a
        # handoff that passed some other id could not satisfy it.
        wid, _, popen = self.create()
        argv = popen.call_args.args[0]
        self.assertEqual(argv[argv.index("--workspace") + 1], wid)

    def test_no_agent_name_is_passed(self):
        # The point of this handoff. Given --agent-name the sibling hands that
        # name to Herdr verbatim and fails loudly when a live agent holds it;
        # left off, it derives the name from the workspace label and retries as
        # base-2, base-3 on agent_name_taken. Passing a name here — even a
        # correct one — is what opts the picker back out of that dedupe, and
        # nothing about it raises: it shows up as one pane of a multi-select
        # missing its Claude.
        self.assertNotIn("--agent-name", self.handoff())

    def test_no_agent_flag_is_passed(self):
        # --no-agent stops the sibling starting an agent at all, and this is
        # deliberately the shape that keeps the agent on the sibling's side. The
        # other shape works too and would put ten herdr round trips back on the
        # critical path of a multi-select.
        self.assertNotIn("--no-agent", self.handoff())

    def test_the_call_is_detached_and_its_output_discarded(self):
        # `agent start` blocks until the agent is ready, 30s by default, and
        # --timeout cannot be cut below its 3000ms minimum. Waiting on that
        # once per selected project is the stall this picker exists not to
        # have. start_new_session also keeps the sibling alive past the popup,
        # which exits as soon as main() returns.
        _, _, popen = self.create()
        kwargs = popen.call_args.kwargs
        self.assertIs(kwargs["start_new_session"], True)
        self.assertIs(kwargs["stdout"], pp.subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], pp.subprocess.DEVNULL)

    def test_the_sibling_is_run_with_the_corrected_environment(self):
        # The two corrections themselves are LayoutEnv's; this pins that the
        # handoff uses them rather than inheriting this process's environment,
        # which would send the sibling looking for its own files in THIS
        # plugin's checkout and config directory.
        _, _, popen = self.create()
        self.assertEqual(popen.call_args.kwargs["env"],
                         pp.layout_env(self.LAYOUT))


class LayoutCommand(unittest.TestCase):
    """Finding the sibling plugin's executable, and failing closed without it.

    The path is asked for rather than guessed at, because nothing in this
    plugin knows where the sibling was installed: it may be a GitHub install
    under Herdr's own directory or a local link anywhere on the disk.
    """

    def call(self, reply, executable=True):
        with mock.patch.object(pp, "herdr", return_value=reply) as h, \
             mock.patch.object(pp.os, "access", return_value=executable):
            got = pp.layout_command()
        return got, h.call_args.args if h.call_args else None

    def test_the_sibling_is_asked_for_by_its_own_plugin_id(self):
        # --json because `plugin list` prints a human table by default, unlike
        # every other command herdr() parses. --plugin so the answer is one
        # plugin or nothing, rather than a list to search.
        _, args = self.call({"plugins": [{"plugin_root": "/x/panes"}]})
        self.assertEqual(args, ("plugin", "list", "--plugin",
                                "mikebronner.agentic-panes-layout", "--json"))

    def test_the_id_is_the_siblings_and_not_this_plugins(self):
        # A copy-paste of this plugin's own id here would ask Herdr for the
        # picker, find it, and hand every workspace to a bin/agent-layout that
        # does not exist in this checkout.
        self.assertEqual(pp.LAYOUT_PLUGIN, "mikebronner.agentic-panes-layout")
        with open(MANIFEST, encoding="utf-8") as f:
            self.assertNotIn(pp.LAYOUT_PLUGIN, f.read())

    def test_the_command_is_the_executable_under_the_reported_root(self):
        got, _ = self.call({"plugins": [{"plugin_root": "/x/panes"}]})
        self.assertEqual(got, os.path.join("/x/panes", "bin", "agent-layout"))

    def test_no_plugin_row_yields_none(self):
        # The first shape of an absent sibling, and the ordinary one: 0.8.2
        # answers an unknown --plugin id with an empty list rather than an
        # error, so nothing here raises and nothing distinguishes "not
        # installed" from "installed" except this emptiness.
        self.assertIsNone(self.call({"plugins": []})[0])

    def test_a_command_that_is_not_executable_yields_none(self):
        # The second shape: the sibling is installed and its file lost the mode
        # bit, or the checkout is there and bin/agent-layout is not. It is run
        # as a program, so a file that cannot be executed is exactly as unusable
        # as an absent one and the caller's degraded path is right for both.
        self.assertIsNone(
            self.call({"plugins": [{"plugin_root": "/x/panes"}]},
                      executable=False)[0])

    def test_the_executable_bit_is_what_is_asked_for(self):
        # os.F_OK would pass a present-but-unrunnable file straight through to
        # Popen, which raises PermissionError inside a fire-and-forget call
        # nothing is watching.
        with mock.patch.object(pp, "herdr",
                               return_value={"plugins": [{"plugin_root": "/x/p"}]}), \
             mock.patch.object(pp.os, "access", return_value=True) as access:
            pp.layout_command()
        self.assertEqual(access.call_args.args[1], os.X_OK)

    def test_a_row_with_no_root_yields_none(self):
        # An empty root would join to "bin/agent-layout", a RELATIVE path that
        # os.access resolves against the picker's own cwd.
        self.assertIsNone(self.call({"plugins": [{"plugin_root": ""}]})[0])
        self.assertIsNone(self.call({"plugins": [{}]})[0])

    def test_an_explicitly_null_root_yields_none(self):
        # A null is not a missing key, and os.path.join would raise on it.
        self.assertIsNone(self.call({"plugins": [{"plugin_root": None}]})[0])

    def test_a_reply_with_no_plugins_key_yields_none(self):
        self.assertIsNone(self.call({})[0])

    def test_an_unreachable_server_yields_none_rather_than_raising(self):
        # herdr() answers None for a non-zero exit or unparseable output. The
        # picker is mid-run with workspaces to open, so this degrades like any
        # other absent sibling.
        self.assertIsNone(self.call(None)[0])

    def test_a_disabled_plugin_is_still_run(self):
        # `plugin disable` stops Herdr DISPATCHING EVENTS to a plugin, which is
        # the sibling's other way in. This is a direct call to an executable and
        # is not dispatch, so refusing to lay out a picker workspace because the
        # sibling's event hook was turned off would be a surprise from a plugin
        # the user did not touch.
        got, _ = self.call({"plugins": [{"plugin_root": "/x/panes",
                                         "enabled": False}]})
        self.assertEqual(got, "/x/panes/bin/agent-layout")


class ResolveLayout(unittest.TestCase):
    """Resolving the sibling once for a whole run, and saying so when it is not
    there.

    The sibling is a separate install and nothing in herdr-plugin.toml declares
    a dependency on it, so its absence is a legitimate state rather than a bug.
    A silent absence would still be wrong: the user asked for a project and got
    a bare pane, with nothing on screen to explain it.
    """

    def resolve(self, creating, command):
        with mock.patch.object(pp, "layout_command",
                               return_value=command) as lc, \
             mock.patch.object(pp, "warn") as warn:
            got = pp.resolve_layout(creating)
        return got, lc, warn

    def test_a_present_sibling_is_returned_and_says_nothing(self):
        got, _, warn = self.resolve(True, "/x/panes/bin/agent-layout")
        self.assertEqual(got, "/x/panes/bin/agent-layout")
        warn.assert_not_called()

    def test_an_absent_sibling_yields_none_and_warns_once(self):
        got, _, warn = self.resolve(True, None)
        self.assertIsNone(got)
        self.assertEqual(warn.call_count, 1)

    def test_the_notice_names_the_plugin_to_install(self):
        # A toast reading "the layout plugin is missing" tells the user nothing
        # they can act on. The id is what `herdr plugin install` takes.
        _, _, warn = self.resolve(True, None)
        self.assertIn(pp.LAYOUT_PLUGIN, warn.call_args.args[0])

    def test_a_run_that_creates_nothing_asks_herdr_nothing(self):
        # A selection that only CLOSES workspaces costs no `plugin list` call.
        # And it raises no notice: a layout that was never going to be applied
        # is not something to interrupt the user about.
        got, lc, warn = self.resolve(False, "/x/panes/bin/agent-layout")
        self.assertIsNone(got)
        lc.assert_not_called()
        warn.assert_not_called()

    def test_a_run_that_creates_nothing_is_silent_even_with_no_sibling(self):
        got, lc, warn = self.resolve(False, None)
        self.assertIsNone(got)
        lc.assert_not_called()
        warn.assert_not_called()

    def test_the_notice_goes_to_both_channels_warn_owns(self):
        # Not a duplicate of the warn() tests elsewhere: it pins that the
        # absent sibling is reported through warn() rather than through die(),
        # which would stop the run, or through a bare stderr write, which the
        # popup destroys before it can be read.
        with mock.patch.object(pp, "layout_command", return_value=None), \
             mock.patch.object(pp, "HERDR", "/bin/herdr"), \
             mock.patch.object(pp.subprocess, "run") as run, \
             mock.patch.object(pp.sys, "stderr", io.StringIO()) as err:
            pp.resolve_layout(True)
        self.assertIn(pp.LAYOUT_PLUGIN, err.getvalue())
        self.assertEqual(run.call_args.args[0][:2],
                         ["/bin/herdr", "notification"])


class LayoutEnv(unittest.TestCase):
    """The environment the sibling is run with: this one, with two corrections.

    Both corrections exist because the sibling reads the same two variables
    this plugin was handed by Herdr, and would read them as its own. Neither
    mistake raises: the sibling would find no bin/config-env under this
    plugin's root and lay out on its built-in defaults, having silently ignored
    every setting the user wrote.
    """

    COMMAND = "/x/panes/bin/agent-layout"

    def env(self, base):
        with mock.patch.dict(os.environ, base, clear=True):
            return pp.layout_env(self.COMMAND)

    def test_the_plugin_root_is_repointed_at_the_siblings_checkout(self):
        # Herdr injects THIS plugin's root, which is where the sibling would
        # otherwise look for its own bin/config-env. Derived from the command
        # so the two cannot disagree.
        env = self.env({"HERDR_PLUGIN_ROOT": "/x/picker"})
        self.assertEqual(env["HERDR_PLUGIN_ROOT"], "/x/panes")

    def test_the_root_is_set_even_when_this_process_has_none(self):
        self.assertEqual(self.env({})["HERDR_PLUGIN_ROOT"], "/x/panes")

    def test_the_config_dir_is_dropped(self):
        # The mirror image: the sibling takes HERDR_PLUGIN_CONFIG_DIR as "your
        # config directory" and only asks Herdr for its own when the variable
        # is absent. Left in place, it would read THIS plugin's .env as its own
        # and pick up settings meant for the picker alone.
        env = self.env({"HERDR_PLUGIN_CONFIG_DIR": "/x/picker/config"})
        self.assertNotIn("HERDR_PLUGIN_CONFIG_DIR", env)

    def test_an_absent_config_dir_is_not_an_error(self):
        self.assertNotIn("HERDR_PLUGIN_CONFIG_DIR", self.env({}))

    def test_everything_else_passes_through(self):
        # What keeps the shared AGENT_LAYOUT_ vocabulary working now that the
        # picker reads none of it. A name exported in the real environment, or
        # written in this plugin's .env — which the loaders at the top of the
        # script fold into os.environ — arrives at the sibling as a real
        # environment variable and beats the sibling's own .env.
        env = self.env({"AGENT_LAYOUT_RATIO": "0.3", "PATH": "/usr/bin"})
        self.assertEqual(env["AGENT_LAYOUT_RATIO"], "0.3")
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_the_process_environment_is_not_mutated(self):
        # A copy, not os.environ itself. Popping the config dir out of the live
        # environment would change what every LATER call in the same run sees,
        # including this picker's own config loaders.
        with mock.patch.dict(os.environ,
                             {"HERDR_PLUGIN_CONFIG_DIR": "/x/picker/config",
                              "HERDR_PLUGIN_ROOT": "/x/picker"}, clear=True):
            pp.layout_env(self.COMMAND)
            self.assertEqual(os.environ["HERDR_PLUGIN_CONFIG_DIR"],
                             "/x/picker/config")
            self.assertEqual(os.environ["HERDR_PLUGIN_ROOT"], "/x/picker")


class LayoutIsResolvedOncePerRun(unittest.TestCase):
    """What driving main() shows that a unit test of resolve_layout() cannot:
    how many times a whole selection resolves the sibling, and how many notices
    one absent install produces.

    N toasts for one missing plugin would bury the projects the user just asked
    for, which is why the resolution sits in main() and not in
    create_workspace(). Nothing about that placement raises if it moves.
    """

    def run_main(self, chosen, command):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(pp, "HERDR", "/bin/herdr"), \
             mock.patch.object(pp, "ensure_fzf", return_value=True), \
             mock.patch.object(pp, "repos", return_value=["/x/a", "/x/b"]), \
             mock.patch.object(pp, "open_workspaces", return_value=(None, [])), \
             mock.patch.object(pp, "resolve_theme", return_value=({}, {})), \
             mock.patch.object(pp, "order_rows", return_value=([], [])), \
             mock.patch.object(pp, "build_lines", return_value=[]), \
             mock.patch.object(pp, "parse_selection", return_value=chosen), \
             mock.patch.object(pp, "layout_command", return_value=command) as lc, \
             mock.patch.object(pp, "warn") as warn, \
             mock.patch.object(pp, "herdr",
                               return_value=CreateWorkspaceHarness.RES), \
             mock.patch.object(pp.subprocess, "run"), \
             mock.patch.object(pp.subprocess, "Popen") as popen:
            pp.main()
        return lc, warn, popen

    def test_two_new_workspaces_resolve_the_sibling_once(self):
        lc, warn, popen = self.run_main(["/x/a", "/x/b"],
                                        "/x/panes/bin/agent-layout")
        self.assertEqual(lc.call_count, 1)
        self.assertEqual(popen.call_count, 2)
        warn.assert_not_called()

    def test_an_absent_sibling_warns_once_for_two_workspaces(self):
        # The user is told about one missing install one time, and still gets
        # both projects.
        lc, warn, popen = self.run_main(["/x/a", "/x/b"], None)
        self.assertEqual(lc.call_count, 1)
        self.assertEqual(warn.call_count, 1)
        popen.assert_not_called()

    def test_a_selection_that_creates_nothing_never_asks(self):
        lc, warn, popen = self.run_main([], None)
        lc.assert_not_called()
        warn.assert_not_called()
        popen.assert_not_called()


class LayoutSettings(unittest.TestCase):
    """The ten AGENT_LAYOUT_ settings: documented here, applied by the sibling.

    They carry the sibling herdr-plugin-agentic-panes-layout's prefix rather
    than this plugin's HERDR_PICKER_ one because they are ONE vocabulary for one
    layout, and both READMEs promise that a value learned in either place reads
    the same in the other. That promise survived the delegation: what changed is
    who applies them, not what they are called. So this class documents them and
    asserts the inverse of what it used to — that the picker reads none of them
    itself, and passes the environment carrying them straight through.
    """

    DEFAULTS = {"KIND": "claude", "TAB_NAME": "agent",
                "DIRECTION": "right", "RATIO": "0.5",
                "TOOL_COMMAND": "lazygit", "TOOL_DIRECTION": "down",
                "TOOL_RATIO": "0.6", "AGENT_LABEL": "agent",
                "TOOL_LABEL": "lazygit", "SHELL_LABEL": "shell"}

    README = os.path.join(HERE, "..", "README.md")

    def test_no_layout_setting_is_read_by_this_plugin(self):
        # The inverse of the assertion this used to make, and the point of
        # delegating: one reader for one layout. A setting read here as well
        # would be applied twice, from two files that cannot see each other,
        # and the picker's copy would win by racing the sibling's.
        #
        # Read from the source rather than from the module, because a constant
        # bound at import leaves nothing to observe afterwards.
        with open(pp.__file__, encoding="utf-8") as f:
            source = f.read()
        self.assertEqual(
            set(re.findall(r'os\.environ\.get\("(AGENT_LAYOUT_\w+)"\)', source)),
            set())

    def test_every_setting_still_reaches_the_sibling(self):
        # The other half: reading none of them is only correct because they are
        # all passed on. Dropping the environment, or filtering it, would make
        # every value in this README silently do nothing when set for the
        # picker.
        base = {f"AGENT_LAYOUT_{name}": value
                for name, value in self.DEFAULTS.items()}
        with mock.patch.dict(os.environ, base, clear=True):
            env = pp.layout_env("/x/panes/bin/agent-layout")
        for key, value in base.items():
            self.assertEqual(env.get(key), value, key)

    def test_no_layout_setting_has_a_home_in_the_plugins_config_toml(self):
        # The ownership rule, and the reason these ten stay environment-only
        # while the picker's own three moved into [picker]: the sibling plugin
        # reads these same names, and a value written into ONE plugin's private
        # file is invisible to the other. Folding them into PICKER_KEYS would
        # hand the sibling a setting it can never see.
        layout = {f"AGENT_LAYOUT_{name}" for name in self.DEFAULTS}
        self.assertEqual(set(pp.PICKER_KEYS.values()) & layout, set())

    def test_the_readme_documents_every_setting_at_its_default(self):
        # Doc-drift guard, in the shape KeyBindings uses below. The defaults
        # are the SIBLING's now, so this cannot be checked against a constant
        # in this file any more: the README is the only copy of them here, and
        # a wrong one is a promise this plugin cannot keep.
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

    def test_the_readme_names_the_sibling_as_the_one_that_applies_them(self):
        # The README documents ten settings this plugin does not read. Without
        # saying who does, that block reads as a list of things the picker
        # applies, which is exactly what it stopped doing.
        with open(self.README, encoding="utf-8") as f:
            readme = f.read()
        self.assertIn("herdr-plugin-agentic-panes-layout", readme)

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


class LoadPickerConfig(unittest.TestCase):
    """The [picker] table in the plugin's own config.toml."""

    def load(self, body, env=None):
        """Write `body` as a config.toml, load it over `env`, return the environ."""
        with tempfile.TemporaryDirectory() as d:
            path = write_config(d, body)
            with mock.patch.dict(os.environ, env or {}, clear=True):
                pp.load_picker_config(path)
                return dict(os.environ)

    def test_all_three_settings_are_applied(self):
        env = self.load('[picker]\nroot = "/x/code"\nhome = "base"\ndebug = true\n')
        self.assertEqual(env["HERDR_PICKER_ROOT"], "/x/code")
        self.assertEqual(env["HERDR_PICKER_HOME"], "base")
        self.assertEqual(env["HERDR_PICKER_DEBUG"], "1")

    def test_a_real_env_var_overrides_the_file(self):
        # The contract at the top of the module: a variable set for one run wins.
        env = self.load('[picker]\nroot = "/from/file"\n',
                        {"HERDR_PICKER_ROOT": "/from/env"})
        self.assertEqual(env["HERDR_PICKER_ROOT"], "/from/env")

    def test_only_the_named_settings_are_applied(self):
        # Whole-environ, so an unknown key under [picker] cannot quietly become
        # an environment variable of its own.
        env = self.load('[picker]\nroot = "/x/code"\nnonsense = "boom"\n')
        self.assertEqual(env, {"HERDR_PICKER_ROOT": "/x/code"})

    def test_a_key_outside_the_picker_table_is_not_claimed(self):
        # read_toml() qualifies by table, and this pins that the picker relies
        # on it: a root under some other table must not answer for picker.root.
        env = self.load('[worktrees]\nroot = "/x/wrong"\n\n'
                        '[picker]\nroot = "/x/right"\n')
        self.assertEqual(env, {"HERDR_PICKER_ROOT": "/x/right"})

    def test_a_missing_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {}, clear=True):
                pp.load_picker_config(os.path.join(d, "config.toml"))  # never created
                self.assertEqual(dict(os.environ), {})

    def test_an_unreadable_path_is_a_noop(self):
        # A directory where a file is expected: OSError, not a crash.
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {}, clear=True):
                pp.load_picker_config(d)
                self.assertEqual(dict(os.environ), {})

    def test_a_malformed_file_does_not_raise_and_sets_nothing(self):
        # A new config file is one more thing a user can typo, and the picker is
        # a popup: it has to open anyway, with the defaults it would have used.
        self.assertEqual(self.load("[picker\nroot /x/code\n}{\n"), {})

    def test_a_malformed_line_does_not_stop_the_valid_ones(self):
        env = self.load('[picker]\nroot /x/typo\nhome = "base"\n')
        self.assertEqual(env, {"HERDR_PICKER_HOME": "base"})

    def test_debug_false_leaves_debugging_off(self):
        # The one setting that is a boolean rather than a string. "false" is a
        # non-empty string, so applying it raw would switch debugging ON.
        env = self.load("[picker]\ndebug = false\n")
        self.assertEqual(env["HERDR_PICKER_DEBUG"], "")

    def test_debug_false_really_silences_the_debug_line(self):
        # End to end through the real reader: the assertion above only matters
        # because this is what the empty value buys.
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            path = write_config(d, "[picker]\ndebug = false\n")
            with mock.patch.dict(os.environ, {}, clear=True):
                pp.load_picker_config(path)
                pp.debug_discovery(0.026, ["/x/a"], {"bands": 1, "containers": 0}, out)
        self.assertEqual(out.getvalue(), "")

    def test_debug_true_really_writes_the_debug_line(self):
        # The other half of the pair, so a loader that set nothing at all would
        # not pass the test above by accident.
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            path = write_config(d, "[picker]\ndebug = true\n")
            with mock.patch.dict(os.environ, {}, clear=True):
                pp.load_picker_config(path)
                pp.debug_discovery(0.026, ["/x/a"], {"bands": 1, "containers": 0}, out)
        self.assertIn("picker: discovery", out.getvalue())


class ConfigPrecedence(unittest.TestCase):
    """Environment variable, then config.toml, then .env — in that order.

    The whole of the ordering lives in the call site at the top of the module:
    both loaders apply values with setdefault(), so whoever writes first wins.
    These run the two loaders in that same order over one config directory.
    """

    def resolve(self, toml=None, env_file=None, environ=None):
        """The environ after loading both files over `environ`, in order."""
        with tempfile.TemporaryDirectory() as d:
            if toml is not None:
                write_config(d, toml)
            if env_file is not None:
                with open(os.path.join(d, ".env"), "w", encoding="utf-8") as f:
                    f.write(env_file)
            with mock.patch.dict(os.environ, environ or {}, clear=True):
                pp.load_picker_config(os.path.join(d, "config.toml"))
                pp.load_env(os.path.join(d, ".env"))
                return dict(os.environ)

    def test_the_env_file_alone_still_works(self):
        # Mike has a live .env; a change that stopped reading it would present
        # as lost settings rather than as a migration.
        env = self.resolve(env_file="HERDR_PICKER_ROOT=/from/env-file\n")
        self.assertEqual(env["HERDR_PICKER_ROOT"], "/from/env-file")

    def test_config_toml_wins_over_the_env_file(self):
        # TOML is the format these settings are moving to, so a .env left behind
        # must not quietly outrank the file that replaced it.
        env = self.resolve(toml='[picker]\nroot = "/from/toml"\n',
                           env_file="HERDR_PICKER_ROOT=/from/env-file\n")
        self.assertEqual(env["HERDR_PICKER_ROOT"], "/from/toml")

    def test_the_env_file_still_supplies_what_the_toml_omits(self):
        # The two layers merge per setting; config.toml is not all-or-nothing.
        env = self.resolve(toml='[picker]\nroot = "/from/toml"\n',
                           env_file="HERDR_PICKER_HOME=base\n")
        self.assertEqual(env["HERDR_PICKER_ROOT"], "/from/toml")
        self.assertEqual(env["HERDR_PICKER_HOME"], "base")

    def test_a_real_env_var_beats_both_files(self):
        env = self.resolve(toml='[picker]\nroot = "/from/toml"\n',
                           env_file="HERDR_PICKER_ROOT=/from/env-file\n",
                           environ={"HERDR_PICKER_ROOT": "/from/env"})
        self.assertEqual(env["HERDR_PICKER_ROOT"], "/from/env")

    def test_debug_false_in_the_toml_beats_the_env_file_turning_it_on(self):
        # Why the false case SETS the empty string rather than skipping the key:
        # skipping would let a stale .env decide, which is the wrong file.
        env = self.resolve(toml="[picker]\ndebug = false\n",
                           env_file="HERDR_PICKER_DEBUG=1\n")
        self.assertEqual(env["HERDR_PICKER_DEBUG"], "")

    def test_a_malformed_toml_leaves_the_env_file_working(self):
        # The degradation that matters: a typo in the new file must not take the
        # old one down with it.
        env = self.resolve(toml="[picker\nroot /x/typo\n",
                           env_file="HERDR_PICKER_ROOT=/from/env-file\n")
        self.assertEqual(env["HERDR_PICKER_ROOT"], "/from/env-file")


class ModuleBootstrap(unittest.TestCase):
    """The settings the module binds at import, from a real config directory.

    ConfigPrecedence above runs the two loaders in the order this class checks
    the module actually calls them in. That call site is where the precedence
    lives, and re-executing the module is the only way to see it: DEV and
    HOME_LABEL are bound once, at import, so an environment set afterwards
    changes nothing. The loader here is the suite's own, so
    sys.dont_write_bytecode at the top of this file still holds and no .pyc is
    written for the re-execution.
    """

    def boot(self, files, environ=None):
        """Re-execute the script over a plugin config dir holding `files`."""
        with tempfile.TemporaryDirectory() as d:
            for name, body in files.items():
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write(body)
            # PATH is carried over because the script reads it at import, the
            # way any real run has one. HERDR_CONFIG_PATH is pinned at a file
            # that does not exist so the run cannot pick up the developer's own
            # Herdr config.
            env = dict(environ or {}, PATH=os.environ.get("PATH", ""),
                       HERDR_PLUGIN_CONFIG_DIR=d,
                       HERDR_CONFIG_PATH=os.path.join(d, "absent.toml"))
            with mock.patch.dict(os.environ, env, clear=True):
                fresh = importlib.util.module_from_spec(spec)
                loader.exec_module(fresh)
                return fresh

    def test_the_toml_supplies_the_root_and_the_home_label(self):
        with tempfile.TemporaryDirectory() as root:
            fresh = self.boot({"config.toml":
                               '[picker]\nroot = "%s"\nhome = "base"\n' % root})
            self.assertEqual(fresh.DEV, root)
            self.assertEqual(fresh.HOME_LABEL, "base")

    def test_the_env_file_still_supplies_them_on_its_own(self):
        with tempfile.TemporaryDirectory() as root:
            fresh = self.boot({".env": "HERDR_PICKER_ROOT=%s\n"
                                       "HERDR_PICKER_HOME=base\n" % root})
            self.assertEqual(fresh.DEV, root)
            self.assertEqual(fresh.HOME_LABEL, "base")

    def test_the_toml_is_loaded_before_the_env_file(self):
        # The ordering guard. Swapping the two calls at the top of the script
        # makes this the .env's value, because both loaders use setdefault().
        with tempfile.TemporaryDirectory() as root:
            fresh = self.boot({"config.toml": '[picker]\nroot = "%s"\n' % root,
                               ".env": "HERDR_PICKER_ROOT=/from/env-file\n"})
            self.assertEqual(fresh.DEV, root)

    def test_a_real_env_var_beats_both_files_at_import(self):
        with tempfile.TemporaryDirectory() as root:
            fresh = self.boot({"config.toml": '[picker]\nroot = "/x/toml"\n',
                               ".env": "HERDR_PICKER_ROOT=/from/env-file\n"},
                              environ={"HERDR_PICKER_ROOT": root})
            self.assertEqual(fresh.DEV, root)

    def test_neither_file_present_leaves_the_defaults(self):
        fresh = self.boot({})
        self.assertEqual(fresh.DEV, os.path.expanduser("~"))
        self.assertEqual(fresh.HOME_LABEL, "~")

    def test_a_malformed_toml_still_lets_the_module_import(self):
        # The picker is a popup: a typo in optional config must never be what
        # stops it appearing.
        fresh = self.boot({"config.toml": "[picker\nroot /x/typo\n}{\n"})
        self.assertEqual(fresh.DEV, os.path.expanduser("~"))


class PickerConfigIsDocumented(unittest.TestCase):
    """Doc-drift guard for the [picker] table, in LayoutSettings' shape.

    A key added to PICKER_KEYS without a README line, or renamed in one place
    only, fails here.
    """

    README = os.path.join(HERE, "..", "README.md")

    def lines(self):
        with open(self.README, encoding="utf-8") as f:
            return [line.rstrip("\n") for line in f]

    def test_the_table_header_is_documented(self):
        self.assertIn("[picker]", self.lines())

    def test_every_key_is_documented_exactly_once(self):
        # Line-scoped rather than a whole-file search, because "root" and "home"
        # are ordinary words in this README's prose.
        lines = self.lines()
        for key in pp.PICKER_KEYS:
            bare = key.split(".", 1)[1]
            at = [line for line in lines if line.startswith(bare + " = ")]
            self.assertEqual(len(at), 1, key)

    def test_every_key_names_the_variable_it_stands_in_for(self):
        # The two spellings have to stay findable from each other: a reader with
        # HERDR_PICKER_ROOT in a .env needs to reach the key that replaces it.
        text = "\n".join(self.lines())
        for var in pp.PICKER_KEYS.values():
            self.assertIn(var, text, var)


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
