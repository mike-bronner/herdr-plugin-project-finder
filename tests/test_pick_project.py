import importlib.machinery, importlib.util, io, os, re, sys, tempfile, time
import unittest
from unittest import mock

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

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "pick-project")
MANIFEST = os.path.join(HERE, "..", "herdr-plugin.toml")
DEFAULTS = os.path.join(HERE, "..", "defaults.toml")
loader = importlib.machinery.SourceFileLoader("pick_project", SCRIPT)
spec = importlib.util.spec_from_loader("pick_project", loader)
pp = importlib.util.module_from_spec(spec)
loader.exec_module(pp)


def ws(label, wid, focused=False, status="idle"):
    return {"label": label, "workspace_id": wid, "focused": focused,
            "agent_status": status}


def make_project(path, worktree=False):
    os.makedirs(path, exist_ok=True)
    git = os.path.join(path, ".git")
    if worktree:
        with open(git, "w", encoding="utf-8") as f:
            f.write("gitdir: /x/parent/.git/worktrees/wt\n")
    else:
        os.makedirs(git, exist_ok=True)
    return path


def write_config(directory, body):
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
        self.assertEqual(head, [("c", "/c", 40), ("a", "/a", 30)])
        self.assertEqual(rest, [("d", "/d", 20), ("b", "/b", 10)])

    def test_nothing_open(self):
        with mock.patch.object(pp, "touched_at", return_value=0):
            head, rest = pp.order_rows([("a", "/a")], [])
        self.assertEqual(head, [])
        self.assertEqual(rest, [("a", "/a", 0)])

    def test_equal_touch_times_keep_input_order(self):
        labelled = [("b", "/b"), ("a", "/a")]
        with mock.patch.object(pp, "touched_at", return_value=7):
            _, rest = pp.order_rows(labelled, [])
        self.assertEqual([l for l, _, _ in rest], ["b", "a"])

    def test_open_rows_carry_their_touch_time(self):
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
        out = f"{pp.EMPTY}\ncursor-row   3m ago  \t/x/cursor-row\n"
        self.assertEqual(pp.parse_selection(0, out), [])

    def test_paths_extracted(self):
        out = "a  1m ago \t/x/a\nb  2m ago \t/x/b\n"
        self.assertEqual(pp.parse_selection(0, out), ["/x/a", "/x/b"])

    def test_trailing_label_field_is_not_glued_onto_the_path(self):
        out = "a-very-long-na…  repo  1m ago \t/x/a\ta-very-long-name\n"
        self.assertEqual(pp.parse_selection(0, out), ["/x/a"])

    def test_no_match_without_sentinel_is_cancel(self):
        self.assertIsNone(pp.parse_selection(1, ""))

    def test_heading_line_is_never_a_path(self):
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
    RES = {"workspace": {"workspace_id": "w9"}}

    LAYOUT = ["/x/tool/bin/lay", "--space", pp.WORKSPACE_TOKEN, "--quiet"]

    def create(self, res=RES, layout=LAYOUT, label="proj", path="/x/proj"):
        with mock.patch.object(pp, "herdr", return_value=res) as h, \
             mock.patch.object(pp.subprocess, "Popen") as popen, \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            wid = pp.create_workspace(label, path, layout)
        return wid, [c.args for c in h.call_args_list], popen

    def handoff(self, **kwargs):
        return self.create(**kwargs)[2].call_args.args[0]


class CreateWorkspace(CreateWorkspaceHarness, unittest.TestCase):
    def test_the_opened_workspaces_id_is_returned(self):
        wid, calls, _ = self.create()
        self.assertEqual(wid, "w9")
        self.assertEqual(calls[0][:2], ("workspace", "create"))

    def test_create_failure_dies(self):
        with self.assertRaises(SystemExit):
            self.create(res=None)

    def test_the_picker_builds_no_part_of_the_layout(self):
        _, calls, _ = self.create()
        self.assertEqual(len(calls), 1)

    def test_no_agent_is_started_here_either(self):
        argv = self.handoff()
        self.assertEqual(argv[0], self.LAYOUT[0])
        self.assertNotIn("start", argv)

    def test_the_workspace_is_opened_before_it_is_handed_over(self):
        manager = mock.Mock()
        with mock.patch.object(pp, "herdr", return_value=self.RES) as h, \
             mock.patch.object(pp.subprocess, "Popen") as popen, \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            manager.attach_mock(h, "herdr")
            manager.attach_mock(popen, "popen")
            pp.create_workspace("proj", "/x/proj", self.LAYOUT)
        self.assertEqual([c[0] for c in manager.mock_calls], ["herdr", "popen"])

    def test_the_label_is_what_the_workspace_is_opened_under(self):
        _, calls, _ = self.create(label="my-proj")
        self.assertEqual(calls[0][calls[0].index("--label") + 1], "my-proj")

    def test_a_worktree_row_is_handed_over_exactly_like_a_repo_row(self):
        with mock.patch.object(pp, "parent_repo", return_value="/x/myrepo"), \
             mock.patch.object(pp, "herdr", return_value=self.RES) as h, \
             mock.patch.object(pp.subprocess, "Popen") as popen, \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            wid = pp.create_workspace("feat-x", "/x/wt/feat-x", self.LAYOUT)
        calls = [c.args for c in h.call_args_list]
        self.assertEqual(wid, "w9")
        self.assertEqual(calls[0][:2], ("worktree", "open"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(popen.call_args.args[0],
                         ["/x/tool/bin/lay", "--space", "w9", "--quiet"])

    def test_no_layout_command_still_opens_the_workspace_and_fires_nothing(self):
        wid, calls, popen = self.create(layout=None)
        self.assertEqual(wid, "w9")
        self.assertEqual(len(calls), 1)
        popen.assert_not_called()


class LayoutHandoff(CreateWorkspaceHarness, unittest.TestCase):
    def test_the_resolved_argv_is_run_with_the_workspace_substituted(self):
        self.assertEqual(self.handoff(),
                         ["/x/tool/bin/lay", "--space", "w9", "--quiet"])

    def test_the_workspace_handed_over_is_the_one_that_was_opened(self):
        wid, _, popen = self.create()
        argv = popen.call_args.args[0]
        self.assertEqual(argv[argv.index("--space") + 1], wid)

    def test_the_token_is_substituted_wherever_it_appears(self):
        argv = self.handoff(layout=["/x/tool/bin/lay", "--space=" + pp.WORKSPACE_TOKEN,
                                    "--log=/tmp/" + pp.WORKSPACE_TOKEN + ".log"])
        self.assertEqual(argv, ["/x/tool/bin/lay", "--space=w9",
                                "--log=/tmp/w9.log"])

    def test_a_command_with_no_token_is_run_unchanged(self):
        self.assertEqual(self.handoff(layout=["/x/tool/bin/lay"]),
                         ["/x/tool/bin/lay"])

    def test_no_flag_of_this_files_own_is_added(self):
        self.assertEqual(len(self.handoff()), len(self.LAYOUT))

    def test_the_call_is_detached_and_its_output_discarded(self):
        _, _, popen = self.create()
        kwargs = popen.call_args.kwargs
        self.assertIs(kwargs["start_new_session"], True)
        self.assertIs(kwargs["stdout"], pp.subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], pp.subprocess.DEVNULL)

    def test_the_command_is_run_with_the_stripped_environment(self):
        _, _, popen = self.create()
        self.assertEqual(popen.call_args.kwargs["env"], pp.child_env())


class PluginRoot(unittest.TestCase):
    def call(self, reply):
        with mock.patch.object(pp, "herdr", return_value=reply) as h:
            got = pp.plugin_root("some.plugin")
        return got, h.call_args.args if h.call_args else None

    def test_the_plugin_is_asked_for_by_the_id_it_was_given(self):
        _, args = self.call({"plugins": [{"plugin_root": "/x/tool"}]})
        self.assertEqual(args, ("plugin", "list", "--plugin",
                                "some.plugin", "--json"))

    def test_the_reported_root_is_returned(self):
        self.assertEqual(self.call({"plugins": [{"plugin_root": "/x/tool"}]})[0],
                         "/x/tool")

    def test_no_plugin_row_yields_none(self):
        self.assertIsNone(self.call({"plugins": []})[0])

    def test_a_row_with_no_root_yields_none(self):
        self.assertIsNone(self.call({"plugins": [{"plugin_root": ""}]})[0])
        self.assertIsNone(self.call({"plugins": [{}]})[0])

    def test_an_explicitly_null_root_yields_none(self):
        self.assertIsNone(self.call({"plugins": [{"plugin_root": None}]})[0])

    def test_a_reply_with_no_plugins_key_yields_none(self):
        self.assertIsNone(self.call({})[0])

    def test_an_unreachable_server_yields_none_rather_than_raising(self):
        self.assertIsNone(self.call(None)[0])

    def test_a_disabled_plugin_is_still_resolved(self):
        got, _ = self.call({"plugins": [{"plugin_root": "/x/tool",
                                         "enabled": False}]})
        self.assertEqual(got, "/x/tool")


class ResolveLayout(unittest.TestCase):
    def resolve(self, setting, creating=True, root="/x/tool", executable=True):
        env = {} if setting is None else {"HERDR_PICKER_LAYOUT": setting}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(pp, "plugin_root", return_value=root) as pr, \
             mock.patch.object(pp.shutil, "which",
                               side_effect=lambda c: c if executable else None), \
             mock.patch.object(pp, "warn") as warn:
            got = pp.resolve_layout(creating)
        return got, pr, warn

    def test_a_plain_command_line_is_split_the_way_a_shell_splits_one(self):
        got, _, warn = self.resolve("/x/tool/lay --space {workspace} --quiet")
        self.assertEqual(got, ["/x/tool/lay", "--space", "{workspace}",
                               "--quiet"])
        warn.assert_not_called()

    def test_a_quoted_argument_survives_as_one_argument(self):
        got, _, _ = self.resolve('"/x/my tool/lay" --title "two words"')
        self.assertEqual(got, ["/x/my tool/lay", "--title", "two words"])

    def test_the_workspace_token_is_left_for_the_caller(self):
        got, _, _ = self.resolve("/x/tool/lay {workspace}")
        self.assertIn(pp.WORKSPACE_TOKEN, got)

    def test_a_plugin_token_becomes_the_reported_checkout(self):
        got, pr, warn = self.resolve(
            "{plugin:some.plugin}/bin/lay --space {workspace}")
        self.assertEqual(got, ["/x/tool/bin/lay", "--space", "{workspace}"])
        self.assertEqual(pr.call_args.args, ("some.plugin",))
        warn.assert_not_called()

    def test_an_uninstalled_plugin_yields_none_and_names_it_once(self):
        got, _, warn = self.resolve("{plugin:some.plugin}/bin/lay", root=None)
        self.assertIsNone(got)
        self.assertEqual(warn.call_count, 1)
        self.assertIn("some.plugin", warn.call_args.args[0])

    def test_a_command_that_cannot_be_run_yields_none_and_names_it(self):
        got, _, warn = self.resolve("/x/tool/lay", executable=False)
        self.assertIsNone(got)
        self.assertEqual(warn.call_count, 1)
        self.assertIn("/x/tool/lay", warn.call_args.args[0])

    def test_a_bare_name_is_resolved_on_the_path(self):
        with mock.patch.dict(os.environ, {"HERDR_PICKER_LAYOUT": "lay"},
                             clear=True), \
             mock.patch.object(pp.shutil, "which", return_value="/opt/bin/lay"), \
             mock.patch.object(pp, "warn"):
            self.assertEqual(pp.resolve_layout(True), ["/opt/bin/lay"])

    def test_an_unparseable_command_line_yields_none_and_warns(self):
        got, _, warn = self.resolve('/x/tool/lay --title "unclosed')
        self.assertIsNone(got)
        self.assertEqual(warn.call_count, 1)

    def test_an_unset_setting_yields_none_in_silence(self):
        got, pr, warn = self.resolve(None)
        self.assertIsNone(got)
        pr.assert_not_called()
        warn.assert_not_called()

    def test_an_emptied_setting_yields_none_in_silence(self):
        for value in ("", "   "):
            got, _, warn = self.resolve(value)
            self.assertIsNone(got, value)
            warn.assert_not_called()

    def test_a_run_that_creates_nothing_asks_herdr_nothing(self):
        got, pr, warn = self.resolve("{plugin:some.plugin}/bin/lay",
                                     creating=False)
        self.assertIsNone(got)
        pr.assert_not_called()
        warn.assert_not_called()

    def test_a_run_that_creates_nothing_is_silent_with_a_broken_setting(self):
        got, pr, warn = self.resolve('"unclosed', creating=False)
        self.assertIsNone(got)
        pr.assert_not_called()
        warn.assert_not_called()

    def test_the_notice_goes_to_both_channels_warn_owns(self):
        with mock.patch.dict(os.environ,
                             {"HERDR_PICKER_LAYOUT": "{plugin:some.plugin}/lay"},
                             clear=True), \
             mock.patch.object(pp, "plugin_root", return_value=None), \
             mock.patch.object(pp, "HERDR", "/bin/herdr"), \
             mock.patch.object(pp.subprocess, "run") as run, \
             mock.patch.object(pp.sys, "stderr", io.StringIO()) as err:
            pp.resolve_layout(True)
        self.assertIn("some.plugin", err.getvalue())
        self.assertEqual(run.call_args.args[0][:2],
                         ["/bin/herdr", "notification"])


class ChildEnv(unittest.TestCase):
    def env(self, base):
        with mock.patch.dict(os.environ, base, clear=True):
            return pp.child_env()

    def test_the_plugin_root_is_dropped(self):
        self.assertNotIn("HERDR_PLUGIN_ROOT",
                         self.env({"HERDR_PLUGIN_ROOT": "/x/picker"}))

    def test_the_config_dir_is_dropped(self):
        self.assertNotIn("HERDR_PLUGIN_CONFIG_DIR",
                         self.env({"HERDR_PLUGIN_CONFIG_DIR": "/x/picker/cfg"}))

    def test_neither_being_set_is_an_error(self):
        self.assertEqual(self.env({}), {})

    def test_everything_else_passes_through(self):
        env = self.env({"SOME_TOOL_RATIO": "0.3", "PATH": "/usr/bin"})
        self.assertEqual(env["SOME_TOOL_RATIO"], "0.3")
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_the_process_environment_is_not_mutated(self):
        with mock.patch.dict(os.environ,
                             {"HERDR_PLUGIN_CONFIG_DIR": "/x/picker/config",
                              "HERDR_PLUGIN_ROOT": "/x/picker"}, clear=True):
            pp.child_env()
            self.assertEqual(os.environ["HERDR_PLUGIN_CONFIG_DIR"],
                             "/x/picker/config")
            self.assertEqual(os.environ["HERDR_PLUGIN_ROOT"], "/x/picker")


class LayoutIsResolvedOncePerRun(unittest.TestCase):
    SETTING = "{plugin:some.plugin}/bin/lay --space {workspace}"

    def run_main(self, chosen, root):
        with mock.patch.dict(os.environ,
                             {"HERDR_PICKER_LAYOUT": self.SETTING,
                              "PATH": "/usr/bin"}, clear=True), \
             mock.patch.object(pp, "plugin_root", return_value=root) as lc, \
             mock.patch.object(pp.shutil, "which", side_effect=lambda c: c), \
             mock.patch.object(pp, "HERDR", "/bin/herdr"), \
             mock.patch.object(pp, "ensure_fzf", return_value=True), \
             mock.patch.object(pp, "repos", return_value=["/x/a", "/x/b"]), \
             mock.patch.object(pp, "open_workspaces", return_value=(None, [])), \
             mock.patch.object(pp, "resolve_theme", return_value=({}, {})), \
             mock.patch.object(pp, "order_rows", return_value=([], [])), \
             mock.patch.object(pp, "build_lines", return_value=[]), \
             mock.patch.object(pp, "parse_selection", return_value=chosen), \
             mock.patch.object(pp, "warn") as warn, \
             mock.patch.object(pp, "herdr",
                               return_value=CreateWorkspaceHarness.RES), \
             mock.patch.object(pp.subprocess, "run"), \
             mock.patch.object(pp.subprocess, "Popen") as popen:
            pp.main()
        return lc, warn, popen

    def test_two_new_workspaces_resolve_the_setting_once(self):
        lc, warn, popen = self.run_main(["/x/a", "/x/b"], "/x/tool")
        self.assertEqual(lc.call_count, 1)
        self.assertEqual(popen.call_count, 2)
        warn.assert_not_called()

    def test_a_setting_that_will_not_resolve_warns_once_for_two_workspaces(self):
        lc, warn, popen = self.run_main(["/x/a", "/x/b"], None)
        self.assertEqual(lc.call_count, 1)
        self.assertEqual(warn.call_count, 1)
        popen.assert_not_called()

    def test_a_selection_that_creates_nothing_never_asks(self):
        lc, warn, popen = self.run_main([], None)
        lc.assert_not_called()
        warn.assert_not_called()
        popen.assert_not_called()


class ThePickerNamesNoProgram(unittest.TestCase):
    FOREIGN = ("agentic-panes-layout", "agent-layout", "AGENT_LAYOUT",
               "mikebronner.", "--workspace", "--agent-name", "--no-agent")

    def source(self):
        with open(pp.__file__, encoding="utf-8") as f:
            return f.read()

    def test_the_script_names_no_plugin_executable_or_flag_of_anothers(self):
        source = self.source()
        for token in self.FOREIGN:
            self.assertNotIn(token, source, token)

    def test_the_script_reads_only_its_own_settings(self):
        read = set(re.findall(r'os\.environ(?:\.get)?[(\[]"(\w+)"', self.source()))
        self.assertTrue(read)
        for name in read:
            self.assertTrue(name.startswith(("HERDR_PICKER_", "HERDR_PLUGIN_",
                                             "HERDR_CONFIG_", "HERDR_BIN_",
                                             "PATH")), name)

    def test_only_this_plugins_settings_have_a_home_in_its_config_toml(self):
        for key in pp.PICKER_KEYS.values():
            self.assertTrue(key.startswith("HERDR_PICKER_"), key)

    def test_the_environment_reaches_the_command_whatever_it_reads(self):
        sample = {"SOME_TOOL_ALPHA": "one", "OTHER_BETA": "two"}
        with mock.patch.dict(os.environ, sample, clear=True):
            env = pp.child_env()
        for key, value in sample.items():
            self.assertEqual(env.get(key), value, key)


class ShippedDefaults(unittest.TestCase):
    DEFAULTS = DEFAULTS

    def text(self):
        with open(self.DEFAULTS, encoding="utf-8") as f:
            return f.read()

    def test_it_supplies_a_layout_setting(self):
        got = pp.read_toml(self.DEFAULTS, {"picker.layout"})
        self.assertTrue(got.get("picker.layout"))

    def test_the_default_is_what_an_unconfigured_picker_runs(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            pp.load_picker_config(self.DEFAULTS)
            self.assertEqual(os.environ["HERDR_PICKER_LAYOUT"],
                             pp.read_toml(self.DEFAULTS,
                                          {"picker.layout"})["picker.layout"])

    def test_a_user_setting_beats_it(self):
        with mock.patch.dict(os.environ, {"HERDR_PICKER_LAYOUT": "/x/mine"},
                             clear=True):
            pp.load_picker_config(self.DEFAULTS)
            self.assertEqual(os.environ["HERDR_PICKER_LAYOUT"], "/x/mine")

    def test_the_default_still_lays_a_workspace_out_the_way_it_used_to(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(pp, "plugin_root", return_value="/x/panes") as pr, \
             mock.patch.object(pp.shutil, "which", side_effect=lambda c: c), \
             mock.patch.object(pp, "warn") as warn:
            pp.load_picker_config(self.DEFAULTS)
            got = pp.resolve_layout(True)
        warn.assert_not_called()
        self.assertEqual(pr.call_args.args,
                         ("mikebronner.agentic-panes-layout",))
        self.assertEqual(got, ["/x/panes/bin/agent-layout", "--workspace",
                               pp.WORKSPACE_TOKEN])

    def test_the_default_does_not_name_this_plugin(self):
        self.assertNotIn("project-finder", self.text())

    def test_the_readme_names_the_plugin_the_default_points_at(self):
        with open(os.path.join(HERE, "..", "README.md"), encoding="utf-8") as f:
            self.assertIn("agentic-panes-layout", f.read())

    def test_no_foreign_setting_is_copied_into_this_repo(self):
        for path in (self.DEFAULTS, os.path.join(HERE, "..", "README.md"),
                     pp.__file__, __file__):
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            self.assertEqual([line for line in lines
                              if re.match(r"\s*(export )?AGENT_LAYOUT_\w+\s*=",
                                          line)], [], path)


class OpenProject(unittest.TestCase):
    RES = {"workspace": {"workspace_id": "w9"}}

    def open(self, parent, fails=()):
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            return None if args[:2] in fails else self.RES

        with mock.patch.object(pp, "parent_repo", return_value=parent), \
             mock.patch.object(pp, "herdr", side_effect=fake_herdr):
            return pp.open_project("feat-x", "/x/wt/feat-x"), calls

    def test_a_worktree_is_opened_against_its_parent_repo(self):
        _, calls = self.open("/x/myrepo")
        self.assertEqual(calls[0], ("worktree", "open", "--cwd", "/x/myrepo",
                                    "--path", "/x/wt/feat-x",
                                    "--label", "feat-x", "--no-focus"))

    def test_opening_a_worktree_never_falls_through_to_workspace_create(self):
        _, calls = self.open("/x/myrepo")
        self.assertEqual([c[:2] for c in calls], [("worktree", "open")])

    def test_the_worktree_result_is_returned_unchanged(self):
        res, _ = self.open("/x/myrepo")
        self.assertIs(res, self.RES)

    def test_a_row_that_is_no_git_checkout_uses_workspace_create(self):
        _, calls = self.open(None)
        self.assertEqual(calls, [("workspace", "create", "--cwd", "/x/wt/feat-x",
                                  "--label", "feat-x", "--no-focus")])

    def test_a_failed_worktree_open_falls_back_to_workspace_create(self):
        res, calls = self.open("/x/myrepo", fails={("worktree", "open")})
        self.assertEqual([c[:2] for c in calls],
                         [("worktree", "open"), ("workspace", "create")])
        self.assertIs(res, self.RES)

    def test_both_commands_failing_yields_none_so_the_caller_dies(self):
        res, _ = self.open("/x/myrepo",
                           fails={("worktree", "open"), ("workspace", "create")})
        self.assertIsNone(res)

    def test_nothing_is_ever_focused(self):
        for parent in ("/x/myrepo", None):
            with self.subTest(parent=parent):
                _, calls = self.open(parent)
                self.assertIn("--no-focus", calls[0])
                self.assertNotIn("--focus", calls[0])

    def real(self, kind, fails=()):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        repo = make_project(os.path.join(d.name, "myrepo"))
        checkout = os.path.join(repo, ".worktrees", "myrepo", "feat-x")
        os.makedirs(checkout)
        with open(os.path.join(checkout, ".git"), "w", encoding="utf-8") as f:
            f.write(f"gitdir: {repo}/.git/worktrees/feat-x\n")
        plain = os.path.join(d.name, "notes")
        os.makedirs(plain)
        calls = []

        def fake_herdr(*args):
            calls.append(args)
            return None if args[:2] in fails else self.RES

        with mock.patch.object(pp, "herdr", side_effect=fake_herdr):
            pp.open_project("feat-x", {"worktree": checkout, "repo": repo,
                                       "plain": plain}[kind])
        return repo, calls

    def test_a_real_worktree_names_its_real_parent(self):
        repo, calls = self.real("worktree")
        self.assertEqual(calls[0][:4], ("worktree", "open", "--cwd", repo))

    def test_a_real_repo_names_itself_as_the_repo_it_belongs_to(self):
        repo, calls = self.real("repo")
        self.assertEqual(calls[0], ("worktree", "open", "--cwd", repo,
                                    "--path", repo,
                                    "--label", "feat-x", "--no-focus"))

    def test_opening_a_real_repo_never_falls_through_to_workspace_create(self):
        _, calls = self.real("repo")
        self.assertEqual([c[:2] for c in calls], [("worktree", "open")])

    def test_a_failed_worktree_open_on_a_repo_falls_back_to_workspace_create(self):
        repo, calls = self.real("repo", fails={("worktree", "open")})
        self.assertEqual(calls[1], ("workspace", "create", "--cwd", repo,
                                    "--label", "feat-x", "--no-focus"))

    def test_a_real_directory_with_no_git_uses_workspace_create(self):
        _, calls = self.real("plain")
        self.assertEqual([c[:2] for c in calls], [("workspace", "create")])


class LoadEnv(unittest.TestCase):
    def load(self, body, env=None):
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
                pp.load_env(os.path.join(d, ".env"))
                self.assertEqual(dict(os.environ), {})

    def test_unreadable_path_is_a_noop(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {}, clear=True):
                pp.load_env(d)
                self.assertEqual(dict(os.environ), {})

    def test_comments_and_blank_lines_are_skipped(self):
        env = self.load("# HERDR_PICKER_ROOT=/commented\n\n"
                        "   # indented comment\n"
                        "HERDR_PICKER_HOME=base\n")
        self.assertEqual(env, {"HERDR_PICKER_HOME": "base"})

    def test_quotes_are_stripped_but_only_matching_pairs(self):
        env = self.load('A="/x/one"\nB=\'/x/two\'\nC="/x/three\nD=""\n')
        self.assertEqual(env["A"], "/x/one")
        self.assertEqual(env["B"], "/x/two")
        self.assertEqual(env["C"], '"/x/three')
        self.assertEqual(env["D"], "")

    def test_hash_inside_a_value_is_not_a_comment(self):
        self.assertEqual(self.load("A=/x/a#b\n")["A"], "/x/a#b")

    def test_surrounding_whitespace_is_trimmed(self):
        self.assertEqual(self.load("  A = /x/a  \n")["A"], "/x/a")

    def test_first_equals_wins_so_values_may_contain_one(self):
        self.assertEqual(self.load("A=k=v\n")["A"], "k=v")

    def test_malformed_line_is_skipped_and_later_lines_still_apply(self):
        env = self.load("HERDR_PICKER_ROOT /x/typo\nHERDR_PICKER_HOME=base\n")
        self.assertEqual(env, {"HERDR_PICKER_HOME": "base"})

    def test_empty_key_is_skipped(self):
        self.assertEqual(self.load("=/x/a\nA=/x/b\n"), {"A": "/x/b"})


class LoadPickerConfig(unittest.TestCase):
    def load(self, body, env=None):
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
        env = self.load('[picker]\nroot = "/from/file"\n',
                        {"HERDR_PICKER_ROOT": "/from/env"})
        self.assertEqual(env["HERDR_PICKER_ROOT"], "/from/env")

    def test_only_the_named_settings_are_applied(self):
        env = self.load('[picker]\nroot = "/x/code"\nnonsense = "boom"\n')
        self.assertEqual(env, {"HERDR_PICKER_ROOT": "/x/code"})

    def test_a_key_outside_the_picker_table_is_not_claimed(self):
        env = self.load('[worktrees]\nroot = "/x/wrong"\n\n'
                        '[picker]\nroot = "/x/right"\n')
        self.assertEqual(env, {"HERDR_PICKER_ROOT": "/x/right"})

    def test_a_missing_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {}, clear=True):
                pp.load_picker_config(os.path.join(d, "config.toml"))
                self.assertEqual(dict(os.environ), {})

    def test_an_unreadable_path_is_a_noop(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {}, clear=True):
                pp.load_picker_config(d)
                self.assertEqual(dict(os.environ), {})

    def test_a_malformed_file_does_not_raise_and_sets_nothing(self):
        self.assertEqual(self.load("[picker\nroot /x/code\n}{\n"), {})

    def test_a_malformed_line_does_not_stop_the_valid_ones(self):
        env = self.load('[picker]\nroot /x/typo\nhome = "base"\n')
        self.assertEqual(env, {"HERDR_PICKER_HOME": "base"})

    def test_debug_false_leaves_debugging_off(self):
        env = self.load("[picker]\ndebug = false\n")
        self.assertEqual(env["HERDR_PICKER_DEBUG"], "")

    def test_debug_false_really_silences_the_debug_line(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            path = write_config(d, "[picker]\ndebug = false\n")
            with mock.patch.dict(os.environ, {}, clear=True):
                pp.load_picker_config(path)
                pp.debug_discovery(0.026, ["/x/a"], {"bands": 1, "containers": 0}, out)
        self.assertEqual(out.getvalue(), "")

    def test_debug_true_really_writes_the_debug_line(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            path = write_config(d, "[picker]\ndebug = true\n")
            with mock.patch.dict(os.environ, {}, clear=True):
                pp.load_picker_config(path)
                pp.debug_discovery(0.026, ["/x/a"], {"bands": 1, "containers": 0}, out)
        self.assertIn("picker: discovery", out.getvalue())


class ConfigPrecedence(unittest.TestCase):
    def resolve(self, toml=None, env_file=None, environ=None):
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
        env = self.resolve(env_file="HERDR_PICKER_ROOT=/from/env-file\n")
        self.assertEqual(env["HERDR_PICKER_ROOT"], "/from/env-file")

    def test_config_toml_wins_over_the_env_file(self):
        env = self.resolve(toml='[picker]\nroot = "/from/toml"\n',
                           env_file="HERDR_PICKER_ROOT=/from/env-file\n")
        self.assertEqual(env["HERDR_PICKER_ROOT"], "/from/toml")

    def test_the_env_file_still_supplies_what_the_toml_omits(self):
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
        env = self.resolve(toml="[picker]\ndebug = false\n",
                           env_file="HERDR_PICKER_DEBUG=1\n")
        self.assertEqual(env["HERDR_PICKER_DEBUG"], "")

    def test_a_malformed_toml_leaves_the_env_file_working(self):
        env = self.resolve(toml="[picker\nroot /x/typo\n",
                           env_file="HERDR_PICKER_ROOT=/from/env-file\n")
        self.assertEqual(env["HERDR_PICKER_ROOT"], "/from/env-file")


class ModuleBootstrap(unittest.TestCase):
    def boot(self, files, environ=None):
        with tempfile.TemporaryDirectory() as d:
            for name, body in files.items():
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write(body)
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

    def boot_environ(self, files):
        with tempfile.TemporaryDirectory() as d:
            for name, body in files.items():
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write(body)
            env = dict(PATH=os.environ.get("PATH", ""),
                       HERDR_PLUGIN_CONFIG_DIR=d,
                       HERDR_CONFIG_PATH=os.path.join(d, "absent.toml"))
            with mock.patch.dict(os.environ, env, clear=True):
                loader.exec_module(importlib.util.module_from_spec(spec))
                return dict(os.environ)

    def test_the_shipped_defaults_are_loaded_at_import(self):
        self.assertEqual(
            self.boot_environ({}).get("HERDR_PICKER_LAYOUT"),
            pp.read_toml(DEFAULTS, {"picker.layout"})["picker.layout"])

    def test_the_shipped_defaults_are_loaded_last(self):
        environ = self.boot_environ({"config.toml": '[picker]\nlayout = "/x/mine"\n'})
        self.assertEqual(environ["HERDR_PICKER_LAYOUT"], "/x/mine")
        environ = self.boot_environ({".env": "HERDR_PICKER_LAYOUT=/x/from-env-file\n"})
        self.assertEqual(environ["HERDR_PICKER_LAYOUT"], "/x/from-env-file")

    def test_a_malformed_toml_still_lets_the_module_import(self):
        fresh = self.boot({"config.toml": "[picker\nroot /x/typo\n}{\n"})
        self.assertEqual(fresh.DEV, os.path.expanduser("~"))


class PickerConfigIsDocumented(unittest.TestCase):
    README = os.path.join(HERE, "..", "README.md")

    def lines(self):
        with open(self.README, encoding="utf-8") as f:
            return [line.rstrip("\n") for line in f]

    def test_the_table_header_is_documented(self):
        self.assertIn("[picker]", self.lines())

    def test_every_key_is_documented_exactly_once(self):
        lines = self.lines()
        for key in pp.PICKER_KEYS:
            bare = key.split(".", 1)[1]
            at = [line for line in lines if line.startswith(bare + " = ")]
            self.assertEqual(len(at), 1, key)

    def test_every_key_names_the_variable_it_stands_in_for(self):
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
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "Code")
            os.mkdir(sub)
            with mock.patch.dict(os.environ, {"HOME": d, "HERDR_PICKER_ROOT": "~/Code"},
                                 clear=True):
                self.assertEqual(pp.resolve_root(), sub)

    def test_unexpanded_tilde_would_not_be_a_directory_and_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "Code"))
            with mock.patch.dict(os.environ, {"HOME": d, "HERDR_PICKER_ROOT": "~/Nope"},
                                 clear=True):
                self.assertEqual(pp.resolve_root(), d)


class Repos(unittest.TestCase):
    def discover(self, repos=(), worktrees=(), dirs=None, roots=None, counts=None):
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
        root, found = self.discover(repos=["myrepo"],
                                    worktrees=["worktrees/myrepo/feat-x"])
        self.assertEqual(sorted(found),
                         sorted([os.path.join(root, "myrepo"),
                                 os.path.join(root, "worktrees", "myrepo", "feat-x")]))

    def test_a_worktree_found_three_levels_down_is_tagged_worktree(self):
        root, found = self.discover(worktrees=["worktrees/myrepo/feat-x"])
        self.assertEqual([pp.row_kind(p) for p in found], ["worktree"])

    def test_a_fourth_level_is_not_searched(self):
        _, found = self.discover(repos=["a/b/c/d"])
        self.assertEqual(found, [])

    def test_a_repo_nested_inside_a_repo_is_skipped(self):
        root, found = self.discover(repos=["myrepo", "myrepo/vendor/pkg",
                                           "group-a/repo", "group-a/repo/sub"])
        self.assertEqual(sorted(found),
                         sorted([os.path.join(root, "myrepo"),
                                 os.path.join(root, "group-a", "repo")]))

    def test_a_nested_repo_sorting_ahead_of_its_parent_is_still_skipped(self):
        root, found = self.discover(repos=["a", "a/-x"])
        self.assertEqual(found, [os.path.join(root, "a")])

    def test_a_sibling_sharing_a_name_prefix_is_not_mistaken_for_nesting(self):
        root, found = self.discover(repos=["myrepo", "myrepo_old"])
        self.assertEqual(sorted(found),
                         sorted([os.path.join(root, "myrepo"),
                                 os.path.join(root, "myrepo_old")]))

    def test_a_worktree_in_herdrs_own_container_is_found(self):
        root, found = self.discover(
            repos=["myrepo"], worktrees=["myrepo/.worktrees/myrepo/feat-x"])
        self.assertEqual(
            sorted(found),
            sorted([os.path.join(root, "myrepo"),
                    os.path.join(root, "myrepo", ".worktrees", "myrepo", "feat-x")]))

    def test_a_worktree_in_claude_codes_container_is_found(self):
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
        root, found = self.discover(
            repos=["group/myrepo"],
            worktrees=["group/myrepo/.worktrees/myrepo/feat-x"])
        self.assertIn(
            os.path.join(root, "group", "myrepo", ".worktrees", "myrepo", "feat-x"),
            found)

    def test_a_nested_worktree_is_tagged_worktree(self):
        root, found = self.discover(
            repos=["myrepo"], worktrees=["myrepo/.worktrees/myrepo/feat-x"])
        nested = [p for p in found if ".worktrees" in p]
        self.assertEqual([pp.row_kind(p) for p in nested], ["worktree"])

    def test_an_ordinary_repo_inside_a_container_is_still_skipped(self):
        root, found = self.discover(repos=["myrepo", "myrepo/worktrees/vendored"])
        self.assertEqual(found, [os.path.join(root, "myrepo")])

    def test_a_container_under_a_repo_that_is_itself_nested_is_not_searched(self):
        root, found = self.discover(
            repos=["myrepo", "myrepo/vendor/pkg"],
            worktrees=["myrepo/vendor/pkg/.worktrees/pkg/feat-x"])
        self.assertEqual(found, [os.path.join(root, "myrepo")])

    def test_a_container_named_by_herdrs_config_is_searched(self):
        cfg = tempfile.TemporaryDirectory()
        self.addCleanup(cfg.cleanup)
        path = write_config(cfg.name, '[worktrees]\ndirectory = "trees"\n')
        with mock.patch.dict(os.environ, {"HERDR_CONFIG_PATH": path}):
            dirs, roots = pp.worktree_locations()
        root, found = self.discover(repos=["myrepo"],
                                    worktrees=["myrepo/trees/feat-x"],
                                    dirs=dirs, roots=roots)
        self.assertIn(os.path.join(root, "myrepo", "trees", "feat-x"), found)

    def sibling_locations(self):
        cfg = tempfile.TemporaryDirectory()
        self.addCleanup(cfg.cleanup)
        path = write_config(cfg.name, '[worktrees]\ndirectory = "../worktrees"\n')
        with mock.patch.dict(os.environ, {"HERDR_CONFIG_PATH": path}):
            return pp.worktree_locations()

    def sibling(self, **kwargs):
        dirs, roots = self.sibling_locations()
        return self.discover(dirs=dirs, roots=roots, **kwargs)

    def test_a_worktree_beside_its_repo_is_found(self):
        root, found = self.sibling(
            repos=["group-a/repo-one"],
            worktrees=["group-a/worktrees/repo-one/feat-x"])
        self.assertEqual(
            sorted(found),
            sorted([os.path.join(root, "group-a", "repo-one"),
                    os.path.join(root, "group-a", "worktrees", "repo-one",
                                 "feat-x")]))

    def test_a_worktree_beside_its_repo_is_tagged_worktree(self):
        root, found = self.sibling(
            repos=["group-a/repo-one"],
            worktrees=["group-a/worktrees/repo-one/feat-x"])
        beside = [p for p in found if os.sep + "worktrees" + os.sep in p]
        self.assertEqual([pp.row_kind(p) for p in beside], ["worktree"])

    def test_each_repo_resolves_its_own_sibling_container(self):
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
        root, found = self.discover(
            repos=["a/group/one", "a/group/trees/vendored"],
            worktrees=["a/group/trees/feat-x"],
            dirs=list(pp.FIXED_WORKTREE_DIRS) + [".."])
        self.assertEqual(
            sorted(found),
            sorted([os.path.join(root, "a", "group", "one"),
                    os.path.join(root, "a", "group", "trees", "feat-x")]))

    def globbed(self, **kwargs):
        patterns, real = [], pp.glob.glob
        with mock.patch.object(
                pp.glob, "glob",
                side_effect=lambda p, **kw: patterns.append(p) or real(p, **kw)):
            return self.discover(**kwargs), patterns

    def test_a_shared_sibling_container_is_globbed_once(self):
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
        flat = tempfile.TemporaryDirectory()
        self.addCleanup(flat.cleanup)
        wt = make_project(os.path.join(flat.name, "myrepo", "feat-x"), worktree=True)
        root, found = self.discover(repos=["myrepo"], roots=[flat.name])
        self.assertEqual(sorted(found), sorted([os.path.join(root, "myrepo"), wt]))

    def test_a_worktree_reached_twice_is_listed_once(self):
        root, found = self.discover(repos=["myrepo"],
                                    worktrees=["worktrees/myrepo/feat-x"],
                                    roots=["worktrees"])
        self.assertEqual(found, [os.path.join(root, "myrepo"),
                                 os.path.join(root, "worktrees", "myrepo", "feat-x")])

    def tally(self, **kwargs):
        counts = {}
        _, found = self.discover(counts=counts, **kwargs)
        return found, counts

    MIXED_PASSES = {"repos": ["myrepo"],
                    "worktrees": ["worktrees/myrepo/feat-x",
                                  "myrepo/.worktrees/myrepo/feat-y"]}

    def test_each_pass_reports_what_it_contributed(self):
        found, counts = self.tally(**self.MIXED_PASSES)
        self.assertEqual(len(found), 3)
        self.assertEqual(counts, {"bands": 2, "containers": 1})

    def test_the_two_figures_account_for_every_row(self):
        found, counts = self.tally(**self.MIXED_PASSES)
        self.assertEqual(counts["bands"] + counts["containers"], len(found))

    def test_a_pass_that_found_nothing_reports_zero(self):
        found, counts = self.tally(repos=["myrepo"])
        self.assertEqual((len(found), counts), (1, {"bands": 1, "containers": 0}))

    def test_nothing_found_at_all_reports_two_zeroes(self):
        found, counts = self.tally()
        self.assertEqual((found, counts), ([], {"bands": 0, "containers": 0}))


class DebugDiscovery(unittest.TestCase):
    def report(self, flag=None, paths=("/x/a", "/x/b", "/x/c"),
               counts=None, elapsed=0.0264):
        out = io.StringIO()
        env = {} if flag is None else {"HERDR_PICKER_DEBUG": flag}
        with mock.patch.dict(os.environ, env, clear=True):
            pp.debug_discovery(elapsed, list(paths),
                               counts or {"bands": 2, "containers": 1}, out)
        return out.getvalue()

    def test_unset_writes_nothing(self):
        self.assertEqual(self.report(), "")

    def test_an_empty_value_writes_nothing(self):
        self.assertEqual(self.report(flag=""), "")

    def test_the_elapsed_seconds_are_reported_as_milliseconds(self):
        self.assertIn("26.4ms", self.report(flag="1"))

    def test_the_row_total_and_both_passes_are_reported(self):
        text = self.report(flag="1")
        self.assertIn("3 rows", text)
        self.assertIn("2 from depth bands", text)
        self.assertIn("1 from worktree containers", text)

    def test_the_row_total_is_the_rows_not_the_sum_of_the_passes(self):
        self.assertIn("3 rows", self.report(flag="1",
                                            counts={"bands": 1, "containers": 1}))

    def test_it_is_one_line(self):
        text = self.report(flag="1")
        self.assertEqual(text.count("\n"), 1)
        self.assertTrue(text.endswith("\n"), text)

    def test_it_names_the_picker(self):
        self.assertTrue(self.report(flag="1").startswith("picker: "))

    def test_the_default_stream_is_stderr(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"HERDR_PICKER_DEBUG": "1"}, clear=True), \
             mock.patch.object(pp.sys, "stderr", err):
            pp.debug_discovery(0.0264, ["/x/a"], {"bands": 1, "containers": 0})
        self.assertIn("picker: discovery", err.getvalue())


class DebugLineFromARealRun(unittest.TestCase):
    def run_main(self, env, delay=0):
        events = []

        class Recorder(io.StringIO):
            def write(self, text):
                events.append(("write", text))
                return super().write(text)

        def fake_run(argv, **kwargs):
            events.append(("run", argv[0]))
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
        events = self.run_main({})
        self.assertEqual(events, [("run", "fzf")])

    def test_the_reported_time_is_the_time_discovery_took(self):
        events = self.run_main({"HERDR_PICKER_DEBUG": "1"}, delay=0.02)
        reported = float(events[0][1].split("discovery ")[1].split("ms")[0])
        self.assertGreaterEqual(reported, 15.0, events[0][1])


class WorktreeLocations(unittest.TestCase):
    def locate(self, config):
        return pp.worktree_locations(config)

    def test_the_fixed_containers_are_always_searched(self):
        dirs, roots = self.locate({})
        self.assertEqual(dirs, list(pp.FIXED_WORKTREE_DIRS))
        self.assertEqual(roots, [])

    def test_both_real_layouts_are_covered_by_the_fixed_names(self):
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
        dirs, roots = self.locate({"worktrees.directory": "../trees"})
        self.assertIn(os.path.join("..", "trees"), dirs)
        self.assertEqual(roots, [])

    def test_an_absolute_setting_becomes_a_flat_root(self):
        dirs, roots = self.locate({"worktrees.directory": "/srv/worktrees/"})
        self.assertEqual(dirs, list(pp.FIXED_WORKTREE_DIRS))
        self.assertEqual(roots, ["/srv/worktrees"])

    def test_herdrs_shipped_default_is_read_as_a_root_not_a_container(self):
        _, roots = self.locate({"worktrees.directory": "~/.herdr/worktrees"})
        self.assertEqual(roots, [os.path.expanduser("~/.herdr/worktrees")])

    def test_an_empty_setting_degrades_to_the_fixed_containers(self):
        dirs, roots = self.locate({"worktrees.directory": "   "})
        self.assertEqual(dirs, list(pp.FIXED_WORKTREE_DIRS))
        self.assertEqual(roots, [])

    def test_an_unreadable_config_degrades_rather_than_raising(self):
        with mock.patch.dict(os.environ, {"HERDR_CONFIG_PATH": "/nope/config.toml"}):
            dirs, roots = pp.worktree_locations()
        self.assertEqual((dirs, roots), (list(pp.FIXED_WORKTREE_DIRS), []))

    def test_a_malformed_setting_line_degrades_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_config(d, '[worktrees]\ndirectory = ["a", "b"]\n')
            with mock.patch.dict(os.environ, {"HERDR_CONFIG_PATH": path}):
                dirs, roots = pp.worktree_locations()
        self.assertEqual((dirs, roots), (list(pp.FIXED_WORKTREE_DIRS), []))


class TouchedAt(unittest.TestCase):
    def touch(self, path, when):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("x")
        os.utime(path, (when, when))

    def touched(self, build, dirs=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = make_project(os.path.join(tmp.name, "myrepo"))
        build(repo)
        with mock.patch.object(pp, "WORKTREE_DIRS",
                               list(pp.FIXED_WORKTREE_DIRS) if dirs is None else dirs):
            return repo, pp.touched_at(repo)

    def test_the_newest_working_tree_file_is_reported(self):
        def build(repo):
            self.touch(os.path.join(repo, "old.txt"), 1000)
            self.touch(os.path.join(repo, "src", "new.txt"), 2000)
        self.assertEqual(self.touched(build)[1], 2000)

    def test_a_nested_worktrees_files_do_not_count_as_the_parents_touch(self):
        def build(repo):
            self.touch(os.path.join(repo, "own.txt"), 1000)
            self.touch(os.path.join(repo, "worktrees", "feat-x", "README.md"), 9000)
        self.assertEqual(self.touched(build)[1], 1000)

    def test_herdrs_own_container_is_not_walked(self):
        def build(repo):
            self.touch(os.path.join(repo, "own.txt"), 1000)
            self.touch(os.path.join(repo, ".worktrees", "myrepo", "stamp"), 9000)
        self.assertEqual(self.touched(build)[1], 1000)

    def test_the_claude_container_is_not_walked(self):
        def build(repo):
            self.touch(os.path.join(repo, "own.txt"), 1000)
            self.touch(os.path.join(repo, ".claude", "worktrees", "stamp"), 9000)
        self.assertEqual(self.touched(build)[1], 1000)

    def test_the_prune_is_by_path_so_the_rest_of_dot_claude_still_counts(self):
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
        def build(repo):
            self.touch(os.path.join(repo, "src", "worktrees", "f.txt"), 9000)
        self.assertEqual(self.touched(build)[1], 9000)

    def test_the_git_index_still_counts(self):
        def build(repo):
            self.touch(os.path.join(repo, ".git", "index"), 5000)
            self.touch(os.path.join(repo, "own.txt"), 1000)
        self.assertEqual(self.touched(build)[1], 5000)


class Kind(unittest.TestCase):
    def kind_of(self, make):
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
        def dangling(g):
            with open(g, "w", encoding="utf-8") as f:
                f.write("gitdir: /x/deleted/.git/worktrees/proj\n")
        self.assertEqual(self.kind_of(dangling), "worktree")

    def test_absent_dot_git_reads_as_a_repo(self):
        self.assertEqual(self.kind_of(lambda g: None), "repo")


class ParentRepo(unittest.TestCase):
    def derive(self, pointer):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        checkout = os.path.join(d.name, "feat-x")
        os.makedirs(checkout)
        with open(os.path.join(checkout, ".git"), "w", encoding="utf-8") as f:
            f.write(pointer)
        return checkout, pp.parent_repo(checkout)

    def test_the_repository_nested_layout_yields_the_parent(self):
        _, parent = self.derive(
            "gitdir: /x/code/group-a/repo-one/.git/worktrees/feat-x\n")
        self.assertEqual(parent, "/x/code/group-a/repo-one")

    def test_the_flat_layout_yields_the_parent(self):
        _, parent = self.derive(
            "gitdir: /x/code/group-b/repo-two/.git/worktrees/feat-y\n")
        self.assertEqual(parent, "/x/code/group-b/repo-two")

    def test_claude_codes_layout_yields_the_parent(self):
        _, parent = self.derive(
            "gitdir: /x/code/repo-three/.git/worktrees/slug-1a2b\n")
        self.assertEqual(parent, "/x/code/repo-three")

    def test_a_relative_pointer_is_resolved_against_the_checkout(self):
        checkout, parent = self.derive("gitdir: ../myrepo/.git/worktrees/feat-x\n")
        self.assertEqual(parent,
                         os.path.join(os.path.dirname(checkout), "myrepo"))

    def test_an_ordinary_repo_has_no_worktree_parent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(pp.parent_repo(make_project(os.path.join(d, "r"))))

    def test_a_submodule_pointer_yields_none(self):
        self.assertIsNone(self.derive("gitdir: /x/super/.git/modules/sub\n")[1])

    def test_an_admin_dir_outside_dot_git_yields_none(self):
        self.assertIsNone(self.derive("gitdir: /x/parent/git/worktrees/wt\n")[1])

    def test_an_admin_dir_with_no_worktrees_component_yields_none(self):
        self.assertIsNone(self.derive("gitdir: /x/parent/.git\n")[1])

    def test_a_line_whose_key_is_not_gitdir_yields_none(self):
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
        _, parent = self.derive("gitdir:  /x/parent/.git/worktrees/wt  \n")
        self.assertEqual(parent, "/x/parent")

    def test_only_the_first_line_is_read(self):
        _, parent = self.derive("gitdir: /x/parent/.git/worktrees/wt\n"
                                "/x/other/.git/worktrees/wt2\n")
        self.assertEqual(parent, "/x/parent")

    def test_a_trailing_separator_on_the_admin_path_is_tolerated(self):
        _, parent = self.derive("gitdir: /x/parent/.git/worktrees/wt/\n")
        self.assertEqual(parent, "/x/parent")


class Elide(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(pp.elide("proj", 10), "proj")

    def test_text_exactly_at_the_width_is_untouched(self):
        self.assertEqual(pp.elide("0123456789", 10), "0123456789")

    def test_longer_text_is_cut_to_the_width_and_marked(self):
        self.assertEqual(pp.elide("0123456789x", 10), "012345678\u2026")

    def test_result_never_exceeds_the_width(self):
        for n in range(1, 60):
            self.assertLessEqual(len(pp.elide("x" * n, 10)), 10)

    def test_the_front_is_kept_not_the_tail(self):
        cut = pp.elide("group-a/a-project-with-a-very-long-name", 12)
        self.assertTrue(cut.startswith("group-a/"))


class Row(unittest.TestCase):
    def test_label_field_is_padded_to_label_width(self):
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
    def which(self, *present):
        return mock.patch.object(pp.shutil, "which",
                                 side_effect=lambda b: f"/bin/{b}" if b in present else None)

    def test_homebrew_is_preferred_and_runnable(self):
        with self.which("brew", "apt-get"):
            self.assertEqual(pp.fzf_installer(), (["brew", "install", "fzf"], True))

    def test_apt_is_found_but_not_runnable(self):
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
        code, _ = self.run_check(fzf=False)
        self.assertEqual(code, 1)

    def test_missing_fzf_reports_how_to_install_it(self):
        _, text = self.run_check(fzf=False, brew=True)
        self.assertIn("brew install fzf", text)

    def test_missing_fzf_with_no_package_manager_still_advises(self):
        _, text = self.run_check(fzf=False)
        self.assertIn("github.com/junegunn/fzf", text)

    def test_it_never_prompts(self):
        with mock.patch("builtins.input", side_effect=AssertionError("prompted")):
            self.run_check(fzf=False)


class EnsureFzf(unittest.TestCase):
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
        ok, run = self.run_ensure("")
        self.assertIsNone(ok)
        run.assert_not_called()

    def test_a_failed_install_dies_rather_than_starting_the_picker(self):
        self.assertIsNone(self.run_ensure("y", fzf_after=True, rc=1)[0])

    def test_an_install_that_reports_success_but_produces_no_fzf_dies(self):
        self.assertIsNone(self.run_ensure("y", fzf_after=False, rc=0)[0])

    def test_a_sudo_installer_is_never_run_only_advised(self):
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
    def test_bytecode_writing_stays_disabled(self):
        self.assertTrue(sys.dont_write_bytecode)

    def test_no_cache_was_written_for_the_script(self):
        stem = os.path.splitext(os.path.basename(SCRIPT))[0]
        if sys.pycache_prefix:
            d = sys.pycache_prefix + os.path.dirname(os.path.abspath(SCRIPT))
        else:
            d = os.path.join(os.path.dirname(os.path.abspath(SCRIPT)), "__pycache__")
        stale = [f for f in (os.listdir(d) if os.path.isdir(d) else [])
                 if f.startswith(stem) and f.endswith(".pyc")]
        self.assertEqual(stale, [], f"stale bytecode in {d}: {stale}")


class BuildLines(unittest.TestCase):
    def build(self, rows, status=None, kinds=None, touched=None):
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
        visible = [f[0] for f in self.fields([("w", "/x/w"), ("r", "/x/r")],
                                             kinds={"/x/w": "worktree"})]
        self.assertIn("worktree", visible[0])
        self.assertIn("repo", visible[1])
        self.assertNotIn("worktree", visible[1])

    def test_age_comes_from_the_row_touch_time(self):
        self.assertIn("1m ago",
                      self.fields([("a", "/x/a")], touched={"/x/a": 40})[0][0])

    def test_the_touch_time_is_read_per_row_not_once_for_all(self):
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
        f = self.fields([("a", "/x/a")], status={"a": "● idle"})[0]
        self.assertIn("● idle", f[0])

    def test_rows_that_are_not_open_get_a_blank_status(self):
        f = self.fields([("a", "/x/a"), ("b", "/x/b")], status={"a": "● idle"})
        self.assertIn("● idle", f[0][0])
        self.assertNotIn("●", f[1][0])

    def test_status_is_matched_on_the_full_label_not_the_elided_one(self):
        long = "w" * (pp.LABEL_WIDTH + 20)
        f = self.fields([(long, "/x/w")], status={long: "● busy"})[0]
        self.assertIn("● busy", f[0])


class TouchTimeIsComputedOncePerRun(unittest.TestCase):
    def walk_counts(self, labelled, open_ws):
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
        self.assertEqual(sorted(calls), ["/x/a", "/x/b", "/x/c"])

    def test_open_repos_are_not_walked_twice(self):
        calls = self.walk_counts([("a", "/x/a")], [ws("a", "w1")])
        self.assertEqual(calls, ["/x/a"])

    def test_a_repo_with_no_open_workspace_is_walked_once(self):
        calls = self.walk_counts([("a", "/x/a")], [])
        self.assertEqual(calls, ["/x/a"])


class ReadToml(unittest.TestCase):
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
        got = self.read('[ui]\nname = "wrong"\n\n[theme]\nname = "nord"\n')
        self.assertEqual(got, {"theme.name": "nord"})

    def test_nested_table_headers_are_kept_whole(self):
        self.assertEqual(self.read('[theme.custom]\nred = "#ff0000"\n'),
                         {"theme.custom.red": "#ff0000"})

    def test_keys_before_any_table_header_are_not_claimed(self):
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
        self.assertEqual(pp.read_toml("/x/does/not/exist.toml", self.WANTED), {})

    def test_an_unreadable_path_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pp.read_toml(d, self.WANTED), {})

    def test_a_quoted_value_that_opens_with_a_brace_is_a_string(self):
        self.assertEqual(
            self.read('[picker]\nlayout = "{plugin:x.y}/bin/lay {workspace}"\n',
                      {"picker.layout"}),
            {"picker.layout": "{plugin:x.y}/bin/lay {workspace}"})

    def test_a_key_with_no_value_contributes_nothing_and_does_not_raise(self):
        self.assertEqual(self.read("[theme]\nname =\n"), {})
        self.assertEqual(self.read('[theme]\nname = ""\n'), {})


class HerdrConfigPath(unittest.TestCase):
    def test_the_documented_override_wins(self):
        with mock.patch.dict(os.environ, {"HERDR_CONFIG_PATH": "/x/other.toml"},
                             clear=True):
            self.assertEqual(pp.herdr_config_path(), "/x/other.toml")

    def test_the_override_beats_the_plugin_config_dir(self):
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
        self.assertFalse(self.probe(field="theme.dark_name",
                                    stdout=self.DIAGNOSTIC))

    def test_stderr_is_read_too(self):
        self.assertTrue(self.probe(stderr=self.DIAGNOSTIC))

    def test_an_unrunnable_herdr_is_not_a_rejection(self):
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
        for alias, target in pp.THEME_ALIASES.items():
            with self.subTest(alias=alias):
                self.assertIn(target, pp.PALETTES)

    def test_every_palette_has_all_five_roles(self):
        for name, roles in pp.PALETTES.items():
            with self.subTest(theme=name):
                self.assertEqual(len(roles), len(pp.STATUS_ROLE_ORDER))


class ParseColor(unittest.TestCase):
    def test_six_digit_hex(self):
        self.assertEqual(pp.parse_color("#8899aa"), (0x88, 0x99, 0xaa))

    def test_three_digit_hex_expands_by_seventeen(self):
        self.assertEqual(pp.parse_color("#f0a"), (255, 0, 170))

    def test_rgb_function(self):
        self.assertEqual(pp.parse_color("rgb(137, 180, 250)"), (137, 180, 250))

    def test_named_colors_map_to_their_crossterm_index(self):
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
    def resolve(self, rejects=False, **config):
        return pp.resolve_theme(config, rejects=lambda field: rejects)

    def test_defaults_are_dots_on_catppuccin(self):
        icons, colours = self.resolve()
        self.assertEqual(icons["working"], "●")
        self.assertEqual(icons["idle"], "○")
        self.assertEqual(colours["working"], (249, 226, 175))

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
        _, colours = self.resolve(**{"theme.name": "terminal"})
        self.assertEqual(colours, {"working": 3, "blocked": 9, "done": 6,
                                   "idle": 2, "unknown": 7})

    def test_each_status_reads_its_own_role(self):
        _, colours = self.resolve(**{"theme.name": "gruvbox"})
        self.assertEqual(colours["working"], (250, 189, 47))
        self.assertEqual(colours["blocked"], (251, 73, 52))
        self.assertEqual(colours["done"], (142, 192, 124))
        self.assertEqual(colours["idle"], (184, 187, 38))
        self.assertEqual(colours["unknown"], (146, 131, 116))

    def test_a_name_herdr_also_rejects_uses_herdrs_own_default(self):
        _, typo = self.resolve(rejects=True, **{"theme.name": "monokai"})
        _, default = self.resolve()
        self.assertEqual(typo, default)

    def test_a_name_herdr_accepts_but_this_table_lacks_uses_the_terminal_palette(self):
        _, newer = self.resolve(rejects=False, **{"theme.name": "brand-new-theme"})
        _, terminal = self.resolve(**{"theme.name": "terminal"})
        _, default = self.resolve()
        self.assertEqual(newer, terminal)
        self.assertNotEqual(newer, default)

    def test_an_unset_name_never_asks_herdr(self):
        asked = []
        pp.resolve_theme({}, rejects=lambda f: asked.append(f) or False)
        self.assertEqual(asked, [])

    def test_a_known_name_never_asks_herdr(self):
        asked = []
        pp.resolve_theme({"theme.name": "gruvbox"},
                         rejects=lambda f: asked.append(f) or False)
        self.assertEqual(asked, [])

    def test_the_probe_is_told_which_field_to_look_for(self):
        asked = []
        pp.resolve_theme({"theme.auto_switch": "true", "theme.dark_name": "nope"},
                         rejects=lambda f: asked.append(f) or False)
        self.assertEqual(asked, ["theme.dark_name"])

    def test_a_custom_override_replaces_only_its_role(self):
        _, colours = self.resolve(**{"theme.name": "terminal",
                                     "theme.custom.red": "#ff8800"})
        self.assertEqual(colours["blocked"], (255, 136, 0))
        self.assertEqual(colours["working"], 3)

    def test_every_status_role_is_overridable(self):
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
        self.assertEqual(colours["working"], (250, 189, 47))

    def test_auto_switch_off_ignores_dark_name(self):
        _, colours = self.resolve(**{"theme.name": "terminal",
                                     "theme.auto_switch": "false",
                                     "theme.dark_name": "gruvbox"})
        self.assertEqual(colours["working"], 3)

    def test_mode_overrides_apply_only_under_auto_switch(self):
        keys = {"theme.name": "terminal", "theme.custom.dark.yellow": "#010203"}
        _, off = self.resolve(**keys)
        _, on = self.resolve(**dict(keys, **{"theme.auto_switch": "true"}))
        self.assertEqual(off["working"], 3)
        self.assertEqual(on["working"], (1, 2, 3))

    def test_a_mode_override_beats_the_unqualified_one(self):
        _, colours = self.resolve(**{"theme.name": "terminal",
                                     "theme.auto_switch": "true",
                                     "theme.custom.yellow": "#111111",
                                     "theme.custom.dark.yellow": "#222222"})
        self.assertEqual(colours["working"], (0x22, 0x22, 0x22))

    def test_theme_keys_covers_every_key_resolve_theme_reads(self):
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
        return self.cell(status, colours).split("m", 1)[0] + "m"

    def test_glyph_word_and_colour_are_all_present(self):
        self.assertEqual(self.cell("working"),
                         "\033[38;5;3m" + "◐ working".ljust(pp.STATUS_WIDTH) + "\033[39m")

    def test_the_status_word_is_kept_so_the_filter_can_match_it(self):
        self.assertIn("idle", self.cell("idle"))

    def test_visible_text_is_padded_not_the_escaped_string(self):
        import re as _re
        visible = _re.sub(r"\033\[[0-9;]*m", "", self.cell("idle"))
        self.assertEqual(len(visible), pp.STATUS_WIDTH)

    def test_an_unrecognised_status_takes_the_unknown_glyph_and_colour(self):
        self.assertIn("·", self.cell("brand-new"))
        self.assertEqual(self.escape("brand-new"), self.escape("unknown"))

    def test_a_reset_colour_is_kept_distinct_from_a_missing_one(self):
        reset = {"working": 3, "idle": 2, "unknown": None}
        self.assertEqual(self.escape("unknown", reset), "\033[39m")


class Heading(unittest.TestCase):
    def test_is_exactly_one_line(self):
        self.assertNotIn("\n", pp.HEADING)

    def test_hidden_fields_are_empty(self):
        self.assertEqual(pp.HEADING.split("\t")[1:], ["", ""])

    def test_columns_line_up_with_a_row(self):
        row = pp.ROW.format("proj", "worktree", "3m ago", "\u25cf idle",
                            "/x/proj", "proj")
        self.assertEqual(pp.HEADING.index("KIND"), row.index("worktree"))
        self.assertEqual(pp.HEADING.index("TOUCHED"), row.index("3m ago"))
        self.assertEqual(pp.HEADING.index("AGENT STATUS"), row.index("\u25cf idle"))

    def test_kind_column_fits_its_widest_value(self):
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
    def test_space_is_not_bound(self):
        self.assertNotIn("space:", pp.KEYS)

    def test_select_all_and_none_stay_bound(self):
        self.assertIn("ctrl-a:select-all", pp.KEYS)
        self.assertIn("ctrl-d:deselect-all", pp.KEYS)

    def test_legend_agrees_with_the_bindings_about_space(self):
        self.assertEqual("space" in pp.KEYS, "space" in pp.HEADER)

    def test_legend_names_every_bound_key(self):
        for key in ("ctrl-a", "ctrl-d"):
            self.assertIn(key, pp.HEADER)


class ManifestIsValidToml(unittest.TestCase):
    def setUp(self):
        if tomllib is None:
            self.skipTest("herdr-plugin.toml was NOT parsed: " + NO_TOML)

    def parse(self, path):
        with open(path, "rb") as f:
            return tomllib.load(f)

    def test_the_manifest_parses(self):
        try:
            self.parse(MANIFEST)
        except tomllib.TOMLDecodeError as e:
            self.fail("herdr-plugin.toml is not valid TOML: %s" % e)

    def test_a_typo_in_the_manifest_is_really_caught(self):
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
