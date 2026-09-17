"""Window regressions; requires GTK 4, libadwaita and a display."""

import sys
import time
import unittest
from unittest.mock import patch

from caelestia_installer.main import MainWindow, PAGE_META
from caelestia_installer.pages.install import InstallPage
from caelestia_installer.pages.setup import SetupPage
from caelestia_installer.pages.updates import UpdatesPage
from caelestia_installer.pages.advanced import AdvancedPage
from gi.repository import Adw, GLib, Gtk


def settle(predicate, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        GLib.MainContext.default().iteration(False)
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("Window did not settle")


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
                               (UpdatesPage, "refresh_async"),
                               (AdvancedPage, "_refresh_advanced")):
            patcher = patch.object(target, method)
            patcher.start()
            self.addCleanup(patcher.stop)
        state = patch("caelestia_installer.main.checks.installed_state",
                      return_value={"installed": False})
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
            settle(lambda: self.win.transition._phase == "idle"
                   and self.win.stack.get_visible_child_name() == name)
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
                   and self.win.stack.get_visible_child_name() == name)
        self.win.dock._prev_t = time.monotonic()
        self.assertFalse(self.win.dock._tick())
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
        self.win.state["busy"] = True
        advanced._refresh_advanced_buttons()
        self.assertFalse(advanced.row_qtver.get_sensitive())
        self.assertFalse(advanced.btn_upstream.get_sensitive())
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
