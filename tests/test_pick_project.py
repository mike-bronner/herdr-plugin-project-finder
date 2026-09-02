"""Unit tests for bin/pick-project's pure selection logic.

Run: python3 -m unittest discover tests
"""
import importlib.machinery, importlib.util, os, unittest
from unittest import mock

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
        touched = {"/b": 10, "/d": 20}
        with mock.patch.object(pp, "touched_at", side_effect=lambda p: touched[p]):
            head, rest = pp.order_rows(labelled, open_ws)
        self.assertEqual(head, [("c", "/c"), ("a", "/a")])   # sidebar order, ghost skipped
        self.assertEqual(rest, [("d", "/d"), ("b", "/b")])   # newest first

    def test_nothing_open(self):
        with mock.patch.object(pp, "touched_at", return_value=0):
            head, rest = pp.order_rows([("a", "/a")], [])
        self.assertEqual(head, [])
        self.assertEqual(rest, [("a", "/a")])


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

    def test_no_match_without_sentinel_is_cancel(self):
        self.assertIsNone(pp.parse_selection(1, ""))


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



class CreateWorkspace(unittest.TestCase):
    RES = {"workspace": {"workspace_id": "w9"}, "tab": {"tab_id": "w9:t1"},
           "root_pane": {"pane_id": "p1"}}

    def run_create(self, res):
        with mock.patch.object(pp, "herdr", return_value=res) as h, \
             mock.patch.object(pp.subprocess, "Popen"), \
             mock.patch.object(pp, "die", side_effect=SystemExit):
            wid = pp.create_workspace("proj", "/x/proj", set())
        return wid, [c.args for c in h.call_args_list]

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


if __name__ == "__main__":
    unittest.main()
