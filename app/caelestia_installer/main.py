"""GTK4 / libadwaita main window.

Layout: a :class:`Adw.ToolbarView` with a header bar and a
:class:`Gtk.Overlay` that layers an :class:`Adw.ToastOverlay` (holding
an :class:`Adw.ViewStack`) with a :class:`FloatingDock` floating at
the bottom centre.

All heavy work runs in threads; UI updates go through ``GLib.idle_add``.
"""

from __future__ import annotations

import math
import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import VERSION, checks, paths  # noqa: E402
from .motion import PageTransition  # noqa: E402
from .dock import FloatingDock  # noqa: E402
from . import glass  # noqa: E402
from .pages.advanced import AdvancedPage  # noqa: E402
from .pages.about import AboutPage  # noqa: E402
from .pages.guides import GuidesPage  # noqa: E402
from .pages.install import InstallPage  # noqa: E402
from .pages.keybinds import KeybindsPage  # noqa: E402
from .pages.setup import SetupPage  # noqa: E402
from .pages.updates import UpdatesPage  # noqa: E402
from .pages.welcome import WelcomePage  # noqa: E402
from . import theme  # noqa: E402
from .runner import sudo_ready, verify_sudo_password  # noqa: E402

# page id -> (page title, header subtitle)
PAGE_META: dict[str, tuple[str, str, str]] = {
    "welcome":  ("Welcome",  "Welcome",  "Install, set up and learn Caelestia"),
    "install":  ("Install",  "Install",  "System checks, options and live progress"),
    "setup":    ("Setup",    "Setup",    "Wallpaper, appearance and idle settings"),
    "keybinds": ("Keybinds", "Keybinds", "See, add and edit every shortcut"),
    "updates":  ("Updates",  "Updates",  "Check installed revisions and apply updates"),
    "guides":   ("Guides",   "Guides",   "First login, keybinds and troubleshooting"),
    "advanced": ("Advanced", "Advanced", "Installation overrides, experimental builds and recovery"),
    "about":    ("About",    "About",    "App version, updates and issue reporting"),
}

# Dock icons, chosen so each glyph *says* what its tab does at the 20px idle
# size (the dock briefly doubles them on hover, but 20px is the size that has
# to read). Every name below is a long-standing Adwaita symbolic icon, so it
# resolves on Ubuntu/Zorin/Mint/Pop!_OS alike; dock.py still falls back to a
# generic glyph if a theme is missing one.
#
# Picked by rendering each candidate through the icon theme and comparing the
# actual pixels, because names lie: on Zorin's theme emblem-system,
# preferences-system and applications-system are the *same* picture, while
# software-update-available draws as a packed badge that muddles at 20px.
DOCK_ICONS = {
    "welcome":  "go-home-symbolic",                 # house  -> the landing tab
    "install":  "folder-download-symbolic",         # arrow into tray -> install the stack
    "setup":    "preferences-system-symbolic",      # cog    -> appearance / settings
    "keybinds": "input-keyboard-symbolic",          # keyboard -> the shortcut manager
    "updates":  "update-symbolic",                  # crisp circular arrows -> check & apply
    "guides":   "accessories-dictionary-symbolic",  # open book -> the built-in manual
    "advanced": "utilities-terminal-symbolic",      # $ prompt -> run repo scripts directly
    "about":    "help-about-symbolic",              # info badge -> version & feedback
}


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, **kwargs) -> None:
        super().__init__(application=app, **kwargs)
        self.set_title(paths.APP_NAME)
        self.set_icon_name(paths.APP_ID)
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
        # The page fades to transparent during navigation. Keep its backdrop
        # identical to the pages rather than exposing the decorative aurora.
        self.stack = Adw.ViewStack.new()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.transition = PageTransition(self.stack)
        self._selected_page = None
        self.toast_overlay.set_child(self.transition)
        overlay.set_child(self.toast_overlay)

        # Floating dock (overlaid at the bottom centre) — wrapped in Liquid Glass.
        self.dock = FloatingDock(on_select=self.select_page)
        # Brand mark in the middle gap. There are eight tabs, so the two halves
        # hold four icons each and no padding is needed: the mark sits dead
        # centre with the bar perfectly balanced around it.
        half = len(PAGE_META) // 2
        for i, (row_id, (_label, _title, _sub)) in enumerate(PAGE_META.items()):
            self.dock.add_item(row_id, DOCK_ICONS[row_id], _title)
            if i + 1 == half:
                self.dock.add_brand(paths.BRAND_LOGO, paths.APP_NAME)

        # The glass panel floats above the content (source = toast_overlay) so
        # it refracts whatever page is visible behind the dock.
        self.glass_dock = glass.glassify(
            self.dock,
            self.toast_overlay,
            glass.REGULAR,
            corner_radius=16.0,
            light=self._dock_light,
            hover=self.dock,
        )
        self.glass_dock.set_margin_bottom(18)
        self.glass_dock.set_halign(Gtk.Align.CENTER)
        self.glass_dock.set_valign(Gtk.Align.END)
        overlay.add_overlay(self.glass_dock)

        toolbar_view.set_content(overlay)
        self.set_content(toolbar_view)

        # Build pages --------------------------------------------------------
        self.page_welcome  = WelcomePage(self)
        self.page_install  = InstallPage(self)
        self.page_setup    = SetupPage(self)
        self.page_keybinds = KeybindsPage(self)
        self.page_updates  = UpdatesPage(self)
        self.page_guides   = GuidesPage(self)
        self.page_advanced = AdvancedPage(self)
        self.page_about = AboutPage(self)

        self.pages = {
            "welcome":  self.page_welcome,
            "install":  self.page_install,
            "setup":    self.page_setup,
            "keybinds": self.page_keybinds,
            "updates":  self.page_updates,
            "guides":   self.page_guides,
            "advanced": self.page_advanced,
            "about":    self.page_about,
        }
        for row_id, page in self.pages.items():
            self.stack.add_named(page, row_id)

        self.state["installed"] = checks.installed_state()["installed"]
        self.select_page("welcome")

    # ------------------------------------------------------------ navigation
    def select_page(self, page_id: str) -> None:
        """Navigate to a page by id."""
        page = self.pages.get(page_id)
        if page is None or page_id == self._selected_page:
            return
        order = list(self.pages)
        previous = self._selected_page
        self._selected_page = page_id
        direction = 1 if previous is None or order.index(page_id) > order.index(previous) else -1

        def commit() -> None:
            if hasattr(page, "on_navigate_to"):
                page.on_navigate_to()
            self.stack.set_visible_child_name(page_id)
            title, subtitle = PAGE_META[page_id][1], PAGE_META[page_id][2]
            self.title_widget.set_title(title)
            self.title_widget.set_subtitle(subtitle)

        self.transition.navigate(commit, direction)
        self.dock.set_active(page_id)

    def goto_setup(self) -> None:
        self.select_page("setup")

    def goto_updates(self) -> None:
        self.select_page("updates")

    def goto_install(self) -> None:
        self.select_page("install")

    def _dock_light(self) -> tuple[float, float]:
        """Pointer direction from the glass dock centre — drives the specular rim."""
        try:
            mx = self.dock.mouse_x
            if mx == math.inf:
                return (0.0, -1.0)
            alloc = self.glass_dock.get_allocation()
            cx = alloc.x + alloc.width / 2.0
            return ((mx - cx) / max(alloc.width / 2.0, 1.0), -1.0)
        except Exception:  # noqa: BLE001
            return (0.0, -1.0)

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
    theme.apply()          # before any window exists: no flash of default chrome
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
