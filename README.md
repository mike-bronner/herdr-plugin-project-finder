# Project Finder — a Herdr plugin

Fuzzy-pick git repos and open them as workspaces in
[Herdr](https://herdr.dev), the agent-aware terminal multiplexer.

## Install

```sh
herdr plugin install mike-bronner/herdr-plugin-project-finder
```

Pin a particular revision with `--ref`:

```sh
herdr plugin install mike-bronner/herdr-plugin-project-finder --ref v0.7.0
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

## What it does

`bin/pick-project` opens an fzf popup listing every git repo up to three levels
under your home folder (`HERDR_PICKER_ROOT` to point it elsewhere), plus the
linked worktrees belonging to those repos. Open workspaces are listed first and
pre-selected, so what is checked is exactly what is loaded.
On Enter the selection becomes the truth: unchecked open projects are closed,
newly checked ones are opened, and an empty selection closes everything except
the home workspace. Esc changes nothing.

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

A `KIND` column marks each row `repo` or `worktree`, so a linked git worktree
is recognisable at a glance. The filter searches the whole visible row, so
typing `worktree` narrows the list to worktrees.

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
and would not be cheap. No wildcard matches a leading dot, so no amount of
extra depth would ever see `.worktrees` or `.claude`. And naming them holds the
cost to 9 ms on top of 18 ms across 85 repos here. A container shared by every
repo in a parent, which is what `../worktrees` produces, is globbed once rather
than once per repo: 340 container paths across those 85 repos collapse to 262
distinct ones, and the whole search takes 23.0 ms against 22.5 ms for
`.worktrees`, so moving the worktrees out of the repos costs 0.5 ms. An
absolute `[worktrees] directory` is read as a flat root instead, and searched
even when it sits outside `HERDR_PICKER_ROOT`. A missing, unreadable or
malformed setting leaves the fixed names in place and never fails.

Anything else nested inside a repo is skipped, so a submodule or a vendored
checkout is not listed twice. Only a real linked worktree is exempt from that,
which is why an ordinary repo sitting in one of those container directories is
still skipped. A repo's `TOUCHED` time ignores its nested worktrees for the
same reason: each one is its own row, and work on a branch is not work on its
parent.

Every git checkout is *opened* as a worktree rather than as a bare directory,
so Herdr records which repo it belongs to. `workspace create` records nothing
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
the shape the command accepts for a checkout that is not linked. Herdr is told
the repo by **path**, so its own workspace does not have to be open: opening
one you did not check would break the rule that the selection is the truth.
Three cases fall back to the old behaviour, where the row opens with no repo
recorded: a directory that is no git checkout at all, a worktree `.git` that
does not carry git's pointer shape, and a Herdr too old for the command.

An `AGENT STATUS` column shows the agent state of every open project, drawn with
the same glyph and colour Herdr's own spaces sidebar uses. Both are read from
`~/.config/herdr/config.toml` rather than assumed: `[ui] status_indicators`
picks `dots` or `symbols`, and `[theme]` plus any `[theme.custom]` override of
`green`, `yellow`, `red`, `teal`, or `overlay0` decides the colour. All eighteen
built-in themes are covered, and under `theme.name = "terminal"` the colours are
ANSI indexes, so the picker follows your terminal profile exactly as Herdr does.
Herdr shows the glyph alone; the picker keeps the status word beside it because
fzf searches the visible text, so typing `blocked` narrows the list.

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
the columns after it out of alignment. The preview pane shows the full name
above the git log. Note that the filter can only match what is displayed —
fzf does not search hidden fields — so text past the ellipsis will not match.

Pairs well with [herdr-plugin-recent-spaces](https://github.com/mike-bronner/herdr-plugin-recent-spaces),
which keeps the sidebar in most-recently-used order.

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

Leave the variable unset and the picker is silent, which is the default for
every normal run. To read the line it is easier to run the picker from a shell
than from the popup, since fzf takes the whole pane:

```sh
HERDR_PICKER_DEBUG=1 python3 bin/pick-project
```

## Requires

[`fzf`](https://github.com/junegunn/fzf) and `python3` on the PATH.

[agentic-panes-layout](https://github.com/mikebronner/herdr-plugin-agentic-panes-layout)
is wanted, not required, and only because it is what the `layout` setting
defaults to. Without it every workspace opens as one bare pane; the picker still
opens them, and says once per run why they are bare. Point `layout` at something
else and this plugin is not wanted either.

Herdr's manifest has no dependency field, so the requirement is declared as a
`[[build]]` step that runs `python3 bin/pick-project --check-deps` at install
time. When fzf is missing the check exits non-zero and prints the install
command for the platform it detects:

```
project-finder requires fzf, which is not on the PATH.
Install it with:

    brew install fzf
```

If the plugin is already installed and fzf is not, the picker offers to
install it on first run:

```
fzf is required and not installed.
Install it now with `brew install fzf`? [y/N]
```

Only a privilege-free installer is ever run for you, which in practice means
Homebrew. A command needing `sudo` (`apt-get`, `dnf`, `pacman`) is printed for
you to run yourself: a popup pane is a bad place to ask for a password, and
installing system packages without asking is not a plugin's business. Decline,
and nothing is installed.

## Tests

```sh
python3 -m unittest discover tests
```

The suite loads `bin/pick-project` as a module and sets
`sys.dont_write_bytecode`, because a `.pyc` stays valid while the source keeps
the same size and whole-second mtime. Without that, editing the script to a
same-size version inside one second makes the suite run the previous code and
report failures against source that is correct. On macOS the system `python3`
puts the cache under `sys.pycache_prefix`
(`~/Library/Caches/com.apple.python`), outside the repo, so `find . -name
'*.pyc'` does not reveal it.

The test module cannot protect its own compilation this way, since the flag
runs after it. Prefix the command with `PYTHONDONTWRITEBYTECODE=1` if you are
making rapid same-size edits to the tests themselves.

One check needs a newer interpreter than the plugin does. The suite parses
`herdr-plugin.toml` for real, because Herdr re-reads that file at dispatch time
and a syntax error in it stops the plugin silently. Parsing needs `tomllib`,
which arrived in Python 3.11, and the `python3` this plugin runs under is 3.9
on macOS. Under 3.9 that one check is skipped and the run prints a banner
saying so, because a green suite there is not a checked manifest. Run the suite
under a 3.11 or newer interpreter to include it.
