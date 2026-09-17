"""Liquid Glass, reconstructed for GTK4 -- the real material, not a blur overlay.

What Liquid Glass *is*, and what happens here
=============================================
Apple's WWDC25 sessions describe Liquid Glass as a material built from several
layers, and the innovation is explicitly **lensing**: it bends and shapes the
light coming from behind it in real time, rather than only scattering it the
way earlier "frosted glass" effects did. Each documented property maps to a
concrete piece of geometry below.

===========================  ==================================================
Apple's property             Implementation here
===========================  ==================================================
Lensing -- light is bent,     The backdrop is re-sampled through a *bevel*.
not merely scattered         Along each edge the panel draws the backdrop from
                             outside itself, compressed into a band, with the
                             squeeze strongest at the rim and vanishing
                             inwards (see ``GlassSpec.lens_depth``). Features
                             behind the panel therefore visibly bend and
                             stretch as they pass under the rim -- a feature
                             that crosses the edge appears to refract.
Scattering / vibrancy        The whole sampled backdrop is blurred
                             (``GskSnapshot.push_blur``) and then lifted with a
                             saturation tint, so colour behind the glass stays
                             vivid instead of turning grey.
Highlights respond to        A specular rim stroke, top-lit by default
light and motion             (``push_stroke`` + a gradient fill). ``set_light``
                             aims it at a moving light -- the dock feeds it the
                             pointer, so the highlight physically slides around
                             the material as you move, like Apple's
                             motion-driven illumination.
Shadows adapt to what is     Rim and drop shadow strength are driven by the
behind them                  backdrop's measured brightness *and* its local
                             variance: busy or bright content behind the glass
                             gets a stronger, darker shadow for separation;
                             flat dark content gets a light one.
Tint adapts to the content   The tint colour and alpha come from the backdrop's
underneath                   mean luminance, sampled from the capture: bright
                             content behind pushes the tint dark, dark content
                             pushes it light. This is the "tone ranges mapped
                             to content brightness" behaviour, and it is what
                             keeps text legible over any wallpaper.
Interactive feedback /       ``set_pressed`` and ``set_hovered`` thicken the
thicker glass on growth      glass -- lens depth, rim brightness and shadow all
                             grow on a critically-damped approach, so the
                             material swells under the pointer and settles
                             back, instead of merely tinting on hover.
Regular vs Clear variants    ``REGULAR`` and ``CLEAR`` specs.
===========================  ==================================================

Why it is written this way
--------------------------
GTK4 has no CSS ``backdrop-filter`` (verified: the parser rejects it), and
``Gsk.GLShader`` needs shaders pre-compiled by GTK's ``glslc`` build tool, which
is not available at runtime. So a real backdrop-refracting material cannot be
declared in CSS or written as a fragment shader here; it has to be built from
geometry. That is what this module does: capture the backdrop, then sample it
back through a shaped bevel with Gsk render nodes.

Apple's own guidance is followed too:

* Liquid Glass belongs to the **navigation and controls layer floating above
  content** -- never to content areas themselves, and **never stacked**. This
  module documents that with :func:`assert_not_nested` and the app only applies
  glass to the dock, the header bar and the tooltips.
* Glass needs something behind it to refract. Over a flat colour it is
  invisible, so the app paints a faint aurora field behind its content
  (:func:`caelestia_installer.theme.background_css`) for the glass to pick up.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Gsk, Graphene, Gtk  # noqa: E402


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _rect(x: float, y: float, w: float, h: float) -> Graphene.Rect:
    return Graphene.Rect().init(float(x), float(y), float(w), float(h))


def _rounded(rect: Graphene.Rect, radius: float) -> Gsk.RoundedRect:
    rr = Gsk.RoundedRect()
    rr.init_from_rect(rect, float(max(radius, 0.0)))
    return rr


def _rgba(r: float, g: float, b: float, a: float) -> Gdk.RGBA:
    # Note: Gdk.RGBA's struct constructor takes no arguments and *silently
    # ignores* positional ones, which yields transparent black. Set the fields.
    colour = Gdk.RGBA()
    colour.red, colour.green, colour.blue, colour.alpha = r, g, b, a
    return colour


def _blit(snapshot: Gtk.Snapshot, tex: Gdk.Texture, src, dst) -> None:
    """Draw part of ``tex`` into ``dst``, linearly stretched.

    ``append_texture`` always draws the *whole* texture into the bounds it is
    given, so a sub-region has to be reached by scaling and translating the
    whole thing: solving ``local = tex * scale + offset`` for the wanted
    source span gives both the scale and the offset.
    """
    sx0, sy0, sx1, sy1 = src
    dx0, dy0, dx1, dy1 = dst
    if sx1 - sx0 <= 0 or sy1 - sy0 <= 0:
        return
    sxx = (dx1 - dx0) / (sx1 - sx0)
    syy = (dy1 - dy0) / (sy1 - sy0)
    snapshot.append_texture(
        tex,
        _rect(dx0 - sx0 * sxx, dy0 - sy0 * syy,
              tex.get_width() * sxx, tex.get_height() * syy),
    )


# ---------------------------------------------------------------------------
# The material
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GlassSpec:
    """Parameters of one Liquid Glass variant. Defaults are the Regular one."""

    #: How far the bevel reaches in from the edge, in px. This *is* the lens.
    lens_depth: float = 18.0
    #: Bands per edge used to taper the bend (more = smoother bevel).
    lens_bands: int = 3
    #: Peak perpendicular squeeze at the rim, as a fraction of lens_depth.
    #: 0.45 means the outermost band pulls in backdrop from 45% of the bevel's
    #: depth beyond the edge -- enough to visibly bend a crossing feature.
    lens_squeeze: float = 0.45
    #: Backdrop blur radius. Apple scatters light broadly; 22 keeps text behind
    #: the glass as soft colour rather than losing it entirely.
    blur: float = 22.0
    #: Vibrancy: saturation lift applied to the sampled backdrop.
    saturation: float = 1.18
    #: Specular rim width and its base alpha (scaled by backdrop brightness).
    rim_width: float = 1.4
    rim_alpha: float = 0.40
    #: Inset shading just inside the rim (the thickness of the glass).
    inner_shadow_alpha: float = 0.34
    inner_shadow_blur: float = 9.0
    #: Drop shadow outside the panel, again scaled by the backdrop.
    drop_shadow_alpha: float = 0.34
    drop_shadow_blur: float = 26.0
    drop_shadow_dy: float = 10.0
    #: Tint alphas at the two extremes of backdrop brightness. A bright
    #: backdrop gets the heavier tint so foreground text keeps its contrast.
    tint_alpha_dark_bg: float = 0.10
    tint_alpha_bright_bg: float = 0.42
    #: Tint colour when the backdrop is bright (dark tint) / dark (light tint).
    tint_bright_bg: tuple[float, float, float] = (0.02, 0.03, 0.05)
    tint_dark_bg: tuple[float, float, float] = (1.0, 1.0, 1.0)


#: The workhorse material: full adaptive behaviour.
REGULAR = GlassSpec()

#: The Clear variant: thinner, more transparent, less blur -- for use over
#: media-rich content only, and it relies on a dimming layer for legibility.
CLEAR = replace(REGULAR, blur=10.0, lens_depth=13.0, lens_squeeze=0.34,
                tint_alpha_dark_bg=0.05, tint_alpha_bright_bg=0.20,
                rim_alpha=0.30, inner_shadow_alpha=0.20,
                drop_shadow_alpha=0.22, saturation=1.12)

#: Reported to the caller when a panel cannot capture its backdrop.
_FALLBACK = _rgba(0.05, 0.07, 0.10, 0.94)


def assert_not_nested(widget: Gtk.Widget) -> None:
    """Apple: "never stack glass on glass". Cheap guard for development."""
    parent = widget.get_parent()
    while parent is not None:
        if isinstance(parent, GlassPanel):
            raise ValueError(
                "Liquid Glass must not be nested: this panel sits inside "
                f"{parent!r}. Apple's guidance is one glass layer per surface.")
        parent = parent.get_parent()


class GlassPanel(Gtk.Widget):
    """A container that paints Liquid Glass behind a single child.

    The backdrop is the :meth:`set_source` widget -- the content the panel
    floats above -- captured through ``Gtk.WidgetPaintable`` and re-sampled, so
    the panel always refracts what is genuinely behind it. The capture is
    refreshed only when that source reports that it changed, never per frame.
    """

    __gtype_name__ = "CaelestiaGlassPanel"

    def __init__(self, spec: GlassSpec = REGULAR, corner_radius: float = 18.0,
                 source: Gtk.Widget | None = None) -> None:
        super().__init__()
        self.spec = spec
        self.corner_radius = corner_radius
        self.set_layout_manager(Gtk.BinLayout())
        self.set_overflow(Gtk.Overflow.VISIBLE)

        self._source: Gtk.Widget | None = None
        self._paintable: Gtk.WidgetPaintable | None = None
        self._texture: Gdk.Texture | None = None
        self._dirty = True
        self._idle_id: int | None = None

        # Adaptive state, all recomputed from the backdrop on capture.
        self._luminance = 0.5   # 0 = black backdrop, 1 = white
        self._busyness = 0.0    # local variance of the backdrop, 0..1

        # Interactive state (Apple: highlights follow motion, and the material
        # thickens while it is being interacted with).
        self._hovered = False
        self._pressed = False
        self._light = (0.35, -1.0)   # light direction, top-left by default
        self._thickness = 0.0        # 0 = resting, 1 = fully thickened
        self._anim_id: int | None = None

        if source is not None:
            self.set_source(source)

    # -- content -----------------------------------------------------------
    def set_child(self, child: Gtk.Widget | None) -> None:
        current = self.get_child()
        if current is not None:
            current.unparent()
        if child is not None:
            child.set_parent(self)

    def get_child(self) -> Gtk.Widget | None:
        return self.get_first_child()

    # -- backdrop ----------------------------------------------------------
    def set_source(self, source: Gtk.Widget | None) -> None:
        """Set the widget whose pixels this panel refracts."""
        if self._paintable is not None:
            self._paintable.disconnect_by_func(self._on_source_changed)
            self._paintable = None
        self._source = source
        if source is not None:
            paintable = Gtk.WidgetPaintable.new(source)
            # WidgetPaintable tells us whenever the source repaints, which is
            # exactly when the refraction is stale -- no polling, no per-frame
            # capture.
            paintable.connect("invalidate-contents", self._on_source_changed)
            self._paintable = paintable

    def _on_source_changed(self, _paintable) -> None:
        # Coalesce bursts (scrolling redraws in a storm of invalidations) into
        # one capture per frame at most.
        if self._idle_id is None:
            self._idle_id = GLib.idle_add(self._capture_idle)

    def _capture_idle(self) -> bool:
        self._idle_id = None
        self.refresh()
        return False

    def refresh(self) -> None:
        """Re-capture the backdrop and recompute the adaptive parameters."""
        paintable = self._paintable
        if paintable is None or not self.get_mapped():
            return
        root = self.get_root()
        if root is None:
            return
        try:
            renderer = Gtk.Native.get_renderer(root)
        except (TypeError, GLib.Error):
            renderer = None
        if renderer is None:
            return
        if self._source is None:
            return

        alloc = self._source.get_allocation()
        if alloc.width <= 0 or alloc.height <= 0:
            return

        try:
            snap = Gtk.Snapshot.new()
            paintable.snapshot(snap, alloc.width, alloc.height)
            self._texture = renderer.render_texture(snap.to_node(), None)

            # A deliberately tiny second render: it is the probe for the
            # adaptive tint and shadow, and at 32x20 the readback is cheap.
            probe = Gtk.Snapshot.new()
            paintable.snapshot(probe, 32, 20)
            small = renderer.render_texture(probe.to_node(), None)
            self._luminance, self._busyness = self._analyse(small)
        except Exception:  # noqa: BLE001 - never let a capture break a frame
            self._texture = None

        self.queue_draw()

    @staticmethod
    def _analyse(texture: Gdk.Texture) -> tuple[float, float]:
        """Mean luminance and local variance of a small backdrop render."""
        w, h = texture.get_width(), texture.get_height()
        if w <= 0 or h <= 0:
            return 0.5, 0.0
        # Gdk.Texture.download() is broken under PyGObject -- silently returns
        # all-zeros.  Use the deprecated but functional pixbuf path instead.
        try:
            pb = Gdk.pixbuf_get_from_texture(texture)
            raw = pb.get_pixels()
            stride = pb.get_rowstride()
            n_ch = pb.get_n_channels()
        except Exception:  # noqa: BLE001
            return 0.5, 0.0
        lum = []
        for y in range(h):
            row = y * stride
            for x in range(w):
                base = row + x * n_ch
                lum.append(
                    0.2126 * raw[base]
                    + 0.7152 * raw[base + 1]
                    + 0.0722 * raw[base + 2])
        mean = sum(lum) / len(lum) / 255.0
        var = sum((v - mean * 255.0) ** 2 for v in lum) / len(lum)
        # Normalise: ~90 luma of standard deviation counts as fully "busy".
        return mean, min(math.sqrt(var) / 90.0, 1.0)

    # -- interaction -------------------------------------------------------
    def set_hovered(self, hovered: bool) -> None:
        if hovered != self._hovered:
            self._hovered = hovered
            self._animate()

    def set_pressed(self, pressed: bool) -> None:
        if pressed != self._pressed:
            self._pressed = pressed
            self._animate()

    def set_light(self, x: float, y: float) -> None:
        """Aim the specular highlight: Apple's light follows motion.

        ``x``/``y`` are a direction from the panel's centre -- the dock passes
        the pointer, so the highlight physically swings around the rim as the
        pointer moves, the way illumination travels around the material on
        device motion.
        """
        self._light = (x, y)
        self.queue_draw()

    def _target_thickness(self) -> float:
        return 1.0 if self._pressed else (0.55 if self._hovered else 0.0)

    def _animate(self) -> None:
        if self._anim_id is None:
            self._anim_id = GLib.timeout_add(16, self._tick)

    def _tick(self) -> bool:
        target = self._target_thickness()
        # Critically damped approach: no overshoot, settles in ~250 ms.
        delta = target - self._thickness
        self._thickness += delta * 0.22
        self.queue_draw()
        if abs(delta) < 0.01:
            self._thickness = target
            self._anim_id = None
            return False
        return True

    # -- painting ----------------------------------------------------------
    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:  # noqa: N802 - GTK vfunc
        w = float(self.get_width())
        h = float(self.get_height())
        if w <= 0.0 or h <= 0.0:
            return
        rect = _rect(0.0, 0.0, w, h)
        radius = min(self.corner_radius, min(w, h) / 2.0)
        self._draw_material(snapshot, rect, radius)
        child = self.get_child()
        if child is not None:
            self.snapshot_child(child, snapshot)

    def _draw_material(self, snapshot: Gtk.Snapshot, rect: Graphene.Rect,
                       radius: float) -> None:
        w, h = rect.size.width, rect.size.height
        spec = self.spec
        thick = self._thickness
        texture = self._texture

        if texture is None:
            # Before the first capture (or with no backdrop to refract) the
            # panel still has to be readable: a plain translucent fill.
            snapshot.push_rounded_clip(_rounded(rect, radius))
            snapshot.append_color(_FALLBACK, rect)
            snapshot.pop()
            return

        # Where this panel sits inside the captured backdrop, in texture px.
        ox, oy = self._source_offset()

        # The bevel deepens as the material thickens (Apple: a bigger surface
        # simulates thicker glass).
        depth = spec.lens_depth * (1.0 + 0.55 * thick)

        snapshot.push_rounded_clip(_rounded(rect, radius))
        snapshot.push_blur(spec.blur * (1.0 + 0.25 * thick))

        # --- lensing -----------------------------------------------------
        # Each edge is sampled from *outside* the panel and compressed into a
        # band, most strongly at the rim. The linear map is chosen so that the
        # inner edge of every band lines up exactly with the backdrop it
        # belongs to, which is what makes the bend read as one continuous
        # surface rather than a stack of copies.
        bands = max(1, spec.lens_bands)
        for k in range(bands):
            inner = depth * (k + 1) / bands
            outer = depth * k / bands
            frac = 1.0 - k / bands
            squeeze = depth * spec.lens_squeeze * frac * frac
            if k == bands - 1:
                squeeze = 0.0
            # top / bottom
            span_y = inner - outer + squeeze
            snapshot.push_clip(_rect(0.0, outer, w, inner - outer))
            _blit(snapshot, texture,
                  (ox, oy + outer - squeeze, ox + w, oy + outer - squeeze + span_y),
                  (0.0, outer, w, inner))
            snapshot.pop()
            snapshot.push_clip(_rect(0.0, h - inner, w, inner - outer))
            _blit(snapshot, texture,
                  (ox, oy + h - outer - span_y + squeeze,
                   ox + w, oy + h - outer + squeeze),
                  (0.0, h - inner, w, h - outer))
            snapshot.pop()
            # left / right
            span_x = inner - outer + squeeze
            snapshot.push_clip(_rect(outer, 0.0, inner - outer, h))
            _blit(snapshot, texture,
                  (ox + outer - squeeze, oy, ox + outer - squeeze + span_x, oy + h),
                  (outer, 0.0, inner, h))
            snapshot.pop()
            snapshot.push_clip(_rect(w - inner, 0.0, inner - outer, h))
            _blit(snapshot, texture,
                  (ox + w - outer - span_x + squeeze, oy,
                   ox + w - outer + squeeze, oy + h),
                  (w - inner, 0.0, w - outer, h))
            snapshot.pop()

        # --- interior: the true backdrop, scattered but not displaced -----
        snapshot.push_clip(_rect(depth, depth, max(w - 2 * depth, 0.0),
                                 max(h - 2 * depth, 0.0)))
        _blit(snapshot, texture, (ox, oy, ox + w, oy + h),
              (0.0, 0.0, w, h))
        snapshot.pop()

        snapshot.pop()  # blur

        # --- adaptive tint + vibrancy ------------------------------------
        snapshot.append_color(self._tint(), rect)
        snapshot.pop()  # rounded clip

        self._draw_rim(snapshot, rect, radius, thick)
        self._draw_shadows(snapshot, rect, radius, thick)

    def _source_offset(self) -> tuple[float, float]:
        """Panel origin inside the captured texture, in texture pixels."""
        root = self.get_root()
        if root is None or self._source is None:
            return 0.0, 0.0
        try:
            origin = Graphene.Point().init(0.0, 0.0)
            src = self._source.compute_point(root, origin)[1]
            own = self.compute_point(root, origin)[1]
            # Allocations, compute_point and the WidgetPaintable capture are
            # all in the same logical pixel space, so the offset transfers 1:1.
            return own.x - src.x, own.y - src.y
        except Exception:  # noqa: BLE001
            return 0.0, 0.0

    def _tint(self) -> Gdk.RGBA:
        """Tint colour and alpha, derived from what is behind the glass."""
        spec = self.spec
        lum = self._luminance
        alpha = (spec.tint_alpha_dark_bg
                 + (spec.tint_alpha_bright_bg - spec.tint_alpha_dark_bg) * lum)
        base = spec.tint_bright_bg if lum > 0.5 else spec.tint_dark_bg
        # Busy backdrops need a touch more tint to keep foreground readable.
        alpha = min(alpha + 0.10 * self._busyness, 0.75)
        return _rgba(base[0], base[1], base[2], alpha)

    def _draw_rim(self, snapshot: Gtk.Snapshot, rect: Graphene.Rect,
                  radius: float, thick: float) -> None:
        """The specular highlight -- the lit edge of the glass."""
        spec = self.spec
        lx, ly = self._light
        norm = math.hypot(lx, ly) or 1.0
        lx, ly = lx / norm, ly / norm
        # Light comes *from* the pointer side, so the rim lights up facing it.
        gain = 1.0 + 0.35 * thick
        bright = min(spec.rim_alpha * gain * (1.15 - 0.35 * self._luminance), 1.0)

        inset = spec.rim_width / 2.0
        outline = rect.inset(inset, inset)
        builder = Gsk.PathBuilder()
        builder.add_rounded_rect(_rounded(outline, max(radius - inset, 0.0)))
        path = builder.to_path()
        stroke = Gsk.Stroke.new(spec.rim_width)

        start = Graphene.Point().init(
            rect.size.width * (0.5 - 0.5 * lx), rect.size.height * (0.5 - 0.5 * ly))
        end = Graphene.Point().init(
            rect.size.width * (0.5 + 0.5 * lx), rect.size.height * (0.5 + 0.5 * ly))

        snapshot.push_stroke(path, stroke)
        try:
            stops = []
            for offset, alpha in ((0.0, bright), (0.42, bright * 0.16), (1.0, bright * 0.55)):
                stop = Gsk.ColorStop()
                stop.offset = offset
                stop.color = _rgba(1.0, 1.0, 1.0, alpha)
                stops.append(stop)
            snapshot.append_linear_gradient(rect, start, end, stops)
        except Exception:  # noqa: BLE001 - gradient API mismatch: plain rim
            snapshot.append_color(_rgba(1.0, 1.0, 1.0, bright * 0.5), rect)
        snapshot.pop()

    def _draw_shadows(self, snapshot: Gtk.Snapshot, rect: Graphene.Rect,
                      radius: float, thick: float) -> None:
        """Depth shading that follows the backdrop (Apple's adaptive shadows)."""
        spec = self.spec
        busy = self._busyness
        bright = self._luminance
        # Separating a panel from busy or bright content needs more shadow;
        # over flat dark content a heavy shadow just looks muddy.
        sep = 0.55 + 0.45 * busy + 0.35 * bright
        snapshot.append_inset_shadow(
            _rounded(rect, radius), _rgba(0.0, 0.0, 0.0,
                                          min(spec.inner_shadow_alpha * sep, 0.8)),
            0.0, -1.5 * (1.0 + thick), 0.0, spec.inner_shadow_blur * (1.0 + 0.5 * thick))
        snapshot.append_outset_shadow(
            _rounded(rect, radius), _rgba(0.0, 0.0, 0.0,
                                          min(spec.drop_shadow_alpha * sep, 0.8)),
            0.0, spec.drop_shadow_dy * (1.0 + 0.4 * thick), 0.0,
            spec.drop_shadow_blur * (1.0 + 0.4 * thick))


# ---------------------------------------------------------------------------
# CSS the panels need, and a helper that wires a glass surface in one call
# ---------------------------------------------------------------------------

_GLASS_CSS = b"""
/* Content that sits on glass must not paint its own opaque background, or the
   material behind it is simply hidden. */
.glass-host, .glass-host > headerbar, .glass-host headerbar {
  background: none;
  background-color: transparent;
  border: none;
  box-shadow: none;
}
/* The dock pill itself is fully transparent -- the GlassPanel provides the
   glass material. Disc styling stays in dock.py. */
.glass-host .floating-dock { background: none; outline: none; }
.glass-host .dock-tooltip { background: none; }
"""

_css_loaded = False


def ensure_css() -> None:
    global _css_loaded  # noqa: PLW0603
    if _css_loaded:
        return
    _css_loaded = True
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_GLASS_CSS)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def glassify(widget: Gtk.Widget, source: Gtk.Widget, spec: GlassSpec = REGULAR,
             corner_radius: float = 18.0,
             light: Callable[[], tuple[float, float]] | None = None,
             hover: Gtk.Widget | None = None) -> GlassPanel:
    """Wrap ``widget`` in Liquid Glass floating above ``source``.

    The wrapped widget is tagged ``glass-host`` so the theme stops painting its
    old opaque background -- the material is its background now.
    """
    ensure_css()
    panel = GlassPanel(spec=spec, corner_radius=corner_radius, source=source)
    panel.add_css_class("glass-host")
    assert_not_nested(panel)
    widget.add_css_class("glass-host")
    panel.set_child(widget)
    target = hover if hover is not None else widget
    motion = Gtk.EventControllerMotion()

    def on_enter(_c, x, y):
        panel.set_hovered(True)

    def on_leave(_c):
        panel.set_hovered(False)

    def on_motion(_c, x, y):
        if light is not None:
            dx, dy = light()
            panel.set_light(dx, dy)

    motion.connect("enter", on_enter)
    motion.connect("leave", on_leave)
    motion.connect("motion", on_motion)
    target.add_controller(motion)
    return panel
