"""Setup panel: status, wallpaper picker, starter settings, maintenance.

Settings are written straight into ~/.config/caelestia/shell.json (the same
file the shell reads live) using JSON merge semantics — nested keys we don't
touch are preserved. While the form is being populated from disk the change
handlers are muted so visiting the tab never rewrites the user's settings.
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import checks, paths  # noqa: E402
from ..page_style import load_page_css  # noqa: E402
from ..runner import run_capture  # noqa: E402
from ..wallpapers import ThumbnailLoader, scan_wallpapers  # noqa: E402


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
    WALL_PAGE_SIZE = 6

    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self._loading = False
        self._wall_state = ""
        self._status_loading = False
        self._wall_scan_loading = False
        self._wall_scan_again = False
        self._wall_page = 0
        self._visible_wall_paths: list[Path] = []
        self._wall_pictures: dict[Path, Gtk.Picture] = {}
        self._thumbnails = ThumbnailLoader()
        self._thumbnail_generation = 0
        load_page_css()
        self.add_css_class("setup-page")

        sc = Gtk.ScrolledWindow.new()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_vexpand(True)
        clamp = Adw.Clamp.new()
        clamp.set_maximum_size(780)
        box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 24)
        box.set_margin_top(32)
        box.set_margin_bottom(140)
        box.set_margin_start(24)
        box.set_margin_end(24)
        clamp.set_child(box)
        sc.set_child(clamp)
        self.set_child(sc)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        hero.append(self._label("SETUP  /  YOUR DESKTOP", "setup-eyebrow"))
        hero.append(self._label("Make yourself at home.", "setup-title"))
        hero.append(self._label(
            "Choose a backdrop, soften the details, and settle into your own rhythm.",
            "setup-copy"))
        box.append(hero)
        self._build_status(box)
        self._build_wallpapers(box)
        self._build_settings(box)

    @staticmethod
    def _label(text: str, css: str) -> Gtk.Label:
        label = Gtk.Label(label=text, wrap=True, xalign=0)
        label.add_css_class(css)
        return label

    # ------------------------------------------------------------------ status
    def _build_status(self, parent: Gtk.Box) -> None:
        group = Adw.PreferencesGroup.new()
        group.set_title("Your session")
        group.add_css_class("setup-panel")
        self.row_service = Adw.ActionRow.new()
        self.row_service.set_title("Caelestia shell service")
        self.row_service.add_prefix(Gtk.Image.new_from_icon_name("computer-symbolic"))
        self.row_service.set_subtitle_lines(0)
        group.add(self.row_service)

        self.row_session = Adw.ActionRow.new()
        self.row_session.set_title("Hyprland session")
        self.row_session.add_prefix(Gtk.Image.new_from_icon_name("preferences-desktop-symbolic"))
        self.row_session.set_title_lines(0)
        self.row_session.set_subtitle_lines(0)
        group.add(self.row_session)
        parent.append(group)

    def refresh_status(self) -> None:
        if self._status_loading:
            return
        self._status_loading = True
        selection = self._wall_state

        def worker():
            state = checks.installed_state()
            active = checks.service_active()
            GLib.idle_add(self._apply_status, state, active, selection)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_status(self, state: dict, active: bool | None, selection: str) -> bool:
        self._status_loading = False
        # Refresh external changes, but never overwrite a selection made while
        # the service query was pending.
        if self._wall_state == selection:
            self._wall_state = state.get("wallpaper") or ""
        self._sync_wallpaper_selection()
        if not state["installed"]:
            self.row_service.set_title("Not installed")
            self.row_service.set_subtitle(
                "Run the Install tab first — everything here activates afterwards.")
            self.row_session.set_title("—")
            return GLib.SOURCE_REMOVE
        svc = {True: "running", False: "stopped", None: "unknown (not in session)"}[active]
        self.row_service.set_title(f"Shell service: {svc}")
        self.row_service.set_subtitle(
            "systemctl --user status caelestia-shell — restarts itself if it crashes.")
        self.row_session.set_title(
            "Registered at the login screen (gear menu → Hyprland)"
            if state["session_registered"] else
            "Session entry missing — re-run the install to register it")
        return GLib.SOURCE_REMOVE

    # -------------------------------------------------------------- wallpapers
    def _build_wallpapers(self, parent: Gtk.Box) -> None:
        gallery = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        gallery.add_css_class("setup-panel")
        gallery.append(self._label("WALLPAPER", "setup-eyebrow"))
        gallery.append(self._label("Start with a different view.", "setup-section-title"))
        gallery.append(self._label(
            "Your wallpaper sets the palette for the whole shell. "
            "Choose from your collection or bring something new.", "setup-copy"))
        self.wall_current = self._label("No wallpaper selected", "setup-caption")
        gallery.append(self.wall_current)
        self.flow = Gtk.FlowBox.new()
        self.flow.set_min_children_per_line(1)
        self.flow.set_max_children_per_line(3)
        self.flow.set_column_spacing(12)
        self.flow.set_row_spacing(12)
        self.flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.flow.set_homogeneous(True)
        self.flow.connect("child-activated", self._on_wallpaper_activated)
        self.flow.set_valign(Gtk.Align.START)
        self._wall_paths: list[Path] = []
        self.wall_scroll = Gtk.ScrolledWindow()
        self.wall_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.wall_scroll.set_min_content_height(224)
        self.wall_scroll.set_max_content_height(224)
        self.wall_scroll.set_propagate_natural_height(True)
        self.wall_scroll.set_child(self.flow)
        gallery.append(self.wall_scroll)
        pager = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.btn_wall_prev = Gtk.Button(label="Previous")
        self.btn_wall_prev.add_css_class("pill")
        self.btn_wall_prev.connect("clicked", lambda *_: self._change_wall_page(-1))
        pager.append(self.btn_wall_prev)
        self.wall_page_label = self._label("Loading wallpapers…", "setup-caption")
        self.wall_page_label.set_hexpand(True)
        self.wall_page_label.set_justify(Gtk.Justification.CENTER)
        self.wall_page_label.set_xalign(0.5)
        pager.append(self.wall_page_label)
        self.btn_wall_next = Gtk.Button(label="Next")
        self.btn_wall_next.add_css_class("pill")
        self.btn_wall_next.connect("clicked", lambda *_: self._change_wall_page(1))
        pager.append(self.btn_wall_next)
        self.btn_wall_prev.set_sensitive(False)
        self.btn_wall_next.set_sensitive(False)
        gallery.append(pager)
        self.wall_empty = self._label(
            "A fresh canvas. Add an image to start your collection.", "setup-caption")
        self.wall_empty.set_visible(False)
        gallery.append(self.wall_empty)
        self.btn_wall_more = Gtk.Button.new_with_label("Add an image…")
        self.btn_wall_more.set_css_classes(["suggested-action", "setup-primary"])
        self.btn_wall_more.set_halign(Gtk.Align.START)
        self.btn_wall_more.connect("clicked", self._pick_wallpaper)
        gallery.append(self.btn_wall_more)
        parent.append(gallery)

    def refresh_wallpapers(self) -> None:
        if self._wall_scan_loading:
            self._wall_scan_again = True
            return
        self._wall_scan_loading = True
        directories = tuple(paths.WALLPAPER_DIRS)

        def worker():
            result = scan_wallpapers(directories)
            GLib.idle_add(self._apply_wallpaper_scan, result)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_wallpaper_scan(self, result: list[Path]) -> bool:
        self._wall_scan_loading = False
        self._wall_paths = result
        self._show_wall_page()
        if self._wall_scan_again:
            self._wall_scan_again = False
            self.refresh_wallpapers()
        return GLib.SOURCE_REMOVE

    def _change_wall_page(self, delta: int) -> None:
        self._wall_page += delta
        self._show_wall_page()

    def _show_wall_page(self) -> None:
        total = len(self._wall_paths)
        pages = max(1, (total + self.WALL_PAGE_SIZE - 1) // self.WALL_PAGE_SIZE)
        self._wall_page = max(0, min(self._wall_page, pages - 1))
        start = self._wall_page * self.WALL_PAGE_SIZE
        visible = self._wall_paths[start:start + self.WALL_PAGE_SIZE]
        # Reopening Setup does not reconstruct an unchanged visible gallery.
        if visible != self._visible_wall_paths:
            while child := self.flow.get_child_at_index(0):
                self.flow.remove(child)
            self._wall_pictures.clear()
            self._visible_wall_paths = visible
            for path in visible:
                self.flow.append(self._wall_tile(path))
        self._thumbnail_generation = self._thumbnails.request(visible, self._thumbnail_ready)
        self.wall_scroll.get_vadjustment().set_value(0)
        self.wall_page_label.set_label(
            f"{start + 1}–{min(start + self.WALL_PAGE_SIZE, total)} of {total}"
            if total else "No wallpapers")
        self.btn_wall_prev.set_sensitive(self._wall_page > 0)
        self.btn_wall_next.set_sensitive(self._wall_page + 1 < pages)
        self.wall_empty.set_visible(not total)
        self.wall_scroll.set_visible(bool(total))
        self._sync_wallpaper_selection()

    def _thumbnail_ready(self, generation: int, path: Path, pixbuf) -> bool:
        if generation != self._thumbnail_generation:
            return GLib.SOURCE_REMOVE
        picture = self._wall_pictures.get(path)
        if picture is not None:
            picture.set_pixbuf(pixbuf)
            picture.get_parent().set_tooltip_text(
                f"Apply wallpaper: {path.name}" if pixbuf is not None
                else f"Preview unavailable: {path.name}")
        return GLib.SOURCE_REMOVE

    def _sync_wallpaper_selection(self) -> None:
        current = self._wall_state
        self.wall_current.set_label(
            f"Current: {Path(current).name}" if current else "No wallpaper selected")
        self.flow.unselect_all()
        for i, path in enumerate(self._visible_wall_paths):
            if str(path) == current:
                self.flow.select_child(self.flow.get_child_at_index(i))
                break

    def _wall_tile(self, path: Path) -> Gtk.Widget:
        btn = Gtk.Button.new()
        pic = Gtk.Picture.new()
        pic.set_content_fit(Gtk.ContentFit.COVER)
        self._wall_pictures[path] = pic
        pic.set_size_request(144, 96)
        btn.add_css_class("setup-wall-tile")
        btn.set_overflow(Gtk.Overflow.HIDDEN)
        btn.set_child(pic)
        btn.set_tooltip_text(f"Apply wallpaper: {path.name}")
        # The tile is a Button inside a FlowBoxChild; a FlowBox "child-activated"
        # signal does NOT fire when the inner button takes the click, so connect
        # the button directly (keyboard activation still works via the button).
        btn.connect("clicked", lambda *_: self._apply_wallpaper(path))
        return btn

    def _on_wallpaper_activated(self, _flow: Gtk.FlowBox, child: Gtk.FlowBoxChild) -> None:
        idx = child.get_index()
        if idx >= len(self._visible_wall_paths):
            return
        self._apply_wallpaper(self._visible_wall_paths[idx])

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

        self._wall_state = str(path)
        self._sync_wallpaper_selection()
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
        group.set_title("Look &amp; feel")
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
