"""GTK regressions: PYTHONPATH=app python3 -m unittest discover -s app/tests."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from caelestia_installer import paths
from caelestia_installer.pages.setup import SetupPage
from caelestia_installer.wallpapers import ThumbnailLoader, scan_wallpapers
from gi.repository import Adw, GdkPixbuf, GLib


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        while GLib.MainContext.default().iteration(False):
            pass
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("Background work did not finish")


class WallpaperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def image(self, width=800, height=450):
        path = self.root / "wall.png"
        image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
        image.fill(0x567890ff)
        image.savev(str(path), "png", [], [])
        return path

    def test_thumbnails_scaled_cached_invalidated_and_bounded(self):
        loader = ThumbnailLoader()
        path = self.image(width=3840, height=2160)
        first = loader._thumbnail(path)
        self.assertLessEqual(first.get_width(), 288)
        self.assertLessEqual(first.get_height(), 192)
        self.assertIs(first, loader._thumbnail(path))
        self.image(width=300, height=600)
        self.assertIsNot(first, loader._thumbnail(path))
        for i in range(loader.CACHE_SIZE + 2):
            link = self.root / f"{i}.png"
            link.symlink_to(path)
            loader._thumbnail(link)
        self.assertEqual(len(loader._cache), loader.CACHE_SIZE)
        bad = self.root / "bad.png"
        bad.write_text("not an image")
        self.assertIsNone(loader._thumbnail(bad))
        self.assertIsNone(loader._thumbnail(self.root / "missing.png"))

    def test_scan_filters_and_deduplicates(self):
        path = self.image()
        (self.root / "folder.png").mkdir()
        (self.root / "notes.txt").write_text("ignore")
        self.assertEqual(scan_wallpapers((self.root, self.root, self.root / "missing")), [path])

    def test_navigation_does_not_wait_for_status_or_scan(self):
        page = SetupPage(Mock(state={}))
        gate = threading.Event()

        def slow_scan(_dirs):
            gate.wait(3)
            return []

        def slow_status():
            gate.wait(3)
            return True

        with patch("caelestia_installer.pages.setup.scan_wallpapers", slow_scan), \
                patch("caelestia_installer.pages.setup.checks.service_active", slow_status), \
                patch("caelestia_installer.pages.setup.checks.installed_state",
                      return_value={"installed": False}), \
                patch.object(paths, "SHELL_JSON", self.root / "shell.json"):
            try:
                start = time.monotonic()
                page.on_navigate_to()
                self.assertLess(time.monotonic() - start, .2)
                ticks = []
                GLib.idle_add(lambda: ticks.append(True) and False)
                wait_for(lambda: bool(ticks))
                self.assertTrue(page._wall_scan_loading)
            finally:
                gate.set()
                wait_for(lambda: not page._wall_scan_loading and not page._status_loading)
            self.assertFalse((self.root / "shell.json").exists())

    def test_large_collection_pagination_selection_and_stale_results(self):
        page = SetupPage(Mock(state={}))
        images = [self.root / f"{i:04}.png" for i in range(1000)]
        with patch.object(page._thumbnails, "request", side_effect=range(1, 20)):
            page._apply_wallpaper_scan(images)
            self.assertEqual(len(page._wall_pictures), 6)
            self.assertEqual(page.wall_page_label.get_label(), "1–6 of 1000")
            first_tile = page.flow.get_child_at_index(0)
            page._apply_wallpaper_scan(images)
            self.assertIs(page.flow.get_child_at_index(0), first_tile)
            page._change_wall_page(1)
            with patch.object(page, "_apply_wallpaper") as apply:
                page._on_wallpaper_activated(page.flow, page.flow.get_child_at_index(0))
                apply.assert_called_once_with(images[6])
            page._wall_state = str(images[7])
            page._sync_wallpaper_selection()
            self.assertEqual(page.flow.get_selected_children()[0].get_index(), 1)
            page._thumbnail_ready(1, images[6], None)
            self.assertEqual(page.flow.get_child_at_index(0).get_child().get_tooltip_text(),
                             f"Apply wallpaper: {images[6].name}")
            page._change_wall_page(1000)
            self.assertEqual(len(page._wall_pictures), 4)
            self.assertFalse(page.btn_wall_next.get_sensitive())
            self.assertEqual(page.wall_scroll.get_max_content_height(), 224)
            page._apply_wallpaper_scan([])
            self.assertEqual(len(page._wall_pictures), 0)
            self.assertTrue(page.wall_empty.get_visible())
            self.assertFalse(page.wall_scroll.get_visible())

    def test_latest_request_replaces_pending_decodes(self):
        loader = ThumbnailLoader()
        gate = threading.Event()
        started = threading.Event()
        decoded, callbacks = [], []

        def decode(path):
            decoded.append(path)
            started.set()
            gate.wait(3)
            return None

        def ready(generation, path, pixbuf):
            callbacks.append(path)
            return GLib.SOURCE_REMOVE

        with patch.object(loader, "_thumbnail", decode):
            try:
                loader.request([Path("first"), Path("old")], ready)
                self.assertTrue(started.wait(1))
                loader.request([Path("skipped")], ready)
                loader.request([Path("latest")], ready)
            finally:
                gate.set()
                wait_for(lambda: not loader._running)
            self.assertEqual(decoded, [Path("first"), Path("latest")])
            self.assertIn(Path("latest"), callbacks)

    def test_status_does_not_replace_a_new_selection(self):
        page = SetupPage(Mock(state={}))
        page._wall_state = "new.png"
        page._apply_status({"installed": False, "wallpaper": "old.png"}, None, "old.png")
        self.assertEqual(page._wall_state, "new.png")
        page._apply_status({"installed": False, "wallpaper": "external.png"}, None, "new.png")
        self.assertEqual(page._wall_state, "external.png")
