# Workbench — a Herdr plugin

Project picker and most-recently-used workspace ordering for
[Herdr](https://herdr.dev), the agent-aware terminal multiplexer.

## What it does

- **`bin/pick-project`** — an fzf popup listing every git repo up to two
  levels under `~/Developer` (`HERDR_PICKER_ROOT` to override). Open
  workspaces are listed first and pre-selected, so what is checked is exactly
  what is loaded. On Enter the selection becomes the truth: unchecked open
  projects are closed, newly checked ones are created with Claude on the left
  and a shell on the right, and an empty selection closes everything except
  the home workspace. Esc changes nothing.
- **`bin/mru-sort`** — a `workspace.focused` hook that promotes the focused
  workspace toward the top of the sidebar after a dwell (`HERDR_MRU_DWELL`,
  default 10s). The home workspace (`HERDR_MRU_PIN`, default `~`) stays
  pinned at the top and is never listed or closed by the picker.

## Install

```sh
git clone git@github.com:mike-bronner/herdr-plugin-workbench.git
cd herdr-plugin-workbench
herdr plugin link
```

Bind the picker in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+f"
type = "popup"
command = "/absolute/path/to/herdr-plugin-workbench/bin/pick-project"
width = "85%"
height = "85%"
```

Requires `fzf` and `python3` on the PATH. Tilde is not reliably expanded in
config values, so use an absolute path.

## Tests

```sh
python3 -m unittest discover tests
```
