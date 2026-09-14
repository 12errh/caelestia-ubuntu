"""GTK4 / libadwaita main window.

Layout: an :class:`Adw.NavigationSplitView` with a sidebar list
(Welcome → Install → Setup → Updates → Guides → Advanced) and a single
content header. Pages are plain widgets switched in an :class:`Adw.ViewStack`,
so there is exactly one title bar at a time (the sidebar keeps its own).

All heavy work runs in threads; UI updates go through ``GLib.idle_add``.
"""

from __future__ import annotations

import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import VERSION, checks, paths  # noqa: E402
from .pages.advanced import AdvancedPage  # noqa: E402
from .pages.guides import GuidesPage  # noqa: E402
from .pages.install import InstallPage  # noqa: E402
from .pages.setup import SetupPage  # noqa: E402
from .pages.updates import UpdatesPage  # noqa: E402
from .pages.welcome import WelcomePage  # noqa: E402
from .runner import sudo_ready, verify_sudo_password  # noqa: E402

# row id -> (sidebar label, page title, header subtitle)
PAGE_META: dict[str, tuple[str, str, str]] = {
    "welcome": ("Welcome", "Welcome", "Install, set up and learn Caelestia"),
    "install": ("Install", "Install", "System checks, options and live progress"),
    "setup": ("Setup", "Setup", "Wallpaper, appearance and maintenance"),
    "updates": ("Updates", "Updates", "Check installed revisions and apply updates"),
    "guides": ("Guides", "Guides", "First login, keybinds and troubleshooting"),
    "advanced": ("Advanced", "Advanced", "Run repository scripts directly"),
}

SIDEBAR_ICONS = {
    "welcome": "starred-symbolic",
    "install": "system-software-install-symbolic",
    "setup": "emblem-system-symbolic",
    "updates": "software-update-available-symbolic",
    "guides": "help-about-symbolic",
    "advanced": "applications-utilities-symbolic",
}


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, **kwargs) -> None:
        super().__init__(application=app, **kwargs)
        self.set_title(paths.APP_NAME)
        self.set_default_size(1000, 720)
        self.app = app

        # Shared app state --------------------------------------------------
        self.state = {
            "password": "",        # sudo password from the auth page / prompt
            "busy": False,         # a script is running
            "installed": False,    # refreshed by pages on demand
        }

        self.split = Adw.NavigationSplitView.new()
        self.split.set_max_sidebar_width(290)
        self.split.set_min_sidebar_width(210)
        self.set_content(self.split)

        self._build_sidebar()
        self._build_content()

        self.state["installed"] = checks.installed_state()["installed"]
        self.select_page("welcome")

    # --------------------------------------------------------------- sidebar
    def _build_sidebar(self) -> None:
        sidebar_page = Adw.NavigationPage.new(Adw.ToolbarView.new(), "Navigation")
        sidebar = sidebar_page.get_child()
        sidebar.add_top_bar(Adw.HeaderBar.new())

        self.listbox = Gtk.ListBox.new()
        self.listbox.set_css_classes(["navigation-sidebar"])
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._on_row_selected)

        for row_id, (label, _title, _sub) in PAGE_META.items():
            box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            box.append(Gtk.Image.new_from_icon_name(SIDEBAR_ICONS[row_id]))
            box.append(Gtk.Label.new(label))
            row = Gtk.ListBoxRow.new()
            row.set_child(box)
            row.row_id = row_id  # type: ignore[attr-defined]
            self.listbox.append(row)

        scrolled = Gtk.ScrolledWindow.new()
        scrolled.set_child(self.listbox)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        sidebar.set_content(scrolled)
        self.split.set_sidebar(sidebar_page)

    # --------------------------------------------------------------- content
    def _build_content(self) -> None:
        content_page = Adw.NavigationPage.new(Adw.ToolbarView.new(), paths.APP_NAME)
        content_view = content_page.get_child()

        self.header = Adw.HeaderBar.new()
        self.title_widget = Adw.WindowTitle.new(PAGE_META["welcome"][1],
                                                PAGE_META["welcome"][2])
        self.header.set_title_widget(self.title_widget)
        content_view.add_top_bar(self.header)

        # A real toast overlay so feedback actually shows up.
        self.toast_overlay = Adw.ToastOverlay.new()
        self.stack = Adw.ViewStack.new()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.toast_overlay.set_child(self.stack)
        content_view.set_content(self.toast_overlay)

        self.page_welcome = WelcomePage(self)
        self.page_install = InstallPage(self)
        self.page_setup = SetupPage(self)
        self.page_updates = UpdatesPage(self)
        self.page_guides = GuidesPage(self)
        self.page_advanced = AdvancedPage(self)

        self.pages = {
            "welcome": self.page_welcome,
            "install": self.page_install,
            "setup": self.page_setup,
            "updates": self.page_updates,
            "guides": self.page_guides,
            "advanced": self.page_advanced,
        }
        for row_id, page in self.pages.items():
            self.stack.add_named(page, row_id)

        self.split.set_content(content_page)

    # ------------------------------------------------------------ navigation
    def _on_row_selected(self, _box: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        row_id = getattr(row, "row_id", None)
        page = self.pages.get(row_id)
        if page is None:
            return
        if hasattr(page, "on_navigate_to"):
            page.on_navigate_to()
        self.stack.set_visible_child_name(row_id)
        title, subtitle = PAGE_META[row_id][1], PAGE_META[row_id][2]
        self.title_widget.set_title(title)
        self.title_widget.set_subtitle(subtitle)
        self.split.set_show_content(True)

    def select_page(self, row_id: str) -> None:
        """Select a sidebar row by id (robust to reordering)."""
        for i in range(len(PAGE_META)):
            row = self.listbox.get_row_at_index(i)
            if getattr(row, "row_id", None) == row_id:
                self.listbox.select_row(row)
                return

    def goto_setup(self) -> None:
        self.select_page("setup")

    def goto_updates(self) -> None:
        self.select_page("updates")

    def goto_install(self) -> None:
        self.select_page("install")

    # -------------------------------------------------------------- feedback
    def toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast.new(message))

    # -------------------------------------------------------------- sudo auth
    def ensure_password(self, on_ok: Callable[[], None],
                        force: bool = False) -> None:
        """Call ``on_ok`` once sudo is usable, prompting for a password if needed.

        ``force=True`` always ensures a password is stored (for long builds whose
        cached sudo timestamp can expire mid-run and re-prompt the PTY). The
        password lives in :attr:`state` only; it is never written to disk.
        """
        if self.state.get("password") or (not force and sudo_ready()):
            on_ok()
            return
        self._prompt_password(on_ok)

    def _prompt_password(self, on_ok: Callable[[], None]) -> None:
        dialog = Adw.AlertDialog.new(
            "Administrator password",
            "This step needs sudo. Your password is kept only in this app's "
            "memory and is never written to disk or to any log file.",
        )
        entry = Gtk.PasswordEntry.new()
        entry.set_show_peek_icon(True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Continue")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response: str) -> None:
            if response != "ok":
                return
            password = entry.get_text()
            if not password:
                self.toast("A password is required for this step.")
                return
            self.toast("Checking password…")

            def worker() -> None:
                ok = verify_sudo_password(password)
                GLib.idle_add(self._after_verify, ok, password, on_ok)

            threading.Thread(target=worker, daemon=True).start()

        dialog.connect("response", on_response)
        dialog.present(self)

    def _after_verify(self, ok: bool, password: str,
                      on_ok: Callable[[], None]) -> bool:
        if not ok:
            self.toast("Incorrect password — please try again.")
            self._prompt_password(on_ok)
            return False
        self.state["password"] = password
        on_ok()
        return False


def run_gui() -> int:
    Adw.init()
    app = Adw.Application(application_id=paths.APP_ID)
    app.set_version(VERSION)

    def on_activate(application: Adw.Application) -> None:
        win = MainWindow(application)
        win.present()

    app.connect("activate", on_activate)
    try:
        app.register(None)
    except Exception:  # noqa: BLE001 - registration is optional (dev runs)
        pass
    return app.run(None)
