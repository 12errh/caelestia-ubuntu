"""Setup panel: status, wallpaper picker, starter settings, maintenance.

Settings are written straight into ~/.config/caelestia/shell.json (the same
file the shell reads live) using JSON merge semantics — nested keys we don't
touch are preserved. While the form is being populated from disk the change
handlers are muted so visiting the tab never rewrites the user's settings.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import checks, paths  # noqa: E402
from ..runner import run_capture  # noqa: E402
from .script_panel import ScriptPanel  # noqa: E402


def _load_shell_json() -> dict:
    try:
        return json.loads(paths.SHELL_JSON.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_shell_json(data: dict) -> None:
    paths.SHELL_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.SHELL_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=4))
    tmp.replace(paths.SHELL_JSON)


def _merge_into_shell_json(path: list[str], value) -> None:
    data = _load_shell_json()
    node = data
    for key in path[:-1]:
        child = node.setdefault(key, {})
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[path[-1]] = value
    _save_shell_json(data)


class SetupPage(Adw.Bin):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self._loading = False
        self._wall_state = ""

        sc = Gtk.ScrolledWindow.new()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_vexpand(True)
        clamp = Adw.Clamp.new()
        clamp.set_maximum_size(760)
        box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 18)
        box.set_margin_top(18)
        box.set_margin_bottom(24)
        box.set_margin_start(18)
        box.set_margin_end(18)
        clamp.set_child(box)
        sc.set_child(clamp)
        self.set_child(sc)

        self._build_status(box)
        self._build_wallpapers(box)
        self._build_settings(box)
        self._build_maintenance(box)

        self.panel = ScriptPanel(win)
        box.append(self.panel)

    # ------------------------------------------------------------------ status
    def _build_status(self, parent: Gtk.Box) -> None:
        group = Adw.PreferencesGroup.new()
        group.set_title("Status")
        self.row_service = Adw.ActionRow.new()
        self.row_service.set_title("Caelestia shell service")
        group.add(self.row_service)

        self.row_session = Adw.ActionRow.new()
        self.row_session.set_title("Hyprland session")
        group.add(self.row_session)
        parent.append(group)

    def refresh_status(self) -> None:
        state = checks.installed_state()
        active = checks.service_active()
        if not state["installed"]:
            self.row_service.set_title("Not installed")
            self.row_service.set_subtitle(
                "Run the Install tab first — everything here activates afterwards.")
            self.row_session.set_title("—")
            return
        svc = {True: "running", False: "stopped", None: "unknown (not in session)"}[active]
        self.row_service.set_title(f"Shell service: {svc}")
        self.row_service.set_subtitle(
            "systemctl --user status caelestia-shell — restarts itself if it crashes.")
        self.row_session.set_title(
            "Registered at the login screen (gear menu → Hyprland)"
            if state["session_registered"] else
            "Session entry missing — re-run the install to register it")
        self._wall_state = state.get("wallpaper") or ""

    # -------------------------------------------------------------- wallpapers
    def _build_wallpapers(self, parent: Gtk.Box) -> None:
        group = Adw.PreferencesGroup.new()
        group.set_title("Wallpaper")
        group.set_description(
            "The whole interface recolours itself from the wallpaper. "
            "Pictures from ~/Pictures/wallpapers are shown here.")

        self.flow = Gtk.FlowBox.new()
        self.flow.set_min_children_per_line(4)
        self.flow.set_max_children_per_line(5)
        self.flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.flow.set_homogeneous(True)
        self.flow.connect("child-activated", self._on_wallpaper_activated)
        self.flow.set_valign(Gtk.Align.START)

        self._wall_paths: list[Path] = []
        flow_scroll = Gtk.ScrolledWindow.new()
        flow_scroll.set_child(self.flow)
        flow_scroll.set_min_content_height(200)
        flow_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        group.add(flow_scroll)
        parent.append(group)

        self.btn_wall_more = Gtk.Button.new_with_label("Add image…")
        self.btn_wall_more.set_css_classes(["flat"])
        self.btn_wall_more.connect("clicked", self._pick_wallpaper)
        group.add(self.btn_wall_more)

    def refresh_wallpapers(self) -> None:
        while self.flow.get_child_at_index(0):
            self.flow.remove(self.flow.get_child_at_index(0))
        self._wall_paths = []
        seen: set[Path] = set()
        for d in paths.WALLPAPER_DIRS:
            if not d.is_dir():
                continue
            for p in sorted(d.iterdir()):
                if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and p not in seen:
                    seen.add(p)
                    self._wall_paths.append(p)
                    self.flow.append(self._wall_tile(p))
        current = self._wall_state or ""
        for i, p in enumerate(self._wall_paths):
            if str(p) == current:
                self.flow.select_child(self.flow.get_child_at_index(i))
                break

    def _wall_tile(self, path: Path) -> Gtk.Widget:
        btn = Gtk.Button.new()
        pic = Gtk.Picture.new()
        pic.set_content_fit(Gtk.ContentFit.COVER)
        try:
            pic.set_filename(str(path))
        except Exception:  # noqa: BLE001
            pass
        pic.set_size_request(120, 78)
        btn.set_child(pic)
        btn.set_tooltip_text(f"Apply wallpaper: {path.name}")
        # The tile is a Button inside a FlowBoxChild; a FlowBox "child-activated"
        # signal does NOT fire when the inner button takes the click, so connect
        # the button directly (keyboard activation still works via the button).
        btn.connect("clicked", lambda *_: self._apply_wallpaper(path))
        return btn

    def _on_wallpaper_activated(self, _flow: Gtk.FlowBox, child: Gtk.FlowBoxChild) -> None:
        idx = child.get_index()
        if idx >= len(self._wall_paths):
            return
        self._apply_wallpaper(self._wall_paths[idx])

    def _apply_wallpaper(self, path: Path) -> None:
        # Mirror what setup.sh does: state file + symlink; live-apply via CLI
        # when the shell/CLI is available, otherwise it applies on next login.
        state = paths.WALLPAPER_STATE
        state.mkdir(parents=True, exist_ok=True)
        (state / "path.txt").write_text(str(path) + "\n")
        try:
            (state / "current").unlink()
        except OSError:
            pass
        try:
            (state / "current").symlink_to(path)
        except OSError:
            pass

        if shutil.which("caelestia") is None:
            self.win.toast(
                f"Saved {path.name} — applied on next login (caelestia CLI missing)")
            self._wall_state = str(path)
            return
        code, out = run_capture(["caelestia", "wallpaper", "-f", str(path)], timeout=30)
        if code == 0:
            self.win.toast(f"Wallpaper applied: {path.name}")
        else:
            last = (out.strip().splitlines() or [""])[-1][:60]
            self.win.toast(
                f"Saved {path.name} — applied on next login"
                + (f" ({last})" if last else ""))
        self._wall_state = str(path)

    def _pick_wallpaper(self, _b: Gtk.Button) -> None:
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Choose a wallpaper image")
        dialog.open(self.win, None, self._on_pick_done)

    def _on_pick_done(self, dialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except Exception:  # noqa: BLE001
            return
        if file is None or file.get_path() is None:
            return
        path = Path(file.get_path())
        dest_dir = paths.WALLPAPER_DIRS[0]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / path.name
        if path.resolve() != dest.resolve():
            try:
                shutil.copy2(path, dest)
            except OSError as exc:
                self.win.toast(f"Copy failed: {exc}")
                return
        self.refresh_wallpapers()
        self._apply_wallpaper(dest)

    # ---------------------------------------------------------------- settings
    def _build_settings(self, parent: Gtk.Box) -> None:
        group = Adw.PreferencesGroup.new()
        group.set_title("Starter settings")
        group.set_description(
            "Written to ~/.config/caelestia/shell.json — the shell picks up "
            "changes live, no restart needed.")

        self.sw_transparency = Adw.SwitchRow.new()
        self.sw_transparency.set_title("Transparency")
        self.sw_transparency.set_subtitle("Blur + see-through panels (the Caelestia look)")
        self.sw_transparency.connect("notify::active", self._set_transparency)
        group.add(self.sw_transparency)

        self.row_rounding = Adw.SpinRow.new_with_range(0.0, 3.0, 0.05)
        self.row_rounding.set_title("Rounding scale")
        self.row_rounding.set_subtitle("Corner rounding multiplier (1.35 = shipped default)")
        self.row_rounding.connect("changed", self._set_rounding)
        group.add(self.row_rounding)

        self.row_anim = Adw.SpinRow.new_with_range(0.0, 3.0, 0.1)
        self.row_anim.set_title("Animation speed scale")
        self.row_anim.set_subtitle("1.4 = shipped default; lower = snappier")
        self.row_anim.connect("changed", self._set_anim)
        group.add(self.row_anim)

        self.row_font_scale = Adw.SpinRow.new_with_range(0.7, 1.6, 0.02)
        self.row_font_scale.set_title("Font scale")
        self.row_font_scale.set_subtitle("1.0 = shipped default")
        self.row_font_scale.connect("changed", self._set_font_scale)
        group.add(self.row_font_scale)
        parent.append(group)

        # --- idle / power -----------------------------------------------------
        idle = Adw.PreferencesGroup.new()
        idle.set_title("Idle &amp; lock")
        idle.set_description(
            "Audio playing keeps the screen awake (inhibition is automatic).")

        self.row_lock = Adw.SpinRow.new_with_range(60, 3600, 30)
        self.row_lock.set_title("Lock after (seconds idle)")
        self.row_lock.connect("changed", self._set_idle_lock)
        idle.add(self.row_lock)

        self.row_dpms = Adw.SpinRow.new_with_range(60, 7200, 30)
        self.row_dpms.set_title("Screen off after (seconds idle)")
        self.row_dpms.connect("changed", self._set_idle_dpms)
        idle.add(self.row_dpms)

        self.row_suspend = Adw.SpinRow.new_with_range(300, 14400, 60)
        self.row_suspend.set_title("Suspend after (seconds idle)")
        self.row_suspend.connect("changed", self._set_idle_suspend)
        idle.add(self.row_suspend)

        self.sw_inhibit_audio = Adw.SwitchRow.new()
        self.sw_inhibit_audio.set_title("Keep awake while audio plays")
        self.sw_inhibit_audio.connect("notify::active", self._set_inhibit_audio)
        idle.add(self.sw_inhibit_audio)
        parent.append(idle)

        # --- quick actions -----------------------------------------------------
        actions = Adw.PreferencesGroup.new()
        actions.set_title("Quick actions")
        self.btn_open_shell_json = Gtk.Button.new_with_label("Open shell.json in editor")
        self.btn_open_shell_json.set_css_classes(["flat"])
        self.btn_open_shell_json.connect("clicked", self._open_shell_json)
        actions.add(self.btn_open_shell_json)

        self.btn_restart_shell = Gtk.Button.new_with_label("Restart the shell service")
        self.btn_restart_shell.set_css_classes(["flat"])
        self.btn_restart_shell.connect("clicked", self._restart_shell)
        actions.add(self.btn_restart_shell)
        parent.append(actions)

    # settings writers ---------------------------------------------------------
    def _write_when_quiet(self, path: list[str], value, cb=None) -> None:
        # Debounced write: SpinRow emits 'changed' rapidly while spinning.
        key = "-".join(path)
        prev = getattr(self, f"_debounce_{key}", None)
        if prev:
            GLib.source_remove(prev)
        src = GLib.timeout_add(600, self._do_write, path, value, cb)
        setattr(self, f"_debounce_{key}", src)

    def _do_write(self, path: list[str], value, cb) -> bool:
        try:
            if path and path[0] == "__idle__":
                self._write_idle_timeout(int(path[1]), value)
            else:
                _merge_into_shell_json(path, value)
            if cb:
                cb()
        except OSError as exc:
            self.win.toast(f"Could not write shell.json: {exc}")
        setattr(self, f"_debounce_{'-'.join(path)}", None)
        return False

    def _write_idle_timeout(self, index: int, timeout: int) -> None:
        """Update general.idle.timeouts[index].timeout, preserving the list."""
        data = _load_shell_json()
        timeouts = data.setdefault("general", {}).setdefault("idle", {}).setdefault(
            "timeouts", [])
        while len(timeouts) <= index:
            timeouts.append({})
        if not isinstance(timeouts[index], dict):
            timeouts[index] = {}
        timeouts[index]["timeout"] = int(timeout)
        _save_shell_json(data)

    def _set_transparency(self, sw, _pspec) -> None:
        if self._loading:
            return
        _merge_into_shell_json(["appearance", "transparency", "enabled"], sw.get_active())
        base = 0.55 if sw.get_active() else 1.0
        _merge_into_shell_json(["appearance", "transparency", "base"], base)

    def _set_rounding(self, row) -> None:
        if self._loading:
            return
        self._write_when_quiet(["appearance", "rounding", "scale"], row.get_value())

    def _set_anim(self, row) -> None:
        if self._loading:
            return
        self._write_when_quiet(["appearance", "anim", "durations", "scale"], row.get_value())

    def _set_font_scale(self, row) -> None:
        if self._loading:
            return
        self._write_when_quiet(["appearance", "font", "scale"], row.get_value())

    def _set_idle_lock(self, row) -> None:
        if self._loading:
            return
        self._write_when_quiet(["__idle__", "0", "timeout"], int(row.get_value()))

    def _set_idle_dpms(self, row) -> None:
        if self._loading:
            return
        self._write_when_quiet(["__idle__", "1", "timeout"], int(row.get_value()))

    def _set_idle_suspend(self, row) -> None:
        if self._loading:
            return
        self._write_when_quiet(["__idle__", "2", "timeout"], int(row.get_value()))

    def _set_inhibit_audio(self, sw, _pspec) -> None:
        if self._loading:
            return
        _merge_into_shell_json(["general", "idle", "inhibitWhenAudio"], sw.get_active())

    # actions ---------------------------------------------------------------
    def _open_shell_json(self, _b: Gtk.Button) -> None:
        paths.SHELL_JSON.parent.mkdir(parents=True, exist_ok=True)
        if not paths.SHELL_JSON.exists():
            paths.SHELL_JSON.write_text("{}\n")
        code, _out = run_capture(["xdg-open", str(paths.SHELL_JSON)], timeout=10)
        if code != 0:
            self.win.toast("Could not open an editor for shell.json")

    def _restart_shell(self, _b: Gtk.Button) -> None:
        code, _out = run_capture(
            ["systemctl", "--user", "restart", "caelestia-shell.service"], timeout=30)
        self.win.toast("Shell restarted" if code == 0 else
                       "Could not restart (are you in the Hyprland session?)")

    # ------------------------------------------------------------ maintenance
    def _build_maintenance(self, parent: Gtk.Box) -> None:
        group = Adw.PreferencesGroup.new()
        group.set_title("Maintenance")

        row_updates = Adw.ActionRow.new()
        row_updates.set_title("Updates")
        row_updates.set_subtitle(
            "Check installed revisions against the pinned ones and apply updates.")
        btn_open = Gtk.Button.new_with_label("Open")
        btn_open.set_valign(Gtk.Align.CENTER)
        btn_open.set_css_classes(["flat"])
        btn_open.connect("clicked", lambda *_: self.win.goto_updates())
        row_updates.add_suffix(btn_open)
        group.add(row_updates)

        row_uninstall = Adw.ActionRow.new()
        row_uninstall.set_title("Uninstall")
        row_uninstall.set_subtitle(
            "Removes the desktop; your configs are archived to ~/.config first.")
        btn2 = Gtk.Button.new_with_label("Run")
        btn2.set_valign(Gtk.Align.CENTER)
        btn2.set_css_classes(["destructive-action"])
        btn2.connect("clicked", self._on_uninstall)
        row_uninstall.add_suffix(btn2)
        group.add(row_uninstall)
        parent.append(group)

    def _on_uninstall(self, _b: Gtk.Button) -> None:
        try:
            argv = ["bash", paths.script("uninstall.sh")]
        except FileNotFoundError as exc:
            self.win.toast(f"Cannot find uninstall.sh: {exc}")
            return
        self._confirm_run(
            "Uninstall Caelestia?",
            "Runs uninstall.sh: stops the shell service, removes binaries/QML "
            "modules and archives your configs. GNOME was never touched.",
            argv, title_label="uninstall.sh", danger=True)

    def _confirm_run(self, heading: str, body: str, argv: list[str],
                     title_label: str, danger: bool = False) -> None:
        dialog = Adw.AlertDialog.new(heading, body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Run")
        dialog.set_response_appearance(
            "ok", Adw.ResponseAppearance.DESTRUCTIVE if danger
            else Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_confirm_response, argv, title_label)
        dialog.present(self.win)

    def _on_confirm_response(self, _dialog, response: str, argv: list[str],
                             title_label: str) -> None:
        if response != "ok":
            return
        self.win.ensure_password(lambda: self._run_argv(argv, title_label))

    def _run_argv(self, argv: list[str], title_label: str) -> None:
        self.panel.start(
            argv, title=title_label, password=self.win.state.get("password"),
            done_note="Finished — see the log above.",
            on_finished=self._maintenance_finished,
        )

    def _maintenance_finished(self, _code: int) -> None:
        self.win.state["installed"] = checks.installed_state()["installed"]
        self.refresh_status()

    # ----------------------------------------------------------------- hook
    def on_navigate_to(self) -> None:
        self.refresh_status()
        self.refresh_wallpapers()
        self._load_current_settings()

    def _load_current_settings(self) -> None:
        data = _load_shell_json()
        appear = data.get("appearance", {})
        idle = data.get("general", {}).get("idle", {})
        self._loading = True
        try:
            self.sw_transparency.set_active(
                appear.get("transparency", {}).get("enabled", True))
            self.row_rounding.set_value(appear.get("rounding", {}).get("scale", 1.35))
            self.row_anim.set_value(
                appear.get("anim", {}).get("durations", {}).get("scale", 1.4))
            self.row_font_scale.set_value(appear.get("font", {}).get("scale", 1.0))
            to = idle.get("timeouts", [])
            if len(to) > 0 and isinstance(to[0], dict):
                self.row_lock.set_value(to[0].get("timeout", 180))
            if len(to) > 1 and isinstance(to[1], dict):
                self.row_dpms.set_value(to[1].get("timeout", 300))
            if len(to) > 2 and isinstance(to[2], dict):
                self.row_suspend.set_value(to[2].get("timeout", 600))
            self.sw_inhibit_audio.set_active(idle.get("inhibitWhenAudio", True))
        finally:
            self._loading = False
