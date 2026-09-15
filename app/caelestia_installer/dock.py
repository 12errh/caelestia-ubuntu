"""macOS-style floating dock with spring magnification.

A horizontal bar of circular icon buttons that magnify as the mouse
approaches, mimicking the macOS dock.  Translated from the Aceternity
UI ``FloatingDock`` React component to GTK4/libadwaita.
"""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

_BASE_ICON = 28
_MAX_ICON = 52
_BASE_BTN = 48
_MAX_BTN = 80
_SPRING = 0.12
_FRAME_MS = 16

_DOCK_CSS = b"""
@define-color dock_bg alpha(@window_bg_color, 0.65);

floating-dock {
  background: @dock_bg;
  border: 1px solid alpha(@borders, 0.45);
  border-radius: 22px;
  padding: 8px 14px;
  box-shadow: 0 4px 24px alpha(black, 0.25),
              0 1px 4px alpha(black, 0.15);
}

floating-dock dock-btn {
  background: transparent;
  border: none;
  border-radius: 999px;
  min-width: 48px;
  min-height: 48px;
  padding: 0;
  transition: background 200ms ease;
}

floating-dock dock-btn:hover {
  background: alpha(@accent_color, 0.18);
}

floating-dock dock-btn.active {
  background: alpha(@accent_color, 0.30);
}
"""

_css_loaded = False


def _ensure_css() -> None:
    global _css_loaded  # noqa: PLW0603
    if _css_loaded:
        return
    _css_loaded = True
    prov = Gtk.CssProvider()
    prov.load_from_data(_DOCK_CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(),
        prov,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def _spring(current: float, target: float, dt: float) -> float:
    """Exponential-decay spring interpolation."""
    return current + (target - current) * (1.0 - pow(_SPRING, dt))


class FloatingDock(Gtk.Box):
    """A floating dock that magnifies icons on hover."""

    def __init__(self, on_select: callable) -> None:
        super().__init__()
        _ensure_css()

        self._on_select = on_select
        self._buttons: list[Gtk.Button] = []
        self._btn_size: list[float] = []
        self._ico_size: list[float] = []
        self._tgt_size: list[float] = []
        self._ico_tgt: list[float] = []
        self._active: str | None = None
        self._anim_id: int | None = None
        self._prev_time: float = 0.0

        self.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.set_spacing(6)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.END)
        self.set_margin_bottom(16)
        self.add_css_class("floating-dock")

        # Track mouse across the whole dock.
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

        # Stop animation when the mouse leaves the dock entirely.
        motion.connect("leave", self._on_leave)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def add_item(self, page_id: str, icon_name: str, label: str) -> None:
        """Append one dock button."""
        btn = Gtk.Button.new()
        btn.set_tooltip_text(label)
        btn.add_css_class("dock-btn")
        btn._page_id = page_id  # type: ignore[attr-defined]

        img = Gtk.Image.new_from_icon_name(icon_name)
        img.set_pixel_size(_BASE_ICON)
        btn.set_child(img)
        btn.connect("clicked", self._on_clicked)

        self.append(btn)
        self._buttons.append(btn)
        self._btn_size.append(float(_BASE_BTN))
        self._ico_size.append(float(_BASE_ICON))
        self._tgt_size.append(float(_BASE_BTN))
        self._ico_tgt.append(float(_BASE_ICON))

    def set_active(self, page_id: str) -> None:
        """Visually highlight the active dock button."""
        if self._active == page_id:
            return
        for btn in self._buttons:
            if getattr(btn, "_page_id", None) == self._active:
                btn.remove_css_class("active")
        self._active = page_id
        for btn in self._buttons:
            if getattr(btn, "_page_id", None) == page_id:
                btn.add_css_class("active")

    # ------------------------------------------------------------------
    # Hover → magnification
    # ------------------------------------------------------------------

    def _on_motion(self, _ctrl: Gtk.EventControllerMotion,
                   x: float, _y: float) -> None:
        for i, btn in enumerate(self._buttons):
            alloc = btn.get_allocation()
            cx = alloc.x + alloc.width / 2.0
            dist = abs(x - cx)
            if dist < 160:
                t = 1.0 - (dist / 160.0)
                t = t * t  # quadratic ease-in
                self._tgt_size[i] = _BASE_BTN + (_MAX_BTN - _BASE_BTN) * t
                self._ico_tgt[i] = _BASE_ICON + (_MAX_ICON - _BASE_ICON) * t
            else:
                self._tgt_size[i] = float(_BASE_BTN)
                self._ico_tgt[i] = float(_BASE_ICON)

        if self._anim_id is None:
            self._prev_time = time.monotonic()
            self._anim_id = GLib.timeout_add(_FRAME_MS, self._tick)

    def _on_leave(self, _ctrl: Gtk.EventControllerMotion) -> None:
        for i in range(len(self._buttons)):
            self._tgt_size[i] = float(_BASE_BTN)
            self._ico_tgt[i] = float(_BASE_ICON)

    def _tick(self) -> bool:
        now = time.monotonic()
        dt = min(now - self._prev_time, 0.05)
        self._prev_time = now

        settled = True
        for i, btn in enumerate(self._buttons):
            old_sz = self._btn_size[i]
            new_sz = _spring(old_sz, self._tgt_size[i], dt)
            self._btn_size[i] = new_sz

            old_ico = self._ico_size[i]
            new_ico = _spring(old_ico, self._ico_tgt[i], dt)
            self._ico_size[i] = new_ico

            if abs(new_sz - self._tgt_size[i]) > 0.3:
                settled = False

            child = btn.get_child()
            if isinstance(child, Gtk.Image):
                child.set_pixel_size(max(int(new_ico), 1))

            pad = max(int((new_sz - new_ico) / 2), 0)
            child.set_margin_top(pad)
            child.set_margin_bottom(pad)
            child.set_margin_start(pad)
            child.set_margin_end(pad)

            if abs(new_sz - old_sz) > 0.1:
                btn.set_size_request(int(new_sz), int(new_sz))

        if settled:
            self._anim_id = None
            return False  # stop timer
        return True  # keep ticking

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_clicked(self, btn: Gtk.Button) -> None:
        page_id = getattr(btn, "_page_id", None)
        if page_id and self._on_select:
            self._on_select(page_id)
