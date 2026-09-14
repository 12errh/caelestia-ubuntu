"""Updates page — check installed revisions against the pinned known-good ones.

Runs ``update.sh --check`` (read-only, no sudo) in a worker thread, parses the
per-component status table it prints, and shows one row per component plus a
prominent banner when updates are available. Applying runs ``update.sh --yes``
through the shared live-log panel.
"""

from __future__ import annotations

import os
import re
import shlex
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import checks, paths, pins  # noqa: E402
from ..runner import run_capture, strip_ansi  # noqa: E402
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
        self._checked_once = False
        self._has_updates = False
        self._has_upstream_moved = False
        self._runtime_broken_state = False
        self._rows: dict[str, Adw.ActionRow] = {}
        self._status_labels: dict[str, Gtk.Label] = {}
        self._row_icons: dict[str, Gtk.Image] = {}

        sc = Gtk.ScrolledWindow.new()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_vexpand(True)
        clamp = Adw.Clamp.new()
        clamp.set_maximum_size(820)
        box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 18)
        box.set_margin_top(18)
        box.set_margin_bottom(24)
        box.set_margin_start(18)
        box.set_margin_end(18)
        clamp.set_child(box)
        sc.set_child(clamp)
        self.set_child(sc)

        intro = Gtk.Label.new(
            "installed = what your machine built  ·  pinned = the known-good "
            "commit in <tt>revisions.conf</tt>  ·  upstream = latest upstream.\n"
            "The installer never silently follows upstream: <i>Up to date</i> "
            "means it matches the pinned revision. Apply rebuilds to the pin; "
            "moving to newer upstream is a deliberate action.")
        intro.set_use_markup(True)
        intro.set_wrap(True)
        intro.set_xalign(0.0)
        intro.set_css_classes(["dim-label"])
        box.append(intro)

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
        btns.set_halign(Gtk.Align.START)
        self.spinner = Gtk.Spinner.new()
        self.btn_check = Gtk.Button.new_with_label("Recheck now")
        self.btn_check.set_tooltip_text("Run update.sh --check again (read-only)")
        self.btn_check.connect("clicked", lambda *_: self.refresh_async(force=True))
        self.btn_apply = Gtk.Button.new_with_label("Apply updates")
        self.btn_apply.set_css_classes(["suggested-action"])
        self.btn_apply.set_sensitive(False)
        self.btn_apply.set_tooltip_text("No updates available — run a check first.")
        self.btn_apply.connect("clicked", self._on_apply)
        btns.append(self.spinner)
        btns.append(self.btn_check)
        btns.append(self.btn_apply)
        box.append(btns)

        self.checked_label = Gtk.Label.new("")
        self.checked_label.set_xalign(0.0)
        self.checked_label.set_css_classes(["dim-label"])
        box.append(self.checked_label)

        self._build_advanced(box)

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
        box.append(self.raw_expander)

        self.panel = ScriptPanel(win)
        box.append(self.panel)

    # ------------------------------------------------------------------ hook
    def on_navigate_to(self) -> None:
        self._refresh_advanced()
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
        idle = not self._checking and not self.win.state.get("busy")
        enabled = self._has_updates and idle
        self.btn_apply.set_sensitive(enabled)
        self.btn_apply.set_tooltip_text(
            None if enabled else "No updates available — run a check first.")
        self._refresh_advanced_buttons()

    # ---------------------------------------------------------- advanced UI
    def _build_advanced(self, parent: Gtk.Box) -> None:
        group = Adw.PreferencesGroup.new()
        group.set_title("Advanced — latest upstream")
        group.set_description(
            "Installed revisions match the pin, so there is normally nothing to "
            "apply here. This jumps every component to the newest upstream "
            "commit, rebuilds it, and lets you keep it as your known-good pin "
            "after testing — or revert. It rebuilds sources only: your configs "
            "and shell.json are never overwritten (shell.json is snapshotted "
            "first).")

        self.adv_state_row = Adw.ActionRow.new()
        self.adv_state_row.set_title("Revision state")
        self.adv_state_row.set_subtitle("Checking…")
        self.adv_state_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        self.adv_state_icon.set_valign(Gtk.Align.CENTER)
        self.adv_state_icon.set_css_classes(["dim-label"])
        self.adv_state_row.add_prefix(self.adv_state_icon)
        group.add(self.adv_state_row)

        self.adv_pins_row = Adw.ActionRow.new()
        self.adv_pins_row.set_title("Pins")
        self.adv_pins_row.set_subtitle("—")
        group.add(self.adv_pins_row)

        self.adv_repo_row = Adw.ActionRow.new()
        self.adv_repo_row.set_title("Revision file")
        self.adv_repo_row.set_subtitle("—")
        group.add(self.adv_repo_row)

        self.adv_runtime_row = Adw.ActionRow.new()
        self.adv_runtime_row.set_title("Quickshell runtime")
        self.adv_runtime_row.set_subtitle("Checking…")
        self.btn_repair = Gtk.Button.new_with_label("Repair")
        self.btn_repair.set_valign(Gtk.Align.CENTER)
        self.btn_repair.set_css_classes(["flat"])
        self.btn_repair.set_sensitive(False)
        self.btn_repair.set_tooltip_text(
            "Re-apply the Qt RPATH to /usr/local/bin/quickshell so `qs` runs "
            "without LD_LIBRARY_PATH (needed for update checks and IPC).")
        self.btn_repair.connect("clicked", self._on_repair_runtime)
        self.adv_runtime_row.add_suffix(self.btn_repair)
        group.add(self.adv_runtime_row)
        parent.append(group)

        btns = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        btns.set_halign(Gtk.Align.START)

        self.btn_upstream = Gtk.Button.new_with_label("Update to latest upstream")
        self.btn_upstream.set_css_classes(["destructive-action"])
        self.btn_upstream.connect("clicked", self._on_update_upstream)
        self.btn_keep = Gtk.Button.new_with_label("Mark tested & keep")
        self.btn_keep.connect("clicked", self._on_keep_tested)
        self.btn_revert = Gtk.Button.new_with_label("Revert to previous")
        self.btn_revert.connect("clicked", self._on_revert)
        btns.append(self.btn_upstream)
        btns.append(self.btn_keep)
        btns.append(self.btn_revert)
        parent.append(btns)

        self.adv_hint = Gtk.Label.new(
            "Test a new build by logging into Hyprland; if the shell looks "
            "right, Keep — otherwise Revert.")
        self.adv_hint.set_wrap(True)
        self.adv_hint.set_xalign(0.0)
        self.adv_hint.set_css_classes(["dim-label"])
        parent.append(self.adv_hint)

    def _refresh_advanced(self) -> None:
        try:
            pins.seed_if_needed()
            cur = pins.current()
            known = pins.known_good()
        except FileNotFoundError:
            cur, known = {}, {}
        prev = pins.previous()
        inst = pins.installed_revisions()
        self._pins_cur, self._pins_known = cur, known
        self._pins_prev, self._pins_inst = prev, inst

        self.adv_pins_row.set_subtitle(
            f"installed: {pins.short(inst)}\n"
            f"pinned: {pins.short(cur)}\n"
            f"known-good: {pins.short(known)}\n"
            f"previous: {pins.short(prev)}")
        self.adv_pins_row.set_subtitle_lines(0)

        try:
            self.adv_repo_row.set_subtitle(str(pins.revisions_path()))
        except FileNotFoundError:
            self.adv_repo_row.set_subtitle("repository not found")

        broken = self._runtime_broken()
        self._runtime_broken_state = broken
        installed = bool(self.win.state.get("installed"))
        if not installed:
            self.adv_runtime_row.set_subtitle("Not installed")
        elif broken:
            self.adv_runtime_row.set_subtitle(
                "qs does not run without LD_LIBRARY_PATH — click Repair.")
        else:
            self.adv_runtime_row.set_subtitle("ok — qs runs standalone.")

        if not inst:
            state, css = "No revisions found — is the repository present?", "dim-label"
        elif inst != known:
            state, css = ("Installed revisions differ from your known-good pins — "
                          "test in Hyprland, then Keep to record them or Revert.",
                          "warning")
        else:
            state, css = ("Stable — installed revisions match your known-good "
                          "pins."), "success"
        self.adv_state_row.set_subtitle(state)
        self.adv_state_row.set_subtitle_lines(0)
        self.adv_state_icon.set_css_classes([css])
        self._refresh_advanced_buttons()

    def _refresh_advanced_buttons(self) -> None:
        idle = not self._checking and not self.win.state.get("busy")
        installed = bool(self.win.state.get("installed"))
        known = getattr(self, "_pins_known", {})
        prev = getattr(self, "_pins_prev", {})
        inst = getattr(self, "_pins_inst", {})
        writable = pins.repo_writable()

        self.btn_upstream.set_sensitive(idle and installed and writable)
        self.btn_upstream.set_tooltip_text(
            None if (installed and writable) else
            ("Install Caelestia first." if not installed else
             "revisions.conf is not writable (system install) — run from a clone "
             "or install with app/install.sh --user."))
        # Keep records the *installed* revisions as the pins, so it is offered
        # whenever they differ from the known-good set.
        self.btn_keep.set_sensitive(idle and bool(inst) and inst != known)
        self.btn_revert.set_sensitive(
            idle and bool(inst) and bool(prev) and prev != inst)
        self.btn_repair.set_sensitive(
            idle and installed and self._runtime_broken_state)

    def _runtime_broken(self) -> bool:
        """True when the installed qs cannot run without LD_LIBRARY_PATH."""
        if not self.win.state.get("installed"):
            return False
        qs = str(paths.QS_BIN)
        if not os.path.exists(qs):
            return False
        code, _out = run_capture(
            ["env", "-u", "LD_LIBRARY_PATH", qs, "--version"], timeout=15)
        return code != 0

    # ----------------------------------------------------------------- check
    def refresh_async(self, force: bool = False) -> None:
        if self._checking or self.win.state.get("busy"):
            return
        if self._checked_once and not force:
            return
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
        self._refresh_advanced()
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
                    "— pinning is intentional; use update.sh --update-sources to "
                    "move to them.")
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
        if self.win.state.get("busy"):
            self.win.toast("A script is already running — wait for it to finish.")
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

    def _on_update_upstream(self, _b: Gtk.Button) -> None:
        if self.win.state.get("busy"):
            self.win.toast("A script is already running — wait for it to finish.")
            return
        dialog = Adw.AlertDialog.new(
            "Update to the latest upstream commits?",
            "This fetches the newest upstream commit of every component, "
            "rewrites revisions.conf, and rebuilds them. It deliberately jumps "
            "ahead of the known-good pins, so it may break — you can Revert "
            "afterwards. Your configs and shell.json are not touched (a copy is "
            "saved first). Rebuilding can take a while.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Update & build")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response: str) -> None:
            if response != "ok":
                return
            self.win.ensure_password(self._start_update_upstream, force=True)

        dialog.connect("response", on_response)
        dialog.present(self.win)

    def _start_update_upstream(self) -> None:
        try:
            argv = ["bash", paths.script("update.sh"), "--update-sources", "--yes"]
        except FileNotFoundError as exc:
            self.win.toast(f"Cannot find update.sh: {exc}")
            return
        # Save the one-step undo target and a copy of the user's settings.
        pins.snapshot_previous()
        backup = pins.backup_shell_json()
        if backup is not None:
            self.win.toast(f"Saved settings backup: {backup.name}")
        started = self.panel.start(
            argv, title="update.sh --update-sources",
            password=self.win.state.get("password"),
            done_note="Latest upstream built — test in Hyprland, then Keep or Revert.",
            on_finished=self._upstream_finished,
        )
        if started:
            self._refresh_advanced()

    def _upstream_finished(self, code: int) -> None:
        self._refresh_advanced()
        self.refresh_async(force=True)
        if code == 0:
            self.win.toast("Latest upstream built. Test it, then Keep or Revert.")
        else:
            self.win.toast("Upstream update failed — use Revert to go back.")

    # ---------------------------------------------------------- keep / revert
    def _on_keep_tested(self, _b: Gtk.Button) -> None:
        if self.win.state.get("busy"):
            self.win.toast("A script is already running — wait for it to finish.")
            return
        if pins.mark_tested_installed():
            self.win.toast("Tested revisions recorded as the known-good pins.")
        else:
            self.win.toast("Could not record the pins (is revisions.conf writable?).")
        self._refresh_advanced()
        self.refresh_async(force=True)

    def _on_revert(self, _b: Gtk.Button) -> None:
        if self.win.state.get("busy"):
            self.win.toast("A script is already running — wait for it to finish.")
            return
        dialog = Adw.AlertDialog.new(
            "Revert to the previous pins?",
            "Restores the revisions.conf snapshot taken before the last change "
            "and rebuilds those components. Your configs and shell.json are not "
            "touched.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Revert & rebuild")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response: str) -> None:
            if response != "ok":
                return
            self.win.ensure_password(self._start_revert, force=True)

        dialog.connect("response", on_response)
        dialog.present(self.win)

    def _on_repair_runtime(self, _b: Gtk.Button) -> None:
        if self.win.state.get("busy"):
            self.win.toast("A script is already running — wait for it to finish.")
            return
        self.win.ensure_password(self._start_repair_runtime, force=True)

    def _start_repair_runtime(self) -> None:
        manifest = checks.read_manifest()
        qt = manifest.get("QT_PREFIX") or "/opt/qt611/6.11.2/gcc_64"
        rpath = f"{qt}/lib:$ORIGIN:$ORIGIN/../lib"
        qs = os.path.realpath(str(paths.QS_BIN))
        tmp = os.path.join(os.path.dirname(qs), ".quickshell.rpath-new")
        # The shell runs qs, so the file is "Text file busy" for in-place edits.
        # Stage a patched copy and atomically rename it over the target instead;
        # the running shell keeps its old inode until the service restarts.
        script = (
            "set -e\n"
            f"QS={shlex.quote(qs)}\n"
            f"TMP={shlex.quote(tmp)}\n"
            f"RPATH={shlex.quote(rpath)}\n"
            'echo "==> staging a patched copy of quickshell"\n'
            'sudo cp -f "$QS" "$TMP"\n'
            'sudo patchelf --force-rpath --set-rpath "$RPATH" "$TMP"\n'
            'sudo chmod 755 "$TMP"\n'
            'sudo mv -f "$TMP" "$QS"\n'
            'echo "==> rpath now: $(patchelf --print-rpath "$QS")"\n'
            'env -u LD_LIBRARY_PATH "$QS" --version\n'
            'echo "==> restarting caelestia-shell"\n'
            'systemctl --user try-restart caelestia-shell.service 2>/dev/null || true\n'
        )
        self.panel.start(
            ["bash", "-c", script], title="repair quickshell runtime",
            password=self.win.state.get("password"),
            done_note="Quickshell runtime repaired.",
            on_finished=self._repair_finished,
        )

    def _repair_finished(self, code: int) -> None:
        self._refresh_advanced()
        if code == 0 and not self._runtime_broken():
            self.win.toast("Quickshell runtime repaired.")
        else:
            self.win.toast("Repair did not fix the runtime — see the log.")

    def _start_revert(self) -> None:
        if not pins.restore_previous():
            self.win.toast("Nothing to revert to, or revisions.conf is not writable.")
            self._refresh_advanced()
            return
        try:
            argv = ["bash", paths.script("update.sh"), "--yes"]
        except FileNotFoundError as exc:
            self.win.toast(f"Cannot find update.sh: {exc}")
            return
        self.panel.start(
            argv, title="update.sh (revert)", password=self.win.state.get("password"),
            done_note="Reverted to the previous pins.",
            on_finished=self._revert_finished,
        )

    def _revert_finished(self, code: int) -> None:
        self._refresh_advanced()
        self.refresh_async(force=True)
        self.win.toast("Reverted and rebuilt." if code == 0
                       else "Revert rebuild failed — see the log.")

    def _apply_finished(self, _code: int) -> None:
        self._refresh_advanced()
        self.refresh_async(force=True)
