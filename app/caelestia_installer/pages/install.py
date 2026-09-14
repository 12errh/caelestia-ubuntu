"""Install wizard: checks → options → sudo → progress → result.

The live-log step uses the shared :class:`ScriptPanel`. Setup, Updates and
Advanced embed their own panels, so this page only runs ``setup.sh``.
"""

from __future__ import annotations

import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import checks, paths  # noqa: E402
from .script_panel import SETUP_STAGES, ScriptPanel  # noqa: E402


class InstallPage(Adw.Bin):
    """Left wizard carousel (Adw.Carousel + dots) with the shared log panel."""

    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self.checks: list[checks.Check] = []
        self.runner = None
        self._stage_idx = 0
        self._page_idx = 0
        self._t0 = 0.0
        self._done_note = ""
        self._script_title = "setup.sh"

        root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        root.set_vexpand(True)

        self.banner = Adw.Banner.new("")
        self.banner.set_button_label("Open Updates")
        self.banner.connect("button-clicked", lambda *_: self.win.goto_updates())
        self.banner.set_revealed(False)
        root.append(self.banner)

        self.carousel = Adw.Carousel.new()
        self.carousel.set_hexpand(True)
        self.carousel.set_vexpand(True)
        self.pages: list[Gtk.Widget] = []
        for factory in (self._page_checks, self._page_options,
                        self._page_auth, self._page_progress, self._page_done):
            p = factory()
            self.pages.append(p)
            self.carousel.append(p)
        root.append(self.carousel)

        dots = Adw.CarouselIndicatorDots.new()
        dots.set_carousel(self.carousel)
        root.append(dots)

        self.set_child(root)
        self.refresh_checks_async()

    # ------------------------------------------------------------------ hook
    def on_navigate_to(self) -> None:
        state = checks.installed_state()
        self.win.state["installed"] = state["installed"]
        if state["installed"]:
            self.banner.set_title(
                "Caelestia is already installed — re-running this wizard is a "
                "safe repair/update.")
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

    # ------------------------------------------------------------------ p1
    def _page_checks(self) -> Gtk.Widget:
        page = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
        page.set_margin_top(18)
        page.set_margin_bottom(12)
        page.set_margin_start(24)
        page.set_margin_end(24)

        head = Gtk.Label.new("<big><b>Step 1 — System check</b></big>")
        head.set_use_markup(True)
        head.set_halign(Gtk.Align.START)
        page.append(head)
        sub = Gtk.Label.new(
            "Make sure your system can run the Caelestia desktop. "
            "Warnings don't block the install; failures do.")
        sub.set_wrap(True)
        sub.set_xalign(0)
        sub.set_css_classes(["dim-label"])
        page.append(sub)

        self.checks_list = Gtk.ListBox.new()
        self.checks_list.set_css_classes(["boxed-list"])
        self.checks_list.set_selection_mode(Gtk.SelectionMode.NONE)
        page.append(self.checks_list)

        row_btns = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        row_btns.set_halign(Gtk.Align.END)
        self.btn_recheck = Gtk.Button.new_with_label("Re-run checks")
        self.btn_recheck.connect("clicked", lambda *_: self.refresh_checks_async())
        self.btn_next1 = Gtk.Button.new_with_label("Continue")
        self.btn_next1.set_css_classes(["suggested-action"])
        self.btn_next1.set_sensitive(False)
        self.btn_next1.connect("clicked", self._next)
        row_btns.append(self.btn_recheck)
        row_btns.append(self.btn_next1)
        page.append(row_btns)

        sc = Gtk.ScrolledWindow.new()
        sc.set_child(page)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        return sc

    def refresh_checks_async(self) -> None:
        """Collect checks in a worker thread (network probe has a timeout)."""
        self._set_checks_loading(True)

        def worker() -> None:
            result = checks.collect()
            GLib.idle_add(self._apply_checks, result)

        threading.Thread(target=worker, daemon=True).start()

    def _set_checks_loading(self, loading: bool) -> None:
        if loading:
            self.btn_next1.set_sensitive(False)
        self.btn_recheck.set_sensitive(not loading)

    def _apply_checks(self, result: list) -> bool:
        self.checks = result
        while self.checks_list.get_row_at_index(0):
            self.checks_list.remove(self.checks_list.get_row_at_index(0))

        blocking = checks.blocking(result)
        for c in result:
            row = Adw.ActionRow.new()
            row.set_title(c.label)
            if c.detail:
                row.set_subtitle(c.detail)
            prefix = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 6)
            icon_name = ("emblem-ok-symbolic" if c.ok
                         else "dialog-warning-symbolic" if c.severity == "warning"
                         else "dialog-error-symbolic")
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_valign(Gtk.Align.CENTER)
            if c.ok:
                icon.set_css_classes(["success"])
            elif c.severity == "warning":
                icon.set_css_classes(["warning"])
            else:
                icon.set_css_classes(["error"])
            prefix.append(icon)
            row.add_prefix(prefix)
            self.checks_list.append(row)

        if blocking:
            self.btn_next1.set_sensitive(False)
            self.btn_next1.set_tooltip_text(
                "Fix the failing checks first (e.g. free disk space, connect to the internet).")
        else:
            self.btn_next1.set_sensitive(True)
            self.btn_next1.set_tooltip_text(None)
        self._set_checks_loading(False)
        return False  # one-shot idle callback

    # ------------------------------------------------------------------ p2
    def _page_options(self) -> Gtk.Widget:
        page = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
        page.set_margin_top(18)
        page.set_margin_bottom(12)
        page.set_margin_start(24)
        page.set_margin_end(24)

        head = Gtk.Label.new("<big><b>Step 2 — Install options</b></big>")
        head.set_use_markup(True)
        head.set_halign(Gtk.Align.START)
        page.append(head)

        group = Adw.PreferencesGroup.new()
        group.set_title("What gets installed")
        group.set_description(
            "The full desktop: Qt 6.11 toolchain (only if missing), Hyprland, "
            "Quickshell + Caelestia shell, fonts, wallpaper and idle/lock setup.")

        def switch_row(title, subtitle, key, default=True):
            r = Adw.SwitchRow.new()
            r.set_title(title)
            r.set_subtitle(subtitle)
            r.set_active(default)
            setattr(self, key, r)
            return r

        group.add(switch_row(
            "Install system packages (apt)",
            "Build tools, Hyprland from the cppiber PPA, portals. Skip only on a re-run.",
            "opt_apt"))
        group.add(switch_row(
            "Download the Qt 6.11 toolchain",
            "~1.5 GB under /opt — skipped automatically if a compatible Qt is already installed.",
            "opt_qt"))
        group.add(switch_row(
            "Download fonts",
            "Rubik, CaskaydiaCove Nerd Font and Material Symbols Rounded.",
            "opt_fonts"))
        group.add(switch_row(
            "Deploy theme and configs to ~/.config",
            "Backs up any existing configs first. Unchecking builds only, deploys nothing.",
            "opt_config"))
        page.append(group)

        adv = Adw.PreferencesGroup.new()
        adv.set_title("Advanced")
        self.row_qtver = Adw.EntryRow.new()
        self.row_qtver.set_title("Qt version (default 6.11.2 — leave as is)")
        self.row_qtver.set_text("6.11.2")
        adv.add(self.row_qtver)
        self.row_ignore_space = Adw.SwitchRow.new()
        self.row_ignore_space.set_title("Skip the 8 GB free-disk check")
        self.row_ignore_space.set_subtitle(
            "Only if / is small but a bigger disk is mounted elsewhere.")
        adv.add(self.row_ignore_space)
        page.append(adv)

        row_btns = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        row_btns.set_halign(Gtk.Align.END)
        back = Gtk.Button.new_with_label("Back")
        back.connect("clicked", self._prev)
        self.btn_next2 = Gtk.Button.new_with_label("Continue")
        self.btn_next2.set_css_classes(["suggested-action"])
        self.btn_next2.connect("clicked", self._next)
        row_btns.append(back)
        row_btns.append(self.btn_next2)
        page.append(row_btns)

        sc = Gtk.ScrolledWindow.new()
        sc.set_child(page)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        return sc

    # ------------------------------------------------------------------ p3
    def _page_auth(self) -> Gtk.Widget:
        page = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
        page.set_valign(Gtk.Align.CENTER)
        page.set_margin_top(18)
        page.set_margin_bottom(18)
        page.set_margin_start(24)
        page.set_margin_end(24)

        head = Gtk.Label.new("<big><b>Step 3 — Your password</b></big>")
        head.set_use_markup(True)
        head.set_halign(Gtk.Align.START)
        page.append(head)

        expl = Gtk.Label.new(
            "The installer needs sudo for package installs and writes to /opt and "
            "/usr/local. Your password is kept only in this app's memory, used "
            "to answer sudo prompts in the install log, and never written to disk "
            "or to any log file.")
        expl.set_wrap(True)
        expl.set_xalign(0)
        page.append(expl)

        self.pw_entry = Adw.PasswordEntryRow.new()
        self.pw_entry.set_title("sudo password")
        self.pw_entry.connect("changed", lambda *_: self._auth_changed())
        pw_group = Adw.PreferencesGroup.new()
        pw_group.add(self.pw_entry)
        page.append(pw_group)

        self.auth_status = Gtk.Label.new("")
        self.auth_status.set_css_classes(["dim-label"])
        self.auth_status.set_wrap(True)
        self.auth_status.set_xalign(0)
        page.append(self.auth_status)

        row_btns = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        row_btns.set_halign(Gtk.Align.END)
        back = Gtk.Button.new_with_label("Back")
        back.connect("clicked", self._prev)
        self.btn_start = Gtk.Button.new_with_label("Start installation")
        self.btn_start.set_css_classes(["suggested-action"])
        self.btn_start.set_sensitive(False)
        self.btn_start.connect("clicked", self._start_install)
        row_btns.append(back)
        row_btns.append(self.btn_start)
        page.append(row_btns)
        return page

    def _auth_changed(self) -> None:
        self.btn_start.set_sensitive(bool(self.pw_entry.get_text()))

    # ------------------------------------------------------------------ p4
    def _page_progress(self) -> Gtk.Widget:
        page = Gtk.Box.new(Gtk.Orientation.VERTICAL, 10)
        page.set_margin_top(18)
        page.set_margin_bottom(12)
        page.set_margin_start(24)
        page.set_margin_end(24)

        head = Gtk.Label.new("<big><b>Installing…</b></big>")
        head.set_use_markup(True)
        head.set_halign(Gtk.Align.START)
        page.append(head)

        self.panel = ScriptPanel(self.win, stages=SETUP_STAGES)
        page.append(self.panel)

        sc = Gtk.ScrolledWindow.new()
        sc.set_child(page)
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        return sc

    # ------------------------------------------------------------------ p5
    def _page_done(self) -> Gtk.Widget:
        page = Gtk.Box.new(Gtk.Orientation.VERTICAL, 14)
        page.set_valign(Gtk.Align.CENTER)
        page.set_margin_start(24)
        page.set_margin_end(24)

        self.done_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        self.done_icon.set_pixel_size(64)
        self.done_icon.set_halign(Gtk.Align.CENTER)
        page.append(self.done_icon)

        self.done_title = Gtk.Label.new("<big><b>Install finished</b></big>")
        self.done_title.set_use_markup(True)
        self.done_title.set_halign(Gtk.Align.CENTER)
        page.append(self.done_title)

        self.done_text = Gtk.Label.new("")
        self.done_text.set_wrap(True)
        self.done_text.set_justify(Gtk.Justification.CENTER)
        self.done_text.set_css_classes(["dim-label"])
        page.append(self.done_text)

        self.btn_finish = Gtk.Button.new_with_label("Open Setup")
        self.btn_finish.set_css_classes(["suggested-action", "pill"])
        self.btn_finish.set_halign(Gtk.Align.CENTER)
        self.btn_finish.connect("clicked", lambda *_: self.win.goto_setup())
        page.append(self.btn_finish)
        return page

    # ------------------------------------------------------------- carousel
    def _go_to(self, idx: int) -> None:
        idx = max(0, min(idx, len(self.pages) - 1))
        self._page_idx = idx
        self.carousel.scroll_to(self.pages[idx], True)

    def _next(self, _b: Gtk.Button) -> None:
        if self._page_idx == 0 and not self.btn_next1.get_sensitive():
            return
        self._go_to(self._page_idx + 1)

    def _prev(self, _b: Gtk.Button) -> None:
        self._go_to(self._page_idx - 1)

    # ------------------------------------------------------------- running
    def run_script(self, argv: list[str], title: str,
                   done_note: str = "", stages: list[str] | None = None) -> None:
        """Run a repository script with the shared progress UI."""
        self._script_title = title
        self._done_note = done_note
        self._t0 = time.time()
        started = self.panel.start(
            argv, title=title, password=self.win.state.get("password"),
            stages=stages or SETUP_STAGES, done_note=done_note,
            on_finished=self._install_finished,
        )
        if started:
            self._go_to(3)

    def _install_finished(self, code: int) -> None:
        ok = code == 0
        icon = "emblem-ok-symbolic" if ok else "dialog-error-symbolic"
        self.done_icon.set_from_icon_name(icon)
        self.done_icon.set_css_classes(["success" if ok else "error"])
        self.done_title.set_label(
            "<big><b>Install finished</b></big>" if ok
            else "<big><b>Something went wrong</b></big>")
        elapsed = int(time.time() - self._t0)
        mins, secs = divmod(elapsed, 60)
        if ok:
            self.done_text.set_text(
                f"{self._done_note or 'All checks passed.'}\n"
                f"Took {mins}m {secs:02d}s. Next: reboot → gear menu → Hyprland.")
        else:
            self.done_text.set_text(
                f"{self._script_title} exited with code {code} after "
                f"{mins}m {secs:02d}s. Check the log above — the last lines "
                "usually say which stage failed.")
        self.win.state["installed"] = checks.installed_state()["installed"]
        self._go_to(4)

    def _start_install(self, _b: Gtk.Button | None = None) -> None:
        try:
            script = paths.script("setup.sh")
        except FileNotFoundError as exc:
            self.win.toast(f"Cannot find setup.sh: {exc}")
            return
        self.win.state["password"] = self.pw_entry.get_text()
        argv = ["bash", script]
        if self.opt_apt.get_active() is False:
            argv.append("--skip-apt")
        if self.opt_qt.get_active() is False:
            argv.append("--skip-qt")
        if self.opt_fonts.get_active() is False:
            argv.append("--skip-fonts")
        if self.opt_config.get_active() is False:
            argv.append("--skip-config")
        if self.row_ignore_space.get_active():
            argv.append("--ignore-space")
        qtver = self.row_qtver.get_text().strip()
        if qtver and qtver != "6.11.2":
            argv += ["--qt-version", qtver]
        self.run_script(argv, title="setup.sh",
                        done_note="The Caelestia desktop is installed.")
