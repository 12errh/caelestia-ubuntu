"""Guides page — the complete built-in user guide.

Content is written for the exact setup this repository deploys: the keybinds
come from configs/hypr/hyprland.conf, the launcher actions and idle timeouts
from configs/caelestia/shell.json, and the troubleshooting items from the
README.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..page_style import build_page  # noqa: E402
from ..ui import bullet_list, make_label  # noqa: E402

GUIDES = [
    {
        "title": "Getting started",
        "icon": "starred-symbolic",
        "sections": [
            ("First login", [
                "Reboot (or log out) after the installer finishes.",
                "At the GDM login screen click your name, then the **gear icon** "
                "in the bottom-right corner.",
                "Choose **Hyprland** and log in. GNOME stays untouched — pick it "
                "again from the same menu any time.",
                "The Caelestia shell (bar, wallpaper, launcher) starts "
                "automatically as a systemd user service.",
            ]),
            ("Verify everything works", [
                "The top bar shows workspaces, the clock and status icons.",
                "Press **Super+D** — the launcher should appear.",
                "If the bar is missing: run `systemctl --user status "
                "caelestia-shell` in a terminal (GNOME session works too).",
            ]),
        ],
    },
    {
        "title": "Keybinds",
        "icon": "input-keyboard-symbolic",
        "sections": [
            ("Everyday", [
                "**Super+D** — launcher (apps, `>calc`, `>wallpaper`, `>scheme`)",
                "**Super+N** — notification / control drawer (sidebar)",
                "**Super+Return** — terminal",
                "**Super+E** — file manager (nautilus)",
                "**Super+B** — Chrome · **Super+Shift+B** — Bazaar (app store)",
                "**Super+Q** — close window · **Super+M** — power menu (wlogout)",
                "**Super+L** — lock screen (Caelestia lock, fingerprint works)",
            ]),
            ("Windows &amp; workspaces", [
                "**Super+1…0** — go to workspace 1–10",
                "**Super+Shift+1…0** — move the window to that workspace",
                "**Super+H/J/K/L** — move the window",
                "**Super+R** — resize submap (arrow keys, **Esc** to leave)",
                "**Super+V** or **Super+Space** — toggle floating",
                "**Super+F** — fullscreen",
                "**Super+mouse buttons** — move / resize by dragging",
            ]),
            ("Screenshots", [
                "**Super+Shift+S** — select a region",
                "**Super+Shift+P** — the active window",
                "**Super+Shift+A** — the whole screen",
                "Saved into `~/Pictures/Screenshots` via hyprshot.",
            ]),
        ],
    },
    {
        "title": "Wallpaper &amp; looks",
        "icon": "image-x-generic-symbolic",
        "sections": [
            ("Change the wallpaper", [
                "Easiest: this app's **Setup tab → Wallpaper**.",
                "Inside Hyprland: **Super+D**, type `>wallpaper`, pick an image.",
                "Terminal: `caelestia wallpaper -f ~/Pictures/wallpapers/file.jpg`.",
                "Put your own images in `~/Pictures/wallpapers/` first.",
            ]),
            ("Dynamic colours", [
                "The whole interface — bar, drawers, lock screen, OSD — "
                "recolours itself from the wallpaper automatically.",
                "Change the colour scheme variant with **Super+D → >scheme**.",
            ]),
            ("Tweaks in this app", [
                "Setup tab: transparency, rounding, animation speed, font scale "
                "— written live to `~/.config/caelestia/shell.json`.",
            ]),
        ],
    },
    {
        "title": "Idle, lock &amp; power",
        "icon": "system-lock-screen-symbolic",
        "sections": [
            ("What happens by default", [
                "**3 min** idle → lock screen",
                "**5 min** idle → screen off (DPMS)",
                "**10 min** idle → suspend-then-hibernate",
                "Audio playing keeps the machine awake (configurable in Setup).",
            ]),
            ("Laptop lid", [
                "Closing the lid suspends; on wake the shell restarts cleanly "
                "and the lock screen is re-acquired automatically "
                "(systemd-sleep hooks installed with the desktop).",
            ]),
            ("Lock screen", [
                "**Super+L** locks instantly. Type your password or use the "
                "fingerprint reader (quickshell PAM).",
                "Note: `hyprlock` alone can't lock while Caelestia holds the "
                "session lock — that's normal, use Super+L.",
            ]),
        ],
    },
    {
        "title": "Updating",
        "icon": "view-refresh-symbolic",
        "sections": [
            ("Keep it current", [
                "This app: **Updates tab → Recheck now**, then **Apply updates**.",
                "Terminal: `./update.sh` — check + apply, `./update.sh --check` "
                "for a read-only report.",
                "System packages (Hyprland, drivers) update normally with apt.",
            ]),
            ("How versions work", [
                "Every component builds from an exact commit pinned in "
                "`revisions.conf` — the known-good revision the maintainer "
                "tested, so updates never silently break.",
                "Qt is a fixed toolchain; bump deliberately with "
                "`./setup.sh --qt-version X`.",
            ]),
        ],
    },
    {
        "title": "Troubleshooting",
        "icon": "dialog-question-symbolic",
        "sections": [
            ("Black screen / no bar at login", [
                "Log into GNOME, open a terminal:",
                "`systemctl --user status caelestia-shell`",
                "`journalctl --user -u caelestia-shell -e`",
                "Then `systemctl --user restart caelestia-shell` or reboot.",
            ]),
            ("Lock screen issues after lid resume", [
                "The sleep hooks re-acquire the lock automatically. If a "
                "frozen screen persists, re-run the installer once (Install "
                "tab) — it re-deploys the hooks with your user id.",
            ]),
            ("hyprctl configerrors looks wrong", [
                "Run `hyprctl reload` to pick up edited configs.",
            ]),
            ("Missing Calculator in the launcher", [
                "`sudo apt install qalculate` (the CLI, not just the library).",
            ]),
            ("Uninstall / start over", [
                "Advanced tab → Remove Caelestia. Configs are archived to "
                "`~/.config/caelestia-ubuntu-uninstalled-<date>`.",
                "GNOME was never modified — removing this returns the system "
                "to stock.",
            ]),
        ],
    },
]


class GuidesPage(Adw.Bin):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win

        box = build_page(self, "GUIDES  /  FIND YOUR WAY",
                         "A familiar place to begin.",
                         "First login, everyday shortcuts and help when something feels off. "
                         "Choose a topic to explore.")

        for g in GUIDES:
            exp = Adw.ExpanderRow.new()
            exp.set_title(g["title"])
            exp.set_subtitle(f"{len(g["sections"])} short sections")
            exp.add_prefix(Gtk.Image.new_from_icon_name(g["icon"]))
            for section_title, items in g["sections"]:
                row = Adw.PreferencesRow.new()
                row.set_activatable(False)
                inner = Gtk.Box.new(Gtk.Orientation.VERTICAL, 6)
                inner.set_margin_top(12)
                inner.set_margin_bottom(12)
                inner.set_margin_start(15)
                inner.set_margin_end(15)
                st = make_label(f"<b>{section_title}</b>")
                inner.append(st)
                inner.append(bullet_list(items))
                row.set_child(inner)
                exp.add_row(row)
            group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            group.add_css_class("boxed-list")
            group.append(exp)
            box.append(group)
