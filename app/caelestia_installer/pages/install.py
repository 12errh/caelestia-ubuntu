"""Focused install wizard: checks → options → password → progress and result."""

from __future__ import annotations

import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import checks, paths  # noqa: E402
from ..motion import PageTransition  # noqa: E402
from ..page_style import load_page_css  # noqa: E402
from .script_panel import SETUP_STAGES, ScriptPanel  # noqa: E402


def _label(text: str, css: str) -> Gtk.Label:
    label = Gtk.Label(label=text, wrap=True, xalign=0)
    label.add_css_class(css)
    return label


class InstallPage(Adw.Bin):
    """Keep step state alive, without swipe navigation bypassing checks."""

    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self.checks: list[checks.Check] = []
        self._page_idx = 0
        self._selected_step = 0
        self._checks_loading = False
        self._t0 = 0.0
        self._done_note = ""
        self._script_title = "setup.sh"
        load_page_css()
        self.add_css_class("install-page")
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.banner = Adw.Banner.new("")
        self.banner.set_button_label("Open Updates")
        self.banner.connect("button-clicked", lambda *_: self.win.goto_updates())
        root.append(self.banner)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_vhomogeneous(False)
        self.stack.set_hhomogeneous(False)
        self.stack.set_vexpand(True)
        self.pages = []
        for factory in (self._page_checks, self._page_options,
                        self._page_auth, self._page_progress):
            page = factory()
            self.pages.append(page)
            self.stack.add_named(page, str(len(self.pages) - 1))
        self.transition = PageTransition(self.stack)
        self.transition.set_vexpand(True)
        root.append(self.transition)
        self.set_child(root)
        self.refresh_checks_async()

    def on_navigate_to(self) -> None:
        state = checks.installed_state()
        self.win.state["installed"] = state["installed"]
        self.banner.set_title(
            "Caelestia is already installed. Use this wizard to repair or reinstall.")
        self.banner.set_revealed(bool(state["installed"]))

    def _step(self, index: int, title: str, subtitle: str):
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_margin_top(32)
        content.set_margin_bottom(140)  # Clear the floating dock and tooltips.
        content.set_margin_start(24)
        content.set_margin_end(24)
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        eyebrow = _label(f"INSTALL  /  STEP {index + 1} OF 4", "install-eyebrow")
        heading = _label(title, "install-title")
        copy = _label(subtitle, "install-copy")
        for label in (eyebrow, heading, copy):
            head.append(label)
        content.append(head)
        clamp = Adw.Clamp(maximum_size=780)
        clamp.set_child(content)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(clamp)
        return scroll, content, (eyebrow, heading, copy)

    def _actions(self, page: Gtk.Box, label: str, callback, *, back: bool = True):
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        if back:
            button = Gtk.Button(label="Back")
            button.add_css_class("pill")
            button.connect("clicked", self._prev)
            actions.append(button)
        actions.append(Gtk.Box(hexpand=True))
        primary = Gtk.Button(label=label)
        primary.set_css_classes(["suggested-action", "install-primary"])
        primary.connect("clicked", callback)
        actions.append(primary)
        page.append(actions)
        return primary

    def _page_checks(self) -> Gtk.Widget:
        scroll, page, _ = self._step(
            0, "A good place to start.",
            "A few checks before we build your desktop. Warnings are advisory; "
            "failed requirements need your attention.")
        self.check_status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.check_status.add_css_class("install-status")
        self.check_spinner = Gtk.Spinner()
        self.check_status.append(self.check_spinner)
        self.check_summary = _label("Checking your system…", "install-copy")
        self.check_summary.set_hexpand(True)
        self.check_status.append(self.check_summary)
        page.append(self.check_status)
        self.checks_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.checks_list.set_css_classes(["boxed-list", "install-list"])
        page.append(self.checks_list)
        self.btn_recheck = Gtk.Button(label="Run checks again")
        self.btn_recheck.set_halign(Gtk.Align.START)
        self.btn_recheck.add_css_class("pill")
        self.btn_recheck.connect("clicked", lambda *_: self.refresh_checks_async())
        self.btn_next1 = self._actions(page, "Continue", self._next, back=False)
        self.btn_next1.get_parent().prepend(self.btn_recheck)
        self.btn_next1.set_sensitive(False)
        return scroll

    def refresh_checks_async(self) -> None:
        """Collect checks off the GTK thread, keeping navigation gated."""
        if self._checks_loading:
            return
        self._set_checks_loading(True)

        def worker() -> None:
            try:
                result = checks.collect()
            except Exception as exc:  # noqa: BLE001 - surface worker failures
                result = [checks.Check("check-error", "Could not complete system checks",
                                       str(exc), False, "error")]
            GLib.idle_add(self._apply_checks, result)

        threading.Thread(target=worker, daemon=True).start()

    def _set_checks_loading(self, loading: bool) -> None:
        self._checks_loading = loading
        self.btn_recheck.set_sensitive(not loading)
        self.check_spinner.set_spinning(loading)
        self.check_spinner.set_visible(loading)
        if loading:
            self.btn_next1.set_sensitive(False)
            self.check_summary.set_label("Checking your system…")
            self.check_status.remove_css_class("blocked")
            self.check_status.remove_css_class("ready")

    def _apply_checks(self, result: list[checks.Check]) -> bool:
        self.checks = result
        while self.checks_list.get_row_at_index(0):
            self.checks_list.remove(self.checks_list.get_row_at_index(0))
        for check in result:
            row = Adw.ActionRow(title=check.label, subtitle=check.detail)
            row.set_use_markup(False)
            row.set_title_lines(0)
            row.set_subtitle_lines(0)
            kind = "success" if check.ok else "warning" if check.severity == "warning" else "error"
            icon_name = {"success": "emblem-ok-symbolic", "warning": "dialog-warning-symbolic",
                         "error": "dialog-error-symbolic"}[kind]
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.add_css_class(kind)
            row.add_prefix(icon)
            self.checks_list.append(row)
        blocking = checks.blocking(result)
        warnings = sum(not c.ok and c.severity == "warning" for c in result)
        self._set_checks_loading(False)
        self.btn_next1.set_sensitive(not blocking)
        self.btn_next1.set_tooltip_text("Resolve failed requirements, then run checks again."
                                       if blocking else None)
        self.check_status.remove_css_class("ready")
        self.check_status.remove_css_class("blocked")
        self.check_status.add_css_class("blocked" if blocking else "ready")
        self.check_summary.set_label(
            "Some requirements need attention. Review the checks below." if blocking else
            f"Ready to continue, with {warnings} advisory warning(s)." if warnings else
            "Everything is ready. Let's make it yours.")
        return GLib.SOURCE_REMOVE

    def _page_options(self) -> Gtk.Widget:
        scroll, page, _ = self._step(
            1, "Make room for your desktop.",
            "The defaults build the full Caelestia experience: Hyprland, Quickshell, "
            "the shell and its everyday essentials.")
        group = Adw.PreferencesGroup(title="What gets installed")
        group.add_css_class("install-list")
        for key, title, subtitle in (
            ("opt_apt", "System packages", "Build tools, Hyprland from the cppiber PPA and portals. "
             "Skip only on a re-run."),
            ("opt_qt", "Qt 6.11 toolchain", "About 1.5 GB under /opt. Automatically skipped "
             "if compatible Qt is already installed."),
            ("opt_fonts", "Fonts & symbols", "Rubik, CaskaydiaCove Nerd Font and Material Symbols Rounded."),
            ("opt_config", "Theme & configuration", "Deploy to ~/.config, backing up existing files first. "
             "Switch off to build without deploying."),
        ):
            row = Adw.SwitchRow(title=title, subtitle=subtitle, active=True, use_markup=False)
            row.set_subtitle_lines(0)
            setattr(self, key, row)
            group.add(row)
        page.append(group)
        note = _label("Expert overrides are in Advanced and apply to this install.", "install-caption")
        page.append(note)
        self.btn_next2 = self._actions(page, "Continue", self._next)
        return scroll
    def _page_auth(self) -> Gtk.Widget:
        scroll, page, _ = self._step(
            2, "One last thing before we begin.",
            "Package installs and writes to /opt and /usr/local need administrator access.")
        note = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        note.add_css_class("install-panel")
        note.append(_label("YOUR PASSWORD", "install-eyebrow"))
        note.append(_label(
            "Your password is held in this app's memory to answer sudo prompts. "
            "It is never saved to disk or written to the install log.", "install-copy"))
        page.append(note)
        self.pw_entry = Adw.PasswordEntryRow(title="Administrator password")
        self.pw_entry.connect("changed", lambda *_: self._auth_changed())
        group = Adw.PreferencesGroup()
        group.add(self.pw_entry)
        page.append(group)
        self.auth_status = _label("Enter your sudo password to continue.", "install-caption")
        page.append(self.auth_status)
        self.btn_start = self._actions(page, "Start installation", self._start_install)
        self.btn_start.set_sensitive(False)
        return scroll

    def _auth_changed(self) -> None:
        has_password = bool(self.pw_entry.get_text())
        self.btn_start.set_sensitive(has_password and not self.win.state.get("busy"))
        self.auth_status.set_label("Ready when you are. The build may take a while."
                                   if has_password else "Enter your sudo password to continue.")

    def _page_progress(self) -> Gtk.Widget:
        scroll, page, labels = self._step(
            3, "Building your new space.",
            "Follow the build below. You can cancel the running script if you need to stop.")
        self.progress_eyebrow, self.progress_title, self.progress_copy = labels
        self.panel = ScriptPanel(self.win, stages=SETUP_STAGES)
        self.panel.add_css_class("install-panel")
        self.panel.log_view.set_size_request(-1, 260)
        # This embedded panel stays visible after completion so logs remain accessible.
        self.panel.btn_close.set_visible(False)
        page.append(self.panel)
        self.result = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.result.add_css_class("install-panel")
        self.done_title = _label("", "install-result-title")
        self.done_text = _label("", "install-copy")
        self.result.append(self.done_title)
        self.result.append(self.done_text)
        self.btn_finish = self._actions(
            self.result, "Open Setup", lambda *_: self.win.goto_setup(), back=False)
        self.btn_retry = Gtk.Button(label="Review options")
        self.btn_retry.add_css_class("pill")
        self.btn_retry.set_halign(Gtk.Align.START)
        self.btn_retry.connect("clicked", lambda *_: self._go_to(0))
        self.result.append(self.btn_retry)
        self.result.set_visible(False)
        page.append(self.result)
        return scroll

    def _go_to(self, idx: int) -> None:
        idx = max(0, min(idx, len(self.pages) - 1))
        if idx == self._selected_step:
            return
        if self.panel.is_running() and idx != 3:
            return
        direction = 1 if idx > self._page_idx else -1
        self._selected_step = idx

        def commit() -> None:
            self._page_idx = idx
            self.stack.set_visible_child_name(str(idx))
            if idx == 2:
                self._auth_changed()
                self.pw_entry.grab_focus()

        self.transition.navigate(commit, direction)

    def _next(self, _button: Gtk.Button) -> None:
        # Repeated activation during fade-out cannot skip a step.
        if self._selected_step != self._page_idx:
            return
        if self._page_idx == 0 and not self.btn_next1.get_sensitive():
            return
        if self._page_idx < 2:
            self._go_to(self._page_idx + 1)

    def _prev(self, _button: Gtk.Button) -> None:
        if self._selected_step == self._page_idx and self._page_idx in (1, 2):
            self._go_to(self._page_idx - 1)

    def run_script(self, argv: list[str], title: str,
                   done_note: str = "", stages: list[str] | None = None) -> None:
        started = self.panel.start(
            argv, title=title, password=self.win.state.get("password"),
            stages=stages or SETUP_STAGES, done_note=done_note,
            on_finished=self._install_finished)
        if started:
            self._script_title = title
            self._done_note = done_note
            self._t0 = time.time()
            self.result.set_visible(False)
            self.progress_eyebrow.set_label("INSTALL  /  STEP 4 OF 4")
            self.progress_title.set_label("Building your new space.")
            self.progress_copy.set_label("Follow the build below. You can cancel if you need to stop.")
            self._go_to(3)
        else:
            self.auth_status.set_label("Installation did not start. See the notification for details.")

    def _install_finished(self, code: int) -> None:
        ok = code == 0
        mins, secs = divmod(int(time.time() - self._t0), 60)
        self.progress_eyebrow.set_label("INSTALL  /  FINISHED" if ok else "INSTALL  /  STOPPED")
        self.progress_title.set_label("Your new space is ready." if ok else "Let's see what happened.")
        self.progress_copy.set_label("The full build log is kept below for reference.")
        self.done_title.set_label("Installation complete" if ok else "Installation did not complete")
        self.done_title.set_css_classes(["install-result-title", "success" if ok else "error"])
        self.done_text.set_label(
            f"{self._done_note or 'The build completed.'}\n"
            f"Took {mins}m {secs:02d}s. Next: reboot, then choose Hyprland from the login gear menu."
            if ok else
            f"{self._script_title} exited with code {code} after {mins}m {secs:02d}s. "
            "Review the last lines of the log above before trying again.")
        self.btn_finish.set_visible(ok)
        self.btn_retry.set_visible(not ok)
        self.result.set_visible(True)
        self.win.state["installed"] = checks.installed_state()["installed"]
        self._go_to(3)

    def _start_install(self, _button: Gtk.Button | None = None) -> None:
        if self.win.state.get("busy") or not self.pw_entry.get_text():
            return
        if self._checks_loading or checks.blocking(self.checks):
            self.win.toast("Resolve the system checks before starting installation.")
            return
        try:
            script = paths.script("setup.sh")
        except FileNotFoundError as exc:
            self.win.toast(f"Cannot find setup.sh: {exc}")
            return
        self.win.state["password"] = self.pw_entry.get_text()
        argv = ["bash", script]
        for option, flag in ((self.opt_apt, "--skip-apt"), (self.opt_qt, "--skip-qt"),
                             (self.opt_fonts, "--skip-fonts"), (self.opt_config, "--skip-config")):
            if not option.get_active():
                argv.append(flag)
        if self.win.page_advanced.row_ignore_space.get_active():
            argv.append("--ignore-space")
        qtver = self.win.page_advanced.row_qtver.get_text().strip()
        if qtver and qtver != "6.11.2":
            argv += ["--qt-version", qtver]
        self.run_script(argv, title="setup.sh", done_note="The Caelestia desktop is installed.")
