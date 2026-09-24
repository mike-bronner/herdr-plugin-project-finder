mod support;

use std::cell::Cell;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::rc::Rc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

use herdr_plugin_kit::api::generated::{
    InstalledPluginInfo, NotificationShowParams, NotificationShowReason, PluginPaneOpenParams,
};
use herdr_plugin_kit::dialog::{
    self, Answer, Button, Explained, Key, OpenError, Transport, Unanswered,
};
use herdr_plugin_kit::update::{self as kit, Available, Files, Installer, Releases};
use pick_project::api::PLUGIN_ID;
use pick_project::update::{
    self, answer_check, ask_when_free, buttons, claim_path, held, on_launch, question, settle,
    spawn_detached, Claim, Clock, CHANNEL, INTERVAL, LEASE, SLOT_RETRY, SLOT_WAIT, STATE_DIR,
    UPDATE,
};
use pick_project::version::{requested, Request, CHECK_FLAG, DIALOG_FLAG};

use support::*;

const OWNER: &str = "mike-bronner";
const REPO: &str = "herdr-plugin-project-finder";

fn at(seconds: u64) -> SystemTime {
    UNIX_EPOCH + Duration::from_secs(seconds)
}

const NOW: u64 = 2_000_000_000;

fn record(kind: &str, root: &Path) -> Value {
    json!({"plugin_id": PLUGIN_ID, "name": "Project Finder",
           "plugin_root": root.to_string_lossy(),
           "manifest_path": root.join("herdr-plugin.toml").to_string_lossy(),
           "version": "0.9.4", "enabled": true,
           "source": {"kind": kind, "owner": OWNER, "repo": REPO}})
}

fn installed(kind: &str, root: &Path) -> InstalledPluginInfo {
    serde_json::from_value(record(kind, root)).unwrap()
}

fn newer() -> Available {
    Available {
        installed: "0.9.4".into(),
        tag: "0.9.5".into(),
        repository: format!("{}/{}", OWNER, REPO),
    }
}

struct Latest {
    answer: Result<Option<String>, String>,
    asked: Cell<usize>,
}

impl Latest {
    fn tag(tag: &str) -> Latest {
        Latest {
            answer: Ok(Some(tag.to_string())),
            asked: Cell::new(0),
        }
    }
}

impl Releases for Latest {
    fn latest(&self, _: &str, _: &str) -> Result<Option<String>, String> {
        self.asked.set(self.asked.get() + 1);
        self.answer.clone()
    }
}

#[derive(Default)]
struct Recorder {
    installs: Vec<(String, String, String)>,
    refuse: Option<String>,
}

impl Installer for &mut Recorder {
    fn install(&mut self, owner: &str, repo: &str, tag: &str) -> Result<(), String> {
        Recorder::install(self, owner, repo, tag)
    }
}

impl Installer for Recorder {
    fn install(&mut self, owner: &str, repo: &str, tag: &str) -> Result<(), String> {
        self.installs.push((owner.into(), repo.into(), tag.into()));
        match &self.refuse {
            Some(why) => Err(why.clone()),
            None => Ok(()),
        }
    }
}

struct Popup {
    busy_for: usize,
    word: Option<String>,
    refusal: Option<String>,
    attempts: usize,
    opened: Vec<PluginPaneOpenParams>,
    notified: usize,
    after_answer: Option<Box<dyn FnMut()>>,
}

impl Popup {
    fn answering(word: &str) -> Popup {
        Popup::busy_then(0, word)
    }

    fn refusing(why: &str) -> Popup {
        Popup {
            refusal: Some(why.to_string()),
            ..Popup::busy_then(0, "0")
        }
    }

    fn busy_then(busy_for: usize, word: &str) -> Popup {
        Popup {
            busy_for,
            word: Some(word.to_string()),
            refusal: None,
            attempts: 0,
            opened: Vec::new(),
            notified: 0,
            after_answer: None,
        }
    }
}

impl Transport for Popup {
    fn open_pane(&mut self, params: PluginPaneOpenParams) -> Result<(), OpenError> {
        self.attempts += 1;
        if self.attempts <= self.busy_for {
            return Err(OpenError::Busy);
        }
        if let Some(why) = &self.refusal {
            return Err(OpenError::Failed(why.clone()));
        }
        if let Some(word) = &self.word {
            std::fs::write(
                &params.env[dialog::STARTED_FILE_VAR],
                std::process::id().to_string(),
            )
            .unwrap();
            std::fs::write(&params.env[dialog::ANSWER_FILE_VAR], word).unwrap();
        }
        self.opened.push(params);
        if let Some(hook) = &mut self.after_answer {
            hook();
        }
        Ok(())
    }

    fn show_notification(
        &mut self,
        _: NotificationShowParams,
    ) -> Result<NotificationShowReason, String> {
        self.notified += 1;
        Ok(NotificationShowReason::Shown)
    }
}

struct Install {
    root: TempDir,
    stub: Stub,
}

impl Install {
    fn new(kind: &str) -> Install {
        let root = TempDir::new();
        let stub = Stub::start(Script::default().plugins(vec![record(kind, root.path())]));
        Install { root, stub }
    }

    fn files(&self) -> Files {
        Files::under(self.root.path(), STATE_DIR).unwrap()
    }

    fn check(
        &self,
        popup: &mut Popup,
        installer: Option<&mut Recorder>,
        ticker: &Ticker,
        releases: &Latest,
    ) -> Option<String> {
        answer_check(
            &self.stub.client(),
            popup,
            installer,
            releases,
            &Clock {
                now: &|| ticker.now(),
                sleep: &|pause| ticker.sleep(pause),
            },
        )
    }

    fn launch(&self, at_seconds: u64) -> (bool, usize, usize) {
        let checks = Cell::new(0);
        let offers = Cell::new(0);
        let started = on_launch(
            &self.stub.client(),
            at(at_seconds),
            &mut |files, now, interval| {
                let due = kit::due(&files.stamp(), now, interval);
                checks.set(checks.get() + usize::from(due));
                due
            },
            &mut || {
                offers.set(offers.get() + 1);
                true
            },
        );
        (started, checks.get(), offers.get())
    }
}

struct Ticker {
    time: Cell<u64>,
    sleeps: Cell<usize>,
    on_sleep: Option<Box<dyn Fn()>>,
}

impl Ticker {
    fn at(seconds: u64) -> Ticker {
        Ticker {
            time: Cell::new(seconds),
            sleeps: Cell::new(0),
            on_sleep: None,
        }
    }

    fn now(&self) -> SystemTime {
        at(self.time.get())
    }

    fn sleep(&self, pause: Duration) {
        self.time.set(self.time.get() + pause.as_secs());
        self.sleeps.set(self.sleeps.get() + 1);
        if let Some(hook) = &self.on_sleep {
            hook();
        }
    }
}

fn saved(files: &Files) {
    std::fs::create_dir_all(files.dir()).unwrap();
    std::fs::write(files.result(), serde_json::to_string(&newer()).unwrap()).unwrap();
}

fn ask_now(popup: &mut Popup, ticker: &Ticker) -> Answer {
    ask_when_free(
        popup,
        &question(&newer()),
        &buttons(&newer()),
        &Clock {
            now: &|| ticker.now(),
            sleep: &|pause| ticker.sleep(pause),
        },
        &mut || true,
    )
}

#[test]
fn the_two_update_modes_are_answered_and_refuse_anything_trailing_them() {
    let args = |of: &[&str]| -> Vec<std::ffi::OsString> { of.iter().map(|a| a.into()).collect() };
    assert_eq!(requested(&args(&[DIALOG_FLAG])), Request::Dialog);
    assert_eq!(requested(&args(&[CHECK_FLAG])), Request::CheckUpdate);
    for flag in [DIALOG_FLAG, CHECK_FLAG] {
        assert_eq!(
            requested(&args(&[flag, "extra"])),
            Request::Refuse("extra".to_string())
        );
    }
    assert_eq!(CHECK_FLAG, "--check-update");
}

#[test]
fn a_linked_working_tree_is_never_checked_offered_or_written_to() {
    let install = Install::new("local");
    assert_eq!(install.launch(NOW), (false, 0, 0));
    let releases = Latest::tag("0.9.5");
    let mut popup = Popup::answering("0");
    let mut installer = Recorder::default();
    let note = install.check(
        &mut popup,
        Some(&mut installer),
        &Ticker::at(NOW),
        &releases,
    );
    assert_eq!(note, None);
    assert_eq!(releases.asked.get(), 0);
    assert_eq!(popup.attempts, 0);
    assert!(installer.installs.is_empty());
    assert_eq!(std::fs::read_dir(install.root.path()).unwrap().count(), 0);
}

#[test]
fn a_linked_working_tree_with_a_saved_result_is_still_offered_nothing_at_launch() {
    let install = Install::new("local");
    saved(&install.files());
    assert_eq!(install.launch(NOW), (false, 0, 0));
}

#[test]
fn a_github_install_spawns_the_check_at_launch_under_its_own_root() {
    let install = Install::new("github");
    let mut seen = Vec::new();
    assert!(on_launch(
        &install.stub.client(),
        at(NOW),
        &mut |files, now, interval| {
            seen.push((files.clone(), now, interval));
            true
        },
        &mut || panic!("a launch that started a check starts no second process")
    ));
    assert_eq!(seen, vec![(install.files(), at(NOW), INTERVAL)]);
    assert_eq!(
        install.files().dir(),
        install.root.path().join(".project-finder-update")
    );
    assert_eq!(install.launch(NOW), (true, 1, 0));
}

#[test]
fn a_launch_that_cannot_find_its_install_spawns_nothing() {
    let failing = Stub::start(Script::default().failing("plugin.list", "internal"));
    let other = Stub::start(Script::default().plugins(vec![plugin_row("/private/tmp/x")]));
    for stub in [failing, other] {
        let spawned = Cell::new(0);
        assert!(!on_launch(
            &stub.client(),
            at(NOW),
            &mut |_, _, _| {
                spawned.set(spawned.get() + 1);
                true
            },
            &mut || {
                spawned.set(spawned.get() + 1);
                true
            }
        ));
        assert_eq!(spawned.get(), 0);
    }
}

#[test]
fn a_newer_release_is_offered_the_moment_the_check_finds_it_and_yes_applies_it() {
    let install = Install::new("github");
    let mut popup = Popup::answering("0");
    let releases = Latest::tag("0.9.5");
    let mut installer = Recorder::default();
    let note = install.check(
        &mut popup,
        Some(&mut installer),
        &Ticker::at(NOW),
        &releases,
    );
    assert_eq!(releases.asked.get(), 1);
    assert_eq!(popup.opened.len(), 1);
    assert_eq!(popup.opened[0].entrypoint, dialog::ENTRYPOINT);
    assert_eq!(popup.opened[0].plugin_id, PLUGIN_ID);
    assert_eq!(
        installer.installs,
        vec![(OWNER.to_string(), REPO.to_string(), "0.9.5".to_string())]
    );
    assert!(note.unwrap().contains("updated Project Finder to 0.9.5"));
    assert!(install.files().offered(CHANNEL).unwrap().exists());
}

#[test]
fn a_release_that_is_not_newer_opens_no_dialog() {
    let install = Install::new("github");
    let mut popup = Popup::answering("0");
    let note = install.check(
        &mut popup,
        Some(&mut Recorder::default()),
        &Ticker::at(NOW),
        &Latest::tag("0.9.4"),
    );
    assert_eq!(note, None);
    assert_eq!(popup.attempts, 0);
    assert!(!install.files().result().exists());
}

#[test]
fn without_a_herdr_binary_the_check_saves_its_answer_and_asks_nothing() {
    let install = Install::new("github");
    let mut popup = Popup::answering("0");
    let releases = Latest::tag("0.9.5");
    let note = install.check(&mut popup, None, &Ticker::at(NOW), &releases);
    assert_eq!(note, None);
    assert_eq!(releases.asked.get(), 1);
    assert_eq!(popup.attempts, 0);
    assert!(install.files().result().exists());
    assert!(!install.files().offered(CHANNEL).unwrap().exists());
}

#[test]
fn the_offer_waits_out_the_pickers_popup_and_then_opens() {
    let install = Install::new("github");
    let mut popup = Popup::busy_then(3, "1");
    let ticker = Ticker::at(NOW);
    let note = install.check(
        &mut popup,
        Some(&mut Recorder::default()),
        &ticker,
        &Latest::tag("0.9.5"),
    );
    assert_eq!(note, None);
    assert_eq!(popup.attempts, 4);
    assert_eq!(popup.opened.len(), 1);
    assert_eq!(ticker.sleeps.get(), 3);
    assert_eq!(
        popup.notified, 0,
        "each busy attempt would otherwise raise a toast"
    );
    assert_eq!(
        std::fs::read_to_string(install.files().offered(CHANNEL).unwrap())
            .unwrap()
            .trim(),
        (NOW + 3 * SLOT_RETRY.as_secs()).to_string(),
        "the interval runs from when the question was shown, not from when the check began"
    );
}

#[test]
fn the_wait_for_the_popup_slot_is_bounded_and_the_next_launch_offers_again() {
    assert_eq!(SLOT_WAIT, Duration::from_secs(600));
    let install = Install::new("github");
    let mut popup = Popup::busy_then(usize::MAX, "0");
    let releases = Latest::tag("0.9.5");
    let ticker = Ticker::at(NOW);
    let note = install.check(
        &mut popup,
        Some(&mut Recorder::default()),
        &ticker,
        &releases,
    );
    let rounds = (SLOT_WAIT.as_secs() / SLOT_RETRY.as_secs()) as usize;
    assert_eq!(note, None);
    assert_eq!(ticker.sleeps.get(), rounds);
    assert_eq!(popup.attempts, rounds + 1);
    assert_eq!(popup.notified, 0);
    assert!(install.files().result().exists());
    assert!(!install.files().offered(CHANNEL).unwrap().exists());

    let later = NOW + 3600;
    assert_eq!(install.launch(later), (true, 0, 1));
    let mut next = Popup::answering("1");
    install.check(
        &mut next,
        Some(&mut Recorder::default()),
        &Ticker::at(later),
        &releases,
    );
    assert_eq!(
        releases.asked.get(),
        1,
        "the check is not due, so it asks nothing"
    );
    assert_eq!(next.opened.len(), 1);
}

#[test]
fn an_open_that_failed_is_offered_at_the_next_launch() {
    let install = Install::new("github");
    let releases = Latest::tag("0.9.5");
    let mut refused = Popup::refusing("no_foreground_client: no client is attached");
    let note = install.check(
        &mut refused,
        Some(&mut Recorder::default()),
        &Ticker::at(NOW),
        &releases,
    );
    assert_eq!(note, None);
    assert_eq!(refused.attempts, 1, "only a busy slot is retried");
    assert!(!install.files().offered(CHANNEL).unwrap().exists());
    assert!(!kit::due(&install.files().stamp(), at(NOW + 60), INTERVAL));

    assert_eq!(install.launch(NOW + 60), (true, 0, 1));
    let mut popup = Popup::answering("0");
    let mut installer = Recorder::default();
    let note = install.check(
        &mut popup,
        Some(&mut installer),
        &Ticker::at(NOW + 60),
        &releases,
    );
    assert_eq!(popup.opened.len(), 1);
    assert_eq!(installer.installs.len(), 1);
    assert!(note.unwrap().contains("0.9.5"));
}

#[test]
fn a_launch_after_the_offer_was_shown_starts_nothing() {
    let install = Install::new("github");
    install.check(
        &mut Popup::answering("1"),
        Some(&mut Recorder::default()),
        &Ticker::at(NOW),
        &Latest::tag("0.9.5"),
    );
    assert_eq!(install.launch(NOW + 60), (false, 0, 0));
}

#[test]
fn a_launch_with_nothing_saved_starts_nothing() {
    let install = Install::new("github");
    install.check(
        &mut Popup::answering("0"),
        Some(&mut Recorder::default()),
        &Ticker::at(NOW),
        &Latest::tag("0.9.4"),
    );
    assert_eq!(install.launch(NOW + 60), (false, 0, 0));
}

#[test]
fn a_second_process_stops_once_the_first_records_the_offer() {
    let install = Install::new("github");
    saved(&install.files());
    let offered = install.files().offered(CHANNEL).unwrap();
    let recorded_at = NOW + 30;
    let mut ticker = Ticker::at(NOW);
    ticker.on_sleep = Some(Box::new(move || {
        kit::record_offer(&offered, at(recorded_at))
    }));
    let mut popup = Popup::busy_then(usize::MAX, "0");
    let mut installer = Recorder::default();
    let note = update::offer(
        &mut popup,
        &installed("github", install.root.path()),
        &install.files(),
        &mut installer,
        &Clock {
            now: &|| ticker.now(),
            sleep: &|pause| ticker.sleep(pause),
        },
    );
    assert_eq!(note, None);
    assert_eq!(popup.attempts, 1);
    assert!(installer.installs.is_empty());
    assert_eq!(
        std::fs::read_to_string(install.files().offered(CHANNEL).unwrap())
            .unwrap()
            .trim(),
        recorded_at.to_string(),
        "the second process must not overwrite the first one's record"
    );
}

#[test]
fn a_second_process_stops_once_the_first_applied_the_update() {
    let install = Install::new("github");
    saved(&install.files());
    let result = install.files().result();
    let mut ticker = Ticker::at(NOW);
    ticker.on_sleep = Some(Box::new(move || {
        let _ = std::fs::remove_file(&result);
    }));
    let mut popup = Popup::busy_then(usize::MAX, "0");
    let mut installer = Recorder::default();
    update::offer(
        &mut popup,
        &installed("github", install.root.path()),
        &install.files(),
        &mut installer,
        &Clock {
            now: &|| ticker.now(),
            sleep: &|pause| ticker.sleep(pause),
        },
    );
    assert_eq!(popup.attempts, 1);
    assert!(installer.installs.is_empty());
}

#[test]
fn a_declined_offer_is_asked_again_only_after_the_interval() {
    let install = Install::new("github");
    let releases = Latest::tag("0.9.5");
    let mut first = Popup::answering("1");
    install.check(
        &mut first,
        Some(&mut Recorder::default()),
        &Ticker::at(NOW),
        &releases,
    );
    assert_eq!(first.opened.len(), 1);

    let mut soon = Popup::answering("1");
    install.check(
        &mut soon,
        Some(&mut Recorder::default()),
        &Ticker::at(NOW + INTERVAL.as_secs() - 60),
        &releases,
    );
    assert_eq!(soon.attempts, 0);
    assert_eq!(releases.asked.get(), 1);

    let mut later = Popup::answering("1");
    install.check(
        &mut later,
        Some(&mut Recorder::default()),
        &Ticker::at(NOW + INTERVAL.as_secs()),
        &releases,
    );
    assert_eq!(later.opened.len(), 1);
}

#[test]
fn enter_chooses_update_and_escape_chooses_not_now() {
    let offered = buttons(&newer());
    assert_eq!(Button::keys(&offered), vec![Key::Enter, Key::Escape]);
    assert_eq!(UPDATE, 0);
    assert_eq!(offered[UPDATE].label, "update to 0.9.5");
    assert_eq!(offered[1].label, "not now");
}

#[test]
fn the_question_names_the_release_and_the_installed_version() {
    let asked = question(&newer());
    assert!(asked.body.contains("0.9.5 is available"), "{}", asked.body);
    assert!(asked.body.contains("You have 0.9.4"), "{}", asked.body);
}

#[test]
fn a_shown_question_is_recorded_and_an_unshown_one_is_not() {
    for (answered, recorded) in [
        (Answer::Chose(1), true),
        (Answer::Unanswered(Unanswered::Dismissed), true),
        (Answer::Unanswered(Unanswered::TimedOut), false),
        (
            Answer::Unanswered(Unanswered::Unrecognised("x".into())),
            true,
        ),
        (Answer::Unanswered(Unanswered::NeverShown), false),
        (Answer::Unanswered(Unanswered::Failed("x".into())), false),
        (
            Answer::Unanswered(Unanswered::Busy(Explained::Unreachable("x".into()))),
            false,
        ),
    ] {
        let root = TempDir::new();
        let files = Files::under(root.path(), STATE_DIR).unwrap();
        let mut installer = Recorder::default();
        let note = settle(
            answered.clone(),
            &installed("github", root.path()),
            &newer(),
            &files,
            at(NOW),
            &mut installer,
        );
        assert_eq!(note, None, "{:?}", answered);
        assert!(installer.installs.is_empty(), "{:?} applied", answered);
        assert_eq!(
            files.offered(CHANNEL).unwrap().exists(),
            recorded,
            "{:?}",
            answered
        );
    }
}

#[test]
fn a_refused_install_is_reported_rather_than_claimed() {
    let root = TempDir::new();
    let mut installer = Recorder {
        refuse: Some("no network".into()),
        ..Recorder::default()
    };
    let note = settle(
        Answer::Chose(UPDATE),
        &installed("github", root.path()),
        &newer(),
        &Files::under(root.path(), STATE_DIR).unwrap(),
        at(NOW),
        &mut installer,
    )
    .unwrap();
    assert!(
        note.contains("could not update Project Finder to 0.9.5"),
        "{}",
        note
    );
    assert!(note.contains("no network"), "{}", note);
}

#[test]
fn update_on_a_local_record_reinstalls_nothing() {
    let root = TempDir::new();
    let mut installer = Recorder::default();
    settle(
        Answer::Chose(UPDATE),
        &installed("local", root.path()),
        &newer(),
        &Files::under(root.path(), STATE_DIR).unwrap(),
        at(NOW),
        &mut installer,
    );
    assert!(installer.installs.is_empty());
}

#[test]
fn a_free_slot_is_asked_once_and_never_waits() {
    let mut popup = Popup::answering("0");
    let ticker = Ticker::at(NOW);
    assert_eq!(ask_now(&mut popup, &ticker), Answer::Chose(0));
    assert_eq!(ticker.sleeps.get(), 0);
    assert_eq!(popup.attempts, 1);
}

#[test]
fn a_failure_other_than_busy_is_not_retried() {
    let mut popup = Popup::refusing("no_foreground_client: nobody");
    let ticker = Ticker::at(NOW);
    assert!(matches!(
        ask_now(&mut popup, &ticker),
        Answer::Unanswered(Unanswered::Failed(_))
    ));
    assert_eq!(popup.attempts, 1);
    assert_eq!(ticker.sleeps.get(), 0);
}

#[test]
fn the_state_directory_keeps_its_name() {
    assert_eq!(update::STATE_DIR, ".project-finder-update");
    assert_eq!(CHANNEL, "dialog");
}

fn wait_for(done: impl Fn() -> bool) -> bool {
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if done() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    done()
}

#[test]
fn the_offer_process_starts_detached_and_the_launch_never_waits_for_it() {
    let dir = TempDir::new();
    let script = dir.write(
        "offer",
        "#!/bin/sh\nprintf '%s' \"$1\" > \"$(dirname \"$0\")/argument\"\nsleep 5\n",
    );
    Command::new("chmod")
        .arg("+x")
        .arg(&script)
        .status()
        .unwrap();
    let started = Instant::now();
    assert!(spawn_detached(&script));
    assert!(
        started.elapsed() < Duration::from_secs(2),
        "the picker waited {:?} on the offer process",
        started.elapsed()
    );
    let argument = dir.join("argument");
    assert!(wait_for(|| argument.exists()));
    assert_eq!(std::fs::read_to_string(&argument).unwrap(), CHECK_FLAG);
    assert!(!spawn_detached(&dir.join("absent")));
}

struct Ran {
    status: i32,
    stderr: String,
}

fn binary(arguments: &[&str], variables: &[(String, String)], typed: &str) -> Ran {
    let empty = TempDir::new();
    let mut command = Command::new(env!("CARGO_BIN_EXE_pick-project"));
    command
        .args(arguments)
        .env_clear()
        .env("PATH", empty.path())
        .env("HOME", "/private/tmp")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    for (name, value) in variables {
        command.env(name, value);
    }
    let mut child = command.spawn().expect("cannot run the binary");
    {
        use std::io::Write;
        let _ = child.stdin.take().unwrap().write_all(typed.as_bytes());
    }
    let out = child.wait_with_output().unwrap();
    Ran {
        status: out.status.code().unwrap_or(-1),
        stderr: String::from_utf8_lossy(&out.stderr).to_string(),
    }
}

fn pair(name: &str, value: &Path) -> (String, String) {
    (name.to_string(), value.to_string_lossy().to_string())
}

fn socket_of(install: &Install) -> Vec<(String, String)> {
    vec![pair("HERDR_SOCKET_PATH", install.stub.socket())]
}

fn plugin_lists(stub: &Stub) -> usize {
    stub.methods()
        .iter()
        .filter(|method| *method == "plugin.list")
        .count()
}

#[test]
fn the_dialog_mode_writes_the_chosen_button_and_its_own_pid() {
    let dir = TempDir::new();
    let answer = dir.join("answer");
    let started = dir.join("started");
    let ran = binary(
        &[DIALOG_FLAG],
        &[
            (
                dialog::button_var(0),
                "\u{21b5} update to 0.9.5".to_string(),
            ),
            (dialog::button_var(1), "esc not now".to_string()),
            pair(dialog::ANSWER_FILE_VAR, &answer),
            pair(dialog::STARTED_FILE_VAR, &started),
        ],
        "\n",
    );
    assert_eq!(ran.status, 0, "{}", ran.stderr);
    assert_eq!(std::fs::read_to_string(&answer).unwrap(), "0");
    assert!(std::fs::read_to_string(&started)
        .unwrap()
        .parse::<u32>()
        .is_ok());
}

#[test]
fn the_dialog_mode_exits_1_naming_the_plugin_when_it_cannot_answer() {
    let ran = binary(
        &[DIALOG_FLAG],
        &[
            (
                dialog::button_var(0),
                "\u{21b5} update to 0.9.5".to_string(),
            ),
            pair(
                dialog::ANSWER_FILE_VAR,
                &PathBuf::from("/private/tmp/pick-project-absent-dir/answer"),
            ),
        ],
        "\n",
    );
    assert_eq!(ran.status, 1);
    assert!(
        ran.stderr
            .starts_with("project-finder: cannot write the answer"),
        "{}",
        ran.stderr
    );
}

#[test]
fn the_check_mode_on_a_github_install_stamps_the_attempt_and_opens_nothing_without_an_answer() {
    let install = Install::new("github");
    saved(&install.files());
    let ran = binary(&[CHECK_FLAG], &socket_of(&install), "");
    assert_eq!(ran.status, 0, "{}", ran.stderr);
    assert!(install.files().stamp().exists());
    assert!(
        !install.files().result().exists(),
        "no curl on PATH is no answer, and no answer clears the older result"
    );
    assert_eq!(install.stub.params_for("plugin.pane.open").len(), 0);
}

#[test]
fn the_check_mode_on_a_linked_tree_asks_once_and_writes_nothing() {
    let install = Install::new("local");
    let ran = binary(&[CHECK_FLAG], &socket_of(&install), "");
    assert_eq!(ran.status, 0, "{}", ran.stderr);
    assert_eq!(plugin_lists(&install.stub), 1);
    assert_eq!(std::fs::read_dir(install.root.path()).unwrap().count(), 0);
}

fn offer_at(popup: &mut Popup, install: &Install, installer: &mut Recorder) -> Option<String> {
    let ticker = Ticker::at(NOW);
    update::offer(
        popup,
        &installed("github", install.root.path()),
        &install.files(),
        installer,
        &Clock {
            now: &|| ticker.now(),
            sleep: &|pause| ticker.sleep(pause),
        },
    )
}

fn claimed_by(install: &Install, pid: &str) -> PathBuf {
    let path = claim_path(&install.files());
    std::fs::create_dir_all(install.files().dir()).unwrap();
    std::fs::write(&path, pid).unwrap();
    path
}

fn dead_pid() -> u32 {
    let mut child = Command::new("/usr/bin/true").spawn().unwrap();
    let pid = child.id();
    child.wait().unwrap();
    pid
}

fn pending_since(install: &Install) {
    saved(&install.files());
    kit::record_offer(&install.files().stamp(), at(NOW));
}

#[test]
fn a_second_process_never_asks_while_the_first_holds_the_question_even_once_its_slot_frees() {
    let install = Install::new("github");
    saved(&install.files());
    let plugin = installed("github", install.root.path());
    let files = install.files();
    let second = Rc::new(Cell::new((usize::MAX, usize::MAX)));
    let (their_plugin, their_files, seen) = (plugin.clone(), files.clone(), Rc::clone(&second));
    let mut first = Popup::answering("0");
    first.after_answer = Some(Box::new(move || {
        let mut popup = Popup::answering("0");
        let mut installer = Recorder::default();
        let ticker = Ticker::at(NOW);
        update::offer(
            &mut popup,
            &their_plugin,
            &their_files,
            &mut installer,
            &Clock {
                now: &|| ticker.now(),
                sleep: &|pause| ticker.sleep(pause),
            },
        );
        seen.set((popup.attempts, installer.installs.len()));
    }));
    let mut installer = Recorder::default();
    let ticker = Ticker::at(NOW);
    let note = update::offer(
        &mut first,
        &plugin,
        &files,
        &mut installer,
        &Clock {
            now: &|| ticker.now(),
            sleep: &|pause| ticker.sleep(pause),
        },
    );
    assert_eq!(
        second.get(),
        (0, 0),
        "the pane had answered and freed the slot, and the first process had not yet recorded"
    );
    assert_eq!(first.opened.len(), 1);
    assert_eq!(installer.installs.len(), 1);
    assert!(note.unwrap().contains("0.9.5"));
    assert!(
        !claim_path(&files).exists(),
        "the winner releases its claim"
    );
}

#[test]
fn a_live_claim_stops_the_offer_and_the_launch_fallback() {
    let install = Install::new("github");
    pending_since(&install);
    let claim = claimed_by(&install, &std::process::id().to_string());
    assert!(held(&claim));
    assert_eq!(install.launch(NOW + 60), (false, 0, 0));
    let mut popup = Popup::answering("0");
    let mut installer = Recorder::default();
    assert_eq!(offer_at(&mut popup, &install, &mut installer), None);
    assert_eq!(popup.attempts, 0);
    assert!(installer.installs.is_empty());
    assert!(
        claim.exists(),
        "a process that did not take the claim leaves it alone"
    );
}

#[test]
fn a_claim_whose_owner_died_is_taken_over() {
    let install = Install::new("github");
    pending_since(&install);
    let claim = claimed_by(&install, &dead_pid().to_string());
    assert!(!held(&claim));
    assert_eq!(install.launch(NOW + 60), (true, 0, 1));
    let mut popup = Popup::answering("1");
    offer_at(&mut popup, &install, &mut Recorder::default());
    assert_eq!(popup.opened.len(), 1);
    assert!(!claim.exists());
}

#[test]
fn a_claim_older_than_the_lease_is_taken_over_even_when_its_pid_lives() {
    let install = Install::new("github");
    pending_since(&install);
    let claim = claimed_by(&install, &std::process::id().to_string());
    let long_ago = SystemTime::now() - LEASE - Duration::from_secs(60);
    set_mtime(
        &claim,
        long_ago.duration_since(UNIX_EPOCH).unwrap().as_secs(),
    );
    assert!(
        !held(&claim),
        "a reused pid must not hold the offer forever"
    );
    let mut popup = Popup::answering("1");
    offer_at(&mut popup, &install, &mut Recorder::default());
    assert_eq!(popup.opened.len(), 1);
    assert!(!claim.exists());
}

#[test]
fn an_unreadable_claim_is_taken_over() {
    let install = Install::new("github");
    pending_since(&install);
    let claim = claimed_by(&install, "");
    assert!(!held(&claim));
    let taken = Claim::take(&install.files());
    assert!(taken.is_some());
    assert_eq!(
        std::fs::read_to_string(&claim).unwrap(),
        std::process::id().to_string()
    );
    drop(taken);
    assert!(!claim.exists());
}

#[test]
fn a_released_claim_never_removes_one_somebody_else_took() {
    let install = Install::new("github");
    let taken = Claim::take(&install.files()).unwrap();
    assert!(Claim::take(&install.files()).is_none());
    let claim = claim_path(&install.files());
    std::fs::write(&claim, "1").unwrap();
    drop(taken);
    assert!(claim.exists());
}

#[test]
fn a_timed_out_question_is_not_silenced_and_the_next_launch_asks_again() {
    let install = Install::new("github");
    pending_since(&install);
    settle(
        Answer::Unanswered(Unanswered::TimedOut),
        &installed("github", install.root.path()),
        &newer(),
        &install.files(),
        at(NOW),
        &mut Recorder::default(),
    );
    assert!(!install.files().offered(CHANNEL).unwrap().exists());
    assert_eq!(install.launch(NOW + 60), (true, 0, 1));
}

#[test]
fn a_process_that_lost_its_claim_stops_before_its_next_ask() {
    let install = Install::new("github");
    pending_since(&install);
    let claim = claim_path(&install.files());
    let mut ticker = Ticker::at(NOW);
    let stolen = claim.clone();
    ticker.on_sleep = Some(Box::new(move || std::fs::write(&stolen, "1").unwrap()));
    let mut popup = Popup::busy_then(usize::MAX, "0");
    let mut installer = Recorder::default();
    let note = update::offer(
        &mut popup,
        &installed("github", install.root.path()),
        &install.files(),
        &mut installer,
        &Clock {
            now: &|| ticker.now(),
            sleep: &|pause| ticker.sleep(pause),
        },
    );
    assert_eq!(note, None);
    assert_eq!(popup.attempts, 1, "a lost claim must not ask again");
    assert!(installer.installs.is_empty());
    assert!(!install.files().offered(CHANNEL).unwrap().exists());
    assert_eq!(
        std::fs::read_to_string(&claim).unwrap(),
        "1",
        "the new owner's claim is left alone"
    );
}
