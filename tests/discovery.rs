mod support;

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use pick_project::config::{
    normpath, worktree_locations, Environment, HerdrConfig, FIXED_WORKTREE_DIRS,
};
use pick_project::discover::{
    debug_line, duplicated_basenames, elide, human_age, label_for, labelled,
    order_rows, parent_repo, repo_name, repos, row_kind, touched_at, Counts, Kind,
};
use support::*;

const POINTER: &str = "/x/parent/.git/worktrees/wt";

struct Tree {
    dir: TempDir,
}

impl Tree {
    fn new() -> Tree {
        Tree {
            dir: TempDir::new(),
        }
    }

    fn root(&self) -> &Path {
        self.dir.path()
    }

    fn repo(&self, rel: &str) -> PathBuf {
        make_repo(&self.dir.join(rel))
    }

    fn worktree(&self, rel: &str) -> PathBuf {
        make_worktree(&self.dir.join(rel), POINTER)
    }

    fn at(&self, rel: &str) -> PathBuf {
        self.dir.join(rel)
    }
}

fn fixed() -> Vec<String> {
    FIXED_WORKTREE_DIRS.iter().map(|d| d.to_string()).collect()
}

fn find(tree: &Tree, dirs: &[String], roots: &[String]) -> Vec<PathBuf> {
    repos(tree.root(), dirs, roots).0
}

fn find_default(tree: &Tree) -> Vec<PathBuf> {
    find(tree, &fixed(), &[])
}

fn set(paths: &[PathBuf]) -> HashSet<String> {
    names(paths)
}

fn locations_for(setting: &str) -> (Vec<String>, Vec<String>) {
    let config = HerdrConfig::from_pairs(&[("worktrees.directory", setting)]);
    worktree_locations(&config, &bare_env(&[]))
}

#[test]
fn a_repo_directly_under_the_root_is_found() {
    let tree = Tree::new();
    let repo = tree.repo("myrepo");
    assert_eq!(find_default(&tree), vec![repo]);
}

#[test]
fn a_repo_inside_a_grouping_dir_is_found() {
    let tree = Tree::new();
    let repo = tree.repo("group-a/myrepo");
    assert_eq!(find_default(&tree), vec![repo]);
}

#[test]
fn a_herdr_worktree_three_levels_down_is_found() {
    let tree = Tree::new();
    let repo = tree.repo("myrepo");
    let wt = tree.worktree("worktrees/myrepo/feat-x");
    assert_eq!(set(&find_default(&tree)), set(&[repo, wt]));
}

#[test]
fn a_worktree_found_three_levels_down_is_tagged_worktree() {
    let tree = Tree::new();
    tree.worktree("worktrees/myrepo/feat-x");
    let found = find_default(&tree);
    assert_eq!(found.iter().map(|p| row_kind(p)).collect::<Vec<_>>(), vec![Kind::Worktree]);
}

#[test]
fn a_fourth_level_is_not_searched() {
    let tree = Tree::new();
    tree.repo("a/b/c/d");
    assert!(find_default(&tree).is_empty());
}

#[test]
fn a_repo_nested_inside_a_repo_is_skipped() {
    let tree = Tree::new();
    let outer = tree.repo("myrepo");
    tree.repo("myrepo/vendor/pkg");
    let inner = tree.repo("group-a/repo");
    tree.repo("group-a/repo/sub");
    assert_eq!(set(&find_default(&tree)), set(&[outer, inner]));
}

#[test]
fn a_nested_repo_sorting_ahead_of_its_parent_is_still_skipped() {
    let tree = Tree::new();
    let outer = tree.repo("a");
    tree.repo("a/-x");
    assert_eq!(find_default(&tree), vec![outer]);
}

#[test]
fn a_sibling_sharing_a_name_prefix_is_not_mistaken_for_nesting() {
    let tree = Tree::new();
    let one = tree.repo("myrepo");
    let two = tree.repo("myrepo_old");
    assert_eq!(set(&find_default(&tree)), set(&[one, two]));
}

#[test]
fn a_worktree_in_herdrs_own_container_is_found() {
    let tree = Tree::new();
    let repo = tree.repo("myrepo");
    let wt = tree.worktree("myrepo/.worktrees/myrepo/feat-x");
    assert_eq!(set(&find_default(&tree)), set(&[repo, wt]));
}

#[test]
fn a_worktree_in_claude_codes_container_is_found() {
    let tree = Tree::new();
    let repo = tree.repo("myrepo");
    let wt = tree.worktree("myrepo/.claude/worktrees/slug-1a2b");
    assert_eq!(set(&find_default(&tree)), set(&[repo, wt]));
}

#[test]
fn a_worktree_in_the_undotted_container_is_found() {
    let tree = Tree::new();
    tree.repo("myrepo");
    let wt = tree.worktree("myrepo/worktrees/feat-x");
    assert!(find_default(&tree).contains(&wt));
}

#[test]
fn a_nested_worktree_is_found_under_a_repo_at_any_depth() {
    let tree = Tree::new();
    tree.repo("group/myrepo");
    let wt = tree.worktree("group/myrepo/.worktrees/myrepo/feat-x");
    assert!(find_default(&tree).contains(&wt));
}

#[test]
fn a_nested_worktree_is_tagged_worktree() {
    let tree = Tree::new();
    tree.repo("myrepo");
    tree.worktree("myrepo/.worktrees/myrepo/feat-x");
    let nested: Vec<Kind> = find_default(&tree)
        .iter()
        .filter(|p| p.to_string_lossy().contains(".worktrees"))
        .map(|p| row_kind(p))
        .collect();
    assert_eq!(nested, vec![Kind::Worktree]);
}

#[test]
fn an_ordinary_repo_inside_a_container_is_still_skipped() {
    let tree = Tree::new();
    let repo = tree.repo("myrepo");
    tree.repo("myrepo/worktrees/vendored");
    assert_eq!(find_default(&tree), vec![repo]);
}

#[test]
fn a_container_under_a_repo_that_is_itself_nested_is_not_searched() {
    let tree = Tree::new();
    let repo = tree.repo("myrepo");
    tree.repo("myrepo/vendor/pkg");
    tree.worktree("myrepo/vendor/pkg/.worktrees/pkg/feat-x");
    assert_eq!(find_default(&tree), vec![repo]);
}

#[test]
fn a_container_named_by_herdrs_config_is_searched() {
    let tree = Tree::new();
    tree.repo("myrepo");
    let wt = tree.worktree("myrepo/trees/feat-x");
    let (dirs, roots) = locations_for("trees");
    assert!(find(&tree, &dirs, &roots).contains(&wt));
}

#[test]
fn a_worktree_beside_its_repo_is_found() {
    let tree = Tree::new();
    let repo = tree.repo("group-a/repo-one");
    let wt = tree.worktree("group-a/worktrees/repo-one/feat-x");
    let (dirs, roots) = locations_for("../worktrees");
    assert_eq!(set(&find(&tree, &dirs, &roots)), set(&[repo, wt]));
}

#[test]
fn a_worktree_beside_its_repo_is_tagged_worktree() {
    let tree = Tree::new();
    tree.repo("group-a/repo-one");
    tree.worktree("group-a/worktrees/repo-one/feat-x");
    let (dirs, roots) = locations_for("../worktrees");
    let beside: Vec<Kind> = find(&tree, &dirs, &roots)
        .iter()
        .filter(|p| p.to_string_lossy().contains("/worktrees/"))
        .map(|p| row_kind(p))
        .collect();
    assert_eq!(beside, vec![Kind::Worktree]);
}

#[test]
fn each_repo_resolves_its_own_sibling_container() {
    let tree = Tree::new();
    let one = tree.repo("group-a/repo-one");
    let two = tree.repo("group-b/repo-two");
    let x = tree.worktree("group-a/worktrees/repo-one/feat-x");
    let y = tree.worktree("group-b/worktrees/repo-two/feat-y");
    let (dirs, roots) = locations_for("../worktrees");
    assert_eq!(set(&find(&tree, &dirs, &roots)), set(&[one, two, x, y]));
}

#[test]
fn a_sibling_container_the_depth_bands_also_reach_lists_once() {
    let tree = Tree::new();
    let repo = tree.repo("myrepo");
    let wt = tree.worktree("worktrees/myrepo/feat-x");
    let (dirs, roots) = locations_for("../worktrees");
    let (found, counts) = repos(tree.root(), &dirs, &roots);
    assert_eq!(found, vec![repo, wt]);
    assert_eq!(counts, Counts { bands: 2, containers: 0 });
}

#[test]
fn every_worktree_layout_lists_at_once() {
    let tree = Tree::new();
    let one = tree.repo("group-a/repo-one");
    let two = tree.repo("repo-two");
    let a = tree.worktree("repo-two/.claude/worktrees/slug-1a2b");
    let b = tree.worktree("group-a/repo-one/.worktrees/repo-one/feat-old");
    let c = tree.worktree("worktrees/repo-two/feat-flat");
    let d = tree.worktree("group-a/worktrees/repo-one/feat-new");
    let (dirs, roots) = locations_for("../worktrees");
    assert_eq!(
        set(&find(&tree, &dirs, &roots)),
        set(&[one, two, a, b, c, d])
    );
}

#[test]
fn a_bare_parent_container_admits_only_worktrees() {
    let tree = Tree::new();
    let one = tree.repo("a/group/one");
    tree.repo("a/group/trees/vendored");
    let wt = tree.worktree("a/group/trees/feat-x");
    let mut dirs = fixed();
    dirs.push("..".to_string());
    assert_eq!(set(&find(&tree, &dirs, &[])), set(&[one, wt]));
}

#[test]
fn an_absolute_worktree_root_outside_the_picker_root_is_searched() {
    let tree = Tree::new();
    let flat = TempDir::new();
    let repo = tree.repo("myrepo");
    let wt = make_worktree(&flat.join("myrepo/feat-x"), POINTER);
    let roots = vec![flat.path().to_string_lossy().to_string()];
    assert_eq!(set(&find(&tree, &fixed(), &roots)), set(&[repo, wt]));
}

#[test]
fn a_worktree_reached_twice_is_listed_once() {
    let tree = Tree::new();
    let repo = tree.repo("myrepo");
    let wt = tree.worktree("worktrees/myrepo/feat-x");
    let roots = vec![tree.at("worktrees").to_string_lossy().to_string()];
    assert_eq!(find(&tree, &fixed(), &roots), vec![repo, wt]);
}

#[test]
fn each_pass_reports_what_it_contributed() {
    let tree = Tree::new();
    tree.repo("myrepo");
    tree.worktree("worktrees/myrepo/feat-x");
    tree.worktree("myrepo/.worktrees/myrepo/feat-y");
    let (found, counts) = repos(tree.root(), &fixed(), &[]);
    assert_eq!(found.len(), 3);
    assert_eq!(counts, Counts { bands: 2, containers: 1 });
}

#[test]
fn the_two_figures_account_for_every_row() {
    let tree = Tree::new();
    tree.repo("myrepo");
    tree.worktree("worktrees/myrepo/feat-x");
    tree.worktree("myrepo/.worktrees/myrepo/feat-y");
    let (found, counts) = repos(tree.root(), &fixed(), &[]);
    assert_eq!(counts.bands + counts.containers, found.len());
}

#[test]
fn a_pass_that_found_nothing_reports_zero() {
    let tree = Tree::new();
    tree.repo("myrepo");
    let (found, counts) = repos(tree.root(), &fixed(), &[]);
    assert_eq!(found.len(), 1);
    assert_eq!(counts, Counts { bands: 1, containers: 0 });
}

#[test]
fn nothing_found_at_all_reports_two_zeroes() {
    let tree = Tree::new();
    let (found, counts) = repos(tree.root(), &fixed(), &[]);
    assert!(found.is_empty());
    assert_eq!(counts, Counts::default());
}

#[test]
fn a_worktree_reached_through_a_symlinked_container_is_found() {
    let tree = Tree::new();
    let repo = tree.repo("myrepo");
    let elsewhere = TempDir::new();
    let real = make_worktree(&elsewhere.join("group/repo-one/feat-x"), POINTER);
    std::os::unix::fs::symlink(elsewhere.join("group"), tree.at("worktrees")).unwrap();
    let roots = vec![tree.at("worktrees").to_string_lossy().to_string()];
    let found = find(&tree, &fixed(), &roots);
    assert!(found.contains(&repo));
    assert_eq!(found.len(), 2, "{:?}", found);
    assert!(std::fs::canonicalize(&found[1]).unwrap() == std::fs::canonicalize(&real).unwrap());
}

#[test]
fn the_fixed_containers_are_always_searched() {
    let (dirs, roots) = worktree_locations(&HerdrConfig::default(), &bare_env(&[]));
    assert_eq!(dirs, fixed());
    assert!(roots.is_empty());
}

#[test]
fn both_real_layouts_are_covered_by_the_fixed_names() {
    assert!(FIXED_WORKTREE_DIRS.contains(&".worktrees"));
    assert!(FIXED_WORKTREE_DIRS.contains(&".claude/worktrees"));
}

#[test]
fn a_relative_setting_joins_the_containers() {
    let (dirs, roots) = locations_for("trees");
    let mut want = fixed();
    want.push("trees".to_string());
    assert_eq!(dirs, want);
    assert!(roots.is_empty());
}

#[test]
fn a_relative_setting_already_covered_is_not_repeated() {
    assert_eq!(locations_for("./.worktrees").0, fixed());
}

#[test]
fn a_setting_beside_the_repo_stays_relative() {
    let (dirs, roots) = locations_for("../trees");
    assert!(dirs.contains(&"../trees".to_string()));
    assert!(roots.is_empty());
}

#[test]
fn an_absolute_setting_becomes_a_flat_root() {
    let (dirs, roots) = locations_for("/srv/worktrees/");
    assert_eq!(dirs, fixed());
    assert_eq!(roots, vec!["/srv/worktrees".to_string()]);
}

#[test]
fn herdrs_shipped_default_is_read_as_a_root_not_a_container() {
    let config = HerdrConfig::from_pairs(&[("worktrees.directory", "~/.herdr/worktrees")]);
    let env = Environment::from_pairs(&[("HOME", "/x/home")]);
    let (_, roots) = worktree_locations(&config, &env);
    assert_eq!(roots, vec!["/x/home/.herdr/worktrees".to_string()]);
}

#[test]
fn an_empty_setting_degrades_to_the_fixed_containers() {
    let (dirs, roots) = locations_for("   ");
    assert_eq!(dirs, fixed());
    assert!(roots.is_empty());
}

#[test]
fn an_unreadable_herdr_config_degrades_rather_than_failing() {
    let config = HerdrConfig::read(Path::new("/nope/config.toml"));
    let (dirs, roots) = worktree_locations(&config, &bare_env(&[]));
    assert_eq!(dirs, fixed());
    assert!(roots.is_empty());
}

#[test]
fn a_setting_that_is_not_a_string_degrades_rather_than_failing() {
    let config = HerdrConfig::parse("[worktrees]\ndirectory = [\"a\", \"b\"]\n");
    let (dirs, roots) = worktree_locations(&config, &bare_env(&[]));
    assert_eq!(dirs, fixed());
    assert!(roots.is_empty());
}

fn touched_with(build: impl Fn(&Path), dirs: &[String]) -> u64 {
    let dir = TempDir::new();
    let repo = make_repo(&dir.join("myrepo"));
    build(&repo);
    touched_at(&repo, dirs)
}

#[test]
fn the_newest_working_tree_file_is_reported() {
    let got = touched_with(
        |repo| {
            touch(&repo.join("old.txt"), 1000);
            touch(&repo.join("src/new.txt"), 2000);
        },
        &fixed(),
    );
    assert_eq!(got, 2000);
}

#[test]
fn a_nested_worktrees_files_do_not_count_as_the_parents_touch() {
    let got = touched_with(
        |repo| {
            touch(&repo.join("own.txt"), 1000);
            touch(&repo.join("worktrees/feat-x/README.md"), 9000);
        },
        &fixed(),
    );
    assert_eq!(got, 1000);
}

#[test]
fn herdrs_own_container_is_not_walked() {
    let got = touched_with(
        |repo| {
            touch(&repo.join("own.txt"), 1000);
            touch(&repo.join(".worktrees/myrepo/stamp"), 9000);
        },
        &fixed(),
    );
    assert_eq!(got, 1000);
}

#[test]
fn the_claude_container_is_not_walked() {
    let got = touched_with(
        |repo| {
            touch(&repo.join("own.txt"), 1000);
            touch(&repo.join(".claude/worktrees/stamp"), 9000);
        },
        &fixed(),
    );
    assert_eq!(got, 1000);
}

#[test]
fn the_prune_is_by_path_so_the_rest_of_dot_claude_still_counts() {
    let got = touched_with(
        |repo| {
            touch(&repo.join("own.txt"), 1000);
            touch(&repo.join(".claude/settings.json"), 9000);
        },
        &fixed(),
    );
    assert_eq!(got, 9000);
}

#[test]
fn a_configured_container_is_pruned_as_well() {
    let got = touched_with(
        |repo| {
            touch(&repo.join("own.txt"), 1000);
            touch(&repo.join("trees/b/f.txt"), 9000);
        },
        &["trees".to_string()],
    );
    assert_eq!(got, 1000);
}

#[test]
fn a_directory_merely_sharing_a_prefix_is_not_pruned() {
    let got = touched_with(
        |repo| touch(&repo.join("worktrees-notes/f.txt"), 9000),
        &fixed(),
    );
    assert_eq!(got, 9000);
}

#[test]
fn the_same_container_name_somewhere_else_in_the_tree_still_counts() {
    let got = touched_with(
        |repo| touch(&repo.join("src/worktrees/f.txt"), 9000),
        &fixed(),
    );
    assert_eq!(got, 9000);
}

#[test]
fn the_git_index_still_counts() {
    let got = touched_with(
        |repo| {
            touch(&repo.join(".git/index"), 5000);
            touch(&repo.join("own.txt"), 1000);
        },
        &fixed(),
    );
    assert_eq!(got, 5000);
}

#[test]
fn a_repo_with_no_files_at_all_reports_nothing() {
    let dir = TempDir::new();
    let repo = make_repo(&dir.join("myrepo"));
    assert_eq!(touched_at(&repo, &fixed()), 0);
}

#[test]
fn a_directory_dot_git_is_a_repo() {
    let dir = TempDir::new();
    let repo = make_repo(&dir.join("proj"));
    assert_eq!(row_kind(&repo), Kind::Repo);
}

#[test]
fn a_file_dot_git_is_a_worktree() {
    let dir = TempDir::new();
    let wt = make_worktree(&dir.join("proj"), "/x/parent/.git/worktrees/proj");
    assert_eq!(row_kind(&wt), Kind::Worktree);
}

#[test]
fn a_worktree_with_a_deleted_parent_still_classifies() {
    let dir = TempDir::new();
    let wt = make_worktree(&dir.join("proj"), "/x/deleted/.git/worktrees/proj");
    assert_eq!(row_kind(&wt), Kind::Worktree);
}

#[test]
fn an_absent_dot_git_reads_as_a_repo() {
    let dir = TempDir::new();
    let plain = dir.dir("proj");
    assert_eq!(row_kind(&plain), Kind::Repo);
}

fn derive(pointer: &str) -> (TempDir, PathBuf, Option<PathBuf>) {
    let dir = TempDir::new();
    let checkout = dir.dir("feat-x");
    std::fs::write(checkout.join(".git"), pointer).unwrap();
    let parent = parent_repo(&checkout);
    (dir, checkout, parent)
}

#[test]
fn the_repository_nested_layout_yields_the_parent() {
    let (_dir, _, parent) = derive("gitdir: /x/code/group-a/repo-one/.git/worktrees/feat-x\n");
    assert_eq!(parent, Some(PathBuf::from("/x/code/group-a/repo-one")));
}

#[test]
fn the_flat_layout_yields_the_parent() {
    let (_dir, _, parent) = derive("gitdir: /x/code/group-b/repo-two/.git/worktrees/feat-y\n");
    assert_eq!(parent, Some(PathBuf::from("/x/code/group-b/repo-two")));
}

#[test]
fn claude_codes_layout_yields_the_parent() {
    let (_dir, _, parent) = derive("gitdir: /x/code/repo-three/.git/worktrees/slug-1a2b\n");
    assert_eq!(parent, Some(PathBuf::from("/x/code/repo-three")));
}

#[test]
fn a_relative_pointer_is_resolved_against_the_checkout() {
    let (_dir, checkout, parent) = derive("gitdir: ../myrepo/.git/worktrees/feat-x\n");
    assert_eq!(parent, Some(checkout.parent().unwrap().join("myrepo")));
}

#[test]
fn an_ordinary_repo_has_no_worktree_parent() {
    let dir = TempDir::new();
    let repo = make_repo(&dir.join("r"));
    assert_eq!(parent_repo(&repo), None);
}

#[test]
fn a_submodule_pointer_yields_none() {
    assert_eq!(derive("gitdir: /x/super/.git/modules/sub\n").2, None);
}

#[test]
fn an_admin_dir_outside_dot_git_yields_none() {
    assert_eq!(derive("gitdir: /x/parent/git/worktrees/wt\n").2, None);
}

#[test]
fn an_admin_dir_with_no_worktrees_component_yields_none() {
    assert_eq!(derive("gitdir: /x/parent/.git\n").2, None);
}

#[test]
fn a_line_whose_key_is_not_gitdir_yields_none() {
    assert_eq!(derive("worktreedir: /x/parent/.git/worktrees/wt\n").2, None);
}

#[test]
fn a_gitdir_key_with_no_target_yields_none() {
    assert_eq!(derive("gitdir:   \n").2, None);
}

#[test]
fn an_empty_dot_git_yields_none() {
    assert_eq!(derive("").2, None);
}

#[test]
fn a_missing_dot_git_yields_none() {
    let dir = TempDir::new();
    assert_eq!(parent_repo(&dir.join("gone")), None);
}

#[test]
fn whitespace_around_the_pointer_target_is_ignored() {
    let (_dir, _, parent) = derive("gitdir:  /x/parent/.git/worktrees/wt  \n");
    assert_eq!(parent, Some(PathBuf::from("/x/parent")));
}

#[test]
fn only_the_first_line_of_the_pointer_is_read() {
    let (_dir, _, parent) = derive("gitdir: /x/parent/.git/worktrees/wt\n/x/other/.git/worktrees/wt2\n");
    assert_eq!(parent, Some(PathBuf::from("/x/parent")));
}

#[test]
fn a_trailing_separator_on_the_admin_path_is_tolerated() {
    let (_dir, _, parent) = derive("gitdir: /x/parent/.git/worktrees/wt/\n");
    assert_eq!(parent, Some(PathBuf::from("/x/parent")));
}

#[test]
fn short_text_is_untouched_by_eliding() {
    assert_eq!(elide("proj", 10), "proj");
}

#[test]
fn text_exactly_at_the_width_is_untouched() {
    assert_eq!(elide("0123456789", 10), "0123456789");
}

#[test]
fn longer_text_is_cut_to_the_width_and_marked() {
    assert_eq!(elide("0123456789x", 10), "012345678…");
}

#[test]
fn an_elided_result_never_exceeds_the_width() {
    for n in 1..60 {
        assert!(elide(&"x".repeat(n), 10).chars().count() <= 10);
    }
}

#[test]
fn eliding_keeps_the_front_not_the_tail() {
    assert!(elide("group-a/a-project-with-a-very-long-name", 12).starts_with("group-a/"));
}

#[test]
fn a_multibyte_label_is_cut_by_character_not_by_byte() {
    assert_eq!(elide("ααααα", 3), "αα…");
}

#[test]
fn a_worktree_names_the_repository_it_belongs_to() {
    let dir = TempDir::new();
    let checkout = make_worktree(&dir.join("feat-x"), "/x/code/tru-data/.git/worktrees/feat-x");
    assert_eq!(repo_name(&checkout), Some("tru-data".to_string()));
}

#[test]
fn a_plain_repository_names_no_parent_repository() {
    let dir = TempDir::new();
    let repo = make_repo(&dir.join("tru-data"));
    assert_eq!(repo_name(&repo), None);
}

#[test]
fn a_worktree_whose_pointer_makes_no_sense_names_no_repository() {
    let dir = TempDir::new();
    let checkout = make_worktree(&dir.join("feat-x"), "/x/super/.git/modules/sub");
    assert_eq!(repo_name(&checkout), None);
}

#[test]
fn an_untouched_row_reads_as_a_dash() {
    assert_eq!(human_age(0, 100), "-");
}

#[test]
fn ages_are_reported_in_the_largest_unit_that_fits() {
    assert_eq!(human_age(100, 100), "0m ago");
    assert_eq!(human_age(100, 100 + 3599), "59m ago");
    assert_eq!(human_age(100, 100 + 3600), "1h ago");
    assert_eq!(human_age(100, 100 + 86399), "23h ago");
    assert_eq!(human_age(100, 100 + 86400), "1d ago");
    assert_eq!(human_age(100, 100 + 604799), "6d ago");
    assert_eq!(human_age(100, 100 + 604800), "1w ago");
}

#[test]
fn a_duplicated_basename_is_qualified_by_its_parent() {
    let paths = vec![
        PathBuf::from("/x/group-a/api"),
        PathBuf::from("/x/group-b/api"),
        PathBuf::from("/x/solo"),
    ];
    let dupes = duplicated_basenames(&paths);
    assert_eq!(label_for(&paths[0], &dupes), "group-a/api");
    assert_eq!(label_for(&paths[1], &dupes), "group-b/api");
    assert_eq!(label_for(&paths[2], &dupes), "solo");
}

#[test]
fn labelling_keeps_the_paths_in_order() {
    let paths = vec![PathBuf::from("/x/b"), PathBuf::from("/x/a")];
    assert_eq!(
        labelled(&paths),
        vec![
            ("b".to_string(), PathBuf::from("/x/b")),
            ("a".to_string(), PathBuf::from("/x/a"))
        ]
    );
}

fn rows(pairs: &[(&str, &str)]) -> Vec<(String, PathBuf)> {
    pairs
        .iter()
        .map(|(l, p)| (l.to_string(), PathBuf::from(p)))
        .collect()
}

#[test]
fn open_rows_come_first_in_sidebar_order_then_the_rest_by_touch() {
    let listed = rows(&[("a", "/a"), ("b", "/b"), ("c", "/c"), ("d", "/d")]);
    let open = vec![open("c", "w1"), open("a", "w2"), open("ghost", "w3")];
    let touched = |p: &Path| match p.to_string_lossy().as_ref() {
        "/a" => 30,
        "/b" => 10,
        "/c" => 40,
        _ => 20,
    };
    let (head, rest) = order_rows(&listed, &open, &mut |p| touched(p));
    assert_eq!(
        head.iter().map(|r| r.label.clone()).collect::<Vec<_>>(),
        vec!["c", "a"]
    );
    assert_eq!(
        rest.iter().map(|r| r.label.clone()).collect::<Vec<_>>(),
        vec!["d", "b"]
    );
}

#[test]
fn nothing_open_leaves_every_row_in_the_tail() {
    let listed = rows(&[("a", "/a")]);
    let (head, rest) = order_rows(&listed, &[], &mut |_| 0);
    assert!(head.is_empty());
    assert_eq!(rest.len(), 1);
}

#[test]
fn equal_touch_times_keep_the_input_order() {
    let listed = rows(&[("b", "/b"), ("a", "/a")]);
    let (_, rest) = order_rows(&listed, &[], &mut |_| 7);
    assert_eq!(
        rest.iter().map(|r| r.label.clone()).collect::<Vec<_>>(),
        vec!["b", "a"]
    );
}

#[test]
fn open_rows_carry_their_touch_time() {
    let listed = rows(&[("a", "/a")]);
    let (head, _) = order_rows(&listed, &[open("a", "w1")], &mut |_| 99);
    assert_eq!(head[0].touched, 99);
}

#[test]
fn each_repo_is_walked_exactly_once() {
    let listed = rows(&[("a", "/a"), ("b", "/b"), ("c", "/c")]);
    let mut walked: Vec<String> = Vec::new();
    order_rows(&listed, &[open("a", "w1")], &mut |p| {
        walked.push(p.to_string_lossy().to_string());
        0
    });
    walked.sort();
    assert_eq!(walked, vec!["/a", "/b", "/c"]);
}

#[test]
fn an_open_repo_is_not_walked_twice() {
    let listed = rows(&[("a", "/a")]);
    let mut walked = 0;
    order_rows(&listed, &[open("a", "w1")], &mut |_| {
        walked += 1;
        0
    });
    assert_eq!(walked, 1);
}

#[test]
fn the_debug_line_reports_the_elapsed_time_in_milliseconds() {
    assert!(debug_line(26.44, 3, Counts { bands: 2, containers: 1 }).contains("26.4ms"));
}

#[test]
fn the_debug_line_reports_the_row_total_and_both_passes() {
    let text = debug_line(26.4, 3, Counts { bands: 2, containers: 1 });
    assert!(text.contains("3 rows"), "{}", text);
    assert!(text.contains("2 from depth bands"), "{}", text);
    assert!(text.contains("1 from worktree containers"), "{}", text);
}

#[test]
fn the_debug_row_total_is_the_rows_not_the_sum_of_the_passes() {
    let text = debug_line(26.4, 3, Counts { bands: 1, containers: 1 });
    assert!(text.contains("3 rows"), "{}", text);
}

#[test]
fn the_debug_line_is_one_line_naming_the_picker() {
    let text = debug_line(26.4, 3, Counts { bands: 2, containers: 1 });
    assert_eq!(text.matches('\n').count(), 1);
    assert!(text.ends_with('\n'));
    assert!(text.starts_with("picker: "));
}

#[test]
fn normalising_a_path_collapses_dots_without_touching_the_disk() {
    assert_eq!(normpath("./.worktrees"), ".worktrees");
    assert_eq!(normpath("../trees"), "../trees");
    assert_eq!(normpath("/srv/worktrees/"), "/srv/worktrees");
    assert_eq!(normpath("a//b/../c"), "a/c");
    assert_eq!(normpath(".."), "..");
}
