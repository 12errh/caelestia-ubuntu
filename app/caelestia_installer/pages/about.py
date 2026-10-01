"""About page: app version, update install, and a guided issue reporter."""

from __future__ import annotations

from pathlib import Path
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import diagnostics, paths, releases, updater  # noqa: E402
from ..page_style import build_page  # noqa: E402
from .script_panel import ScriptPanel  # noqa: E402

#: How to update when this running copy cannot install its own package — key is
#: ``paths.runs_from_clone()``. Both routes keep the install method the user
#: chose; installing the package instead would replace it.
_MANUAL_UPDATE = {
    True: ("This copy runs from a checkout, so update it with "
           "`git pull` in the repository."),
    False: ("This copy was installed with app/install.sh, so update it by "
            "re-running `sudo ./app/install.sh` from the repository."),
}


class AboutPage(Adw.Bin):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self._checking = False
        self._loading = False
        self._release = None
        self._installing = False
        self._package: Path | None = None
        self._progress_step = 0
        box = build_page(
            self, "ABOUT  /  CAELESTIA FOR UBUNTU", "About this app.",
            "Version information, update checks and a way to report problems.")
        self._build_version(box)
        self.panel = ScriptPanel(win)
        self.panel.add_css_class("page-panel")
        box.append(self.panel)
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
        self.btn_check.connect("clicked", lambda *_: self.check_async())
        self.btn_install = Gtk.Button(label="Install update", valign=Gtk.Align.CENTER)
        self.btn_install.add_css_class("suggested-action")
        self.btn_install.set_visible(False)
        self.btn_install.connect("clicked", lambda *_: self.confirm_install(self._release))
        self.row_updates = Adw.ActionRow(
            title="App updates",
            subtitle="Checks the published release on startup and offers to "
                     "install it. Nothing is downloaded or installed without "
                     "your confirmation.")
        self.row_updates.set_use_markup(False)
        self.row_updates.add_suffix(self.btn_install)
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
    def check_async(self, startup: bool = False) -> None:
        """Ask GitHub for the latest release, off the GTK thread.

        Called once per launch and by the Check button. A startup check never
        interrupts with a dialog unless an update is actually there to confirm;
        an offline or rate-limited result is only reported on the row, because
        the user did not ask for that check.
        """
        if self._checking or self._installing:
            return
        self._checking = True
        self.btn_check.set_sensitive(False)
        if not self._release:
            self.row_updates.set_subtitle("Checking for a newer release…")

        def worker():
            try:
                release = releases.fetch_latest()
            except (OSError, ValueError) as exc:
                GLib.idle_add(self._result, None, str(exc), startup)
            else:
                GLib.idle_add(self._result, release, "", startup)
        threading.Thread(target=worker, daemon=True).start()

    def _result(self, release, error, startup: bool) -> bool:
        self._checking = False
        self.btn_check.set_sensitive(True)
        if error:
            self.row_updates.set_subtitle(f"Update check unavailable: {error}")
        elif release is None:
            self.row_updates.set_subtitle("No stable app release has been published yet.")
        elif release.newer_than(paths.VERSION):
            self._release = release
            from_clone = paths.runs_from_clone()
            # Only a packaged install may replace itself: the .deb's postinst
            # removes /usr/local and ~/.local copies on purpose, so offering it
            # to a checkout or an install.sh copy would switch the user's
            # install method underneath them.
            if updater.installed_from_package() and not from_clone:
                self.btn_install.set_visible(True)
                self.row_updates.set_subtitle(
                    f"Version {release.version} is available — it can be "
                    "downloaded and installed from here.")
                if startup:
                    self.confirm_install(release)
            else:
                self.btn_install.set_visible(False)
                self.row_updates.set_subtitle(
                    f"Version {release.version} is available. "
                    f"{_MANUAL_UPDATE[from_clone]}")
        else:
            self._release = None
            self.btn_install.set_visible(False)
            self.row_updates.set_subtitle(
                f"You are up to date (latest release: {release.version}).")
        return False

    # ---------------------------------------------------------------- install
    def confirm_install(self, release) -> None:
        """Ask before anything is downloaded, then before anything is installed."""
        if release is None or self._installing or self.win.state.get("busy"):
            return
        dialog = Adw.AlertDialog.new(
            f"Update to {release.version}?",
            f"The package is downloaded from {releases.REPOSITORY}, checked "
            f"against the release's {updater.CHECKSUMS_NAME} file, then installed "
            "with apt. Installing needs your administrator password.",
        )
        dialog.add_response("cancel", "Not now")
        dialog.add_response("install", "Update")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response: str) -> None:
            if response == "install":
                self.win.ensure_password(lambda: self._download(release))

        dialog.connect("response", on_response)
        dialog.present(self.win)

    def _download(self, release) -> None:
        """Fetch and verify the package, then install it. Off the GTK thread."""
        self._installing = True
        self.btn_install.set_sensitive(False)
        self.btn_check.set_sensitive(False)
        self.win.toast(f"Downloading {release.version}…")

        def worker():
            try:
                package = updater.download(release.version, on_progress=self._progress)
            except (OSError, ValueError) as exc:
                GLib.idle_add(self._download_failed, str(exc))
            else:
                self._package = package
                GLib.idle_add(self._start_install)
        threading.Thread(target=worker, daemon=True).start()

    def _progress(self, written: int) -> None:
        # Called on the download thread; only report meaningful milestones.
        step = written // (256 * 1024)
        if step > self._progress_step:
            self._progress_step = step
            GLib.idle_add(self.win.toast, f"Downloading… {step * 256} kB")

    def _download_failed(self, error: str) -> bool:
        self._installing = False
        self.btn_install.set_sensitive(True)
        self.btn_check.set_sensitive(True)
        self.row_updates.set_subtitle(f"Update not installed: {error}")
        return False

    def _start_install(self) -> bool:
        package = self._package
        if package is None:
            return self._download_failed("no package was downloaded")
        argv = updater.install_command(package)
        started = self.panel.start(
            argv,
            title=f"apt install {package.name}",
            password=self.win.state.get("password") or None,
            done_note=f"Updated to {self._release.version}. Restart the app to use it.",
            on_finished=self._install_finished,
        )
        if not started:
            return self._download_failed("another script is already running")
        self.row_updates.set_subtitle(f"Installing {package.name}…")
        return False

    def _install_finished(self, code: int) -> None:
        self._installing = False
        self.btn_check.set_sensitive(True)
        if self._package is not None:
            updater.remove(self._package)
            self._package = None
        if code != 0:
            self.btn_install.set_sensitive(True)
            self.row_updates.set_subtitle(
                f"Update failed (apt exited with code {code}) — the log below has "
                "the details.")
            return
        version = self._release.version if self._release else paths.VERSION
        self.row_updates.set_subtitle(
            f"Updated to {version}. Restart Caelestia for Ubuntu to use it.")
        self._offer_restart(version)

    def _offer_restart(self, version: str) -> None:
        """The running process still holds the old code; offer to reload it."""
        dialog = Adw.AlertDialog.new(
            f"Caelestia for Ubuntu {version} is installed.",
            "Restart the app to finish the update.",
        )
        dialog.add_response("later", "Later")
        dialog.add_response("restart", "Restart now")
        dialog.set_response_appearance("restart", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("restart")
        dialog.set_close_response("later")

        def on_response(_dialog, response: str) -> None:
            if response == "restart":
                self.win.restart_app()

        dialog.connect("response", on_response)
        dialog.present(self.win)
