# Project Picker — a Herdr plugin

Fuzzy-pick git repos and open them as workspaces in
[Herdr](https://herdr.dev), the agent-aware terminal multiplexer.

## What it does

`bin/pick-project` opens an fzf popup listing every git repo up to two levels
under `~/Developer` (`HERDR_PICKER_ROOT` to override). Open workspaces are
listed first and pre-selected, so what is checked is exactly what is loaded.
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
git clone git@github.com:mike-bronner/herdr-plugin-project-picker.git
cd herdr-plugin-project-picker
herdr plugin link
```

Bind the picker in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+f"
type = "popup"
command = "/absolute/path/to/herdr-plugin-project-picker/bin/pick-project"
width = "85%"
height = "85%"
```

Requires `fzf` and `python3` on the PATH. Tilde is not reliably expanded in
config values, so use an absolute path.

## Tests

```sh
python3 -m unittest discover tests
```
