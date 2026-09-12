use std::io::Write;
use std::path::PathBuf;

use herdr_plugin_kit::env::Environment;
use pick_project::app::{self, with_extra_path};
use pick_project::ui;
use pick_project::version::{self, Request};

fn main() {
    match version::requested(&std::env::args_os().skip(1).collect::<Vec<_>>()) {
        Request::Pick => pick(),
        Request::Report => report(),
        Request::Refuse(argument) => refuse(&argument),
    }
}

fn pick() {
    let env = with_extra_path(Environment::from_process());
    let own_root = plugin_root(&env);
    let mut err = std::io::stderr();

    match app::run(&env, &own_root, &mut err, &mut |entries, theme, env| {
        ui::run(entries, theme, env)
    }) {
        Ok(_) => {}
        Err(fatal) => die(fatal.message()),
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
