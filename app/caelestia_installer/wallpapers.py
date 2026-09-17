"""Background wallpaper discovery and bounded, latest-request thumbnail loading."""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402


def scan_wallpapers(directories: tuple[Path, ...]) -> list[Path]:
    found: set[Path] = set()
    for directory in directories:
        try:
            for path in directory.iterdir():
                if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and path.is_file():
                    found.add(path)
        except OSError:
            continue
    return sorted(found)


class ThumbnailLoader:
    """One decoder at a time; page changes replace pending work, not queue it.

    Only small pixbufs are retained. File timestamps invalidate changed images.
    Callbacks are dispatched on GTK's main loop, never on the decoder thread.
    """

    CACHE_SIZE = 48

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending = None
        self._running = False
        self._generation = 0
        self._cache = OrderedDict()

    def request(self, paths: list[Path], callback) -> int:
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._pending = (generation, tuple(paths), callback)
            if not self._running:
                self._running = True
                threading.Thread(target=self._work, daemon=True).start()
        return generation

    def _work(self) -> None:
        while True:
            with self._lock:
                request = self._pending
                self._pending = None
                if request is None:
                    self._running = False
                    return
            generation, paths, callback = request
            for path in paths:
                with self._lock:
                    if generation != self._generation:
                        break
                pixbuf = self._thumbnail(path)
                GLib.idle_add(callback, generation, path, pixbuf)

    def _thumbnail(self, path: Path):
        try:
            stat = path.stat()
            key = (path, stat.st_mtime_ns, stat.st_size)
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), 288, 192, True)
            pixbuf = pixbuf.apply_embedded_orientation()
        except (OSError, GLib.Error):
            return None
        self._cache[key] = pixbuf
        while len(self._cache) > self.CACHE_SIZE:
            self._cache.popitem(last=False)
        return pixbuf
