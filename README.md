# Project Picker — a Herdr plugin

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

Pairs well with [herdr-plugin-recent-spaces](https://github.com/mike-bronner/herdr-plugin-recent-spaces),
which keeps the sidebar in most-recently-used order.

## Install

```sh
herdr plugin install mike-bronner/herdr-plugin-project-picker
```

To work on the plugin instead, clone it and link the checkout:

```sh
git clone git@github.com:mike-bronner/herdr-plugin-project-picker.git
herdr plugin link /absolute/path/to/herdr-plugin-project-picker
```

Bind the picker pane in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+f"
type = "shell"
command = "\"$HERDR_BIN_PATH\" plugin pane open --plugin mikebronner.project-picker --entrypoint picker"
description = "pick project"
```

There is no keybinding type that opens a plugin pane directly, so the binding
shells out to the CLI. `$HERDR_BIN_PATH` is injected into command keybindings,
so no absolute path is needed.

## Configure

Both settings are optional. To change one, create a `.env` file in the plugin's
config directory:

```sh
$EDITOR "$(herdr plugin config-dir mikebronner.project-picker)/.env"
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
