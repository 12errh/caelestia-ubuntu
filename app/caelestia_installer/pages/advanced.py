"""Advanced page — run the repository scripts directly, with a live log.

Kept deliberately minimal: the Setup/Updates tabs cover the everyday cases,
this is the escape hatch for people who know what the scripts do.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import checks, paths  # noqa: E402
from .script_panel import ScriptPanel  # noqa: E402

# (button label, argv tail, danger, confirmation body)
ACTIONS = [
    ("Run update.sh — rebuild to pinned revisions",
     ["update.sh", "--yes"], False,
     "Rebuilds any component whose installed revision differs from the pinned "
     "one. Safe to run at any time."),
    ("Run update.sh --check — read-only report",
     ["update.sh", "--check"], False,
     "Prints installed vs pinned vs upstream revisions without changing "
     "anything or asking for a password."),
    ("Run uninstall.sh — remove everything (configs preserved)",
     ["uninstall.sh"], True,
     "Stops the shell service, removes binaries/QML modules and archives your "
     "configs. GNOME is never touched."),
    ("Run uninstall.sh --purge-qt — also delete /opt Qt (~2 GB)",
     ["uninstall.sh", "--purge-qt"], True,
     "Everything uninstall.sh does, plus deleting the /opt Qt toolchain. Only "
     "do this if no other build needs that Qt."),
]


class AdvancedPage(Adw.Bin):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win

        sc = Gtk.ScrolledWindow.new()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_vexpand(True)
        clamp = Adw.Clamp.new()
        clamp.set_maximum_size(820)
        box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 18)
        box.set_margin_top(18)
        box.set_margin_bottom(24)
        box.set_margin_start(18)
        box.set_margin_end(18)
        clamp.set_child(box)
        sc.set_child(clamp)
        self.set_child(sc)

        intro = Gtk.Label.new(
            "These run the repository scripts exactly like a terminal would. "
            "Only use them if you know what they do — Setup and Updates cover "
            "the everyday cases with the same live log.")
        intro.set_wrap(True)
        intro.set_xalign(0.0)
        intro.set_css_classes(["dim-label"])
        box.append(intro)

        group = Adw.PreferencesGroup.new()
        group.set_title("Repository scripts")
        for label, argv_tail, danger, body in ACTIONS:
            row = Adw.ActionRow.new()
            row.set_title(label)
            btn = Gtk.Button.new_with_label("Run")
            btn.set_valign(Gtk.Align.CENTER)
            if danger:
                btn.set_css_classes(["destructive-action"])
            btn.connect("clicked", self._on_run, argv_tail, label, danger, body)
            row.add_suffix(btn)
            group.add(row)
        box.append(group)

        self.panel = ScriptPanel(win)
        box.append(self.panel)

    # ------------------------------------------------------------------ run
    def _on_run(self, _b: Gtk.Button, argv_tail: list[str], label: str,
                danger: bool, body: str) -> None:
        if self.win.state.get("busy"):
            self.win.toast("A script is already running — wait for it to finish.")
            return
        dialog = Adw.AlertDialog.new(label, body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Run")
        dialog.set_response_appearance(
            "ok", Adw.ResponseAppearance.DESTRUCTIVE if danger
            else Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        def on_response(_d, response: str) -> None:
            if response != "ok":
                return
            argv = ["bash", paths.script(argv_tail[0]), *argv_tail[1:]]
            script_name = argv_tail[0] + (" " + " ".join(argv_tail[1:]) if argv_tail[1:] else "")
            self._run(argv, script_name)

        dialog.connect("response", on_response)
        dialog.present(self.win)

    def _run(self, argv: list[str], title: str) -> None:
        # --check needs no privileges; everything else does.
        if "--check" in argv:
            self._start(argv, title)
        else:
            self.win.ensure_password(lambda: self._start(argv, title))

    def _start(self, argv: list[str], title: str) -> None:
        self.panel.start(
            argv, title=title, password=self.win.state.get("password"),
            done_note=f"{title} finished.",
            on_finished=self._finished,
        )

    def _finished(self, _code: int) -> None:
        state = checks.installed_state()
        self.win.state["installed"] = state["installed"]
