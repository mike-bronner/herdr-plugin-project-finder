use std::io::Write;
use std::path::PathBuf;
use std::time::SystemTime;

use herdr_plugin_kit::dialog;
use herdr_plugin_kit::env::Environment;
use herdr_plugin_kit::update::{self as kit, CurlReleases, HerdrInstaller};
use pick_project::api::{self, Client, Socket, PLUGIN_ID};
use pick_project::app::{self, with_extra_path};
use pick_project::version::{self, Request};
use pick_project::{ui, update};

fn main() {
    match version::requested(&std::env::args_os().skip(1).collect::<Vec<_>>()) {
        Request::Pick => pick(),
        Request::Report => report(),
        Request::Dialog => ask(),
        Request::CheckUpdate => check_update(),
        Request::Refuse(argument) => refuse(&argument),
    }
}

fn pick() {
    let env = with_extra_path(Environment::from_process());
    launch_offer(&env);
    let own_root = plugin_root(&env);
    let mut err = std::io::stderr();

    match app::run(&env, &own_root, &mut err, &mut |entries, theme, env| {
        ui::run(entries, theme, env)
    }) {
        Ok(_) => {}
        Err(fatal) => die(fatal.message()),
    }
}

fn launch_offer(env: &Environment) {
    if let Ok(socket) = Socket::resolve(env) {
        update::on_launch(
            &Client::new(socket, PLUGIN_ID),
            SystemTime::now(),
            &mut kit::spawn_check_if_due,
            &mut || {
                std::env::current_exe()
                    .map(|me| update::spawn_detached(&me))
                    .unwrap_or(false)
            },
        );
    }
}

fn check_update() {
    let env = with_extra_path(Environment::from_process());
    let Ok(socket) = Socket::resolve(&env) else {
        return;
    };
    let client = Client::new(socket, PLUGIN_ID);
    if let Some(note) = update::answer_check(
        &client,
        &mut client.clone(),
        app::herdr_binary(&env).map(HerdrInstaller::at),
        &CurlReleases::default(),
        &update::Clock {
            now: &SystemTime::now,
            sleep: &std::thread::sleep,
        },
    ) {
        api::notify(&client, &note);
    }
}

fn ask() {
    if let Err(why) = dialog::run(&Environment::from_process()) {
        let _ = writeln!(std::io::stderr(), "project-finder: {}", why);
        std::process::exit(1);
    }
}

fn report() {
    let env = Environment::from_process();
    print!(
        "{}",
        herdr_plugin_kit::version_report!(env!("CARGO_BIN_NAME"), &env)
    );
}

fn refuse(argument: &str) -> ! {
    let _ = writeln!(
        std::io::stderr(),
        "project-finder: unknown argument `{}`; run it with no arguments to pick a project, \
         or `{}` to report the build",
        argument,
        version::FLAG
    );
    std::process::exit(2);
}

fn plugin_root(env: &Environment) -> PathBuf {
    herdr_plugin_kit::version::root_of(env, std::env::current_exe().ok().as_deref())
        .unwrap_or_else(|| PathBuf::from("."))
}

fn die(message: &str) -> ! {
    let _ = std::io::stderr().write_all(
        format!(
            "\n\x1b[31merror:\x1b[0m {}\n\nPress enter to close...",
            message
        )
        .as_bytes(),
    );
    let mut line = String::new();
    if std::io::stdin().read_line(&mut line).is_err() {
        std::thread::sleep(std::time::Duration::from_secs(5));
    }
    std::process::exit(1);
}
