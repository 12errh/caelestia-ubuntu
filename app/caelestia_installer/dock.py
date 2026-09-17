"""macOS-style floating dock -- a faithful GTK4 port of Aceternity UI's
``@aceternity/floating-dock`` component (``components/ui/floating-dock.tsx``).

The React original, mechanics verbatim:

* one shared ``mouseX`` MotionValue, set to ``Infinity`` on mouse-leave so
  every icon springs back smoothly instead of snapping;
* per-icon ``distance = mouseX - (bounds.x + bounds.width / 2)`` where bounds
  come from the *live, animating* element (getBoundingClientRect each frame);
* linear ramps with clamping: ``useTransform(distance, [-150, 0, 150],
  [40, 80, 40])`` for the disc and ``[..., [20, 40, 20]]`` for the inner icon;
* ``useSpring(..., { mass: 0.1, stiffness: 150, damping: 12 })`` chasing each
  target -- a real physical spring (overdamped, ζ ≈ 1.55), which produces the
  dock's characteristic glide-and-settle. :func:`spring_f` reproduces
  Motion's RK4 integrator;
* bar geometry: fixed ``h-16`` (64px) pill, ``px-4 pb-3 gap-4 rounded-2xl``,
  icons pinned ``items-end`` -- a 40px idle disc sits fully inside the pill,
  and at the 80px maximum it rises 28px *above the pill's top edge* while the
  pill itself never changes height (it only widens, ``mx-auto``);
* a tooltip fades/slides in above the hovered disc (in from y=10, out to
  y=2) -- the component's ``AnimatePresence`` tooltip.

GTK mapping of the HTML overflow trick: each item is a Button whose layout
slot is a fixed ``SLOT_H`` tall; the disc and tooltip are ``Gtk.Overlay``
children marked ``measure=False`` so they do not participate in size
negotiation and may extend beyond the bar's top edge. The Button's *width*
tracks the animated disc size, so siblings shift outward and the bar breathes
horizontally exactly like the original.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

# ---------------------------------------------------------------------------
# Constants -- every value below is the Aceternity component's, converted from
# Tailwind px. Nothing here is arbitrary; change only with intent.
# ---------------------------------------------------------------------------
IDLE_DISC = 40.0          # useTransform(distance, [-150, 0, 150], [40, 80, 40])
MAX_DISC = 80.0
IDLE_ICON = 20.0          # useTransform(distance, [-150, 0, 150], [20, 40, 20])
MAX_ICON = 40.0
RAMP = 150.0              # influence window in px

SPRING_MASS = 0.1         # useSpring(..., { mass: 0.1, stiffness: 150,
SPRING_STIFFNESS = 150.0  #               damping: 12 })
SPRING_DAMPING = 12.0

BAR_HEIGHT = 64           # h-16
BAR_PAD_X = 16            # px-4 (implemented as :first-child/:last-child margins)
BAR_PAD_BOTTOM = 12       # pb-3 (implemented as the disc's bottom margin)
BAR_GAP = 16              # gap-4
BAR_RADIUS = 16           # rounded-2xl

TOOLTIP_GAP = 8           # tooltip rest position above the disc's top edge
TOOLTIP_FADE_RATE = 14.0  # opacity easing speed (approx. Motion's tween)

BRAND_SIZE = 26           # the logo mark's square size inside the 64px bar
BRAND_RULE_H = 28         # height of the hairline rules flanking the mark
BRAND_RULE_GAP = 10       # space between a rule and the mark

FRAME_MS = 16             # ~60 fps integration
MAX_DT = 0.05             # clamp after stalls (tab switch, etc.)

_DOCK_CSS = b"""
/* Note: GTK CSS - the dock is styled via *class* selectors (.floating-dock),
   because add_css_class() names only match with a leading dot; bare names
   match widget CSS names, which these widgets do not have. */
/* The bar is a floating panel, so it takes the *elevated* surface rather than
   the window background: a translucent copy of the window colour would be
   invisible against the window it floats over. */
@define-color dock_bg alpha(@popover_bg_color, 0.92);

/* The pill: h-16, rounded-2xl, bg-gray-50. No padding here - the px-4/pb-3
   insets are implemented in widget geometry (button margins + disc margin)
   so the box's allocation stays exactly 64px and the discs can overflow the
   top edge. outline() draws the hairline without affecting layout. */
.floating-dock {
  background: @dock_bg;
  border-radius: 16px;
  outline: 1px solid alpha(@borders, 0.35);
}

/* Neutralise the theme's button chrome completely. */
.floating-dock .dock-btn {
  background: transparent;
  border: none;
  padding: 0;
  margin: 0;
  min-height: 0;
  min-width: 0;
  box-shadow: none;
}

/* px-4: 16px inner side padding on the pill. */
.floating-dock .dock-btn:first-child { margin-left: 16px; }
.floating-dock .dock-btn:last-child  { margin-right: 16px; }

.floating-dock .dock-tooltip {
  background: alpha(@popover_bg_color, 0.92);
  color: @window_fg_color;
  border-radius: 6px;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.01em;
  border: 1px solid alpha(@window_fg_color, 0.08);
  box-shadow: 0 2px 8px alpha(@window_bg_color, 0.5);
}

/* The brand mark is tinted with this colour (the texture is one big alpha
   mask), so keeping it slightly below full opacity stops it out-shouting the
   navigation icons next to it. */
.floating-dock .dock-brand {
  color: alpha(@window_fg_color, 0.95);
}

.floating-dock .dock-brand-rule {
  background: alpha(@window_fg_color, 0.22);
  min-width: 1px;
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


# Last-resort glyphs if a theme lacks one of main.DOCK_ICONS. Ubuntu and Zorin
# ship every name the dock asks for, but other icon themes may not, and a
# missing name makes GTK paint its "broken image" glyph in the nav bar.
_FALLBACK_ICONS = ("application-x-executable-symbolic", "image-missing-symbolic")


def _resolve_icon(name: str) -> str:
    """Return ``name`` if the current theme has it, else the best fallback."""
    display = Gdk.Display.get_default()
    if display is None:
        return name
    theme = Gtk.IconTheme.get_for_display(display)
    if theme.has_icon(name):
        return name
    for candidate in _FALLBACK_ICONS:
        if theme.has_icon(candidate):
            return candidate
    return name


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _ramp(distance: float) -> float:
    """``useTransform(distance, [-150, 0, 150], [40, 80, 40])``.

    Plain linear interpolation, clamped outside the input range -- exactly
    what Motion's useTransform does with a 3-point mapping.
    """
    if distance <= -RAMP or distance >= RAMP:
        return IDLE_DISC
    if distance < 0.0:
        t = (distance + RAMP) / RAMP          # [-150, 0] -> [0, 1]
        return IDLE_DISC + (MAX_DISC - IDLE_DISC) * t
    t = distance / RAMP                        # [0, 150] -> [1, 0]
    return MAX_DISC - (MAX_DISC - IDLE_DISC) * t


def _ramp_inner(distance: float) -> float:
    """``useTransform(distance, [-150, 0, 150], [20, 40, 20])``."""
    if distance <= -RAMP or distance >= RAMP:
        return IDLE_ICON
    if distance < 0.0:
        t = (distance + RAMP) / RAMP
        return IDLE_ICON + (MAX_ICON - IDLE_ICON) * t
    t = distance / RAMP
    return MAX_ICON - (MAX_ICON - IDLE_ICON) * t


def spring_f(x: float, v: float, target: float, dt: float):
    """One RK4 step of Motion's ``useSpring`` integrator.

    Solves  x'' = (-stiffness*(x - target) - damping*v) / mass  -- the same
    equation motion/react integrates. With mass 0.1 / stiffness 150 /
    damping 12 the spring is overdamped (ζ ≈ 1.55): no overshoot, a fast
    glide that settles smoothly.
    """
    m, k, c = SPRING_MASS, SPRING_STIFFNESS, SPRING_DAMPING

    def acc(x_: float, v_: float) -> float:
        return (-k * (x_ - target) - c * v_) / m

    k1x, k1v = v, acc(x, v)
    k2x, k2v = v + 0.5 * dt * k1v, acc(x + 0.5 * dt * k1x, v + 0.5 * dt * k1v)
    k3x, k3v = v + 0.5 * dt * k2v, acc(x + 0.5 * dt * k2x, v + 0.5 * dt * k2v)
    k4x, k4v = v + dt * k3v, acc(x + dt * k3x, v + dt * k3v)
    nx = x + (dt / 6.0) * (k1x + 2.0 * k2x + 2.0 * k3x + k4x)
    nv = v + (dt / 6.0) * (k1v + 2.0 * k2v + 2.0 * k3v + k4v)
    return nx, nv


class _BrandMark(Gtk.Widget):
    """The Caelestia logo, drawn like a symbolic icon.

    The packaged PNG (``paths.BRAND_LOGO``, derived from ``assets/logo.png``)
    is pure white, which would disappear on a light theme. So only its alpha is
    used: the mask is pushed from the texture and then filled with the widget's
    own CSS colour, exactly how GTK paints symbolic icons. That also means CSS
    (``.dock-brand``) can dim or recolour the mark in one place.
    """

    def __init__(self, texture: Gdk.Texture, size: int) -> None:
        super().__init__()
        self._texture = texture
        self.add_css_class("dock-brand")
        self.set_size_request(size, size)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:  # noqa: N802 - GTK vfunc
        w = float(self.get_width())
        h = float(self.get_height())
        if w <= 0.0 or h <= 0.0:
            return
        rect = Graphene.Rect().init(0.0, 0.0, w, h)
        # push_mask opens two snapshot states: one for the mask itself and one
        # for the content it will be applied to - so this needs two pops (GTK
        # warns "too many push() calls" if the second one is missing).
        snapshot.push_mask(Gsk.MaskMode.ALPHA)
        snapshot.append_texture(self._texture, rect)
        snapshot.pop()
        snapshot.append_color(self.get_color(), rect)
        snapshot.pop()


def _load_logo(path: Path | str) -> Gdk.Texture | None:
    """Load the brand texture, or ``None`` if it is absent (dock still works)."""
    try:
        return Gdk.Texture.new_from_filename(str(path))
    except (GLib.Error, TypeError, ValueError):
        return None


def _brand_rule() -> Gtk.Separator:
    """A hairline separating the brand from the navigation icons."""
    rule = Gtk.Separator.new(Gtk.Orientation.VERTICAL)
    rule.add_css_class("dock-brand-rule")
    rule.set_valign(Gtk.Align.CENTER)
    rule.set_size_request(1, BRAND_RULE_H)
    return rule


class _DockButton(Gtk.Button):
    """One dock item.

    Layout slot (the Button itself) is ``SLOT_H`` px tall and as wide as the
    current animated disc size. The disc and tooltip are ``Gtk.Overlay``
    children with ``measure=False``, so they can rise above the pill's top
    edge without inflating the bar's height -- the HTML ``overflow`` trick.
    """

    # spring state indices: 0 disc w, 1 disc h, 2 icon w, 3 icon h, 4 tooltip y
    _DISC_W, _DISC_H, _ICON_W, _ICON_H, _TIP_Y = range(5)

    def __init__(self, page_id: str, icon_name: str, label: str) -> None:
        super().__init__()
        self.page_id = page_id
        self.add_css_class("dock-btn")
        self.set_has_frame(False)
        self.set_overflow(Gtk.Overflow.VISIBLE)
        # The button IS the layout slot: as wide as the animated disc and the
        # full 64px bar height. The disc inside it is bottom-pinned with the
        # pb-3 inset, so growing discs rise past the pill's top edge.
        self.set_size_request(int(IDLE_DISC), BAR_HEIGHT)

        overlay = Gtk.Overlay.new()
        overlay.set_overflow(Gtk.Overflow.VISIBLE)
        overlay.set_child(Gtk.Box.new(Gtk.Orientation.VERTICAL, 0))  # 0x0 main
        self.set_child(overlay)

        # The disc: bottom-centre pinned 12px above the bar's bottom edge
        # (items-end + pb-3), growing upward past the pill's top edge.
        self.disc = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.disc.add_css_class("dock-disc")
        self.disc.set_halign(Gtk.Align.CENTER)
        self.disc.set_valign(Gtk.Align.END)
        self.disc.set_margin_bottom(BAR_PAD_BOTTOM)
        self.disc.set_size_request(int(IDLE_DISC), int(IDLE_DISC))
        overlay.add_overlay(self.disc)

        # The inner icon: centred inside the disc. The disc is a Gtk.Box, and
        # a Box packs its children at the *start* of its main axis (it ignores
        # valign there) - so a plain CENTER-aligned child sits at the top of
        # the disc, not in the middle. vexpand gives the icon the disc's whole
        # slot and Gtk.Image then draws its paintable centred inside that
        # allocation, which is the actual centring we want.
        self.icon = Gtk.Image.new_from_icon_name(_resolve_icon(icon_name))
        self.icon.set_pixel_size(int(IDLE_ICON))
        self.icon.set_halign(Gtk.Align.CENTER)
        self.icon.set_valign(Gtk.Align.CENTER)
        self.icon.set_vexpand(True)
        self.disc.append(self.icon)

        # The tooltip: floats above the disc, animated like AnimatePresence.
        self.tooltip = Gtk.Label.new(label)
        self.tooltip.add_css_class("dock-tooltip")
        self.tooltip.set_halign(Gtk.Align.CENTER)
        self.tooltip.set_valign(Gtk.Align.END)
        self.tooltip.set_opacity(0.0)
        self.tooltip.set_visible(False)
        overlay.add_overlay(self.tooltip)

        # Overlay children must not affect size negotiation (this is the
        # default, but set it explicitly for clarity and future safety).
        if hasattr(overlay, "set_measure_overlay"):
            overlay.set_measure_overlay(self.disc, False)
            overlay.set_measure_overlay(self.tooltip, False)

        # Hover drives only the tooltip, exactly like the component's
        # per-disc onMouseEnter/onMouseLeave.
        self.hovered = False
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_enter)
        motion.connect("leave", self._on_leave)
        self.disc.add_controller(motion)

        # Spring state.
        self.cur = [IDLE_DISC, IDLE_DISC, IDLE_ICON, IDLE_ICON, 10.0]
        self.vel = [0.0] * 5
        self.tooltip_opacity = 0.0

    # -- pointer ------------------------------------------------------------
    def _on_enter(self, _c, _x, _y) -> None:
        self.hovered = True

    def _on_leave(self, _c) -> None:
        self.hovered = False

    # -- apply current spring state to the widgets --------------------------
    def _apply(self) -> None:
        d = max(int(round(self.cur[self._DISC_W])), 1)
        self.disc.set_size_request(d, d)
        self.set_size_request(d, BAR_HEIGHT)
        self.icon.set_pixel_size(max(int(round(self.cur[self._ICON_W])), 1))

        op = _clamp(self.tooltip_opacity, 0.0, 1.0)
        self.tooltip.set_opacity(op)
        self.tooltip.set_visible(op > 0.02)
        # Rest position: TOOLTIP_GAP above the disc's top edge; the spring's
        # y offset (10 in, 2 out) lowers it from there. Distance from the bar's
        # bottom edge: pb-3 inset + disc + gap, minus the spring offset.
        self.tooltip.set_margin_bottom(
            BAR_PAD_BOTTOM + d + TOOLTIP_GAP - int(round(self.cur[self._TIP_Y])))

    def set_active(self, active: bool) -> None:
        if active:
            self.add_css_class("active")
        else:
            self.remove_css_class("active")


class FloatingDock(Gtk.Box):
    """A floating dock that magnifies icons on hover -- Aceternity's, ported."""

    def __init__(self, on_select: callable) -> None:
        super().__init__()
        _ensure_css()

        self._on_select = on_select
        self._buttons: list[_DockButton] = []
        self._active: str | None = None

        # The shared mouseX MotionValue. Infinity == pointer gone, which the
        # distance clamp turns into "everything at idle" while the springs
        # glide back -- the component's onMouseLeave behaviour.
        # Stored in *window* coordinates (the React original uses pageX, and
        # getBoundingClientRect is page-space too): as the bar widens and
        # re-centres, page/window coordinates stay stable for a stationary
        # pointer, so distances do not drift -- parity with the original.
        self.mouse_x = math.inf
        self._anim_id: int | None = None
        self._prev_t: float = 0.0

        self.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.set_spacing(BAR_GAP)
        self.set_halign(Gtk.Align.CENTER)   # mx-auto: bar breathes symmetrically
        self.set_valign(Gtk.Align.END)      # items-end
        self.add_css_class("floating-dock")
        self.set_overflow(Gtk.Overflow.VISIBLE)
        self.set_margin_bottom(0)
        self.set_size_request(-1, BAR_HEIGHT)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

    # ------------------------------------------------------------------
    # Public API (used by main.py)
    # ------------------------------------------------------------------

    def add_item(self, page_id: str, icon_name: str, label: str) -> None:
        btn = _DockButton(page_id, icon_name, label)
        btn.connect("clicked", self._on_clicked)
        self.append(btn)
        self._buttons.append(btn)

    def add_brand(self, logo: Path | str, label: str = "") -> bool:
        """Place the brand mark (logo between two hairline rules) in the dock.

        Call this while building the dock, at the position the mark should sit --
        main.py calls it between the two halves of the navigation items to put
        the brand in the middle of the bar.

        The mark is deliberately *not* a dock item: it never magnifies, never
        responds to hover, and stays out of the magnification maths, so the bar
        breathes around a fixed piece of branding instead of another icon.
        Returns ``False`` (and adds nothing) if the logo file is missing.
        """
        texture = _load_logo(logo)
        if texture is None:
            return False

        mark = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        mark.set_valign(Gtk.Align.CENTER)
        if label:
            mark.set_tooltip_text(label)

        left = _brand_rule()
        left.set_margin_end(BRAND_RULE_GAP)
        right = _brand_rule()
        right.set_margin_start(BRAND_RULE_GAP)

        mark.append(left)
        mark.append(_BrandMark(texture, BRAND_SIZE))
        mark.append(right)
        self.append(mark)
        return True

    def set_active(self, page_id: str) -> None:
        if self._active == page_id:
            return
        for btn in self._buttons:
            btn.set_active(btn.page_id == page_id)
        self._active = page_id

    # ------------------------------------------------------------------
    # Pointer → shared mouseX
    # ------------------------------------------------------------------

    def _to_root_x(self, widget: Gtk.Widget, widget_x: float) -> float:
        """Convert an x in ``widget``'s space into window (root) space."""
        root = self.get_root()
        if root is None:
            return widget_x
        try:
            pt = Graphene.Point().init(widget_x, 0.0)
            ok, pt = widget.compute_point(root, pt)
            if ok:
                return pt.x
        except Exception:  # noqa: BLE001 - unrealized widgets
            pass
        return widget_x

    def _on_motion(self, _c: Gtk.EventControllerMotion, x: float, _y: float) -> None:
        self.mouse_x = self._to_root_x(self, x)   # pageX equivalent
        self._ensure_anim()

    def _on_leave(self, _c: Gtk.EventControllerMotion) -> None:
        self.mouse_x = math.inf
        self._ensure_anim()

    # ------------------------------------------------------------------
    # Per-frame: distance from live allocations, springs, apply
    # ------------------------------------------------------------------

    def _disc_center_x(self, btn: _DockButton) -> float | None:
        """Disc centre in window (root) coordinates -- the getBoundingClientRect
        equivalent: live, animating, and layout-shift-immune."""
        alloc = btn.disc.get_allocation()
        if alloc.width <= 0:
            return None
        root = self.get_root()
        if root is not None:
            try:
                pt = Graphene.Point().init(alloc.width / 2.0, 0.0)
                ok, pt = btn.disc.compute_point(root, pt)
                if ok:
                    return pt.x
            except Exception:  # noqa: BLE001 - unrealized widgets, older GTK
                pass
        # Fallback: the dock is the allocation parent of every button, so the
        # button's centre approximates the disc's centre in dock space (the
        # dock itself is centred, so its origin is stable within the window).
        btn_alloc = btn.get_allocation()
        return self._to_root_x(self, btn_alloc.x + btn_alloc.width / 2.0)

    def _ensure_anim(self) -> None:
        if self._anim_id is None:
            self._prev_t = time.monotonic()
            self._anim_id = GLib.timeout_add(FRAME_MS, self._tick)

    def _tick(self) -> bool:
        now = time.monotonic()
        dt = min(now - self._prev_t, MAX_DT)
        self._prev_t = now

        # Substep so a stalled frame can never destabilise the integrator.
        steps = max(1, int(math.ceil(dt / 0.016)))
        sdt = dt / steps

        moving = False
        for btn in self._buttons:
            cx = self._disc_center_x(btn)
            if cx is None or math.isinf(self.mouse_x):
                distance = -RAMP - 1.0        # fully outside the window
            else:
                distance = self.mouse_x - cx

            targets = (
                _ramp(distance), _ramp(distance),
                _ramp_inner(distance), _ramp_inner(distance),
                0.0 if btn.hovered else 2.0,
            )
            if not self.get_settings().get_property("gtk-enable-animations"):
                btn.cur[:] = targets
                btn.vel[:] = [0.0] * 5
                btn.tooltip_opacity = 1.0 if btn.hovered else 0.0
            else:
                for _ in range(steps):
                    for j in range(5):
                        btn.cur[j], btn.vel[j] = spring_f(
                            btn.cur[j], btn.vel[j], targets[j], sdt)

            op_target = 1.0 if btn.hovered else 0.0
            btn.tooltip_opacity += (
                op_target - btn.tooltip_opacity) * min(1.0, dt * TOOLTIP_FADE_RATE)

            for j in range(4):
                if abs(targets[j] - btn.cur[j]) > 0.1 or abs(btn.vel[j]) > 0.5:
                    moving = True
            if abs(targets[4] - btn.cur[4]) > 0.1 or abs(btn.vel[4]) > 0.5:
                moving = True
            if abs(btn.tooltip_opacity - op_target) > 0.01:
                moving = True

            btn._apply()

        if not moving:
            self._anim_id = None
            return False
        return True

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_clicked(self, btn: Gtk.Button) -> None:
        page_id = getattr(btn, "page_id", None)
        if page_id and self._on_select:
            self._on_select(page_id)
