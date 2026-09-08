# Project Finder — a Herdr plugin

Fuzzy-pick git repos and open them as workspaces in
[Herdr](https://herdr.dev), the agent-aware terminal multiplexer.

## What it does

`bin/pick-project` opens an fzf popup listing every git repo up to three levels
under your home folder (`HERDR_PICKER_ROOT` to point it elsewhere), plus the
linked worktrees kept inside those repos. Open workspaces are listed first and
pre-selected, so what is checked is exactly what is loaded.
On Enter the selection becomes the truth: unchecked open projects are closed,
newly checked ones are created with a tab named `agent` holding Claude on the
left and a shell on the right, and an empty selection closes everything except
the home workspace. Esc changes nothing.

The home workspace (label `~`, `HERDR_PICKER_HOME` to override) is never
listed and never closed.

A `KIND` column marks each row `repo` or `worktree`, so a linked git worktree
is recognisable at a glance. The filter searches the whole visible row, so
typing `worktree` narrows the list to worktrees.

Worktrees are found wherever the tool that made them puts them. A worktree
beside its parent, or in a flat root such as
`<worktrees.directory>/<repo>/<branch-slug>`, is reached by the three depth
levels. A worktree kept **inside** its own repo is found by name instead:
`.worktrees/` (Herdr once its `[worktrees] directory` is relative),
`.claude/worktrees/` (Claude Code), a plain `worktrees/`, and whatever Herdr's
own `[worktrees] directory` setting adds. Those names are searched because no
wildcard matches a leading dot, so no amount of extra depth would ever see
them — and because naming them is what keeps the search cheap: 9 ms on top of
18 ms across 85 repos here. An absolute `[worktrees] directory` is read as a
flat root and searched even when it sits outside `HERDR_PICKER_ROOT`.

Anything else nested inside a repo is skipped, so a submodule or a vendored
checkout is not listed twice. Only a real linked worktree is exempt from that,
which is why an ordinary repo sitting in one of those container directories is
still skipped. A repo's `TOUCHED` time ignores its nested worktrees for the
same reason: each one is its own row, and work on a branch is not work on its
parent.

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

## Install

```sh
herdr plugin install mike-bronner/herdr-plugin-project-finder
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

Every setting is optional. To change one, create a `.env` file in the plugin's
config directory:

```sh
$EDITOR "$(herdr plugin config-dir mikebronner.project-finder)/.env"
```

```sh
# Where to look for git repos, searched three levels deep, plus the worktrees
# inside each one. Default: ~
HERDR_PICKER_ROOT=~/Developer

# Label of the pinned workspace that is never listed and never closed.
# Default: ~
HERDR_PICKER_HOME=home

# Report what finding the projects cost. Any value turns it on.
# Default: unset, and the picker prints nothing at all.
HERDR_PICKER_DEBUG=1
```

Real environment variables win over the file. Values may be quoted, and a
leading `~` in `HERDR_PICKER_ROOT` is expanded. `#` starts a comment only at
the beginning of a line, and a line without `=` is ignored.

If `HERDR_PICKER_ROOT` names a folder that does not exist, the picker searches
the home folder instead of failing.

### Timing the search

`HERDR_PICKER_DEBUG` writes one line to stderr before the list is drawn:

```
picker: discovery 26.9ms, 90 rows (85 from depth bands, 5 from worktree containers)
```

The search runs in two passes, and the two figures say what each one
contributed: the depth bands walk the three levels under the root, and the
second pass looks inside the worktree containers under every repo the bands
found. The passes are named rather than what they return, because the bands
find worktrees as well as repos — a flat `worktrees/<repo>/<branch>` sits three
levels down, so the bands reach it.

Leave the variable unset and the picker is silent, which is the default for
every normal run. To read the line it is easier to run the picker from a shell
than from the popup, since fzf takes the whole pane:

```sh
HERDR_PICKER_DEBUG=1 python3 bin/pick-project
```

## Requires

[`fzf`](https://github.com/junegunn/fzf) and `python3` on the PATH.

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
