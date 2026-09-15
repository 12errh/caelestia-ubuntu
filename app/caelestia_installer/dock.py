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

# ---- tunables -----------------------------------------------------------
BASE_ICON = 36          # idle icon pixel size
MAX_ICON = 64           # fully-hovered icon pixel size
BASE_BTN = 56           # idle button allocation
MAX_BTN = 92            # fully-hovered button allocation
HOVER_RADIUS = 200      # px from button centre to edge of influence
SPRING_FACTOR = 0.08    # lower = smoother / slower
FRAME_MS = 16           # ~60 fps

_DOCK_CSS = b"""
@define-color dock_bg alpha(@window_bg_color, 0.72);

floating-dock {
  background: @dock_bg;
  border: 1px solid alpha(@borders, 0.35);
  border-radius: 24px;
  padding: 10px 18px;
  box-shadow: 0 8px 32px alpha(black, 0.22),
              0 2px 8px alpha(black, 0.12);
}

floating-dock dock-btn {
  background: transparent;
  border: none;
  border-radius: 999px;
  padding: 0;
  min-width: 56px;
  min-height: 56px;
}

floating-dock dock-btn:hover {
  background: alpha(@accent_color, 0.15);
}

floating-dock dock-btn.active {
  background: alpha(@accent_color, 0.25);
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


def _spring(cur: float, tgt: float, dt: float) -> float:
    """Smooth exponential-decay spring."""
    return cur + (tgt - cur) * (1.0 - pow(SPRING_FACTOR, dt))


class FloatingDock(Gtk.Box):
    """A floating dock that magnifies icons on hover."""

    def __init__(self, on_select: callable) -> None:
        super().__init__()
        _ensure_css()

        self._on_select = on_select
        self._buttons: list[Gtk.Button] = []
        self._btn_cur: list[float] = []     # current button size
        self._ico_cur: list[float] = []     # current icon size
        self._btn_tgt: list[float] = []     # target button size
        self._ico_tgt: list[float] = []     # target icon size
        self._active: str | None = None
        self._anim_id: int | None = None
        self._prev_t: float = 0.0

        self.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.set_spacing(4)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.END)
        self.set_margin_bottom(18)
        self.add_css_class("floating-dock")

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def add_item(self, page_id: str, icon_name: str, label: str) -> None:
        btn = Gtk.Button.new()
        btn.set_tooltip_text(label)
        btn.add_css_class("dock-btn")
        btn._page_id = page_id  # type: ignore[attr-defined]

        img = Gtk.Image.new_from_icon_name(icon_name)
        img.set_pixel_size(BASE_ICON)
        btn.set_child(img)
        btn.connect("clicked", self._on_clicked)

        self.append(btn)
        self._buttons.append(btn)
        self._btn_cur.append(float(BASE_BTN))
        self._ico_cur.append(float(BASE_ICON))
        self._btn_tgt.append(float(BASE_BTN))
        self._ico_tgt.append(float(BASE_ICON))

    def set_active(self, page_id: str) -> None:
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
    # Hover → distance-based targets
    # ------------------------------------------------------------------

    def _on_motion(self, _c: Gtk.EventControllerMotion, x: float,
                   _y: float) -> None:
        for i, btn in enumerate(self._buttons):
            a = btn.get_allocation()
            cx = a.x + a.width * 0.5
            dist = abs(x - cx)
            if dist < HOVER_RADIUS:
                t = 1.0 - dist / HOVER_RADIUS
                t = t * t * (3.0 - 2.0 * t)   # smooth-step
                self._btn_tgt[i] = BASE_BTN + (MAX_BTN - BASE_BTN) * t
                self._ico_tgt[i] = BASE_ICON + (MAX_ICON - BASE_ICON) * t
            else:
                self._btn_tgt[i] = float(BASE_BTN)
                self._ico_tgt[i] = float(BASE_ICON)

        if self._anim_id is None:
            self._prev_t = time.monotonic()
            self._anim_id = GLib.timeout_add(FRAME_MS, self._tick)

    def _on_leave(self, _c: Gtk.EventControllerMotion) -> None:
        for i in range(len(self._buttons)):
            self._btn_tgt[i] = float(BASE_BTN)
            self._ico_tgt[i] = float(BASE_ICON)

    # ------------------------------------------------------------------
    # Animation loop
    # ------------------------------------------------------------------

    def _tick(self) -> bool:
        now = time.monotonic()
        dt = min(now - self._prev_t, 0.05)
        self._prev_t = now

        still_going = False
        for i, btn in enumerate(self._buttons):
            # interpolate sizes
            self._btn_cur[i] = _spring(self._btn_cur[i], self._btn_tgt[i], dt)
            self._ico_cur[i] = _spring(self._ico_cur[i], self._ico_tgt[i], dt)

            sz = self._btn_cur[i]
            ico = self._ico_cur[i]
            child = btn.get_child()

            # resize icon
            if isinstance(child, Gtk.Image):
                child.set_pixel_size(max(int(ico), 1))

            # centre icon inside the (growing) button via equal margins
            pad = max(int((sz - ico) * 0.5), 0)
            if isinstance(child, Gtk.Widget):
                child.set_margin_top(pad)
                child.set_margin_bottom(pad)
                child.set_margin_start(pad)
                child.set_margin_end(pad)

            # grow / shrink the button itself
            btn.set_size_request(int(sz), int(sz))

            if abs(sz - self._btn_tgt[i]) > 0.3:
                still_going = True

        if not still_going:
            self._anim_id = None
            return False
        return True

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_clicked(self, btn: Gtk.Button) -> None:
        page_id = getattr(btn, "_page_id", None)
        if page_id and self._on_select:
            self._on_select(page_id)
