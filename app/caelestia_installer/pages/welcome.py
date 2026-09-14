"""Welcome page: what this is, current status, quick entry points."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import checks  # noqa: E402


class WelcomePage(Adw.Bin):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win

        clamp = Adw.Clamp.new()
        clamp.set_maximum_size(760)
        clamp.set_valign(Gtk.Align.CENTER)

        box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 18)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(18)
        box.set_margin_end(18)

        title = Gtk.Label.new(
            "<span size='22000'><b>Caelestia for Ubuntu</b></span>\n"
            "Caelestia Shell + Hyprland as a side-by-side session"
        )
        title.set_use_markup(True)
        title.set_justify(Gtk.Justification.CENTER)
        box.append(title)

        body = Gtk.Label.new(
            "This app installs the full Caelestia desktop — Quickshell shell, "
            "Hyprland compositor, theme, fonts, wallpapers and idle/lock setup — "
            "without touching a terminal.\n\n"
            "Everything builds from pinned upstream commits, your existing GNOME "
            "session stays untouched, and you can uninstall any time."
        )
        body.set_use_markup(False)
        body.set_wrap(True)
        body.set_justify(Gtk.Justification.CENTER)
        box.append(body)

        self.status_row = Adw.ActionRow.new()
        self.status_row.set_title("Checking system…")
        status_group = Adw.PreferencesGroup.new()
        status_group.set_title("Current status")
        status_group.add(self.status_row)
        box.append(status_group)

        btns = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        btns.set_halign(Gtk.Align.CENTER)
        self.btn_install = Gtk.Button.new_with_label("Install now")
        self.btn_install.set_css_classes(["suggested-action", "pill"])
        self.btn_install.connect("clicked", lambda *_: self.win.goto_install())
        self.btn_open_setup = Gtk.Button.new_with_label("Open Setup")
        self.btn_open_setup.set_css_classes(["pill"])
        self.btn_open_setup.connect("clicked", lambda *_: self.win.goto_setup())
        self.btn_updates = Gtk.Button.new_with_label("Check for updates")
        self.btn_updates.set_css_classes(["pill"])
        self.btn_updates.connect("clicked", lambda *_: self.win.goto_updates())
        self.btn_guides = Gtk.Button.new_with_label("Read the guides")
        self.btn_guides.set_css_classes(["pill"])
        self.btn_guides.connect("clicked", lambda *_: self.win.select_page("guides"))
        btns.append(self.btn_install)
        btns.append(self.btn_open_setup)
        btns.append(self.btn_updates)
        btns.append(self.btn_guides)
        box.append(btns)

        warn = Gtk.Label.new(
            "Requirements: Ubuntu 24.04 / Zorin 18 / Mint 22 / Pop!_OS 22.04 on x86_64,\n"
            "~8 GB free disk space, an internet connection and your sudo password."
        )
        warn.set_css_classes(["dim-label"])
        warn.set_justify(Gtk.Justification.CENTER)
        warn.set_wrap(True)
        box.append(warn)

        clamp.set_child(box)
        scrolled = Gtk.ScrolledWindow.new()
        scrolled.set_child(clamp)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        self.set_child(scrolled)

    def on_navigate_to(self) -> None:
        self.refresh_status()

    def refresh_status(self) -> None:
        state = checks.installed_state()
        if state["installed"]:
            qt = state.get("qt_version") or "unknown Qt"
            self.status_row.set_title("Installed ✓")
            self.status_row.set_subtitle(
                f"Caelestia is installed (Qt {qt}). Use Setup for wallpaper and "
                "settings, Updates to apply revisions, or Install to repair.")
            self.btn_install.set_label("Repair / reinstall")
            self.btn_open_setup.set_sensitive(True)
            self.btn_updates.set_sensitive(True)
        else:
            self.status_row.set_title("Not installed yet")
            self.status_row.set_subtitle(
                "Run the installer to add the Hyprland + Caelestia session. "
                "You will pick it from the gear menu at the login screen.")
            self.btn_install.set_label("Install now")
            self.btn_open_setup.set_sensitive(False)
            self.btn_updates.set_sensitive(False)
