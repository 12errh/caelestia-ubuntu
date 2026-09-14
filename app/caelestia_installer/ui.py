"""Shared GTK helpers (small utilities used by all pages)."""

from __future__ import annotations

from gi.repository import GLib, Gtk


def ui(func, *args):
    """Run func on the GTK main loop from any thread."""
    GLib.idle_add(func, *args)


def markdownish_to_pango(text: str) -> str:
    """Tiny markdown → Pango: **bold**, `code`, headings as bold lines."""
    import html
    import re
    out = []
    for raw in text.splitlines():
        line = html.escape(raw)
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"`(.+?)`", r"<tt>\1</tt>", line)
        if line.startswith("# "):
            line = f"<big><b>{line[2:]}</b></big>"
        elif line.startswith("## "):
            line = f"<b>{line[3:]}</b>"
        out.append(line)
    return "\n".join(out)


def make_label(markup: str, wrap: bool = True, xalign: float = 0.0,
               css: str = "") -> Gtk.Label:
    lbl = Gtk.Label.new(markup)
    lbl.set_use_markup(True)
    lbl.set_wrap(wrap)
    lbl.set_xalign(xalign)
    if css:
        lbl.set_css_classes([css])
    return lbl


def bullet_list(items: list[str]) -> Gtk.Box:
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 4)
    for it in items:
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        dot = Gtk.Label.new("•")
        row.append(dot)
        row.append(make_label(markdownish_to_pango(it)))
        box.append(row)
    return box
