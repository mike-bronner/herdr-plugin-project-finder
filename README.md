# Project Finder — a Herdr plugin

Fuzzy-pick git repos and open them as workspaces in
[Herdr](https://herdr.dev), the agent-aware terminal multiplexer.

## What it does

`bin/pick-project` opens an fzf popup listing every git repo up to two levels
under your home folder (`HERDR_PICKER_ROOT` to point it elsewhere). Open
workspaces are listed first and pre-selected, so what is checked is exactly
what is loaded.
On Enter the selection becomes the truth: unchecked open projects are closed,
newly checked ones are created with a tab named `agent` holding Claude on the
left and a shell on the right, and an empty selection closes everything except
the home workspace. Esc changes nothing.

The home workspace (label `~`, `HERDR_PICKER_HOME` to override) is never
listed and never closed.

A `KIND` column marks each row `repo` or `worktree`, so a linked git worktree
kept beside its parent is recognisable at a glance. The filter searches the
whole visible row, so typing `worktree` narrows the list to worktrees.

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
      for _ in {1..40}; do
        sleep 0.25
        command herdr status server 2>/dev/null | grep -q 'status: running' || continue
        command herdr plugin pane open --plugin mikebronner.project-finder --entrypoint picker >/dev/null 2>&1 && exit 0
      done
    ) &!
  fi
  command herdr "$@"
}
```

The waiter gives the server up to ten seconds, so cold and warm starts behave
the same. `HERDR_NO_PICKER=1 herdr` skips it, and Esc in the picker changes
nothing.

## Configure

Both settings are optional. To change one, create a `.env` file in the plugin's
config directory:

```sh
$EDITOR "$(herdr plugin config-dir mikebronner.project-finder)/.env"
```

```sh
# Where to look for git repos, searched two levels deep. Default: ~
HERDR_PICKER_ROOT=~/Developer

# Label of the pinned workspace that is never listed and never closed.
# Default: ~
HERDR_PICKER_HOME=home
```

Real environment variables win over the file. Values may be quoted, and a
leading `~` in `HERDR_PICKER_ROOT` is expanded. `#` starts a comment only at
the beginning of a line, and a line without `=` is ignored.

If `HERDR_PICKER_ROOT` names a folder that does not exist, the picker searches
the home folder instead of failing.

## Requires

`fzf` and `python3` on the PATH.

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
