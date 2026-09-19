"""Updates page — check installed revisions against the pinned known-good ones.

Runs ``update.sh --check`` (read-only, no sudo) in a worker thread, parses the
per-component status table it prints, and shows one row per component plus a
prominent banner when updates are available. Applying runs ``update.sh --yes``
through the shared live-log panel.
"""

from __future__ import annotations

import re
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import checks, paths, pins, published  # noqa: E402
from ..runner import run_capture, strip_ansi  # noqa: E402
from ..page_style import build_page  # noqa: E402
from .script_panel import ScriptPanel  # noqa: E402

# "  quickshell   local abc123 | pinned def456 | upstream 789abc"
# Indentation is tolerated (the first line may lose it to .strip()).
_COMPONENT_RE = re.compile(
    r"^\s*(\S+)\s+local\s+(\S+)\s+\|\s+pinned\s+(\S+)\s+\|\s+upstream\s+(\S+)\s*$"
)
# "     -> upstream moved (789abc); run ..."
_MOVED_RE = re.compile(r"upstream moved \(([0-9a-f]+)\)")

COMPONENT_ORDER = ("quickshell", "caelestia", "m3shapes", "cava")
DISPLAY_NAMES = {
    "quickshell": "Quickshell",
    "caelestia": "Caelestia shell",
    "m3shapes": "M3Shapes",
    "cava": "libcava (audio visualiser)",
}


class UpdateStatus:
    def __init__(self, key: str) -> None:
        self.key = key
        self.name = DISPLAY_NAMES.get(key, key)
        self.installed = ""
        self.pinned = ""
        self.upstream = ""
        self.upstream_moved = ""

    def state(self) -> tuple[str, str]:
        """Return (state_key, human text).

        ``installed == pinned`` is the app's definition of up to date: the
        installer deliberately builds exact known-good commits and never
        silently follows moving upstream. When upstream has newer commits we
        say so ("Up to date (pinned)") so it isn't mistaken for an update the
        app can apply.
        """
        if not self.installed or self.installed == "none":
            return "missing", "Not installed"
        if self.pinned and self.pinned != "none":
            if self.installed != self.pinned:
                return "update", "Update available"
            if self.upstream and self.upstream != "none" and self.upstream != self.pinned:
                return "pinned", "Up to date (pinned)"
            return "ok", "Up to date"
        # No pin — this component tracks upstream directly.
        if self.upstream and self.upstream != "none" and self.installed == self.upstream:
            return "ok", "Up to date"
        return "update", "Update available"

    def subtitle(self) -> str:
        def show(v: str) -> str:
            return v if v and v != "none" else "—"
        text = "  ·  ".join([
            f"installed {show(self.installed)}",
            f"pinned {show(self.pinned)}",
            f"upstream {show(self.upstream)}",
        ])
        if self.upstream_moved:
            text += f"  ·  upstream moved ({self.upstream_moved})"
        return text


_ICONS = {
    "ok": ("emblem-ok-symbolic", "success"),
    "pinned": ("view-pin-symbolic", "success"),
    "update": ("software-update-available-symbolic", "warning"),
    "missing": ("dialog-warning-symbolic", "warning"),
    "unknown": ("dialog-question-symbolic", "dim-label"),
}


class UpdatesPage(Adw.Bin):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self._checking = False
        self._published_checking = False
        self._published_pins = None
        self._published_snapshot = None
        self._checked_once = False
        self._has_updates = False
        self._has_upstream_moved = False
        self._runtime_broken_state = False
        self._rows: dict[str, Adw.ActionRow] = {}
        self._status_labels: dict[str, Gtk.Label] = {}
        self._row_icons: dict[str, Gtk.Image] = {}

        box = build_page(self, "UPDATES  /  PINNED & TESTED",
                         "Keep the good things current.",
                         "Update to the project's pinned revisions. Newer upstream builds "
                         "are optional and live in Advanced.")

        published_group = Adw.PreferencesGroup(title="Maintainer-tested revisions")
        self.published_row = Adw.ActionRow(
            title="Published revisions · main", subtitle="Not checked yet", use_markup=False)
        self.published_row.set_subtitle_lines(0)
        self.btn_adopt = Gtk.Button(label="Use published pins", valign=Gtk.Align.CENTER)
        self.btn_adopt.set_sensitive(False)
        self.btn_adopt.connect("clicked", self._on_adopt)
        self.published_row.add_suffix(self.btn_adopt)
        published_group.add(self.published_row)
        box.append(published_group)

        # Prominent "updates available" banner ------------------------------
        self.banner = Adw.Banner.new("")
        self.banner.set_button_label("Apply updates")
        self.banner.connect("button-clicked", self._on_apply)
        self.banner.set_revealed(False)
        box.append(self.banner)

        self.group = Adw.PreferencesGroup.new()
        self.group.set_title("Components")

        self.summary_row = Adw.ActionRow.new()
        self.summary_row.set_title("Status")
        self.summary_row.set_subtitle_lines(0)
        self.summary_row.set_subtitle("Not checked yet")
        self.summary_icon = Gtk.Image.new_from_icon_name(_ICONS["unknown"][0])
        self.summary_icon.set_valign(Gtk.Align.CENTER)
        self.summary_icon.set_css_classes(["dim-label"])
        self.summary_row.add_prefix(self.summary_icon)
        self.group.add(self.summary_row)

        for key in COMPONENT_ORDER:
            row = Adw.ActionRow.new()
            row.set_title(DISPLAY_NAMES.get(key, key))
            row.set_subtitle("Not checked yet")
            row.set_subtitle_lines(0)
            row.set_use_markup(False)
            icon = Gtk.Image.new_from_icon_name(_ICONS["unknown"][0])
            icon.set_valign(Gtk.Align.CENTER)
            icon.set_css_classes(["dim-label"])
            row.add_prefix(icon)
            status = Gtk.Label.new("—")
            status.set_valign(Gtk.Align.CENTER)
            status.set_css_classes(["dim-label"])
            row.add_suffix(status)
            self._rows[key] = row
            self._row_icons[key] = icon
            self._status_labels[key] = status
            self.group.add(row)

        self.row_qt = Adw.ActionRow.new()
        self.row_qt.set_title("Qt toolchain")
        self.row_qt.set_subtitle("Fixed toolchain — bump with setup.sh --qt-version")
        qt_label = Gtk.Label.new("—")
        qt_label.set_valign(Gtk.Align.CENTER)
        qt_label.set_css_classes(["dim-label"])
        self.row_qt.add_suffix(qt_label)
        self._qt_label = qt_label
        self.group.add(self.row_qt)
        box.append(self.group)

        btns = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        btns.set_hexpand(True)
        self.spinner = Gtk.Spinner.new()
        self.btn_check = Gtk.Button.new_with_label("Recheck now")
        self.btn_check.set_tooltip_text("Run update.sh --check again (read-only)")
        self.btn_check.connect("clicked", lambda *_: self.refresh_async(force=True))
        self.btn_apply = Gtk.Button.new_with_label("Apply updates")
        self.btn_apply.set_css_classes(["suggested-action", "page-primary"])
        self.btn_apply.set_sensitive(False)
        self.btn_apply.set_tooltip_text("No updates available — run a check first.")
        self.btn_apply.connect("clicked", self._on_apply)
        btns.append(self.spinner)
        btns.append(self.btn_check)
        self.btn_check.add_css_class("pill")
        btns.append(Gtk.Box(hexpand=True))
        btns.append(self.btn_apply)
        box.append(btns)

        self.checked_label = Gtk.Label.new("")
        self.checked_label.set_xalign(0.0)
        self.checked_label.set_css_classes(["dim-label"])
        box.append(self.checked_label)


        self.raw_expander = Adw.ExpanderRow.new()
        self.raw_expander.set_title("Raw update.sh output")
        self.raw_label = Gtk.Label.new("Run a check to see the raw report.")
        self.raw_label.set_wrap(True)
        self.raw_label.set_xalign(0.0)
        self.raw_label.set_selectable(True)
        self.raw_label.set_css_classes(["dim-label"])
        raw_row = Adw.PreferencesRow.new()
        raw_row.set_activatable(False)
        raw_row.set_child(self.raw_label)
        self.raw_expander.add_row(raw_row)
        report = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        report.add_css_class("boxed-list")
        report.append(self.raw_expander)
        box.append(report)

        self.panel = ScriptPanel(win)
        self.panel.add_css_class("page-panel")
        box.append(self.panel)

    # ------------------------------------------------------------------ hook
    def on_navigate_to(self) -> None:
        # Auto-check only the first time the tab is opened in this app run; the
        # "Recheck now" button forces a fresh network check afterwards.
        if not self._checked_once:
            self.refresh_async(force=True)

    def _set_busy(self, busy: bool) -> None:
        self._checking = busy
        self.btn_check.set_sensitive(not busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        self._refresh_apply_button()

    def _refresh_apply_button(self) -> None:
        """Apply is only actionable when a checked report found updates."""
        idle = (not self._checking and not self._published_checking
                and not self.win.state.get("busy"))
        self.btn_adopt.set_sensitive(bool(self._published_pins) and idle)
        enabled = self._has_updates and idle
        self.btn_apply.set_sensitive(enabled)
        self.btn_apply.set_tooltip_text(
            None if enabled else "No updates available — run a check first.")

    def _check_published_async(self) -> None:
        self._published_checking = True
        self._published_pins = None
        self._published_snapshot = None
        self.published_row.set_subtitle("Checking the project's published revisions…")
        self._refresh_apply_button()

        def worker():
            try:
                destination = pins.revisions_path()
                original = destination.read_bytes()
                mapping = published.fetch()
            except (OSError, ValueError) as exc:
                GLib.idle_add(self._published_result, None, None, str(exc))
            else:
                GLib.idle_add(self._published_result, mapping, (destination, original), "")

        threading.Thread(target=worker, daemon=True).start()

    def _published_result(self, mapping, snapshot, error) -> bool:
        self._published_checking = False
        if error:
            self.published_row.set_subtitle(
                f"Published check unavailable: {error}. Local component results are separate.")
        else:
            local = pins.parse(snapshot[1].decode("utf-8", errors="replace"))
            changed = [key for key in pins.COMPONENTS if local.get(key) != mapping[key]]
            if changed:
                self._published_pins = mapping
                self._published_snapshot = snapshot
                self.published_row.set_subtitle(
                    "Published tested pins differ: " + ", ".join(changed)
                    + ". Adopt explicitly, then Apply updates. Local experimental pins "
                    "may be ahead; this is not a chronological version comparison.")
            else:
                self.published_row.set_subtitle("Local pins match the published tested revisions.")
        self._refresh_apply_button()
        return False

    def _on_adopt(self, _button) -> None:
        if (not self._published_pins or self._checking or self._published_checking
                or self.win.state.get("busy")):
            return
        mapping = dict(self._published_pins)
        snapshot = self._published_snapshot
        dialog = Adw.AlertDialog.new(
            "Use the published tested revisions?",
            "Replaces your local revision pins with the maintainer's published set. "
            "Experimental pins may be newer. Your current revision file is backed up "
            "for Advanced → Revert. No components are built now; use Apply updates "
            "after the next check to rebuild toward these pins.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("use", "Use published pins")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_dialog, choice):
            if choice != "use":
                return
            if self.win.state.get("busy") or self._checking or self._published_checking:
                self.win.toast("Wait for the current operation to finish, then try again.")
                return
            try:
                published.adopt(mapping, *snapshot)
            except (OSError, ValueError) as exc:
                self.win.toast(f"Could not adopt published revisions: {exc}")
                return
            self._has_updates = False
            self.win.toast("Published pins saved. Checking what needs rebuilding…")
            self.refresh_async(force=True)

        dialog.connect("response", response)
        dialog.present(self.win)

    # ----------------------------------------------------------------- check
    def refresh_async(self, force: bool = False) -> None:
        if self._checking or self._published_checking or self.win.state.get("busy"):
            return
        if self._checked_once and not force:
            return
        self._checked_once = True
        self._check_published_async()
        state = checks.installed_state()
        if not state["installed"]:
            self.raw_label.set_text(
                "Caelestia is not installed yet — run the Install tab first.")
            self.checked_label.set_text("")
            self._has_updates = False
            self._set_summary("not_installed")
            self._clear_rows("Not installed")
            self._qt_label.set_text("—")
            self._qt_label.set_css_classes(["dim-label"])
            self._refresh_apply_button()
            return
        self._checked_once = True
        self._set_busy(True)
        self.checked_label.set_text("Checking installed revisions against upstream…")

        def worker() -> None:
            try:
                argv = ["bash", paths.script("update.sh"), "--check"]
            except FileNotFoundError as exc:
                GLib.idle_add(self._apply_result, 127, str(exc))
                return
            code, out = run_capture(argv, timeout=180)
            GLib.idle_add(self._apply_result, code, strip_ansi(out))

        threading.Thread(target=worker, daemon=True).start()

    def _clear_rows(self, subtitle: str) -> None:
        for key, row in self._rows.items():
            row.set_subtitle(subtitle)
            self._status_labels[key].set_text("—")
            self._status_labels[key].set_css_classes(["dim-label"])
            self._set_icon(self._row_icons[key], "unknown")

    def _set_icon(self, icon: Gtk.Image, state_key: str) -> None:
        name, css = _ICONS.get(state_key, _ICONS["unknown"])
        icon.set_from_icon_name(name)
        icon.set_css_classes([css])

    def _set_summary(self, state_key: str, text: str = "") -> None:
        self._set_icon(self.summary_icon, state_key)
        self.summary_row.set_subtitle(text or {
            "ok": "All components are up to date.",
            "update": "Updates are available.",
            "unknown": "Not checked yet.",
            "not_installed": "Caelestia is not installed yet.",
        }.get(state_key, ""))

    def _apply_result(self, code: int, output: str) -> bool:
        self._set_busy(False)
        self.raw_label.set_text(output.strip() or "(no output)")
        self._parse_output(output)

        state = checks.installed_state()
        qt = state.get("qt_version") or "unknown"
        self._qt_label.set_text(f"{qt} installed")
        self._qt_label.set_css_classes(["success"] if code == 0 else ["dim-label"])
        if code != 0:
            self.checked_label.set_text("")
            self._has_updates = False
            self.win.toast("Could not check for updates — see the raw output.")
        else:
            self.checked_label.set_text(
                f"Last checked at {time.strftime('%H:%M:%S')}.")
        self._refresh_apply_button()
        return False

    def _parse_output(self, output: str) -> None:
        found: dict[str, UpdateStatus] = {}
        pending_moved: UpdateStatus | None = None
        for line in output.splitlines():
            m = _COMPONENT_RE.match(line)
            if m:
                key, installed, pinned, upstream = m.groups()
                st = UpdateStatus(key)
                st.installed, st.pinned, st.upstream = installed, pinned, upstream
                found[key] = st
                pending_moved = st
                continue
            moved = _MOVED_RE.search(line)
            if moved and pending_moved is not None:
                pending_moved.upstream_moved = moved.group(1)

        if not found:
            for key, row in self._rows.items():
                row.set_subtitle("Unavailable (offline or not installed)")
                self._status_labels[key].set_text("—")
                self._status_labels[key].set_css_classes(["dim-label"])
                self._set_icon(self._row_icons[key], "unknown")
            self._set_summary("unknown", "Could not read a report — are you online?")
            self.raw_expander.set_subtitle("could not reach upstream components")
            self._has_updates = False
            self._has_upstream_moved = False
            self._refresh_apply_button()
            return

        self.raw_expander.set_subtitle("")
        to_update: list[str] = []
        missing: list[str] = []
        pinned_behind: list[str] = []
        for key, row in self._rows.items():
            st = found.get(key)
            label = self._status_labels[key]
            if st is None:
                row.set_subtitle("Unavailable (offline or not installed)")
                label.set_text("—")
                label.set_css_classes(["dim-label"])
                self._set_icon(self._row_icons[key], "unknown")
                continue
            state_key, text = st.state()
            row.set_subtitle(st.subtitle())
            label.set_text(text)
            label.set_css_classes([
                "success" if state_key in ("ok", "pinned")
                else "warning" if state_key in ("update", "missing")
                else "dim-label"
            ])
            self._set_icon(self._row_icons[key], state_key)
            if state_key == "update":
                to_update.append(st.name)
            elif state_key == "missing":
                missing.append(st.name)
            elif state_key == "pinned":
                pinned_behind.append(st.name)

        if to_update:
            self.banner.set_title(
                f"{len(to_update)} component"
                f"{'s' if len(to_update) != 1 else ''} can be updated: "
                + ", ".join(to_update))
            self.banner.set_revealed(True)
            self._set_summary("update",
                              f"{len(to_update)} update(s) available.")
        else:
            self.banner.set_revealed(False)
            if missing:
                self._set_summary(
                    "update", f"Not installed: {', '.join(missing)}.")
            elif pinned_behind:
                self._set_summary(
                    "pinned",
                    f"All pinned revisions installed. {len(pinned_behind)} "
                    f"component{'s' if len(pinned_behind) != 1 else ''} "
                    f"({', '.join(pinned_behind)}) have newer upstream commits "
                    "— pinning is intentional. Use Advanced to try newer builds.")
            else:
                self._set_summary("ok")

        self._has_updates = bool(to_update or missing)
        self._has_upstream_moved = bool(pinned_behind)
        self._refresh_apply_button()

    # ----------------------------------------------------------------- apply
    def _on_apply(self, _widget) -> None:
        if not self._has_updates:
            self.win.toast("No updates available — run a check first.")
            return
        if (self.win.state.get("busy") or self._checking
                or self._published_checking):
            self.win.toast("Wait for the current operation to finish.")
            return
        dialog = Adw.AlertDialog.new(
            "Apply updates?",
            "Rebuilds any component whose installed revision differs from the "
            "pinned one. An internet connection is required; Qt is reused.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Update")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        def on_response(_d, response: str) -> None:
            if response != "ok":
                return
            self.win.ensure_password(self._start_apply)

        dialog.connect("response", on_response)
        dialog.present(self.win)

    def _start_apply(self) -> None:
        try:
            argv = ["bash", paths.script("update.sh"), "--yes"]
        except FileNotFoundError as exc:
            self.win.toast(f"Cannot find update.sh: {exc}")
            return
        self.panel.start(
            argv, title="update.sh", password=self.win.state.get("password"),
            done_note="Updates applied.",
            on_finished=self._apply_finished,
        )

    def _apply_finished(self, _code: int) -> None:
        self.refresh_async(force=True)
