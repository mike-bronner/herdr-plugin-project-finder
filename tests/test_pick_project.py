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

    kind() and touched_at() both hit the filesystem, so they are stubbed here
    per path: these tests are about which value lands in which field, not about
    how either is derived.
    """

    def build(self, rows, status=None, kinds=None, touched=None):
        with mock.patch.object(pp, "kind",
                               side_effect=lambda p: (kinds or {}).get(p, "repo")), \
             mock.patch.object(pp, "touched_at",
                               side_effect=lambda p: (touched or {}).get(p, 0)):
            return pp.build_lines(rows, status or {}, 100)

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

    def test_age_comes_from_touched_at(self):
        # now=100 and touched=40 is 60s, which human_age renders as "1m ago".
        self.assertIn("1m ago",
                      self.fields([("a", "/x/a")], touched={"/x/a": 40})[0][0])

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

    def test_open_rows_get_their_agent_status(self):
        f = self.fields([("a", "/x/a")], status={"a": "idle"})[0]
        self.assertIn("● idle", f[0])

    def test_rows_that_are_not_open_get_a_blank_status(self):
        f = self.fields([("a", "/x/a"), ("b", "/x/b")], status={"a": "idle"})
        self.assertIn("● idle", f[0][0])
        self.assertNotIn("●", f[1][0])

    def test_status_is_matched_on_the_full_label_not_the_elided_one(self):
        # The elided label is what gets displayed, but `status` is keyed by the
        # real workspace label, so a truncated name must still find its agent.
        long = "w" * (pp.LABEL_WIDTH + 20)
        f = self.fields([(long, "/x/w")], status={long: "busy"})[0]
        self.assertIn("● busy", f[0])


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
