# Project Finder

A popup that lists every git repository up to three levels under a root folder, plus the linked worktrees that belong to them, on this computer and on every remote machine connected to the app, and makes the checked rows the set of open spaces. Open projects are listed first and already checked. Type to filter, Tab to check or uncheck, Enter to apply: newly checked projects open as spaces in their folders, and unchecked open ones close. Esc changes nothing. The home space (`~`) of every machine is never listed and never closed.

![Project Finder](preview.png)

## What you see

- **A popup** at the top of the window, 40px down, 780px wide, as tall as its content and at most 80% of the window high, on the theme's `popover` color, with 16px padding and 10px between its parts. Text is 13px in the layout's UI font.
- **The title line:** `Projects` at 15px semibold in `foreground`. On the right, a summary at 11px in `dim`, such as `42 projects · 3 checked · 1 held` (`7/42 projects` while a filter hides rows, and no `held` part when nothing is held), then an `Esc` button at 11px in `dim`. While a background refresh runs, a braille spinner and `refreshing` at 11px in `dim` sit just left of the summary that turns `foreground` on `hover` when pointed at.
- **The filter field:** 32px high, 6px corners, on `background`, with a 1px border in `accent` while it has the keyboard and `input` otherwise. Placeholder `filter projects`.
- **Setting problems**, one line each in `blocked`, such as `Setting: agent must be null or a list of words, such as ["claude"]`.
- **Skipped machines**, one line each in `muted`, such as `Skipped devbox: unavailable`.
- **An error line** in `blocked` when there is one.
- **While it looks for the first time:** a braille spinner and `Finding projects…` in `muted`, turning every 90 ms. When the search fails, its error line takes the spinner's place. Once a search has finished, later opens show the last list at once instead, with the `refreshing` spinner in the title line.
- **The column headings** at 11px in `dim`: a 14px gap for the mark, `PROJECT` taking the rest, then `MACHINE` (96px), `KIND` (40px), `TOUCHED` (72px) and `AGENT STATUS` (112px).
- **One row per project**, 28px high, 6px corners, 10px apart inside:
  - A `✓` in `accent` when checked, nothing when not.
  - The project name. A worktree row puts its repository's name and a `/` in front, in `dim`, such as `acme-shop/` before `checkout`. The letters the filter matched are `accent` and bold.
  - `MACHINE` in `dim`: blank for this computer, else the remote machine's name, cut with `…` past 12 characters.
  - `KIND`: `tree` in `accent` for a linked worktree, `repo` in `foreground` for a repository.
  - `TOUCHED`: how long ago the newest file changed, in `dim`: `12m ago`, `3h ago`, `2d ago`, `5w ago`, or `-` when there are no files.
  - `AGENT STATUS` for an open project: a glyph, then the status word at 12px in `muted`.
- **The list** is as tall as its rows, up to 16 rows (448px), and scrolls past that with a scrollbar. When the filter leaves no rows, it is 44px high and shows `No matching projects.` in `dim`. The keys scroll the row they act on into view.
- **The row the keys act on** has the `selected` background. Other rows show `hover` under the pointer.
- **A held row** draws its name in `dim` and keeps its `MACHINE` cell and puts `held by checked worktrees` (12px, `dim`, 240px) where `KIND`, `TOUCHED` and `AGENT STATUS` would be.
- **The footer**, one centered line, 11px, `dim`: `↵ apply: checked open, unchecked close · tab toggle · ctrl-a all · ctrl-d none · esc cancel`.

## How it behaves

- **Agent status** is the most urgent status of the spaces open on that project (blocked, then done, then working, then idle):
  - **working:** a braille spinner in `working`, turning every 90 ms.
  - **blocked:** `●` in `blocked`.
  - **done:** `●` in `done`.
  - **idle:** `○` in `idle`.
  - A project that is not open, or is open with no agent, shows nothing there.
- **Which machines:** this computer, and every machine in `machines()` whose layout is `ready`, all searched at the same time. A machine that is `unavailable` or still `loading` is not searched. A machine whose home folder cannot be read, or whose search fails, is skipped. Each skipped machine gets its own line, and the other machines' rows still load. On a remote machine, every program runs there through `exec` with `on`, and `~` is that machine's home, read with `sh -c 'printf %s "$HOME"'`.
- **The last list:** each machine's rows from its last finished search are kept in the mod's `localStorage`, under the key `projects`, so they last across opens and app restarts. When the popup opens with a kept list, it shows those rows at once, checked by their open spaces now, and searches every machine in the background as usual. As each machine's search ends, that machine's rows are replaced by what it found, with projects added, gone ones removed and `TOUCHED` updated, before the other machines finish. The kept list is rewritten after each one. A machine that is skipped, or whose search fails, keeps its kept rows, shown under its `Skipped` line. Closing the popup during a refresh, with Enter or Esc, stops it waiting for the searches still running and drops their results. Commands already started finish on their own. A machine whose search had not finished keeps its kept rows until a later open's search of it finishes. Kept rows of a machine no longer in `machines()` are left out. A kept list that cannot be read, has another version or has a malformed row is ignored, and the popup loads as on the first open.
- **What a refresh keeps:** the filter text, and the row the keys act on while it is still listed. When it is gone, the highlight stays at the same place in the list. A row that was checked or unchecked by hand keeps that choice. Every other row is checked when it is open. Open state and agent status always come from `spaces()` at the time, never from the kept list. Enter during a refresh acts on the rows shown at that moment.
- **Which rows:** on each machine, every folder up to three levels under that machine's root that holds a `.git`, never entering a folder whose name starts with a dot. A repository inside another listed repository is skipped. Worktrees are then looked for one and two levels inside each repository's containers: `.worktrees`, `.claude/worktrees`, `worktrees`, and the app's `worktrees.directory` setting, with `~` as that machine's home. A relative setting is resolved against each repository, and an absolute or `~` one is searched as it is, even outside the root. Without the setting, the app's own default `~/.herdr/worktrees` is searched. Only a real linked worktree (a `.git` file) is listed from a container.
- **Names:** a row is named by as much of the end of its path as it takes to be the only row on its machine with that name. `MACHINE` tells apart rows on different machines, so the same folder on two machines has the same name: two `api` folders under `group-a` and `group-b` read `group-a/api` and `group-b/api`. A name longer than 34 characters is cut with `…`, and the repository prefix gives up its own characters first.
- **Order:** one list for every machine. Projects with an open space come first, in the spaces sidebar's order, already checked. The rest follow, most recently touched first.
- **Open means** a space on the project's machine whose first terminal is inside the project folder, or that was made there. Both the machine and the folder must match, so a space on one machine never opens or holds a row of another. The deepest listed folder on that machine wins, so a space in a worktree is that worktree's, not its repository's.
- **Keys:** typing filters. `Tab` checks or unchecks the highlighted row and moves down, `Shift-Tab` moves up. `Ctrl-A` checks every row the filter left, `Ctrl-D` unchecks them. `Up`/`Down`, `Ctrl-P`/`Ctrl-N` and `PageUp`/`PageDown` move. `Enter` applies. `Esc`, `Ctrl-C` and `Ctrl-G` close and change nothing. A click on a row checks or unchecks it.
- **The filter** is fuzzy: each word must match its letters in order, in the name, the kind word (`repo` or `worktree`), the age, the status word and the machine name. Lowercase matches any case, and an uppercase letter makes that word exact. Better matches come first.
- **Holding:** an open repository is held while any of its own worktree rows on the same machine is checked. A held row will not uncheck. Checking a worktree also checks its open repository. Unchecking the last checked worktree frees it. A repository that is not open holds nothing, and checking its worktree never opens it. `Ctrl-D` unchecks worktrees before repositories, so a repository and its worktrees clear together.
- **Enter:** closes every open space on an unchecked row, worktrees before repositories, with `space.close` confirmed. Each space closes on its own machine. It never closes a home space or a space with a terminal at its own machine's home. Then it opens every newly checked project with `space.new` in its folder on its machine: `machine` is the machine's name, or `""` for this computer. Only the last one opens in front. When nothing opened and the shown space closed, it shows the first space that stayed, else the home space. A close that fails does not stop the other closes. A space that fails to open stops the remaining opens and selects nothing. A select that fails is ignored, as the TUI ignores a failed focus. Every failed close and open is reported in one warning toast, a remote one as `<machine>:<folder>`.
- **The agent setting:** with `"agent": ["claude"]`, each new space starts `claude` in its first tab. With `null`, it is a plain shell.
- **The root settings:** `root` applies on every machine, with `~` as that machine's home. `machineRoots` maps a machine name to a root for that machine alone, such as `{"devbox": "~/Developer"}`. A root that is not a folder there falls back to that machine's home. `machineRoots` never applies to this computer.
- **A setting problem** is shown at the top, and Enter then changes nothing. A `machineRoots` that is not an object, and each entry that is not an absolute path or one that starts with `~`, is a problem.
- **Nothing moves** apart from three spinners: the `Finding projects…` spinner while the first list loads, the `refreshing` spinner while a kept list is refreshed, and the working spinner beside an agent that is working.

## Built from

- `mods/project-finder/mod.json`: `{ "place": "none", "title": "Project Finder", "overlay": { "kind": "popup", "at": "top", "offset": 40, "width": 780, "height": 0.8 }, "palette": [{ "title": "Find projects", "command": "mod.open", "args": { "name": "project-finder" }, "words": "…" }], "settings": { "root": "~", "agent": null } }`. `machineRoots` is optional and not in the shipped file. It docks nowhere. `mod.open {name: "project-finder"}` opens it, from the palette entry or a key.
- `keys.json`: `{ "global": { "cmd-shift-o": "mod.open:project-finder" } }`.
- `mods/project-finder/discover.js`: pure functions, no `herdr` import. `discover({root, dirs, roots}, run)` runs three programs through `run`:
  1. `find -L <root> -mindepth 1 -maxdepth 4 ( -name .git -prune -print ) -o ( -name '.*' -prune )` for the repositories, in three depth bands, each sorted by path, nested ones skipped.
  2. `find -L <containers…> -mindepth 1 -maxdepth 3 ( -name .git -prune -type f -print ) -o ( -name '.*' -prune )` for the worktrees in the containers.
  3. One `sh -c` script, built by `factsScript`, that prints for each row its kind, the first line of a worktree's `.git` pointer, and the newest mtime of `.git/index` and of the files three levels down. That walk skips `node_modules`, `vendor` and `.git` folders and the row's own containers. It uses `stat -L -c %Y` or `stat -L -f %m`, whichever works, and `awk` keeps the newest.

  Also `parentRepo` (the repository from `gitdir: <repo>/.git/worktrees/<name>`), `labelled`, `tails`, `humanAge`, `elide`, `normpath` and `worktreeLocations`.
- `mods/project-finder/picker.js`: pure functions, no `herdr` import. `searchPlan`, which reads the settings and lists the machines to search and the ones to skip. `discoverAll`, which searches every machine through the `exec` it is given, hands each machine's result to its callback as that search ends, and returns the rows, each machine's home, the roots it searched, the skip notes and the setting problems. `skipNote`. `readSettings` (for one machine and its home), `openSpaces`, `homeSpaceIds`, `labelledPerMachine`, `buildEntries`, `machineCell`, `held`, `toggle`, `setAll`, `fuzzy`, `filtered`, `rowCells`, `listHeight`, `plan`, `pickFocus` and `applySelection`, which runs the Enter commands through the `run` it is given and returns every close and open that failed.
- `mods/project-finder/cache.js`: pure functions, no `herdr` import. `readCache` (the kept list for the listed machines, or none), `cacheText`, `mergeResult` (one machine's finished search into the kept list), `cachedView` (the rows in machine order, and each remote's home) and `refreshed` (the new rows with the hand-made checks, the filter and the highlighted row carried over).
- `mods/project-finder/main.js`: the view. From `herdr`: `spaces`, `panes`, `machines`, `run`, `exec`, `home`, `appHome`, `theme`, `props`, `closeOverlay`, `readTextFile`, `notify`, `checking`, and the built-in `localStorage`. From `gpui-base`: `v_flex`, `h_flex`, `InputState`, `Input`, `ScrollHandle`, `Scrollbar`. It reads `mods/project-finder/mod.json` for its settings and `settings.json` for `worktrees.directory`, and runs `space.close {id, confirmed: true}`, `space.new {folder, machine, argv?, focus}` and `space.select {id}`.
- No art and no other shipped mod.

## Watch out

- It runs programs: `find`, `sh`, `stat`, `awk` and `test`, on this computer and on every ready remote machine. Each `find` and the `sh` script may run for up to 120 seconds. It reads files only under each machine's root and worktree containers, plus its own `mod.json` and the app's `settings.json`.
- The mod reads its settings from `mods/project-finder/mod.json`, so the folder must keep that name. A reinstall that copies the folder again resets the settings.
- On macOS, searching the home folder can make the system ask whether Herdr may read Desktop, Documents or Downloads. Point `root` at a code folder to avoid it.
- `space.new` gives back no id, so the focus rule picks the new space by opening only the last one with `focus: true`.
- Open spaces are matched by machine and folder, not by name, and spaces are left unnamed so they follow their folder. A symlinked root whose terminals report the resolved path does not match.
- The filter has no special syntax: `^`, `$`, `'` and `!` are plain letters.
- `herdr-app check` renders only the loading state, because `init` returns early while checking. It reads and writes no kept list.
- Herdr saves the mod's `localStorage` under `~/.config/herdr-app/data/project-finder/`. Deleting that folder's files makes the next open a first load.

## Done when

- `herdr-app check mods/project-finder` reports no errors.
- `node --test tests/app-mod/discover.test.mjs tests/app-mod/picker.test.mjs tests/app-mod/remote.test.mjs tests/app-mod/cache.test.mjs` passes.
- With `root` pointing at a folder holding `acme-shop` and `acme-shop/.worktrees/acme-shop/checkout`, the popup lists `acme-shop` as `repo` and `acme-shop/checkout` as `tree`.
- Checking a row and pressing Enter opens a space in that folder. With `"agent": ["claude"]`, Claude Code starts in its first tab.
- Unchecking an open worktree and its repository and pressing Enter closes the worktree's space first, then the repository's.
- With a remote machine connected, its projects are listed with its name under `MACHINE`, and typing its name filters to them.
- Checking a remote row and pressing Enter opens a space on that machine in that folder. Unchecking it closes that space, and the same folder on this computer is left alone.
- With a remote machine disconnected, the popup shows `Skipped <machine>: unavailable`, and the other rows still load.
- After one finished search, closing and opening the popup again shows the rows at once with the `refreshing` spinner, and the spinner goes when every machine's search has ended.
- A project folder added on a machine shows up in the open popup when that machine's search ends, while a slower machine is still searching.
- A row checked, a filter typed and a highlighted row during a refresh are still the same after it.
- Esc closes the popup and every space stays as it was.
- No machine's `~` space is ever a row, and each stays open whatever is checked.
