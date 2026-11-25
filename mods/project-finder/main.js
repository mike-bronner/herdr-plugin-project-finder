import { View, div } from "gpui-kit";
import { v_flex, h_flex, InputState, Input, ScrollHandle, Scrollbar } from "gpui-base";
import { spaces, panes, run, exec, home, appHome, theme, props, closeOverlay, readTextFile, notify, checking } from "herdr";
import { discover } from "./discover.js";
import { HELD_NOTE, KIND_CELL, ROW_HEIGHT, applySelection, buildEntries, filtered, held, heldCount, listHeight, nameMarks, openSpaces, readSettings, rowCells, setAll, toggle } from "./picker.js";

const NAME = "project-finder";
const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

export default class ProjectFinder extends View {
  init(_props, cx) {
    this.entries = undefined;
    this.shown = [];
    this.at = 0;
    this.query = "";
    this.error = "";
    this.problems = [];
    this.applying = false;
    this.inputFocused = true;
    this.input = InputState.new({ placeholder: "filter projects" });
    this.input.on("change", (_e, cx) => {
      this.query = this.input.value();
      this.refilter();
      cx.notify();
    });
    this.input.on("submit", (_e, cx) => {
      this.apply();
      cx.notify();
    });
    this.input.on("focus", (_e, cx) => { this.inputFocused = true; cx.notify(); });
    this.input.on("blur", (_e, cx) => { this.inputFocused = false; cx.notify(); });
    this.handle = cx.focus_handle();
    this.handle.focus();
    this.input.focus();
    this.scroll = ScrollHandle.new();
    if (checking()) return;
    cx.timer.after(600, () => this.input.focus());
    cx.spawn(async (cx) => {
      try {
        await this.load();
      } catch (error) {
        this.error = String(error?.message ?? error);
      }
      cx.notify();
      this.input.focus();
    });
  }

  async load() {
    const userHome = home();
    const [modJson, appSettings] = await Promise.all([
      readTextFile(`${appHome()}/mods/${NAME}/mod.json`).catch(() => null),
      readTextFile(`${appHome()}/settings.json`).catch(() => undefined),
    ]);
    const settings = readSettings(modJson, appSettings, userHome);
    this.problems = settings.problems;
    this.agent = settings.agent;
    const isDir = await exec("test", ["-d", settings.root]).then((r) => r.code === 0, () => false);
    this.root = isDir ? settings.root : userHome;
    const { rows } = await discover({ root: this.root, dirs: settings.dirs, roots: settings.roots }, (command, args) => exec(command, args, { timeout: 120_000 }));
    if (!rows.length) {
      this.error = `No git repositories found under ${this.root}`;
      this.entries = [];
      return;
    }
    const open = openSpaces(spaces(), panes(), rows.map((r) => r.path));
    this.entries = buildEntries(rows, open, Math.floor(Date.now() / 1000));
    this.refilter();
  }

  refilter() {
    this.shown = this.entries ? filtered(this.entries, this.query) : [];
    this.at = 0;
    this.scroll.scroll_to_item(0);
  }

  close() {
    closeOverlay(props().overlay);
  }

  move(by) {
    if (!this.shown.length) return;
    this.at = Math.max(0, Math.min(this.shown.length - 1, this.at + by));
    this.scroll.scroll_to_item(this.at);
  }

  toggleAt(at) {
    const row = this.shown[at];
    if (row) toggle(this.entries, row.index);
  }

  apply() {
    if (this.applying || !this.entries?.length) return;
    if (this.problems.length) {
      this.error = "Fix the settings in mod.json first. Nothing was changed.";
      return;
    }
    this.applying = true;
    const state = { entries: this.entries, spaces: spaces(), panes: panes(), home: home(), agent: this.agent };
    this.close();
    const refused = applySelection(state, run);
    if (refused.length) notify({ title: "Project Finder", body: refused.join("\n"), level: "warning" });
  }

  key(e, cx) {
    const k = e.key;
    const control = e.modifiers.control;
    if (k === "escape" || (control && (k === "c" || k === "g"))) return this.close();
    if (k === "tab") {
      this.toggleAt(this.at);
      this.move(e.modifiers.shift ? -1 : 1);
    } else if (control && k === "a") setAll(this.entries ?? [], this.shown.map((r) => r.index), true);
    else if (control && k === "d") setAll(this.entries ?? [], this.shown.map((r) => r.index), false);
    else if (k === "up" || (control && k === "p")) this.move(-1);
    else if (k === "down" || (control && k === "n")) this.move(1);
    else if (k === "pageup") this.move(-10);
    else if (k === "pagedown") this.move(10);
    else if (k === "enter" && !this.inputFocused) this.apply();
    else return;
    cx.stop_propagation();
    cx.notify();
  }

  status(t, entry) {
    if (!entry.status) return div().w(12).flex_none();
    if (entry.status === "working") return div().w(12).flex_none().text_color(t.working).frames(SPINNER, 90);
    return div().w(12).flex_none().text_color(t[entry.status] ?? t.dim).child(entry.status === "idle" ? "○" : "●");
  }

  name(t, entry, marks) {
    const cells = rowCells(entry);
    const hits = new Set(nameMarks(entry, marks));
    const runs = [];
    [...cells.name].forEach((char, at) => {
      const hit = hits.has(at);
      if (runs.length && runs.at(-1).hit === hit) runs.at(-1).text += char;
      else runs.push({ text: char, hit });
    });
    return h_flex()
      .flex_1()
      .min_w(0)
      .overflow_hidden()
      .when(Boolean(cells.repo), (el) => el.child(div().flex_none().text_color(t.dim).child(cells.repo)))
      .children(runs.map((r) => div().flex_none().when(r.hit, (d) => d.text_color(t.accent).font_bold()).child(r.text)));
  }

  row(t, shownRow, at) {
    const entry = this.entries[shownRow.index];
    const isHeld = held(this.entries, shownRow.index);
    const on = at === this.at;
    return h_flex()
      .id(`pf-${at}`)
      .flex_none()
      .h(ROW_HEIGHT)
      .px(10)
      .gap(10)
      .items_center()
      .rounded(6)
      .cursor_pointer()
      .text_color(isHeld ? t.dim : t.foreground)
      .when(on, (el) => el.bg(t.selected))
      .when(!on, (el) => el.hover((s) => s.bg(t.hover)))
      .on_click((_e, cx) => {
        this.at = at;
        this.toggleAt(at);
        cx.notify();
      })
      .child(div().w(14).flex_none().text_color(t.accent).child(entry.selected ? "✓" : ""))
      .child(this.name(t, entry, shownRow.marks))
      .when(isHeld, (el) => el.child(div().w(240).flex_none().text_size(12).text_color(t.dim).child(HELD_NOTE)))
      .when(!isHeld, (el) =>
        el
          .child(div().w(40).flex_none().text_color(entry.kind === "worktree" ? t.accent : t.foreground).child(KIND_CELL[entry.kind]))
          .child(div().w(72).flex_none().text_color(t.dim).child(entry.age))
          .child(h_flex().w(112).flex_none().gap(6).items_center().child(this.status(t, entry)).child(div().text_size(12).text_color(t.muted).child(entry.status ?? ""))),
      );
  }

  render() {
    const t = theme();
    const entries = this.entries ?? [];
    const checked = entries.filter((e) => e.selected).length;
    const heldRows = heldCount(entries);
    const summary = this.entries === undefined
      ? ""
      : `${this.shown.length === entries.length ? entries.length : `${this.shown.length}/${entries.length}`} projects · ${checked} checked${heldRows ? ` · ${heldRows} held` : ""}`;
    const heading = (label, width) => div().when(width !== undefined, (d) => d.w(width).flex_none()).when(width === undefined, (d) => d.flex_1()).child(label);
    return v_flex()
      .id("project-finder")
      .track_focus(this.handle)
      .on_key_down((e, cx) => this.key(e, cx))
      .w_full()
      .min_h(0)
      .p(16)
      .gap(10)
      .bg(t.popover)
      .text_size(13)
      .child(
        h_flex()
          .items_center()
          .gap(8)
          .child(div().flex_1().text_size(15).font_semibold().text_color(t.foreground).child("Projects"))
          .child(div().text_size(11).text_color(t.dim).child(summary))
          .child(div().id("pf-esc").px(6).py(2).rounded(5).text_size(11).text_color(t.dim).cursor_pointer().hover((s) => s.bg(t.hover).text_color(t.foreground)).on_click(() => this.close()).child("Esc")),
      )
      .child(
        Input.new(this.input).h(32).flex_none().px(10).items_center().rounded(6).border(1).border_color(this.inputFocused ? t.accent : t.input).bg(t.background).text_color(t.foreground),
      )
      .when(this.problems.length > 0, (el) => el.children(this.problems.map((p) => div().text_color(t.blocked).child(`Setting: ${p}`))))
      .when(Boolean(this.error), (el) => el.child(div().text_color(t.blocked).child(this.error)))
      .when(this.entries === undefined && !this.error, (el) => el.child(h_flex().gap(6).text_color(t.muted).child(div().frames(SPINNER, 90)).child("Finding projects…")))
      .when(entries.length > 0, (el) =>
        el
          .child(
            h_flex().px(10).gap(10).text_size(11).text_color(t.dim)
              .child(div().w(14).flex_none())
              .child(heading("PROJECT"))
              .child(heading("KIND", 40))
              .child(heading("TOUCHED", 72))
              .child(heading("AGENT STATUS", 112)),
          )
          .child(
            div()
              .relative()
              .h(listHeight(this.shown.length))
              .min_h(0)
              .child(
                v_flex()
                  .id("pf-list")
                  .size_full()
                  .overflow_y_scroll()
                  .track_scroll(this.scroll)
                  .children(this.shown.map((r, at) => this.row(t, r, at)))
                  .when(this.shown.length === 0, (el) => el.child(div().p(10).text_color(t.dim).child("No matching projects."))),
              )
              .child(Scrollbar.vertical("pf-list").absolute().inset_0()),
          ),
      )
      .child(
        v_flex()
          .items_end()
          .text_size(11)
          .text_color(t.dim)
          .child("↵ apply · tab toggle · ctrl-a all · ctrl-d none · esc cancel")
          .child("checked = open · unchecked open projects close on ↵"),
      );
  }
}
