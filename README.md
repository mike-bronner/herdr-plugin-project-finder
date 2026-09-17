# Project Finder — a Herdr plugin

Fuzzy-pick git repos and open them as workspaces in
[Herdr](https://herdr.dev), the agent-aware terminal multiplexer.

## Install

```sh
herdr plugin install mike-bronner/herdr-plugin-project-finder
```

Nothing else needs installing on macOS, Linux or Windows, x86-64 or arm64: for
any revision that has one published, the install step downloads a prebuilt
picker for the platform and verifies its SHA-256. A Rust toolchain is only the
fallback, for a platform or a revision without one. ⚠️ Windows compiles and is
published, and nobody has ever run it — see [Windows](#windows) and
[requires](#requires).

Pin a particular revision with `--ref`, which is also the surest way to land on
one that has binaries published:

```sh
herdr plugin install mike-bronner/herdr-plugin-project-finder --ref 0.9.1
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

Either way the picker refreshes itself on the next run — by download where the
checkout matches a release, by compiling otherwise. See
[how the binary arrives](#how-the-binary-arrives).

## What it does

`bin/launcher` opens a popup listing every git repo up to three levels
under your home folder (`HERDR_PICKER_ROOT` to point it elsewhere), plus the
linked worktrees belonging to those repos. Open workspaces are listed first and
pre-checked, so what is checked is exactly what is loaded.
On Enter the selection becomes the truth: unchecked open projects are closed,
newly checked ones are opened, and an empty selection closes everything except
the home workspace. Esc changes nothing.

Unchecked worktrees close before the repositories they belong to. A repository
and its worktrees therefore all close in one pass, rather than a level at a
time. A close Herdr refuses is reported rather than dropped.

An open repository is **held** while any worktree row of its own stays checked,
because Herdr refuses to close a repository that still has an open worktree. A
held row will not uncheck, and checking a worktree checks the open repository it
belongs to. Uncheck every worktree of a repository and the repository itself is
free again on the same keystroke, so unchecking a project along with its
worktrees still works exactly as it did. A repository that is **not** open holds
nothing, because there is no close for Herdr to refuse, and checking a worktree
of one never opens it.

A held row is drawn dim, and says on the row itself what holds it. The note
stands where that row's `KIND`, `TOUCHED` and `AGENT STATUS` would be, because
none of the three informs a decision the hold has already taken away. The line
under the heading counts the held rows as well.

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
before narrowing the list stays checked. `Ctrl-A` reaches one row further when
it has to: the open repository of a worktree it checks. `Ctrl-D` leaves a
repository checked when the filter hides a worktree of its own.

**Every workspace the picker opens is handed to one command of your choosing.**
That is the `layout` setting, and it is where the panes come from: the picker
opens a workspace with the one bare pane Herdr gives it, then runs your command
against that workspace and leaves. A workspace is all it knows how to make.

The command is run **detached**, so a ten-project selection never waits on one
of them. The command line is yours down to the last flag, and the only thing the
picker contributes to it is the id of the workspace it just opened. That
command's failures are its own to report: the picker reads neither its output
nor its exit code. ⚠️ "Detached" is a weaker promise on Windows than on Unix,
and the difference is spelt out under [Windows](#windows).

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

A row is named by as much of the end of its path as it takes to be the only row
with that name. Usually that is the directory name on its own. Two `api`
directories under `group-a` and `group-b` are named `group-a/api` and
`group-b/api`; where those parents share a name as well, the next directory up
joins in, and so on as far as the whole path. Herdr is handed that name as the
workspace label, and it is what an open workspace is matched back to its row by,
so two rows sharing one are two rows the picker cannot tell apart: one of the
two could never be opened, closed or listed as open at all. Names that grow are
elided in the `PROJECT` column, from the right, so the part that does the
telling apart is the part that survives.

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

An `AGENT STATUS` column shows the agent state of every open project a hold
does not cover, drawn with the same glyph and colour Herdr's own spaces sidebar
uses. Both are read from `~/.config/herdr/config.toml` rather than assumed:
`[ui] status_indicators` picks `dots` or `symbols`, and `[theme]` plus any
`[theme.custom]` override of `green`, `yellow`, `red`, `teal`, or `overlay0`
decides the colour. `accent`, for the `KIND` column, and `match`, for the
characters the filter matched, are read the same way, and each falls back to a
palette slot no status uses — `teal` for the accent, `mauve` for the match. All
eighteen built-in themes are covered, and under `theme.name = "terminal"` the
colours are ANSI indexes, so the picker follows your terminal profile exactly
as Herdr does. Herdr shows the glyph alone; the picker keeps the status word
beside it because the filter searches it.

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

As you type, the characters that made each row match are marked in your theme's
**match** colour, bold and underlined. A fuzzy match does not need the letters
to be adjacent, so the marks answer "why is this row here" without guessing,
and they say which character to type next to narrow the list. `[theme.custom]
match` wins when you set it, and otherwise the match colour is the `mauve` of
the palette in force, the magenta-family slot no agent status draws in.

One theme diverges from Herdr on purpose. Under `theme.name = "terminal"` Herdr
keeps ANSI grey in its own `mauve` slot, the very colour it draws `overlay0`
in, so copying it would mark a match in the colour of an `unknown` status. That
theme has eight ANSI colours to spread over nineteen roles, so the duplication
there is structural rather than a statement about magenta. The picker marks a
match in ANSI bright magenta instead, which stays inside the theme's own ANSI
vocabulary. Every other mauve in the table is Herdr's own value, and
`[theme.custom] match` overrules any of them.

Four rows read the marks differently, on purpose. A repository prefix is never
marked, because it is display only and takes no part in the match. A name cut
with a `…` marks the ellipsis when the match landed in the part that was cut,
so a row is never left with no mark at all. The `KIND` cell searches on the
word `worktree` but shows `tree`, so a match on the `work` half has nothing on
screen to mark and marks nothing. A held row draws none of the three cells the
note takes, so a match in any of them has nothing on screen to mark either. The
row still answers the filter, and still shows its own name marked. A marked
`KIND` or `AGENT STATUS` cell keeps its own colour and is told apart by the
emphasis alone, so no signal is painted over by another.

Pairs well with [herdr-plugin-recent-spaces](https://github.com/mike-bronner/herdr-plugin-recent-spaces),
which keeps the sidebar in most-recently-used order.

## How it talks to Herdr

Over Herdr's socket, at `HERDR_SOCKET_PATH`, and not by shelling out to the CLI.
The client, the envelope and every wire type come from
[herdr-plugin-kit](https://github.com/mike-bronner/herdr-plugin-kit), pinned by
tag, and the types in it are generated from Herdr's own published API schema.
The picker hand-maintains none of it.

The wire protocol is newline-delimited JSON: one connection per request,
`{id, method, params}` out and `{id, result}` or `{id, error}` back. The id is
minted per call and checked on the way in, so an answer addressed to somebody
else is refused rather than read. The picker opens no handshake of its own —
Herdr's `ping` exists and reports the server's protocol number, and a one-shot
popup has nothing useful to do with the answer.

The picker makes seven calls, and **each one names the single result type it
expects**. Herdr's schema declares no link between a method and the result that
answers it, so every pairing below was measured against a live Herdr 0.9.0 on
2026-09-12. A name that looks obvious is a guess, and a wrong guess is a
runtime failure rather than a compile error.

| Call | Answers with |
|---|---|
| `workspace.list` | `workspace_list` |
| `workspace.create` | `workspace_created` |
| `workspace.close` | `ok` |
| `workspace.focus` | `workspace_info` |
| `worktree.open` | `worktree_opened` |
| `plugin.list` | `plugin_list` |
| `notification.show` | `notification_show` |

`workspace.focus` is the one nobody would guess. `ok` is wrong, and there is no
`workspace_focused` result type to reach for either: that name is an event Herdr
publishes to subscribers, never an answer it gives a caller.

Naming one type rather than the union of all 64 is what keeps the binary small,
and it is silent when it is not done: the union still builds and still works.
The suite therefore answers each call with some *other* valid Herdr result and
checks that the call refuses it, so swapping a type reddens the tests instead of
costing a megabyte quietly.

An error comes back with a code, so a refusal can be told apart from a crash
without reading stderr for a phrase. That is the whole reason for the choice: a
`worktree.open` the server refuses falls back to `workspace.create`, and it has
to be sure which of the two it is looking at.

One more refusal shapes both the order the picker closes in and what the list
lets you ask for. Herdr will not close a repository while a linked worktree of
its own is still open. It answers `workspace_group_close_required` and closes
nothing. So `workspace.list` is read for each workspace's
`worktree.is_linked_worktree`, and every unchecked worktree is closed before any
repository. A workspace the server reports no `worktree` block for counts as a
repository and closes last.

Ordering alone only covers the case where the whole group goes. Leave one
worktree checked and no order helps: that repository can never close. So the
picker holds the repository row instead, and the refused selection cannot be
expressed. The rows are matched to each other by path, read from the worktree's
own `gitdir:` pointer, so nothing extra is asked of Herdr to work it out.

`workspace.close` also takes `close_group`, which closes a repository together
with every worktree of its own. The picker never sends it, and never retries a
refusal with it. It would close worktrees you left checked, and the selection is
the contract. Holding the row keeps that contract from the other end: nothing is
closed that you did not uncheck.

With `HERDR_SOCKET_PATH` unset the picker falls back to the documented default,
`~/.config/herdr/herdr.sock`, which is what lets it run by hand from a shell.
Herdr sets the variable for a pane, so the fallback is the by-hand case rather
than the normal one. A socket it cannot reach either way ends the run before the
list is drawn, and the message says which of the two paths it tried and where
that path came from, because "cannot reach this path" and "cannot reach this
path, and nothing named it" are different problems. A selection the picker
cannot act on is worse than no popup at all.

A `workspace.list` the server refuses is treated the same way, and for the same
reason. The list is what every row's checked state is read from, so an answer
that does not arrive is not an empty list of open projects: read as one, every
row would draw unchecked, nothing would close, and every checked row would be
opened a second time. The picker reports the refusal and stops instead. It reads
the list twice, once to draw and once before it acts, and either refusal ends the
run. An answer carrying no list at all is refused on the same grounds. An answer
carrying an **empty** list is not a refusal: no projects are open, which is an
ordinary thing for it to say.

A row the picker cannot read is refused too, and that is a change. The
hand-written client used to walk each workspace field by field and drop one it
did not understand, so a Herdr that changed the shape of a workspace cost the
picker a row and said nothing. The generated types refuse the whole answer and
name the field, and the picker reports a server it cannot read.

## How the binary arrives

The plugin is a Rust binary, and there are two ways it gets there: downloaded
from a GitHub release, or compiled on the spot.

Everything under `bin/` is written by
[herdr-plugin-kit](https://github.com/mike-bronner/herdr-plugin-kit)'s sync task
and lands **byte-identical in every plugin that uses it**, so a `diff` between
two plugins' `bin/` directories shows drift and nothing else. Nothing there is
edited by hand, and CI fails a build where a synced file was changed. Each shim
infers the two facts it needs at run time: the binary's name from `Cargo.toml`'s
`[[bin]]` entry, and this plugin's name from `herdr-plugin.toml`'s top-level
`id`.

Three of the eight files matter to a reader:

- `bin/build` decides between downloading and compiling, and does whichever it
  picked.
- `bin/launcher` is what the manifest points a pane at. It rebuilds when the
  binary is behind and then execs it, so a `git pull` cannot leave the old
  picker running. `--version` is answered **before** that check, because a
  rebuild would replace the very binary the report exists to diagnose.
- `bin/find-cargo` locates a toolchain, and the `.ps1` files mirror the shell
  ones for a Windows nobody here has ever run (see [requires](#requires)).
  `bin/launcher.ps1` is the one the manifest no longer points at, and
  [Windows](#windows) says why.

The manifest points Herdr at the shims and never at the build output, so nothing
breaks when a profile or a path changes.

The launcher exists because Herdr's `[[build]]` steps run **only** during
`herdr plugin install owner/repo`. They do not run for `herdr plugin link`, and
they do not run on update. A linked checkout would therefore never build itself,
and an update would keep running the old binary. `[[build]]` is declared as
well, so that a GitHub install shows a visible install step rather than stalling
silently on first use, and it passes `--install` to name that context.

### When it downloads, and when it compiles

`bin/build` asks four questions, in order, and the first "no" compiles:

1. **Is `BUILD_FROM_SOURCE` sitting in the plugin root?** That file is the
   developer override: create it and this tree always compiles, whatever else
   is true. `.gitignore` carries an entry for it, because committing it would
   turn every install of that release into a source build and say so only in a
   line of a server log.
2. **Is this the install context?** `[[build]]` passes `--install`, and during
   an install the source is a GitHub checkout by construction. On any other path
   — `[[startup]]`, a direct run — the shim asks Herdr what kind of install this
   is and compiles for anything that is not plainly `github`, which includes a
   socket it cannot reach.
3. **Is this checkout exactly a released one?** The plugin folder has to be the
   root of its own git checkout rather than a folder inside somebody else's,
   `origin` has to be a GitHub remote, and `git status --porcelain` has to say
   nothing about the files the binary is built from.
4. **Does the asset exist?** Its name carries **the first 12 characters of the
   checked-out commit**.

That last point is what makes the rule safe rather than hopeful. The asset name
is `pick-project-<platform>-<commit>`, so the download URL itself asserts that
the binary was built from the source in this folder. A checkout one commit past
the tag asks for a file that does not exist, gets a 404, and compiles.

"The files the binary is built from" is seven entries — `src/`, `Cargo.toml`,
`Cargo.lock`, `build.rs`, `.cargo/`, `rust-toolchain` and
`rust-toolchain.toml` — and it is one list, used by the download check, by the
launcher's staleness check, and by the build stamp that records which commit the
binary came from. Letting those disagree is what makes a staleness marker
incoherent. Both toolchain spellings are listed because a git pathspec matches
whole path components, so `rust-toolchain` does not match `rust-toolchain.toml`.

`build.rs` is in this repository now and does exactly one thing: it calls the
kit's stamp helper, which writes the commit and the build instant into the
binary for `--version` to read. It is a `[build-dependencies]` entry, so it is
compiled for the host, used once, and left out of the shipped binary.

Everything else is outside the set. A test, the shipped `defaults.toml`, the
workflows, and this README are read by somebody or something other than the
compiler, so editing one leaves the released asset an exact copy of what this
source still compiles to.

So a GitHub install downloads, and a checkout you are working in compiles as
soon as you touch its code. Editing a file the compiler reads never downloads —
which matters, because a download would otherwise drop a released binary on top
of the change you are testing. Editing a file it never reads still downloads,
because there is no change to drop anything on top of.

A binary that was downloaded is tracked differently from one that was compiled.
The fetch writes a note beside the binary recording the version, the asset and
the URL it came from; after that the launcher compares the manifest's version
against that note rather than comparing timestamps, because a downloaded file's
timestamp says nothing about which release it is. The same note is what
`--version` reads to tell you whether to rebuild or to reinstall.

Downloads are verified. A `.sha256` file is published beside every binary, and a
binary whose hash does not match it is deleted rather than run. That catches a
truncated or corrupted transfer; it is not a signature, and the trust anchor is
GitHub over TLS either way. If the platform is not one this plugin publishes
for, or the network is down, or `curl` is missing, or there is no `sha256sum`
and no `shasum` to check the hash with — then nothing is downloaded and the
source build runs instead, and the shim says which of those it was. There is no
path where an unverified binary is executed.

`curl` and nothing else, deliberately. `curl`, `shasum` and `git` are all in
`/usr/bin`, which is what lets a fetch work under the launchd `PATH` Herdr's
server runs with. A second downloader would need its own exit-code handling to
keep the failure messages honest, and nothing could test it.

### The source build

Finding `cargo` by absolute path is not enough. Herdr's server runs under launchd
with `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, and `cargo` is a rustup shim that
execs `rustc` out of its own directory — so a cold build dies with
`could not execute process rustc -vV`. `bin/build` therefore prepends the cargo
binary's own directory to the `PATH` it builds under. `bin/find-cargo` looks on
the `PATH` first, then at `$CARGO`, `$CARGO_HOME/bin/cargo`,
`~/.cargo/bin/cargo`, and the usual Homebrew rustup and `/usr/local` locations.

When cargo is missing but a binary is already there, the launcher runs that
binary and says on stderr that it may be stale. When there is neither, it stops
and says how to install a toolchain.

What it prints depends on where it runs. With a terminal on stderr — the popup —
cargo's own compile and download output is captured, and a spinner and one line
saying what is happening take its place. With no terminal there — Herdr's
install step, a pipe, a file, CI — nothing is drawn at all: one plain line says
whether it is downloading or compiling and why, and cargo prints what it always
printed, because a spinner in a log is thousands of repeated lines. A build that
fails prints everything cargo said either way, and the captured file is removed
on success, on failure, and on an interruption.

Nothing runs at all when the binary is already current. That is what keeps
`[[startup]]` off the network on every server start.

### Where the prebuilt binaries come from

`.github/workflows/release.yml` runs when a release is created, and it is a
caller and nothing more: the whole job is herdr-plugin-kit's own reusable
`plugin-release.yml`, pinned to the tag `Cargo.toml` pins. A copy of the build
here would be a second opinion about a convention that is already written down,
and the consuming half — the URL `bin/build` asks for — has no way to disagree
out loud. A producer and a consumer that name an asset differently is a 404, a
silent fall back to compiling, and nobody finding out.

That workflow builds six targets on native runners and uploads each binary with
its checksum:

| Target | Runner |
|---|---|
| `aarch64-apple-darwin` | `macos-latest` |
| `x86_64-apple-darwin` | `macos-latest` |
| `aarch64-unknown-linux-musl` | `ubuntu-24.04-arm` |
| `x86_64-unknown-linux-musl` | `ubuntu-24.04` |
| `aarch64-pc-windows-msvc` | `windows-11-arm` |
| `x86_64-pc-windows-msvc` | `windows-latest` |

Native runners are why this needs neither Zig nor `cargo-zigbuild`, which the
previous workflow used to reach arm64 Linux from an x86 runner. Linux is musl
rather than gnu on purpose: a gnu build carries the glibc floor of the runner
that produced it, and a user on an older distribution would get a download that
passes its checksum and *then* refuses to start — worse than a 404, because the
fetch succeeded and no fallback fires.

All six, or none. The publish step counts what every leg produced against the
size of that table and uploads nothing if one is missing, because five platforms
published and a sixth quietly compiling on every install forever is not a
partial success.

All six compile, and CI compiles all six on every push so that a release cannot
be the first thing to discover otherwise. What CI cannot prove is that any of
them *runs*, which for the two Windows rows nobody has ever checked. See
[Windows](#windows).

The assets do not exist until that workflow has finished, so a release is only
fetchable a few minutes after it is published. Before then, and for any release
cut before this workflow existed, an install compiles — which is the old
behaviour, not a failure.

## What this binary is

```sh
sh bin/launcher --version
```

Three lines of fact, and a fourth only when there is something wrong:

```
pick-project 0.9.0 (a1b2c3d, built 2026-09-12T11:04:22Z)
manifest 0.9.0 at /path/to/herdr-plugin-project-finder/herdr-plugin.toml
built from source on this machine
```

The first line is what the binary was compiled from: the crate version, the
short commit, and when. The commit carries `-dirty` when a file the compiler
reads had uncommitted changes, and `-unverified` when the check could not run at
all — unknown is never reported as clean. The second line is what Herdr reads.
The third says whether this binary was downloaded or compiled here.

A `STALE:` line is appended when the first two disagree, and it tells you what
to do about it based on how the binary arrived rather than how it was made:
rebuild if it was compiled here, reinstall if it was fetched, because whoever
installed a published binary has no toolchain to rebuild with.

Nothing in the report can fail. It never touches the socket, because `--version`
has to answer when the server is down, which is exactly when somebody runs it.
Every lookup degrades to a word: a missing stamp reads `unknown`, and an
unreadable manifest says so and names the path it tried.

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
HERDR_PICKER_DEBUG=1 sh bin/launcher
```

## Requires

Nothing, on macOS, Linux or Windows on x86-64 or arm64. Installing from GitHub
downloads a prebuilt picker for the platform and checks it against a published
SHA-256.
The picker draws its own list and talks to Herdr over the socket, so there is no
runtime dependency to install either.

A Rust toolchain is the fallback, and it is needed whenever a download cannot
happen or cannot be trusted: an architecture with no published binary, a release
whose assets have not been built yet, no network, no `curl`, no way to compute a
SHA-256, or a checkout with local changes to the code the binary is built from.
Working on the plugin therefore still needs a toolchain, because an edited source
tree always compiles. See
[when it downloads, and when it compiles](#when-it-downloads-and-when-it-compiles).

**No minimum version is declared, and that is deliberate.** `Cargo.toml` carries
no `rust-version`. It used to say 1.75, which was false twice over: the kit's
generated types need `LazyLock`, from 1.80, and `regress`, which the kit pulls
in, declares `edition = "2024"` and so needs **1.85**. The second is what binds
today. A floor is a promise to the people who compile this, and under
download-by-default almost nobody does; the number it would carry is whatever
the dependency tree currently demands rather than anything this plugin chose.
The one case that is not useless is the fallback path above, and its failure is
loud and names itself.

### Windows

Declared, compiled, and **never run by anyone**. Treat a bug there as new
information rather than as a regression.

All six release targets compile, and CI compiles every one of them on a native
runner on every push. That job is the entire Windows guarantee: nobody on this
project has Windows hardware, so the compiler is the only reader those code
paths ever get. It exists mainly to catch `std::os::unix` creeping back into
`src/layout.rs`, which is the one file that had to be ported and the one most
likely to regress.

Two things in that file differ per platform, and the second is a real difference
in behaviour rather than a difference in spelling.

**Deciding whether a layout command can be run.** On Unix the picker checks the
execute bit. Windows has no execute bit, so it checks only that the path is a
file. `PATHEXT` is not consulted: a bare `launcher` will not be found on
Windows unless a file with exactly that name is on the `PATH`. Name the
executable in full, extension included, or give an absolute path.

**Detaching the layout process.** This is the part to read carefully, because
the guarantee is genuinely weaker on Windows and nothing here can measure it.

| | Unix | Windows |
|---|---|---|
| Mechanism | `setsid` in the child, before `exec` | `DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP` |
| New process group | yes | yes |
| Free of the terminal | yes — a new session with no controlling terminal | no console attached |
| Survives the picker's own death | yes | **not guaranteed** |

On Unix the child becomes a session leader, which is what makes it outlive the
popup that started it. Windows has no session concept to reach for. The flags
used give the child its own process group and no console, so a Ctrl-C aimed at
the picker will not reach it — but if the picker is running inside a job object
configured to kill its children when it closes, the layout process dies with it.
Whether Herdr runs plugin panes inside such a job object is not something anyone
here has checked.

So on Windows, read the hand-off as "started, in its own group, not attached to
a console" — not as "will certainly still be running a second later". The
picker never reads the layout command's output or exit code on either platform,
so nothing tells you when this goes wrong.

`bin/build.ps1`, `bin/launcher.ps1` and `bin/common.ps1` are the kit's, and they
reach a lower bar still. PowerShell has no compiler and no CI job, and it is not
installed on the machine they were written on, so **no `.ps1` file here has ever
been run or even parsed**. Each one says so in its own header.

Each `[[build]]` and `[[startup]]` entry is declared twice, once for `sh` and
once for `powershell`, using Herdr's per-item `platforms` override. The shell
entry is declared first in every pair on purpose: if Herdr ever took the first
match regardless of platform, the tested platforms are the ones that would win.

**`[[panes]]` is declared once, because `platforms` does not mean there what it
means on a build step.** 0.9.0 doubled the pane too, both entries carrying the
id `picker` so that the keybinding stayed one line. Herdr 0.9.0 refused the
whole manifest — `manifest unavailable: duplicate pane id 'picker'` — and served
its cached copy of 0.3.1 instead, whose pane still ran the Python entry point
that same release had deleted. `prefix+f` opened nothing at all until 0.9.1.

Measured on 2026-09-13: two pane entries sharing an id are refused even when
their platform lists are disjoint. A pane id has to be unique across every
entry, whatever `platforms` says, and that is what makes the doubling legal on a
build step and fatal here. So the pane is the `sh` one, with no `platforms` key,
and on Windows it would run `sh bin/launcher`, which will not work there. That
cost is paid knowingly: a second id would make the keybinding
platform-dependent for a platform nobody here can test, and no Windows machine
has ever run this plugin. `bin/launcher.ps1` stays in the tree for the day one
does.

[agentic-panes-layout](https://github.com/mikebronner/herdr-plugin-agentic-panes-layout)
is wanted, not required, and only because it is what the `layout` setting
defaults to. Without it every workspace opens as one bare pane; the picker still
opens them, and says once per run why they are bare. Point `layout` at something
else and this plugin is not wanted either.

Herdr's manifest has no dependency field, so the fallback is declared as a
`[[build]]` step that runs `sh bin/build --install` at install time. With no
terminal attached it says on stderr whether it is downloading or compiling, and
why. When a download is impossible and there is no toolchain either, it stops
wherever it runs and says how to install one:

```
project-finder: install Rust with `curl --proto '=https' --tlsv1.2 -sSf
https://sh.rustup.rs | sh`, then run `cargo build --release` in
/path/to/herdr-plugin-project-finder
```

## Tests

```sh
cargo test
```

The suite runs the picker against a stub Herdr server over a real Unix socket,
so what is checked is the requests it does and does not send — and, since the
migration onto the kit, the results it accepts. The stub answers each method
with the shape a live Herdr was measured to send, and a separate set of tests
answers each call with a *different* valid Herdr result to prove the call
refuses it. Nothing is mocked in process except the terminal itself, which
`cargo test` cannot give it; the screen is checked instead by rendering into
ratatui's test backend and reading the cells back.

Two more checks run in CI and are not part of `cargo test`, because both need
herdr-plugin-kit checked out at the tag this plugin pins:

```sh
python3 ../herdr-plugin-kit/templates/sync_bin.py . --check
python3 ../herdr-plugin-kit/tools/plugin_gate.py versions .
```

The first fails when a file under `bin/` differs from the kit's template or when
`.gitignore` has lost its `BUILD_FROM_SOURCE` entry. The second asserts that
`bin/common` and cargo name the same binary, that `herdr-plugin.toml` and
`Cargo.toml` state the same version, that no `v`-prefixed tag names that
version, and that no release tag sorts above it. Every one of those failures is
silent in production: the install still works, it just stops using the prebuilt
binary the whole mechanism exists to deliver.

One check belongs to no automation at all, and skipping it is what let 0.9.0
ship a manifest Herdr would not load:

```sh
herdr plugin list --json
```

A refused manifest fails nothing. The plugin stays listed and keeps serving
whatever Herdr last cached, with the reason in a `warnings` array beside it — so
ask the live server before tagging, and read three fields of this plugin's
entry: `warnings` is absent, `version` is the one `herdr-plugin.toml` declares,
and the pane's `command` names `bin/launcher`. `cargo test` cannot make that
call, because CI runs no Herdr and a test that skips itself when the binary is
absent would be green there for the wrong reason. The suite pins the property
the refusal turned on instead, in
`the_manifest_declares_exactly_one_pane_so_herdr_will_load_it`.

The tree is rustfmt-formatted on the tool's defaults, with no `rustfmt.toml` to
carry: `cargo fmt --check` is expected to pass, and `cargo fmt` is expected to
change nothing. A wider `max_width` was measured and rejected because it moved
more lines than the default did, not fewer. `cargo clippy --all-targets` is
expected to be silent.

No Rust file carries a comment or a doc comment, tests included, and
`no_rust_source_file_carries_a_comment` fails the suite when one appears. Names
carry the intent instead.
