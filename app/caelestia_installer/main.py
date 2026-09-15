"""GTK4 / libadwaita main window.

Layout: a :class:`Adw.ToolbarView` with a header bar and a
:class:`Gtk.Overlay` that layers an :class:`Adw.ToastOverlay` (holding
an :class:`Adw.ViewStack`) with a :class:`FloatingDock` floating at
the bottom centre.

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
from .dock import FloatingDock  # noqa: E402
from .pages.advanced import AdvancedPage  # noqa: E402
from .pages.guides import GuidesPage  # noqa: E402
from .pages.install import InstallPage  # noqa: E402
from .pages.setup import SetupPage  # noqa: E402
from .pages.updates import UpdatesPage  # noqa: E402
from .pages.welcome import WelcomePage  # noqa: E402
from .runner import sudo_ready, verify_sudo_password  # noqa: E402

# page id -> (page title, header subtitle)
PAGE_META: dict[str, tuple[str, str, str]] = {
    "welcome":  ("Welcome",  "Welcome",  "Install, set up and learn Caelestia"),
    "install":  ("Install",  "Install",  "System checks, options and live progress"),
    "setup":    ("Setup",    "Setup",    "Wallpaper, appearance and maintenance"),
    "updates":  ("Updates",  "Updates",  "Check installed revisions and apply updates"),
    "guides":   ("Guides",   "Guides",   "First login, keybinds and troubleshooting"),
    "advanced": ("Advanced", "Advanced", "Run repository scripts directly"),
}

DOCK_ICONS = {
    "welcome":  "starred-symbolic",
    "install":  "system-software-install-symbolic",
    "setup":    "emblem-system-symbolic",
    "updates":  "software-update-available-symbolic",
    "guides":   "help-about-symbolic",
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

        # ---- root layout: ToolbarView → Overlay → (ToastOverlay + Dock) ----
        toolbar_view = Adw.ToolbarView.new()

        self.header = Adw.HeaderBar.new()
        self.title_widget = Adw.WindowTitle.new(
            PAGE_META["welcome"][1], PAGE_META["welcome"][2])
        self.header.set_title_widget(self.title_widget)
        toolbar_view.add_top_bar(self.header)

        overlay = Gtk.Overlay.new()
        overlay.set_vexpand(True)
        overlay.set_hexpand(True)

        # Content stack (fills the overlay).
        self.toast_overlay = Adw.ToastOverlay.new()
        self.stack = Adw.ViewStack.new()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.toast_overlay.set_child(self.stack)
        overlay.set_child(self.toast_overlay)

        # Floating dock (overlaid at the bottom centre).
        self.dock = FloatingDock(on_select=self.select_page)
        for row_id, (_label, _title, _sub) in PAGE_META.items():
            self.dock.add_item(row_id, DOCK_ICONS[row_id], _title)
        overlay.add_overlay(self.dock)

        toolbar_view.set_content(overlay)
        self.set_content(toolbar_view)

        # Build pages --------------------------------------------------------
        self.page_welcome  = WelcomePage(self)
        self.page_install  = InstallPage(self)
        self.page_setup    = SetupPage(self)
        self.page_updates  = UpdatesPage(self)
        self.page_guides   = GuidesPage(self)
        self.page_advanced = AdvancedPage(self)

        self.pages = {
            "welcome":  self.page_welcome,
            "install":  self.page_install,
            "setup":    self.page_setup,
            "updates":  self.page_updates,
            "guides":   self.page_guides,
            "advanced": self.page_advanced,
        }
        for row_id, page in self.pages.items():
            self.stack.add_named(page, row_id)

        self.state["installed"] = checks.installed_state()["installed"]
        self.select_page("welcome")

    # ------------------------------------------------------------ navigation
    def select_page(self, page_id: str) -> None:
        """Navigate to a page by id."""
        page = self.pages.get(page_id)
        if page is None:
            return
        if hasattr(page, "on_navigate_to"):
            page.on_navigate_to()
        self.stack.set_visible_child_name(page_id)
        title, subtitle = PAGE_META[page_id][1], PAGE_META[page_id][2]
        self.title_widget.set_title(title)
        self.title_widget.set_subtitle(subtitle)
        self.dock.set_active(page_id)

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
            self.toast("Checking password\u2026")

            def worker() -> None:
                ok = verify_sudo_password(password)
                GLib.idle_add(self._after_verify, ok, password, on_ok)

            threading.Thread(target=worker, daemon=True).start()

        dialog.connect("response", on_response)
        dialog.present(self)

    def _after_verify(self, ok: bool, password: str,
                      on_ok: Callable[[], None]) -> bool:
        if not ok:
            self.toast("Incorrect password \u2014 please try again.")
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
