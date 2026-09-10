# Project Finder — a Herdr plugin

Fuzzy-pick git repos and open them as workspaces in
[Herdr](https://herdr.dev), the agent-aware terminal multiplexer.

## Install

```sh
herdr plugin install mike-bronner/herdr-plugin-project-finder
```

Pin a particular revision with `--ref`:

```sh
herdr plugin install mike-bronner/herdr-plugin-project-finder --ref v0.8.0
```

To work on the plugin instead, clone it and link the checkout:

```sh
git clone git@github.com:mike-bronner/herdr-plugin-project-finder.git
herdr plugin link /absolute/path/to/herdr-plugin-project-finder
```

Bind the picker pane in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+f"
type = "shell"
command = "\"$HERDR_BIN_PATH\" plugin pane open --plugin mikebronner.project-finder --entrypoint picker"
description = "find projects"
```

There is no keybinding type that opens a plugin pane directly, so the binding
shells out to the CLI. `$HERDR_BIN_PATH` is injected into command keybindings,
so no absolute path is needed.

### Updating

Herdr v1 has no separate plugin update command. Reinstall from GitHub to refresh
a managed install:

```sh
herdr plugin install mike-bronner/herdr-plugin-project-finder
```

A linked checkout is updated with `git pull`, since Herdr runs the plugin out of
that directory. Note that `herdr plugin list` may still report the version the
link was registered at, so treat the version it prints for a linked plugin as
unreliable.

Either way the picker rebuilds itself on the next run. See
[how the binary is built](#how-the-binary-is-built).

## What it does

`bin/pick-project` opens a popup listing every git repo up to three levels
under your home folder (`HERDR_PICKER_ROOT` to point it elsewhere), plus the
linked worktrees belonging to those repos. Open workspaces are listed first and
pre-checked, so what is checked is exactly what is loaded.
On Enter the selection becomes the truth: unchecked open projects are closed,
newly checked ones are opened, and an empty selection closes everything except
the home workspace. Esc changes nothing.

The list is drawn by the plugin itself, with
[ratatui](https://ratatui.rs) and [nucleo](https://github.com/helix-editor/nucleo)
for the fuzzy matching. Type to filter, `Tab` to check a row, `Enter` to apply.

| Key | Does |
| --- | --- |
| any character | types into the filter, `space` included |
| `Tab` | checks or unchecks the highlighted row, then moves down |
| `Shift-Tab` | the same, then moves up |
| `Ctrl-A` | checks every row the filter left |
| `Ctrl-D` | unchecks every row the filter left |
| `Up` / `Down`, `Ctrl-P` / `Ctrl-N` | move the cursor |
| `PageUp` / `PageDown`, `Home` / `End` | move it further |
| `Backspace`, `Ctrl-W`, `Ctrl-U` | drop a character, a word, the whole filter |
| `Enter` | applies what is checked |
| `Esc`, `Ctrl-C` | changes nothing |

`space` types rather than toggling, so a two-word filter works. `Ctrl-A` and
`Ctrl-D` reach only the rows the filter left on screen, so a row you checked
before narrowing the list stays checked.

**Every workspace the picker opens is handed to one command of your choosing.**
That is the `layout` setting, and it is where the panes come from: the picker
opens a workspace with the one bare pane Herdr gives it, then runs your command
against that workspace and leaves. A workspace is all it knows how to make.

The command is run **detached**, so a ten-project selection never waits on one
of them. The command line is yours down to the last flag, and the only thing the
picker contributes to it is the id of the workspace it just opened. That
command's failures are its own to report: the picker reads neither its output
nor its exit code.

Out of the box the setting hands each workspace to the sibling
[agentic-panes-layout](https://github.com/mikebronner/herdr-plugin-agentic-panes-layout)
plugin, which splits it into an agent, a tool pane and a shell, and starts the
agent. That is a **default, not a dependency**: see
[the layout command](#the-layout-command) for how to point it at something else,
and that plugin's own README for what it does and how to configure it. Without
it installed, a picked project still opens as one bare pane, and the picker
raises one Herdr notification for the whole run saying why.

The home workspace (label `~`, `HERDR_PICKER_HOME` to override) is never
listed and never closed.

A `KIND` column marks each row `repo` or `tree`, so a linked git worktree is
recognisable at a glance. Four characters say both words, which leaves the room
the row spends on the project name intact.

`tree` is drawn in your theme's **accent** colour and `repo` is left at the
default foreground, so the worktree rows are the marked case and an ordinary
repository stays quiet. The colour is read from Herdr's own theme rather than
chosen here: `[theme.custom] accent` wins when you set it, and otherwise the
accent is the cool slot of whichever of the eighteen built-in palettes is in
force — ANSI cyan under `theme.name = "terminal"`. Nothing is hardcoded, so the
column belongs to your colour scheme instead of fighting it.

A worktree row also names the repository it belongs to in front of the branch:
`tru-data/feat-x`. The prefix carries the terminal's DIM attribute, so it reads
as secondary to the branch without claiming a colour of its own. Two worktrees
called `main` under different repositories are told apart by it, and the picker
sorts open workspaces first and then by touch time, so a worktree can sit far
from its repository or with its repository not listed at all. When the pair is
too long for the name column, the **repository** is cut at its end with a `…`
before the `/`, as in `repo-te…/worktree`, and the branch survives whole. A
repository is recognised by how its name starts, and the branch is the part
being read, so both keep their front. The repository is read from the same
one-line `gitdir:` pointer the row is opened by.

A terminal that ignores DIM draws the prefix exactly like the branch. That is
the accepted cost of the attribute: it follows whatever the user's colour scheme
already calls faint, where a fixed palette index would fight it.

The filter searches the project name, the kind, the age and the agent-status
word, so typing `worktree` narrows the list to worktrees and `blocked` narrows
it to blocked agents. The kind is matched on its whole word, `worktree`, even
though the column says `tree`. The repository prefix is drawn and never
matched on, so a repository name finds the rows it always found.

Worktrees are found wherever the tool that made them puts them. A worktree
directly under one of the three depth levels is reached by them, which includes
a flat root such as `<root>/worktrees/<repo>/<branch-slug>`. Everything else is
found by naming the **container**: a relative path resolved against each repo's
own root, searched two levels deep. The names are `.worktrees/`,
`.claude/worktrees/` (Claude Code), a plain `worktrees/`, and whatever Herdr's
own `[worktrees] directory` setting adds.

Resolving against the repo is what covers every layout in one pass, wherever
the container falls. `.worktrees/<repo>/<branch-slug>` and Claude Code's
`.claude/worktrees/<branch-slug>` sit inside the repo. A `../worktrees` setting
resolves to `<parent>/worktrees/<repo>/<branch-slug>` beside it, which is where
Herdr puts checkouts once they are moved out of the repository so that tools
walking the tree stop descending into them.

The names are searched rather than hunted for, because a hunt would not work
and would not be cheap. A directory whose name begins with a dot is never
descended into blindly, so no amount of extra depth would ever reach
`.worktrees` or `.claude`. A container shared by every repo in a parent, which
is what `../worktrees` produces, is searched once rather than once per repo. An
absolute `[worktrees] directory` is read as a flat root instead, and searched
even when it sits outside `HERDR_PICKER_ROOT`. Symbolic links are followed, so a
flat root holding a link into another group directory still lists what is behind
it. A missing, unreadable or malformed setting leaves the fixed names in place
and never fails.

Anything else nested inside a repo is skipped, so a submodule or a vendored
checkout is not listed twice. Only a real linked worktree is exempt from that,
which is why an ordinary repo sitting in one of those container directories is
still skipped. A repo's `TOUCHED` time ignores its nested worktrees for the
same reason: each one is its own row, and work on a branch is not work on its
parent.

Every git checkout is *opened* as a worktree rather than as a bare directory,
so Herdr records which repo it belongs to. `workspace.create` records nothing
about where a checkout came from, and two things break on that. A worktree
carries no repo metadata and floats at top level in Herdr's spaces sidebar as
though it were an unrelated project, instead of nesting under its repo — that
applied to every worktree the picker opened, in every layout, not only the ones
kept inside a repo. An ordinary repo has no root for Herdr's new-worktree
dialog to resolve a relative `[worktrees] directory` against, so the dialog
falls back to your home directory and proposes a checkout at a path you cannot
write.

A worktree names its parent, read from the worktree's own `.git`, the one-line
`gitdir:` pointer at `<repo>/.git/worktrees/<name>`. That is one file read and
no `git` subprocess, the same trade the `KIND` column already makes. A repo
names itself: the checkout and the repo are then the same directory, which is
the shape the call accepts for a checkout that is not linked. Herdr is told
the repo by **path**, so its own workspace does not have to be open: opening
one you did not check would break the rule that the selection is the truth.
Three cases fall back to `workspace.create`, where the row opens with no repo
recorded: a directory that is no git checkout at all, a worktree `.git` that
does not carry git's pointer shape, and a Herdr that refuses the call.

An `AGENT STATUS` column shows the agent state of every open project, drawn with
the same glyph and colour Herdr's own spaces sidebar uses. Both are read from
`~/.config/herdr/config.toml` rather than assumed: `[ui] status_indicators`
picks `dots` or `symbols`, and `[theme]` plus any `[theme.custom]` override of
`green`, `yellow`, `red`, `teal`, or `overlay0` decides the colour. `accent` is
read the same way, for the `KIND` column. All eighteen built-in themes are
covered, and under `theme.name = "terminal"` the colours are ANSI indexes, so
the picker follows your terminal profile exactly as Herdr does. Herdr shows the
glyph alone; the picker keeps the status word beside it because the filter
searches it.

The palette is a copy, because Herdr keeps its own inside the renderer: there is
no colour on the socket API and no theme event to subscribe to. So the picker
handles a theme it does not recognise rather than pretending. A name Herdr also
rejects is a typo, and `herdr config check` says so, naming the fallback it uses
— the picker uses that same fallback, so a typo still matches. A name Herdr
accepts that the table lacks means Herdr gained a theme since the copy was made:
the picker draws the `terminal` palette, which keeps the meaning right (green
idle, yellow working, red blocked) and follows your terminal profile, but is not
guaranteed to match the sidebar. That is the signal to refresh the table from
`src/app/state.rs` upstream.

`herdr config check` is the one thing the picker still runs as a subprocess.
It is a diagnostic command, not an API method, so there is nothing on the socket
to ask instead. A Herdr that cannot be run is simply not a rejection.

Herdr's config is found at `HERDR_CONFIG_PATH` when that is set, otherwise
derived from the plugin config directory Herdr passes in, otherwise
`~/.config/herdr/config.toml`.

One setting cannot be followed: `[theme] auto_switch`. Herdr chooses between
`dark_name` and `light_name` from the host terminal's light/dark appearance,
which it learns over its client connection and exposes on neither the socket API
nor the CLI. With `auto_switch = true` the picker uses `dark_name` and applies
`[theme.custom.dark]`. With it off, which is the default, there is nothing to
follow and the colours match exactly.

A project name longer than 34 characters is cut with a `…` so it cannot push
the columns after it out of alignment. A repository prefix in front of it gives
up its own characters first, from the left, and is dropped entirely rather than
taking a character from the name. The preview pane on the right shows the
full name above the git log. The filter reads the **whole** name rather than
what fits on screen, so text past the ellipsis still matches.

Pairs well with [herdr-plugin-recent-spaces](https://github.com/mike-bronner/herdr-plugin-recent-spaces),
which keeps the sidebar in most-recently-used order.

## How it talks to Herdr

Over Herdr's socket, at `HERDR_SOCKET_PATH`, and not by shelling out to the CLI.
The wire protocol is newline-delimited JSON with no handshake: one connection
per request, `{id, method, params}` out and `{id, result}` or `{id, error}`
back. The picker uses `workspace.list`, `workspace.create`, `workspace.close`,
`workspace.focus`, `worktree.open`, `plugin.list` and `notification.show`.

An error comes back with a code, so a refusal can be told apart from a crash
without reading stderr for a phrase. That is the whole reason for the choice: a
`worktree.open` the server refuses falls back to `workspace.create`, and it has
to be sure which of the two it is looking at.

With `HERDR_SOCKET_PATH` unset the picker says so and draws nothing, because a
selection it cannot act on is worse than no popup at all.

## How the binary is built

The plugin is a Rust binary. `bin/pick-project` is a small `sh` shim: it checks
whether anything under `src/`, `Cargo.toml` or `Cargo.lock` is newer than the
built binary, rebuilds if so, and then runs it. The manifest points Herdr at the
shim and never at the build output, so nothing breaks when a profile or a path
changes.

The shim exists because Herdr's `[[build]]` steps run **only** during
`herdr plugin install owner/repo`. They do not run for `herdr plugin link`, and
they do not run on update. A linked checkout would therefore never build itself,
and an update would keep running the old binary. `[[build]]` is declared as well,
so that a GitHub install shows a visible build step rather than stalling
silently on first use.

Finding `cargo` by absolute path is not enough. Herdr's server runs under launchd
with `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, and `cargo` is a rustup shim that
execs `rustc` out of its own directory — so a cold build dies with
`could not execute process rustc -vV`. `bin/build` therefore prepends the cargo
binary's own directory to the `PATH` it builds under. It looks on the `PATH`
first, then at `$CARGO`, `$CARGO_HOME/bin/cargo`, `~/.cargo/bin/cargo`, and the
usual Homebrew rustup and `/usr/local` locations.

When cargo is missing but a binary is already there, the shim runs that binary
and says on stderr that it may be stale. When there is neither, it stops and
says how to install a toolchain.

What the build prints depends on where it runs. With a terminal on stderr — the
popup — cargo's own compile and download output is captured, and a spinner and
one line saying the picker is building take its place. With no terminal there —
Herdr's install step, a pipe, a file, CI — nothing is captured and cargo prints
what it always printed, because a spinner in a log is thousands of repeated
lines. A build that fails prints everything cargo said either way, and the
captured file is removed on success, on failure, and on an interruption.

## Open on launch

Attaching blocks the shell, so the picker cannot usefully run before it: at a
cold start there is no server yet to open a pane on. Wrap the command in
`~/.zshrc` instead and let a detached waiter poll for the server, then ask
Herdr to open the pane:

```sh
herdr() {
  if [[ $# -eq 0 && -z "$HERDR_NO_PICKER" && -t 0 ]]; then
    (
      for _ in {1..200}; do
        if command herdr status server 2>/dev/null | grep -q 'status: running'; then
          out=$(command herdr plugin pane open --plugin mikebronner.project-finder --entrypoint picker 2>&1) && exit 0
          [[ $out == *ui_busy* ]] && exit 0
        fi
        sleep 0.05
      done
    ) &!
  fi
  command herdr "$@"
}
```

Probe first and sleep second. A warm start finds the server already running and
opens the picker in about 13 ms, where sleeping first paid a flat 250 ms before
the first check on every launch. The waiter still gives the server about ten
seconds, so cold and warm starts behave the same.
`HERDR_NO_PICKER=1 herdr` skips it, and Esc in the picker changes nothing.

The `ui_busy` check is not optional. Herdr holds **one** popup pane globally, and
refuses a second with that error code. A waiter that retries it stays alive
failing, then takes the slot the moment you dismiss whatever popup was already
up — which looks exactly like the picker opening twice. Any other failure means
the server is not ready yet, and is worth retrying.

## Configure

Every setting is optional. The picker's settings are written as TOML, in the
plugin's `config.toml`, so they read like the rest of your Herdr configuration:

```sh
$EDITOR "$(herdr plugin config-dir mikebronner.project-finder)/config.toml"
```

```toml
[picker]
# Where to look for git repos, searched three levels deep, plus the worktrees
# belonging to each one. Default: ~
root = "~/Developer"

# Label of the pinned workspace that is never listed and never closed.
# Default: ~
home = "home"

# Report what finding the projects cost, on one line.
# Default: false, and the picker prints nothing at all.
debug = true

# What every newly opened workspace is handed to. See "the layout command"
# below for the two tokens and what happens when this will not run.
# Default: the sibling agentic-panes-layout plugin, as shipped in this
# plugin's own defaults.toml.
layout = "/usr/local/bin/my-layout --space {workspace}"
```

`root`, `home` and `layout` are strings and `debug` is a boolean, read the same
way Herdr reads its own: only `true` turns it on, so `debug = 1` leaves it off.
A leading `~` in `root` is expanded.

That file is **the plugin's own**, in the directory Herdr hands the plugin. It
is not Herdr's `~/.config/herdr/config.toml`, and these keys do not belong
there: Herdr's schema names no plugin table, so a `[picker]` section added to it
makes `herdr config check` report `config: issues found` from then on.

It is parsed as real TOML, by the same parser Herdr parses its own config with,
so a file that sits beside Herdr's own cannot mean two different things. Herdr's
asymmetry on bad input is followed too: **a key the picker does not know is
named on stderr and the rest of the file still applies**, while **a syntax error
voids the whole file** and the picker falls back to its defaults. A typo in an
optional file must never be what stops the popup appearing.

### The `.env` file

The picker's settings can also be written as environment variables in a `.env`
file in the same directory, which is where they lived before `config.toml`. It
still works, so an existing one keeps being read.

A setting the **layout command** reads can be written here too, under whatever
name that command gives it. The picker reads none of them and passes the whole
environment on — see [which file a setting belongs
in](#which-file-a-setting-belongs-in) below.

```sh
$EDITOR "$(herdr plugin config-dir mikebronner.project-finder)/.env"
```

```sh
# Where to look for git repos, searched three levels deep, plus the worktrees
# belonging to each one. Default: ~
HERDR_PICKER_ROOT=~/Developer

# Label of the pinned workspace that is never listed and never closed.
# Default: ~
HERDR_PICKER_HOME=home

# Report what finding the projects cost. Any value turns it on.
# Default: unset, and the picker prints nothing at all.
HERDR_PICKER_DEBUG=1

# What every newly opened workspace is handed to.
# Default: the value in this plugin's defaults.toml.
HERDR_PICKER_LAYOUT=/usr/local/bin/my-layout --space {workspace}
```

Values may be quoted, and a leading `~` in `HERDR_PICKER_ROOT` is expanded. `#`
starts a comment only at the beginning of a line, and a line without `=` is
ignored.

### Which file a setting belongs in

The split is by **who reads the setting**, not by which file is nicer to edit.

The picker's own four — `root`, `home`, `debug` and `layout` — are read by this
plugin and nothing else, so they live in this plugin's `config.toml`.

A setting the **layout command** reads belongs to that command, and has no key
here at all. This file is the picker's private one, and a value written into one
plugin's config directory is invisible to every other plugin, so a key added
here would be one the command can never see. The picker hands its whole
environment over instead, minus the two variables Herdr uses to tell a plugin
which plugin it is — so the command finds its own files rather than this
plugin's.

Export such a setting wherever your Herdr server picks its environment up: your
shell profile, or the launch agent that starts it. Herdr's plugin docs say
plugin commands run as your user and inherit your environment, so one export
reaches every plugin where no file can.

A `.env` here still works for one, and is the right place for a value you want
when the command is run **from the picker** only. It is the wrong place for one
you want everywhere, since the same command run from a keybinding or an event
never sees this file.

### Which setting wins

A real environment variable, then `config.toml`, then `.env`, then the
`defaults.toml` this plugin ships. Setting one in your shell overrides both your
files, for that run only. Where both of your files name the same setting
`config.toml` wins, because it is the format these settings moved to and a
`.env` left behind should not quietly outrank the file replacing it. A setting
only one file names is taken from that one, so they merge per setting rather
than all or nothing.

`defaults.toml` is read last and lives in the plugin's own checkout, not in your
config directory. It is the same `[picker]` table in the same syntax, which is
the point: a default is a value you replace rather than one buried in the code.
It is also the only file in this plugin that names another program — see [the
layout command](#the-layout-command).

None of the files has to exist, and none has to parse. A missing, unreadable or
malformed one contributes nothing and the picker opens on its defaults: this is
a popup, and a typo in optional config must never be what stops it appearing.

If `HERDR_PICKER_ROOT` names a folder that does not exist, the picker searches
the home folder instead of failing.

### The layout command

`layout` is one command line, and every workspace the picker opens is handed to
it. It is split the way a shell splits a command line — quoted arguments survive
as one argument — and then run directly, so nothing in the value is interpreted
by a shell. Two substitutions are made in it, and they are the whole vocabulary:

| Token | Becomes |
| --- | --- |
| `{workspace}` | the id of the workspace that was just opened |
| `{plugin:<id>}` | the checkout directory Herdr reports for that plugin |

`{plugin:<id>}` is what lets the value point inside another plugin without
knowing where it landed, which Herdr alone knows: a GitHub install sits under
Herdr's own directory, a linked one anywhere on your disk. It is resolved once
per run. `{workspace}` is substituted per workspace, wherever it appears and
however many times.

Anything else is yours: the command's own flags, its arguments, and where the
workspace id goes among them. The picker adds nothing to the argv it was given.

The shipped default hands the workspace to the sibling
[agentic-panes-layout](https://github.com/mikebronner/herdr-plugin-agentic-panes-layout)
plugin. It is written in `defaults.toml` in this plugin's checkout, with the
reasoning for the flags it does and does not pass — that file is the one place
this plugin names another, and replacing the setting replaces the whole
relationship.

The command inherits your environment, which is how its own settings reach it.
Two variables are removed first: `HERDR_PLUGIN_ROOT` and
`HERDR_PLUGIN_CONFIG_DIR`, which Herdr sets to tell **this** plugin where its
own checkout and config directory are. Left in place they would tell the command
that the picker's files are its own, and it would run on its built-in defaults
having silently ignored everything you configured for it.

Nothing in the value is validated beyond being runnable, and the command's own
failures are not the picker's to report: the handoff is fire-and-forget, so that
a multi-select never waits on an agent coming up.

**When the command will not run, the workspaces still open.** All three ways the
setting can be wrong — an unbalanced quote, a `{plugin:<id>}` naming something
Herdr has no checkout for, and a command that is not an executable file — end
the same way: no command runs, every picked project opens with one bare pane,
and **one** Herdr notification for the whole run says which of the three it was.
One per workspace would bury the projects you just asked for, and stopping would
abandon the rest of your selection over a layout.

### Timing the search

`[picker] debug = true`, or `HERDR_PICKER_DEBUG` set to any value, writes one
line to stderr before the list is drawn:

```
picker: discovery 26.9ms, 90 rows (85 from depth bands, 5 from worktree containers)
```

The search runs in two passes, and the two figures say what each one
contributed: the depth bands walk the three levels under the root, and the
second pass searches the worktree containers of every repo the bands found. The
passes are named rather than what they return, because the bands find worktrees
as well as repos — any worktree within three levels whose path carries no
leading dot, which covers a flat `worktrees/<repo>/<branch>` and a
`../worktrees` container beside a repo directly under the root. Each row is
credited to the pass that reached it first, and listed once either way.

Leave the setting unset and the picker is silent, which is the default for
every normal run. To read the line it is easier to run the picker from a shell
than from the popup, since it takes the whole pane:

```sh
HERDR_PICKER_DEBUG=1 sh bin/pick-project
```

## Requires

A Rust toolchain — `cargo` 1.75 or newer — the first time the plugin runs, and
after every change to its source. Nothing else: the picker draws its own list
and talks to Herdr over the socket, so there is no runtime dependency to install.

[agentic-panes-layout](https://github.com/mikebronner/herdr-plugin-agentic-panes-layout)
is wanted, not required, and only because it is what the `layout` setting
defaults to. Without it every workspace opens as one bare pane; the picker still
opens them, and says once per run why they are bare. Point `layout` at something
else and this plugin is not wanted either.

Herdr's manifest has no dependency field, so the toolchain requirement is
declared as a `[[build]]` step that runs `sh bin/build` at install time. With no
terminal attached it prints where it found cargo. With no toolchain at all it
stops wherever it runs and says how to install one:

```
project-finder: cargo not found; install a Rust toolchain (1.75 or newer), then
run `cargo build --release` in /path/to/herdr-plugin-project-finder
```

## Tests

```sh
cargo test
```

The suite runs the picker against a stub Herdr server over a real Unix socket,
so what is checked is the requests it does and does not send. Nothing is mocked
in process except the terminal itself, which `cargo test` cannot give it; the
screen is checked instead by rendering into ratatui's test backend and reading
the cells back.

The tree is rustfmt-formatted on the tool's defaults, with no `rustfmt.toml` to
carry: `cargo fmt --check` is expected to pass, and `cargo fmt` is expected to
change nothing. A wider `max_width` was measured and rejected because it moved
more lines than the default did, not fewer. `cargo clippy --all-targets` is
expected to be silent.

No Rust file carries a comment or a doc comment, tests included, and
`no_rust_source_file_carries_a_comment` fails the suite when one appears. Names
carry the intent instead.
