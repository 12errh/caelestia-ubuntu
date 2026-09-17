"""Advanced controls: install overrides, upstream builds and recovery."""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import checks, paths, pins  # noqa: E402
from ..page_style import build_page  # noqa: E402
from ..runner import run_capture  # noqa: E402
from .advanced_actions import AdvancedActions  # noqa: E402
from .script_panel import ScriptPanel  # noqa: E402

# Normal rebuilds and read-only update reports belong exclusively to Updates.
ACTIONS = [
    ("Uninstall Caelestia", ["uninstall.sh"],
     "Stops the shell service, removes binaries/QML modules and archives your "
     "configs. GNOME is never touched."),
    ("Uninstall and remove Qt", ["uninstall.sh", "--purge-qt"],
     "Removes Caelestia and the /opt Qt toolchain (about 2 GB). Configs are "
     "archived. Only continue if no other build needs that Qt."),
]


class AdvancedPage(Adw.Bin, AdvancedActions):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self._checking = False
        self._refresh_again = False
        self._runtime_broken_state = False
        self._runtime_checked = False
        self._repair_result = None
        self._pins_cur = {}
        self._pins_known = {}
        self._pins_prev = {}
        self._pins_inst = {}
        self._repo_writable = False
        self._action_buttons = []
        box = build_page(
            self, "ADVANCED  /  TOOLS & RECOVERY", "A little more control.",
            "Expert options, experimental builds and recovery tools. "
            "For everyday updates, use Updates; for desktop settings, use Setup.")
        self._build_install_options(box)
        self._build_shell_tools(box)
        self._build_advanced(box)
        self._build_uninstall(box)
        self.panel = ScriptPanel(win)
        self.panel.add_css_class("page-panel")
        box.append(self.panel)
        self._refresh_advanced_buttons()

    @staticmethod
    def _group(parent, title, description=""):
        group = Adw.PreferencesGroup(title=title, description=description)
        group.add_css_class("page-panel")
        parent.append(group)
        return group

    def _action_row(self, group, title, subtitle, label, callback, *, danger=False):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        row.set_use_markup(False)
        row.set_title_lines(0)
        row.set_subtitle_lines(0)
        button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
        if danger:
            button.add_css_class("destructive-action")
        button.connect("clicked", callback)
        row.add_suffix(button)
        group.add(row)
        return row, button

    def _build_install_options(self, parent):
        group = self._group(parent, "Installation overrides",
                            "Used the next time you start the Install wizard.")
        self.row_qtver = Adw.EntryRow(title="Qt version (default 6.11.2)", text="6.11.2")
        self.row_ignore_space = Adw.SwitchRow(
            title="Skip the script's 8 GB disk check",
            subtitle="Only when build space is available elsewhere. "
                     "This does not bypass the system checks on step 1.")
        self.row_ignore_space.set_subtitle_lines(0)
        group.add(self.row_qtver)
        group.add(self.row_ignore_space)

    def _build_shell_tools(self, parent):
        group = self._group(parent, "Shell tools")
        _, self.btn_open_json = self._action_row(
            group, "Edit shell.json", str(paths.SHELL_JSON), "Open", self._open_shell_json)
        _, self.btn_restart = self._action_row(
            group, "Restart the shell", "Restarts the user service without logging out.",
            "Restart", self._restart_shell)
        self._action_buttons.extend((self.btn_open_json, self.btn_restart))


    def _build_advanced(self, parent: Gtk.Box) -> None:
        group = Adw.PreferencesGroup.new()
        group.add_css_class("page-panel")
        group.set_title("Experimental builds")
        group.set_description(
            "Build from the newest upstream commits, then test in Hyprland "
            "before keeping them as known-good. Revert restores your previous pins. "
            "Your settings are backed up before rebuilding.")

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
        self.adv_repo_row.set_use_markup(False)
        self.adv_repo_row.set_subtitle_lines(0)
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

        for title, subtitle, label, callback, attr in (
            ("Latest upstream", "May be untested. Snapshot pins before rebuilding.",
             "Build", self._on_update_upstream, "btn_upstream"),
            ("Keep tested revisions", "Record installed revisions after testing in Hyprland.",
             "Keep", self._on_keep_tested, "btn_keep"),
            ("Previous revisions", "Restore the previous pins and rebuild.",
             "Revert", self._on_revert, "btn_revert"),
        ):
            _, button = self._action_row(group, title, subtitle, label, callback,
                                         danger=attr != "btn_keep")
            setattr(self, attr, button)

        self.adv_hint = Gtk.Label.new(
            "Test a new build by logging into Hyprland; if the shell looks "
            "right, Keep — otherwise Revert.")
        self.adv_hint.set_wrap(True)
        self.adv_hint.set_xalign(0.0)
        self.adv_hint.set_css_classes(["dim-label"])
        parent.append(self.adv_hint)

    def _build_uninstall(self, parent):
        group = self._group(parent, "Remove Caelestia",
                            "Destructive actions always ask for confirmation first.")
        for title, args, description in ACTIONS:
            _, button = self._action_row(
                group, title, description, "Remove",
                lambda _b, t=title, a=args, d=description: self._confirm_remove(t, a, d),
                danger=True)
            self._action_buttons.append(button)

    def _confirm_remove(self, title, args, description):
        if self.win.state.get("busy"):
            self.win.toast("Wait for the current operation to finish.")
            return
        dialog = Adw.AlertDialog.new(title + "?", description)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def respond(_dialog, response):
            if response == "remove":
                self.win.ensure_password(lambda: self._remove(args), force=True)

        dialog.connect("response", respond)
        dialog.present(self.win)

    def _remove(self, args):
        try:
            argv = ["bash", paths.script(args[0]), *args[1:]]
        except FileNotFoundError as exc:
            self.win.toast(str(exc))
            return
        self.panel.start(argv, title=" ".join(args),
                         password=self.win.state.get("password"),
                         done_note="Finished — see the log above.",
                         on_finished=lambda _code: self._refresh_advanced())
        self._refresh_advanced_buttons()

    def _open_shell_json(self, _button):
        if self.win.state.get("busy"):
            return
        try:
            paths.SHELL_JSON.parent.mkdir(parents=True, exist_ok=True)
            if not paths.SHELL_JSON.exists():
                paths.SHELL_JSON.write_text("{}\n")
        except OSError as exc:
            self.win.toast(f"Could not open shell.json: {exc}")
            return
        # Opening an external editor must not block the GTK event loop.
        def worker():
            code, _ = run_capture(["xdg-open", str(paths.SHELL_JSON)], timeout=10)
            if code:
                GLib.idle_add(self.win.toast, "Could not open an editor for shell.json")
        threading.Thread(target=worker, daemon=True).start()

    def _restart_shell(self, _button):
        self.panel.start(
            ["systemctl", "--user", "restart", paths.SHELL_SERVICE],
            title="Restart shell service", done_note="Shell restart finished.",
            on_finished=lambda _code: self._refresh_advanced())
        self._refresh_advanced_buttons()

    def _refresh_updates(self):
        self.win.page_updates.refresh_async(force=True)

    def on_navigate_to(self):
        self._refresh_advanced()

    def _runtime_broken(self):
        if not os.path.exists(paths.QS_BIN):
            return True
        code, _ = run_capture(
            ["env", "-u", "LD_LIBRARY_PATH", str(paths.QS_BIN), "--version"], timeout=15)
        return code != 0

    def _refresh_advanced(self):
        if self._checking:
            self._refresh_again = True
            return
        if self.win.state.get("busy"):
            self._refresh_advanced_buttons()
            return
        self._checking = True
        self._refresh_advanced_buttons()

        def worker():
            try:
                state = checks.installed_state()
                cur = pins.current()
                known = pins.known_good() or cur
                prev = pins.previous()
                inst = pins.installed_revisions() if state["installed"] else {}
                writable = pins.repo_writable()
                repo = str(pins.revisions_path())
                broken = self._runtime_broken() if state["installed"] else False
                result = (state, cur, known, prev, inst, writable, repo, broken)
                GLib.idle_add(self._apply_advanced, result, None)
            except Exception as exc:
                GLib.idle_add(self._apply_advanced, None, str(exc))
        threading.Thread(target=worker, daemon=True).start()



    def _apply_advanced(self, result, error):
        self._checking = False
        self._runtime_checked = error is None
        if error:
            self.adv_state_row.set_subtitle(f"Could not check: {error}")
            self.adv_runtime_row.set_subtitle("Could not check runtime")
            self._repo_writable = False
        else:
            state, cur, known, prev, inst, writable, repo, broken = result
            self.win.state["installed"] = state["installed"]
            self._pins_cur, self._pins_known = cur, known
            self._pins_prev, self._pins_inst = prev, inst
            self._repo_writable = writable
            self._runtime_broken_state = broken
            self.adv_pins_row.set_subtitle(
                f"Installed: {pins.short(inst)}\nPinned: {pins.short(cur)}\n"
                f"Known-good: {pins.short(known)}\nPrevious: {pins.short(prev)}")
            self.adv_repo_row.set_subtitle(repo)
            self.adv_runtime_row.set_subtitle(
                "Not installed" if not state["installed"] else
                "Runtime needs repair — see Repair." if broken else "Runs standalone.")
            different = bool(inst) and inst != known
            self.adv_state_row.set_subtitle(
                "Install Caelestia first." if not inst else
                "Test this build in Hyprland, then Keep or Revert." if different else
                "Installed revisions match your known-good pins.")
            self.adv_state_icon.set_css_classes(["warning" if different else "dim-label"])
            if self._repair_result is not None:
                self.win.toast("Quickshell runtime repaired." if self._repair_result == 0
                               and not broken else "Repair did not fix the runtime — see the log.")
                self._repair_result = None
        self._refresh_advanced_buttons()
        if self._refresh_again:
            self._refresh_again = False
            self._refresh_advanced()
        return GLib.SOURCE_REMOVE

    def _refresh_advanced_buttons(self):
        idle = not self._checking and not self.win.state.get("busy")
        installed = bool(self.win.state.get("installed"))
        writable = self._repo_writable and self._runtime_checked
        self.btn_upstream.set_sensitive(idle and installed and writable)
        self.btn_keep.set_sensitive(idle and writable and bool(self._pins_inst)
                                    and self._pins_inst != self._pins_known)
        self.btn_revert.set_sensitive(idle and writable and bool(self._pins_inst)
                                      and bool(self._pins_prev)
                                      and self._pins_prev != self._pins_inst)
        self.btn_repair.set_sensitive(idle and installed and self._runtime_checked
                                      and self._runtime_broken_state)
        for button in self._action_buttons:
            button.set_sensitive(idle)
        self.row_qtver.set_sensitive(idle)
        self.row_ignore_space.set_sensitive(idle)
        self.btn_upstream.set_tooltip_text(
            None if installed and writable else
            "Install first and use a writable repository to change revisions.")
