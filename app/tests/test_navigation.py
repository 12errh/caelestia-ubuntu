"""Window regressions; requires GTK 4, libadwaita and a display."""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from gi.repository import Adw, GLib, GObject, Graphene, Gtk

from caelestia_installer.main import MainWindow, PAGE_META
from caelestia_installer.pages.install import InstallPage
from caelestia_installer.pages.keybinds import KeybindsPage
from caelestia_installer.pages.setup import SetupPage
from caelestia_installer.pages.updates import UpdatesPage
from caelestia_installer.pages.advanced import AdvancedPage


def settle(predicate, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        GLib.MainContext.default().iteration(False)
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("Window did not settle")


def _descendants(widget):
    """Every widget below ``widget``, in tree order."""
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()


def _toast(window):
    """The live AdwToastWidget, or ``None``.

    AdwToastWidget is internal to libadwaita (Adw.Toast has no Python accessor
    for its widget), so it is found by GType name in the overlay's children.
    """
    for widget in _descendants(window.toast_overlay):
        if GObject.type_name(widget) == "AdwToastWidget" and widget.get_visible():
            return widget
    return None


def _root_edge(widget, root, bottom=True):
    """A widget's top or bottom edge, in window coordinates."""
    ok, point = widget.compute_point(root, Graphene.Point().init(0.0, 0.0))
    if not ok:
        raise AssertionError("widget is not realised")
    return point.y + widget.get_height() if bottom else point.y


class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        cls.app = Adw.Application(application_id="io.github.CaelestiaUbuntu.Tests")
        cls.app.register(None)

    def setUp(self):
        # No network, subprocesses, writes or installation from these tests.
        for target, method in ((InstallPage, "refresh_checks_async"),
                               (SetupPage, "on_navigate_to"),
                               (KeybindsPage, "on_navigate_to"),
                               (UpdatesPage, "refresh_async"),
                               (AdvancedPage, "_refresh_advanced")):
            patcher = patch.object(target, method)
            patcher.start()
            self.addCleanup(patcher.stop)
        state = patch("caelestia_installer.main.checks.installed_state",
                      return_value={
                          "installed": False, "manifest": {}, "qs": False,
                          "qt_version": "", "shell_dir": False, "shell_json": False,
                          "session_registered": False, "wallpaper": None,
                      })
        state.start()
        self.addCleanup(state.stop)
        self.settings = Gtk.Settings.get_default()
        original = self.settings.get_property("gtk-enable-animations")
        self.addCleanup(self.settings.set_property, "gtk-enable-animations", original)
        self.errors = []
        hook = patch.object(sys, "excepthook", lambda *args: self.errors.append(args))
        hook.start()
        self.addCleanup(hook.stop)
        self.win = MainWindow(self.app)
        self.addCleanup(self.win.destroy)
        self.win.present()
        settle(self.win.get_mapped)

    def test_all_tabs_and_rapid_navigation(self):
        self.settings.set_property("gtk-enable-animations", True)
        for name in PAGE_META:
            self.win.select_page(name)
            # A generous budget: this test walks all eight pages with animations
            # switched on, and a loaded machine can take longer than settle()'s
            # default to finish one transition (it used to fail intermittently).
            settle(lambda: self.win.transition._phase == "idle"
                   and self.win.stack.get_visible_child_name() == name, timeout=15)
            self.assertTrue(self.win.pages[name].get_mapped())
            self.assertEqual(self.win.stack.get_opacity(), 1)
            self.assertEqual(self.win.title_widget.get_title(), PAGE_META[name][1])
        for name in ("setup", "updates", "install", "guides"):
            self.win.select_page(name)
        settle(lambda: self.win.transition._phase == "idle"
               and self.win.stack.get_visible_child_name() == "guides")
        self.assertEqual(self.errors, [])

    def test_reduced_motion_including_dock(self):
        self.settings.set_property("gtk-enable-animations", False)
        for name in PAGE_META:
            self.win.select_page(name)
            settle(lambda: self.win.transition._phase == "idle"
                   and self.win.stack.get_visible_child_name() == name, timeout=15)
        self.win.dock._prev_t = time.monotonic()
        self.assertFalse(self.win.dock._tick())
        self.assertEqual(self.errors, [])

    def test_about_page_summary_updates_and_dock_centring(self):
        # Eight tabs: four icons, the brand mark, then four more. The split is
        # even, so the mark is centred by construction and there is no spacer
        # padding anywhere in the bar.
        from gi.repository import Gtk

        children = list(self.win.dock)
        buttons = [c for c in children if hasattr(c, "page_id")]
        boxes = {i: c for i, c in enumerate(children) if isinstance(c, Gtk.Box)}
        brand = [i for i, c in boxes.items() if c.get_first_child() is not None]
        spacers = [i for i, c in boxes.items() if c.get_first_child() is None]
        self.assertEqual(len(buttons), 8)
        self.assertEqual(brand, [len(buttons) // 2])
        self.assertEqual(spacers, [])
        self.assertEqual([b.page_id for b in buttons[:4]],
                         ["welcome", "install", "setup", "keybinds"])
        self.assertEqual([b.page_id for b in buttons[4:]],
                         ["updates", "guides", "advanced", "about"])

        self.win.select_page("about")
        settle(lambda: self.win.transition._phase == "idle"
               and self.win.stack.get_visible_child_name() == "about")
        buf = self.win.page_about.summary.get_buffer()
        settle(lambda: buf.get_text(buf.get_start_iter(), buf.get_end_iter(),
                                    False).startswith("Caelestia for Ubuntu installer:")
               and not self.win.page_about._loading)
        self.assertEqual(self.errors, [])

    def test_about_update_check_reports_newer_release(self):
        from caelestia_installer.releases import Release

        self.win.select_page("about")
        settle(lambda: self.win.transition._phase == "idle"
               and self.win.stack.get_visible_child_name() == "about")
        page = self.win.page_about
        with patch("caelestia_installer.pages.about.releases.fetch_latest",
                   return_value=Release("9.9.9")):
            page.btn_check.emit("clicked")
            settle(lambda: "9.9.9" in page.row_updates.get_subtitle()
                   and not page._checking)
        self.assertTrue(page.btn_check.get_sensitive())
        self.assertEqual(self.errors, [])

    def test_build_confirmation_runs_without_blocking_navigation(self):
        import tempfile
        from pathlib import Path
        from caelestia_installer import pins

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            revision = root / "revisions.conf"
            revision.write_text("# test-only pins\n")
            updater = root / "update.sh"
            updater.write_text("echo 'test build started'\nexec sleep 30\n")
            page = self.win.page_advanced
            with patch.object(pins, "revisions_path", return_value=revision), \
                    patch.object(pins, "PINS_DIR", root / "pins"), \
                    patch.object(pins, "PREVIOUS", root / "pins" / "previous.conf"), \
                    patch.object(pins, "backup_shell_json", return_value=None), \
                    patch("caelestia_installer.pages.advanced_actions.paths.script",
                          return_value=str(updater)), \
                    patch.object(self.win, "ensure_password",
                                 side_effect=lambda callback, **kwargs: callback()):
                self.win.select_page("advanced")
                settle(lambda: self.win.transition._phase == "idle")
                page.btn_upstream.set_sensitive(True)
                page.btn_upstream.emit("clicked")
                dialog = self.win.get_visible_dialog()
                self.assertIsNotNone(dialog)
                dialog.emit("response", "ok")
                dialog.close()
                try:
                    settle(lambda: page.panel.is_running())
                    self.win.select_page("guides")
                    settle(lambda: self.win.transition._phase == "idle"
                           and self.win.stack.get_visible_child_name() == "guides")
                    self.assertTrue(self.win.state["busy"])
                finally:
                    page.panel.btn_cancel.emit("clicked")
                    settle(lambda: not self.win.state["busy"])
                self.assertEqual(revision.read_text(), "# test-only pins\n")
                self.assertEqual(self.errors, [])


    def test_advanced_controls_are_unique_and_used_by_install(self):
        advanced = self.win.page_advanced
        install = self.win.page_install
        self.assertFalse(hasattr(install, "row_qtver"))
        self.assertFalse(hasattr(self.win.page_setup, "btn_restart_shell"))
        self.assertFalse(hasattr(self.win.page_updates, "btn_upstream"))
        advanced.row_qtver.set_text("6.11.3")
        advanced.row_ignore_space.set_active(True)
        install.pw_entry.set_text("test-only")
        with patch.object(install, "run_script") as run:
            install._start_install()
        argv = run.call_args.args[0]
        self.assertIn("--ignore-space", argv)
        self.assertEqual(argv[argv.index("--qt-version") + 1], "6.11.3")
        # setup.sh asks its questions with `read`, and the script panel's PTY
        # only ever answers *sudo* — a prompt left hanging there stops the
        # install dead, with nothing on screen the user could type into. The GUI
        # therefore has to answer them up front, with --yes.
        self.assertIn("--yes", argv)
        self.win.state["busy"] = True
        advanced._refresh_advanced_buttons()
        self.assertFalse(advanced.row_qtver.get_sensitive())
        self.assertFalse(advanced.btn_upstream.get_sensitive())
        self.assertEqual(self.errors, [])


    def test_toasts_open_above_the_floating_dock(self):
        """libadwaita anchors a toast to the bottom, where the dock floats.

        Unfixed, a toast landed 34px above the content edge — well *inside* the
        dock's footprint, so it opened hidden behind the navigation bar.
        """
        from caelestia_installer import dock as dock_module
        from caelestia_installer.main import (DOCK_BOTTOM_MARGIN, DISC_OVERFLOW,
                                              TOAST_CLEARANCE)

        # Everything the dock can occupy: the bar, its bottom margin, and the
        # height a magnified disc rises above the pill while hovered.
        dock_extent = DOCK_BOTTOM_MARGIN + dock_module.BAR_HEIGHT + DISC_OVERFLOW
        self.assertGreaterEqual(TOAST_CLEARANCE, dock_extent)

        self.win.toast("placement probe")
        settle(lambda: _toast(self.win) is not None)

        def clearance():
            toast = _toast(self.win)
            if toast is None:
                return -1
            root = self.win.get_root()
            bottom = _root_edge(self.win.stack, root, bottom=True)
            return bottom - _root_edge(toast, root, bottom=True)

        # Wait for the slide-in to finish rather than measuring mid-animation.
        settle(lambda: clearance() >= TOAST_CLEARANCE - 12)
        self.assertGreaterEqual(clearance(), dock_extent)
        self.assertEqual(self.errors, [])


    def test_startup_check_offers_and_installs_a_confirmed_update(self):
        """Startup check -> confirmation -> download -> apt, in that order."""
        import tempfile
        from caelestia_installer.releases import Release
        from caelestia_installer.updater import asset_name

        page = self.win.page_about
        self.assertFalse(page.btn_install.get_visible())
        calls = []

        with tempfile.TemporaryDirectory() as directory:
            staged = Path(directory) / asset_name("9.9.9")

            def fake_download(version, directory=None, on_progress=None):
                calls.append(("download", version))
                staged.write_bytes(b"not a real deb")
                return staged

            with patch("caelestia_installer.pages.about.releases.fetch_latest",
                       return_value=Release("9.9.9")), \
                    patch("caelestia_installer.pages.about.updater."
                          "installed_from_package", return_value=True), \
                    patch("caelestia_installer.pages.about.paths.runs_from_clone",
                          return_value=False), \
                    patch("caelestia_installer.pages.about.updater.download",
                          side_effect=fake_download), \
                    patch("caelestia_installer.pages.about.updater.install_command",
                          return_value=["/bin/echo", "apt-get", "install", "-y"]), \
                    patch.object(self.win, "ensure_password",
                                 side_effect=lambda callback, **kwargs: callback()):
                page.check_async(startup=True)
                # The check runs on a worker thread; let the result reach the loop.
                settle(lambda: self.win.get_visible_dialog() is not None)

                # Offered, not performed: a confirmation stands between the
                # finding and the download.
                dialog = self.win.get_visible_dialog()
                self.assertEqual(calls, [])
                dialog.emit("response", "install")
                dialog.close()

                # The final subtitle is written by _install_finished, i.e. after
                # the panel has finished writing its log.
                settle(lambda: page.row_updates.get_subtitle().startswith("Updated to"))
                self.assertEqual(calls, [("download", "9.9.9")])
                buf = page.panel.log_view.get_buffer()
                log = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
                self.assertIn(f"[done] apt install {asset_name('9.9.9')} finished", log)

            # The verified package is cleaned up once apt is done with it.
            self.assertFalse(staged.exists())
        self.assertEqual(self.errors, [])

    def test_declining_the_update_downloads_nothing(self):
        from caelestia_installer.releases import Release

        page = self.win.page_about
        with patch("caelestia_installer.pages.about.releases.fetch_latest",
                   return_value=Release("9.9.9")), \
                patch("caelestia_installer.pages.about.updater."
                      "installed_from_package", return_value=True), \
                patch("caelestia_installer.pages.about.paths.runs_from_clone",
                      return_value=False), \
                patch("caelestia_installer.pages.about.updater.download") as download:
            page.check_async(startup=True)
            settle(lambda: self.win.get_visible_dialog() is not None)
            dialog = self.win.get_visible_dialog()
            dialog.emit("response", "cancel")
            dialog.close()
            settle(lambda: not page._checking and not page._installing)
            download.assert_not_called()
            # Still offerable from the row.
            self.assertTrue(page.btn_install.get_visible())
        self.assertEqual(self.errors, [])

    def test_constructing_the_window_never_checks_for_updates(self):
        # The startup check lives in run_gui's activate, so building a window
        # (tests, and any embedder) does no network I/O.
        self.assertFalse(self.win.page_about._checking)
        self.assertIsNone(self.win.page_about._release)


    def test_source_install_is_offered_its_own_update_route(self):
        """The .deb is not offered to an app that is not running as a package.

        The package's postinst deletes /usr/local and ~/.local copies on
        purpose, so installing it here would switch the user's install method
        and remove the app they are running.
        """
        from caelestia_installer.releases import Release

        page = self.win.page_about
        cases = (
            # A checkout, with and without a package also installed: either way
            # the answer is `git pull`, never apt.
            (True, False, "git pull"),
            (True, True, "git pull"),
            (False, False, "app/install.sh"),
        )
        for from_clone, packaged, route in cases:
            page._release = None          # each pass starts from a clean slate
            with self.subTest(from_clone=from_clone, packaged=packaged), \
                    patch("caelestia_installer.pages.about.releases.fetch_latest",
                          return_value=Release("9.9.9")), \
                    patch("caelestia_installer.pages.about.updater."
                          "installed_from_package", return_value=packaged), \
                    patch("caelestia_installer.pages.about.paths.runs_from_clone",
                          return_value=from_clone):
                page.check_async(startup=True)
                settle(lambda: route in page.row_updates.get_subtitle())
                self.assertIsNone(self.win.get_visible_dialog(),
                                  "a source install must not be prompted to apt")
                self.assertFalse(page.btn_install.get_visible())
                subtitle = page.row_updates.get_subtitle()
                self.assertIn("9.9.9", subtitle)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
