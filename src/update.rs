use std::fs;
use std::path::{Path, PathBuf};
use std::process::{self, Command, Stdio};
use std::time::{Duration, SystemTime};

use herdr_plugin_kit::api::generated::{
    InstalledPluginInfo, NotificationShowParams, NotificationShowReason, PluginPaneOpenParams,
};
use herdr_plugin_kit::dialog::{self, Answer, Button, Dialog, OpenError, State, Unanswered};
use herdr_plugin_kit::update::{self as kit, Available, Files, Installer, Releases, CHECK_FLAG};

use crate::api::{Client, PLUGIN_ID};

pub use herdr_plugin_kit::dialog::Transport;

pub const STATE_DIR: &str = ".project-finder-update";

pub const CHANNEL: &str = "dialog";

pub const INTERVAL: Duration = kit::DEFAULT_INTERVAL;

pub const SLOT_WAIT: Duration = Duration::from_secs(10 * 60);

pub const SLOT_RETRY: Duration = Duration::from_secs(2);

pub const UPDATE: usize = 0;

pub const CLAIM: &str = "offering";

pub const LEASE: Duration = Duration::from_secs(30 * 60);

pub type SpawnCheck<'a> = &'a mut dyn FnMut(&Files, SystemTime, Duration) -> bool;

pub type SpawnOffer<'a> = &'a mut dyn FnMut() -> bool;

pub struct Clock<'a> {
    pub now: &'a dyn Fn() -> SystemTime,
    pub sleep: &'a dyn Fn(Duration),
}

pub fn on_launch(
    client: &Client,
    now: SystemTime,
    spawn_check: SpawnCheck,
    spawn_offer: SpawnOffer,
) -> bool {
    let Some((plugin, files)) = kit::lookup(client, PLUGIN_ID, STATE_DIR) else {
        return false;
    };
    if spawn_check(&files, now, INTERVAL) {
        return true;
    }
    pending(&plugin, &files, now).is_some() && !held(&claim_path(&files)) && spawn_offer()
}

pub fn spawn_detached(program: &Path) -> bool {
    let mut command = Command::new(program);
    command
        .arg(CHECK_FLAG)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    let Ok(mut child) = command.spawn() else {
        return false;
    };
    std::thread::spawn(move || {
        let _ = child.wait();
    });
    true
}

pub fn answer_check<I: Installer>(
    client: &Client,
    transport: &mut impl Transport,
    installer: Option<I>,
    releases: &impl Releases,
    clock: &Clock,
) -> Option<String> {
    let (plugin, files) = kit::lookup(client, PLUGIN_ID, STATE_DIR)?;
    kit::check_and_save(
        &plugin,
        &files.stamp(),
        &files.result(),
        (clock.now)(),
        INTERVAL,
        releases,
    );
    let mut installer = installer?;
    offer(transport, &plugin, &files, &mut installer, clock)
}

pub fn offer(
    transport: &mut impl Transport,
    plugin: &InstalledPluginInfo,
    files: &Files,
    installer: &mut impl Installer,
    clock: &Clock,
) -> Option<String> {
    pending(plugin, files, (clock.now)())?;
    let claim = Claim::take(files)?;
    let update = pending(plugin, files, (clock.now)())?;
    let answered = ask_when_free(
        transport,
        &question(&update),
        &buttons(&update),
        clock,
        &mut || claim.owned() && pending(plugin, files, (clock.now)()).is_some(),
    );
    settle(answered, plugin, &update, files, (clock.now)(), installer)
}

fn pending(plugin: &InstalledPluginInfo, files: &Files, now: SystemTime) -> Option<Available> {
    kit::offer(
        plugin,
        &files.result(),
        &files.offered(CHANNEL)?,
        now,
        INTERVAL,
    )
}

pub fn claim_path(files: &Files) -> PathBuf {
    files.dir().join(CLAIM)
}

pub struct Claim {
    path: PathBuf,
}

impl Claim {
    pub fn take(files: &Files) -> Option<Claim> {
        let path = claim_path(files);
        if let Some(claim) = Claim::create(&path) {
            return Some(claim);
        }
        if held(&path) {
            return None;
        }
        let moved = files
            .dir()
            .join(format!("{}.stale-{}", CLAIM, process::id()));
        fs::rename(&path, &moved).ok()?;
        if held(&moved) {
            if fs::hard_link(&moved, &path).is_ok() {
                let _ = fs::remove_file(&moved);
            }
            return None;
        }
        let _ = fs::remove_file(&moved);
        Claim::create(&path)
    }

    fn create(path: &Path) -> Option<Claim> {
        let parent = path.parent()?;
        fs::create_dir_all(parent).ok()?;
        let partial = parent.join(format!("{}.new-{}", CLAIM, process::id()));
        fs::write(&partial, process::id().to_string()).ok()?;
        let linked = fs::hard_link(&partial, path);
        let _ = fs::remove_file(&partial);
        linked.ok()?;
        Some(Claim {
            path: path.to_path_buf(),
        })
    }
}

impl Claim {
    pub fn owned(&self) -> bool {
        owner(&self.path) == Some(process::id())
    }
}

impl Drop for Claim {
    fn drop(&mut self) {
        if self.owned() {
            let _ = fs::remove_file(&self.path);
        }
    }
}

pub fn held(path: &Path) -> bool {
    let Ok(modified) = fs::metadata(path).and_then(|meta| meta.modified()) else {
        return false;
    };
    let young = SystemTime::now()
        .duration_since(modified)
        .map_or(true, |age| age < LEASE);
    young && owner(path).is_some_and(alive)
}

fn owner(path: &Path) -> Option<u32> {
    fs::read_to_string(path).ok()?.trim().parse().ok()
}

#[cfg(unix)]
fn alive(pid: u32) -> bool {
    Command::new("/bin/sh")
        .args(["-c", "kill -0 \"$1\" 2>/dev/null", "sh", &pid.to_string()])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_or(true, |status| status.success())
}

#[cfg(not(unix))]
fn alive(_: u32) -> bool {
    true
}

pub fn question(update: &Available) -> Dialog {
    Dialog::new(
        State::Info,
        "Update Project Finder?",
        &format!(
            "Project Finder {} is available. You have {}.",
            update.tag, update.installed
        ),
    )
}

pub fn buttons(update: &Available) -> Vec<Button> {
    vec![
        Button::new(&format!("update to {}", update.tag)),
        Button::new("not now"),
    ]
}

pub fn ask_when_free(
    transport: &mut impl Transport,
    question: &Dialog,
    buttons: &[Button],
    clock: &Clock,
    still_wanted: &mut dyn FnMut() -> bool,
) -> Answer {
    let mut quiet = Quiet(transport);
    let rounds = SLOT_WAIT.as_secs() / SLOT_RETRY.as_secs();
    let mut round = 0;
    loop {
        let answered = dialog::ask(&mut quiet, PLUGIN_ID, question, buttons);
        let busy = matches!(answered, Answer::Unanswered(Unanswered::Busy(_)));
        if !busy || round >= rounds {
            return answered;
        }
        (clock.sleep)(SLOT_RETRY);
        round += 1;
        if !still_wanted() {
            return answered;
        }
    }
}

pub fn settle(
    answered: Answer,
    plugin: &InstalledPluginInfo,
    update: &Available,
    files: &Files,
    now: SystemTime,
    installer: &mut impl Installer,
) -> Option<String> {
    let shown = !matches!(
        answered,
        Answer::Unanswered(
            Unanswered::Busy(_)
                | Unanswered::Failed(_)
                | Unanswered::NeverShown
                | Unanswered::TimedOut
        )
    );
    if let (true, Some(offered)) = (shown, files.offered(CHANNEL)) {
        kit::record_offer(&offered, now);
    }
    if !answered.chose(UPDATE) {
        return None;
    }
    Some(match kit::apply(plugin, update, installer) {
        Ok(()) => format!(
            "updated Project Finder to {}; it takes effect from the next launch",
            update.tag
        ),
        Err(why) => format!("could not update Project Finder to {}: {}", update.tag, why),
    })
}

struct Quiet<'a, T: Transport>(&'a mut T);

impl<T: Transport> Transport for Quiet<'_, T> {
    fn open_pane(&mut self, params: PluginPaneOpenParams) -> Result<(), OpenError> {
        self.0.open_pane(params)
    }

    fn show_notification(
        &mut self,
        _: NotificationShowParams,
    ) -> Result<NotificationShowReason, String> {
        Err("held back while the update offer waits for the popup slot".to_string())
    }
}
