"""A calm, shell-inspired landing page with state-aware entry points."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import checks, paths  # noqa: E402
from ..page_style import load_page_css  # noqa: E402


def _label(text: str, css: str, centered: bool = False) -> Gtk.Label:
    label = Gtk.Label(label=text, wrap=True, xalign=0.5 if centered else 0.0)
    label.add_css_class(css)
    if centered:
        label.set_justify(Gtk.Justification.CENTER)
    return label


def _link(text: str, callback) -> Gtk.Button:
    button = Gtk.Button(label=text)
    button.add_css_class("welcome-link")
    button.set_halign(Gtk.Align.START)
    button.connect("clicked", lambda *_: callback())
    return button


class WelcomePage(Adw.Bin):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self._installed = False
        load_page_css()
        self.add_css_class("welcome-page")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        content.set_margin_top(32)
        # Reserve the dock's hover/tooltip envelope, including when scrolled.
        content.set_margin_bottom(140)
        content.set_margin_start(24)
        content.set_margin_end(24)
        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        brand.set_halign(Gtk.Align.CENTER)
        logo = Gtk.Image.new_from_file(str(paths.BRAND_LOGO))
        logo.set_pixel_size(36)
        brand.append(logo)
        brand.append(_label("CAELESTIA  /  UBUNTU", "welcome-brand"))
        content.append(brand)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.title = _label("A little space to make your own.", "welcome-title", True)
        self.title.set_max_width_chars(24)
        hero.append(self.title)
        hero.append(_label(
            "A considered desktop. A quieter workflow.\n"
            "Caelestia Shell and Hyprland, alongside your existing session.",
            "welcome-copy", True))
        content.append(hero)
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        actions.set_halign(Gtk.Align.CENTER)
        self.primary = Gtk.Button(label="Set up Caelestia")
        self.primary.set_css_classes(["suggested-action", "welcome-primary"])
        self.primary.connect("clicked", self._primary_clicked)
        actions.append(self.primary)
        actions.append(_label("Your existing desktop stays yours.", "welcome-caption", True))
        content.append(actions)

        # Wrapping panels avoid a fixed-width row clipping small windows.
        panels = Gtk.FlowBox()
        panels.set_selection_mode(Gtk.SelectionMode.NONE)
        panels.set_homogeneous(True)
        panels.set_min_children_per_line(1)
        panels.set_max_children_per_line(2)
        panels.set_column_spacing(12)
        panels.set_row_spacing(12)
        panels.set_activate_on_single_click(False)
        status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        status.add_css_class("welcome-panel")
        status.append(_label("YOUR SESSION", "welcome-eyebrow"))
        self.status_title = _label("Checking installation…", "welcome-panel-title")
        self.status_title.add_css_class("welcome-state")
        status.append(self.status_title)
        self.status_detail = _label("", "welcome-caption")
        self.status_detail.set_max_width_chars(30)
        status.append(self.status_detail)
        status_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.updates = _link("Updates", self.win.goto_updates)
        self.repair = _link("Repair", self.win.goto_install)
        status_actions.append(self.updates)
        status_actions.append(self.repair)
        status.append(status_actions)
        panels.append(status)
        guide = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        guide.add_css_class("welcome-panel")
        guide.append(_label("FIND YOUR WAY", "welcome-eyebrow"))
        guide.append(_label("A familiar starting point.", "welcome-panel-title"))
        hint = _label("First login, useful shortcuts, and the details that make it yours.",
                      "welcome-caption")
        hint.set_max_width_chars(30)
        guide.append(hint)
        guide.append(_link("Explore the guide  →", lambda: self.win.select_page("guides")))
        panels.append(guide)
        content.append(panels)
        self.footnote = _label("", "welcome-caption", True)
        content.append(self.footnote)
        clamp = Adw.Clamp(maximum_size=780)
        clamp.set_child(content)
        clamp.set_valign(Gtk.Align.CENTER)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(clamp)
        self.set_child(scroll)

    def _primary_clicked(self, _button) -> None:
        if self._installed:
            self.win.goto_setup()
        else:
            self.win.goto_install()

    def on_navigate_to(self) -> None:
        self.refresh_status()

    def refresh_status(self) -> None:
        state = checks.installed_state()
        self._installed = bool(state["installed"])
        self.primary.set_label("Make it yours  →" if self._installed else "Set up Caelestia  →")
        self.status_title.set_label("Installed on this machine" if self._installed else "Ready when you are")
        self.status_detail.set_label(
            "Personalise your wallpaper and appearance, or manage your revisions."
            if self._installed else
            "A separate session, built from pinned sources. Start with a system check.")
        self.updates.set_visible(self._installed)
        self.repair.set_visible(self._installed)
        self.footnote.set_label(
            "CAELESTIA SHELL  ·  HYPRLAND  ·  YOUR OWN SPACE" if self._installed else
            "Ubuntu 24.04 family  ·  x86_64  ·  8 GB free  ·  Internet required")
