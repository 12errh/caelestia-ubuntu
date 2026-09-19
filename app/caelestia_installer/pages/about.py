"""About page: app version, update check, and a guided issue reporter."""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import diagnostics, paths, releases  # noqa: E402
from ..page_style import build_page  # noqa: E402


class AboutPage(Adw.Bin):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self._checking = False
        self._loading = False
        self._release = None
        box = build_page(
            self, "ABOUT  /  CAELESTIA FOR UBUNTU", "About this app.",
            "Version information, update checks and a way to report problems.")
        self._build_version(box)
        self._build_report(box)

    @staticmethod
    def _group(parent, title, description=""):
        group = Adw.PreferencesGroup(title=title, description=description)
        group.add_css_class("page-panel")
        parent.append(group)
        return group

    @staticmethod
    def _set_row_link(row, url: str) -> None:
        btn = Gtk.Button(label="Open", valign=Gtk.Align.CENTER)
        btn.set_css_classes(["flat"])
        btn.connect("clicked", lambda *_: Gtk.UriLauncher.new(url).launch(
            None, None, None))
        row.add_suffix(btn)

    # ---------------------------------------------------------------- version
    def _build_version(self, parent) -> None:
        group = self._group(parent, "Version")
        self.row_version = Adw.ActionRow(
            title="Installer version", subtitle=paths.VERSION)
        self.row_version.set_use_markup(False)
        self._set_row_link(self.row_version, f"{releases.REPOSITORY}/releases")
        group.add(self.row_version)

        self.btn_check = Gtk.Button(label="Check for updates", valign=Gtk.Align.CENTER)
        self.btn_check.add_css_class("suggested-action")
        self.btn_check.connect("clicked", lambda *_: self._check_async())
        self.row_updates = Adw.ActionRow(
            title="App updates",
            subtitle="Check stable GitHub releases for a newer installer.")
        self.row_updates.set_use_markup(False)
        self.row_updates.add_suffix(self.btn_check)
        group.add(self.row_updates)

    # ----------------------------------------------------------------- report
    def _build_report(self, parent) -> None:
        group = self._group(
            parent, "Report a problem",
            "A diagnostic summary is prepared locally. Nothing is sent until you "
            "submit the issue in your browser — review it first and remove "
            "anything you would rather not share.")
        self.summary = Gtk.TextView(
            editable=False, cursor_visible=False, monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8,
            left_margin=10, right_margin=10)
        self.summary.set_size_request(-1, 180)
        scroll = Gtk.ScrolledWindow(
            child=self.summary, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        scroll.set_size_request(-1, 180)
        group.add(scroll)

        row = Adw.ActionRow(title="Review, then submit",
                            subtitle="The pre-filled issue opens in your browser.")
        row.set_use_markup(False)
        self.btn_issue = Gtk.Button(label="Open on GitHub", valign=Gtk.Align.CENTER)
        self.btn_issue.add_css_class("suggested-action")
        self.btn_issue.connect("clicked", self._on_open_issue)
        row.add_suffix(self.btn_issue)
        group.add(row)

        btn_copy = Gtk.Button(label="Copy report")
        btn_copy.connect("clicked", self._on_copy)
        self.btn_refresh = Gtk.Button(label="Refresh summary")
        self.btn_refresh.connect("clicked", lambda *_: self.refresh_summary())
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.append(btn_copy)
        buttons.append(self.btn_refresh)
        group.add(buttons)

    # ------------------------------------------------------------------- flow
    def on_navigate_to(self) -> None:
        self.refresh_summary()

    def refresh_summary(self) -> None:
        # diagnostics.collect() probes systemd and the pins file; like the Setup
        # page's wallpaper scan, run it off-thread so opening About never lags.
        if self._loading:
            return
        self._loading = True
        self.btn_refresh.set_sensitive(False)

        def worker():
            text = diagnostics.collect()
            GLib.idle_add(self._summary_ready, text)

        threading.Thread(target=worker, daemon=True).start()

    def _summary_ready(self, text: str) -> bool:
        self._loading = False
        self.btn_refresh.set_sensitive(True)
        self.summary.get_buffer().set_text(text)
        return False

    def _buffer_text(self) -> str:
        buf = self.summary.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def _on_copy(self, _button) -> None:
        self.summary.get_display().get_clipboard().set(self._buffer_text())
        self.win.toast("Report copied to the clipboard.")

    def _on_open_issue(self, _button) -> None:
        launcher = Gtk.UriLauncher.new(diagnostics.issue_url(self._buffer_text()))
        launcher.launch(self.win, None, self._issue_opened)

    def _issue_opened(self, launcher, result) -> None:
        try:
            launcher.launch_finish(result)
        except GLib.Error as exc:
            self.win.toast(f"Could not open the issue page: {exc.message}")


    # ---------------------------------------------------------------- updates
    def _check_async(self) -> None:
        if self._checking:
            return
        self._checking = True
        self.btn_check.set_sensitive(False)
        self.row_updates.set_subtitle("Checking stable GitHub releases…")

        def worker():
            try:
                release = releases.fetch_latest()
            except (OSError, ValueError) as exc:
                GLib.idle_add(self._result, None, str(exc))
            else:
                GLib.idle_add(self._result, release, "")
        threading.Thread(target=worker, daemon=True).start()

    def _result(self, release, error) -> bool:
        self._checking = False
        self.btn_check.set_sensitive(True)
        if error:
            self.row_updates.set_subtitle(f"Update check unavailable: {error}")
        elif release is None:
            self.row_updates.set_subtitle("No stable app release has been published yet.")
        elif release.newer_than(paths.VERSION):
            self._release = release
            self.row_updates.set_subtitle(
                f"Version {release.version} is available — open the release page "
                "to download and install it.")
        else:
            self.row_updates.set_subtitle(
                f"You are up to date (latest release: {release.version}).")
        return False
