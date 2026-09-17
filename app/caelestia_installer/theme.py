"""The app's colour theme — **Caelestia Aurora**.

Every colour the app paints is defined here and nowhere else, so retheming is
a data edit in one file instead of a hunt through CSS. :func:`apply` maps the
palette onto libadwaita's *named colours* (``@window_bg_color``,
``@accent_color``, …): those names are what libadwaita's own stylesheet and the
app's widgets already reference, so overriding them recolours every page,
card, button and dialog at once — no per-widget rules.

Why the palette looks like this:

* **Deep-space base, one accent.** The installer ships a desktop that is
  itself dark and glassy, so the app matches it: a near-black base
  (``#0A0E14``) with a single aqua accent (``#4ADEC8``). One accent means the
  colour still carries meaning — aqua is the app's voice, while green/amber/red
  are reserved for the real install states (up to date / drift / failure).
* **Elevated, not outlined, surfaces.** Cards sit one step up from the window
  (``#121821``) and popovers/the dock one step above that (``#1A222D``), so
  depth comes from luminance rather than from heavy borders.
* **Dark-only, deliberately.** The palette is a dark one, so :func:`apply`
  forces the dark colour scheme. Letting the app follow a light system theme
  would mix light chrome with dark surfaces.

The dock's own CSS (see :mod:`caelestia_installer.dock`) draws from
``@popover_bg_color``, which is why it reads as a floating panel above the
window rather than blending into it.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

#: Human name, shown in the UI and in the module docstring's spirit.
NAME = "Caelestia Aurora"

#: The palette. Change these values to retheme the whole app.
PALETTE: dict[str, str] = {
    "base":       "#0A0E14",   # window background
    "view":       "#0F141C",   # content areas, one step above the window
    "headerbar":  "#0D1119",   # title bar / sidebars
    "surface":    "#121821",   # cards, rows, list backgrounds
    "elevated":   "#1A222D",   # popovers, dialogs, the dock bar
    "fg":         "#E3E9F0",   # primary text
    "muted":      "#8B97A8",   # subtitles, secondary text
    "accent":     "#4ADEC8",   # the one accent: links, selection, focus
    "accent_bg":  "#17786B",   # accent behind white text (buttons, badges)
    "borders":    "#263141",   # hairlines and separators
    "success":    "#6FD08C",
    "success_bg": "#2E7D4F",
    "warning":    "#E6C07B",
    "warning_bg": "#8A6320",
    "error":      "#F2777A",
    "error_bg":   "#A63D3F",
}

#: libadwaita named colour -> palette key. These names are libadwaita's public
#: theming API; overriding them is the supported way to re-skin an app.
_ROLES: tuple[tuple[str, str], ...] = (
    ("window_bg_color", "base"),
    ("window_fg_color", "fg"),
    ("view_bg_color", "view"),
    ("view_fg_color", "fg"),
    ("headerbar_bg_color", "headerbar"),
    ("headerbar_fg_color", "fg"),
    ("headerbar_border_color", "borders"),
    ("headerbar_backdrop_color", "headerbar"),
    ("sidebar_bg_color", "headerbar"),
    ("sidebar_fg_color", "fg"),
    ("sidebar_backdrop_color", "headerbar"),
    ("secondary_sidebar_bg_color", "base"),
    ("secondary_sidebar_fg_color", "fg"),
    ("secondary_sidebar_backdrop_color", "base"),
    ("card_bg_color", "surface"),
    ("card_fg_color", "fg"),
    ("dialog_bg_color", "elevated"),
    ("dialog_fg_color", "fg"),
    ("popover_bg_color", "elevated"),
    ("popover_fg_color", "fg"),
    ("thumbnail_bg_color", "elevated"),
    ("thumbnail_fg_color", "fg"),
    ("accent_color", "accent"),
    ("accent_bg_color", "accent_bg"),
    ("accent_fg_color", "#FFFFFF"),
    ("destructive_color", "error"),
    ("destructive_bg_color", "error_bg"),
    ("destructive_fg_color", "#FFFFFF"),
    ("success_color", "success"),
    ("success_bg_color", "success_bg"),
    ("success_fg_color", "#FFFFFF"),
    ("warning_color", "warning"),
    ("warning_bg_color", "warning_bg"),
    ("warning_fg_color", "#FFFFFF"),
    ("error_color", "error"),
    ("error_bg_color", "error_bg"),
    ("error_fg_color", "#FFFFFF"),
    # Not a libadwaita name: used by this app's own CSS and by nothing else.
    ("borders", "borders"),
)

#: Shades and outlines. Kept separate from the palette because they are about
#: translucency (scroll-edge shadows, card and headerbar shading) rather than
#: about a hue, and libadwaita expects rgba() here.
_SHADES: tuple[tuple[str, str], ...] = (
    ("shade_color", "rgba(0, 0, 0, 0.36)"),
    ("card_shade_color", "rgba(0, 0, 0, 0.28)"),
    ("headerbar_shade_color", "rgba(0, 0, 0, 0.36)"),
    ("sidebar_shade_color", "rgba(0, 0, 0, 0.36)"),
    ("secondary_sidebar_shade_color", "rgba(0, 0, 0, 0.36)"),
    ("popover_shade_color", "rgba(0, 0, 0, 0.36)"),
    ("scrollbar_outline_color", "rgba(0, 0, 0, 0.5)"),
)


def background_css() -> str:
    """CSS for the faint aurora field behind the content — glass needs it to refract.

    Apply the ``calestia-aurora`` class to any widget that should show the
    aurora gradient.  Over a flat colour the liquid-glass material is invisible;
    this gives it something to bend.
    """
    return """
.calestia-aurora {
  background:
    radial-gradient(ellipse at 15% 20%, alpha(#12556B, 0.35) 0%, transparent 55%),
    radial-gradient(ellipse at 75% 35%, alpha(#2F8F74, 0.25) 0%, transparent 50%),
    radial-gradient(ellipse at 50% 80%, alpha(#4ADEC8, 0.10) 0%, transparent 45%),
    radial-gradient(ellipse at 90% 10%, alpha(#8A5AB8, 0.18) 0%, transparent 40%),
    @window_bg_color;
}
"""


def stylesheet() -> str:
    """The palette as GTK CSS — a list of ``@define-color`` overrides."""
    lines = [f"/* {NAME} — generated from theme.PALETTE */"]
    for name, key in _ROLES:
        value = key if key.startswith("#") else PALETTE[key]
        lines.append(f"@define-color {name} {value};")
    for name, value in _SHADES:
        lines.append(f"@define-color {name} {value};")
    return "\n".join(lines) + "\n"


def apply() -> bool:
    """Install the palette for this display and force the dark scheme.

    Idempotent, and safe to call before any window exists — that is the point:
    the very first frame is already themed, with no flash of default chrome.
    Returns ``False`` if there is no display to theme (headless runs).
    """
    global _applied  # noqa: PLW0603
    if _applied:
        return True

    display = Gdk.Display.get_default()
    if display is None:
        return False

    provider = Gtk.CssProvider()
    provider.load_from_data(stylesheet().encode())
    # USER priority so the palette wins over libadwaita's own APPLICATION
    # stylesheet (which is where the default values of these names come from).
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

    # Aurora background for the liquid-glass material to refract.
    aurora = Gtk.CssProvider()
    aurora.load_from_data(background_css().encode())
    Gtk.StyleContext.add_provider_for_display(
        display, aurora, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    manager = Adw.StyleManager.get_default()
    if manager is not None:
        manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    _applied = True
    return True


_applied = False
