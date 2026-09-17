"""Load the shared Welcome / Install stylesheet once per process."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from . import paths  # noqa: E402

_loaded = False


def load_page_css() -> None:
    global _loaded
    if _loaded:
        return
    provider = Gtk.CssProvider()
    provider.load_from_path(str(paths.BRAND_LOGO.parent / "pages.css"))
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _loaded = True


def build_page(page, eyebrow: str, title: str, copy: str) -> Gtk.Box:
    """Shared content geometry; navigation motion belongs to the main window."""
    load_page_css()
    page.add_css_class("content-page")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
    box.set_margin_top(32)
    box.set_margin_bottom(140)
    box.set_margin_start(24)
    box.set_margin_end(24)
    head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    for text, css in ((eyebrow, "page-eyebrow"), (title, "page-title"),
                      (copy, "page-copy")):
        label = Gtk.Label(label=text, wrap=True, xalign=0)
        label.add_css_class(css)
        head.append(label)
    box.append(head)
    from gi.repository import Adw
    clamp = Adw.Clamp(maximum_size=780)
    clamp.set_child(box)
    scroll = Gtk.ScrolledWindow(vexpand=True)
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_child(clamp)
    page.set_child(scroll)
    return box
