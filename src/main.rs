use std::io::Write;
use std::path::{Path, PathBuf};

use pick_project::app::{self, with_extra_path};
use pick_project::config::Environment;
use pick_project::ui;

fn main() {
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

fn plugin_root(env: &Environment) -> PathBuf {
    if let Some(root) = env.get("HERDR_PLUGIN_ROOT").filter(|r| !r.is_empty()) {
        return PathBuf::from(root);
    }
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.ancestors().nth(3).map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."))
}

fn die(message: &str) -> ! {
    let _ = std::io::stderr().write_all(
        format!("\n\x1b[31merror:\x1b[0m {}\n\nPress enter to close...", message).as_bytes(),
    );
    let mut line = String::new();
    if std::io::stdin().read_line(&mut line).is_err() {
        std::thread::sleep(std::time::Duration::from_secs(5));
    }
    std::process::exit(1);
}
