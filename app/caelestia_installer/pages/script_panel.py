"""Reusable live-log panel for running repository scripts.

Encapsulates the progress bar, stage label, cancel button, log view and the
PTY :class:`~caelestia_installer.runner.ScriptRunner` lifecycle so any page
(Install, Setup, Updates, Advanced) can run a script with the same UX.

The panel owns the ``win.state["busy"]`` flag: a second script cannot start
while one is running. ``on_finished`` is invoked on the GTK main thread with
the exit code once the script exits.
"""

from __future__ import annotations

import time
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from ..runner import ScriptRunner  # noqa: E402

# Stage headers setup.sh prints as "==> stage: …", in install order. update.sh
# and uninstall.sh do not print stage headers, so every step just pulses.
SETUP_STAGES = [
    "apt packages", "Qt", "wayland", "libcava", "quickshell",
    "m3shapes", "caelestia shell", "caelestia CLI", "fonts",
    "deploying theme", "post-install verification",
]


class ScriptPanel(Adw.Bin):
    """A self-contained progress + log widget.

    ``start()`` returns ``False`` when another script is already running (the
    caller should do nothing in that case). When the script ends the panel
    stays visible with the final log line, and a Close button lets the user
    dismiss it.
    """

    def __init__(self, win, *, stages: list[str] | None = None) -> None:
        super().__init__()
        self.win = win
        self.runner: ScriptRunner | None = None
        self._stages = list(stages or SETUP_STAGES)
        self._stage_idx = 0
        self._t0 = 0.0
        self._title = ""
        self._on_finished: Callable[[int], None] | None = None

        box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)

        head = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        self.title_label = Gtk.Label.new("")
        self.title_label.set_xalign(0.0)
        self.title_label.set_hexpand(True)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.set_markup("<b>Output</b>")
        head.append(self.title_label)
        self.btn_close = Gtk.Button.new_from_icon_name("window-close-symbolic")
        self.btn_close.set_css_classes(["flat"])
        self.btn_close.set_tooltip_text("Hide this panel")
        self.btn_close.connect("clicked", self._on_close)
        head.append(self.btn_close)
        box.append(head)

        self.progress = Gtk.ProgressBar.new()
        self.progress.set_pulse_step(0.03)
        box.append(self.progress)

        self.stage_label = Gtk.Label.new("waiting")
        self.stage_label.set_xalign(0.0)
        self.stage_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.stage_label.set_css_classes(["dim-label"])
        box.append(self.stage_label)

        self.log_view = Gtk.TextView.new()
        self.log_view.set_editable(False)
        self.log_view.set_cursor_visible(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        buf = self.log_view.get_buffer()
        buf.create_tag("dim", foreground="#8a8a8a")
        buf.create_tag("ok", foreground="#26a269")
        buf.create_tag("err", foreground="#c01c28")
        log_scroll = Gtk.ScrolledWindow.new()
        log_scroll.set_child(self.log_view)
        log_scroll.set_vexpand(True)
        log_scroll.set_min_content_height(220)
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        box.append(log_scroll)

        self.btn_cancel = Gtk.Button.new_with_label("Cancel")
        self.btn_cancel.set_css_classes(["destructive-action"])
        self.btn_cancel.set_halign(Gtk.Align.END)
        self.btn_cancel.connect("clicked", self._on_cancel)
        box.append(self.btn_cancel)

        self.set_child(box)
        self.set_visible(False)

    # ------------------------------------------------------------------ run
    def start(
        self,
        argv: list[str],
        *,
        title: str,
        password: str | None = None,
        stages: list[str] | None = None,
        done_note: str = "",
        on_finished: Callable[[int], None] | None = None,
    ) -> bool:
        """Start ``argv``. Returns False if a script is already running."""
        if self.win.state.get("busy"):
            self.win.toast("A script is already running — wait for it to finish.")
            return False

        self.win.state["busy"] = True
        self._title = title
        self._done_note = done_note
        self._on_finished = on_finished
        if stages:
            self._stages = list(stages)
        self._stage_idx = 0
        self._t0 = time.time()

        self.title_label.set_markup(f"<b>{title}</b>")
        self.log_view.get_buffer().set_text("")
        self.progress.set_fraction(0.0)
        self.progress.set_pulse_step(0.03)
        self.stage_label.set_text(f"starting {title}…")
        self.btn_cancel.set_sensitive(True)
        self.btn_close.set_sensitive(False)
        self.set_visible(True)

        self.runner = ScriptRunner(
            argv,
            on_output=self._on_output,
            on_end=self._on_end,
            password=password or None,
        )
        try:
            self.runner.start()
        except OSError as exc:
            self.win.state["busy"] = False
            self.btn_cancel.set_sensitive(False)
            self.btn_close.set_sensitive(True)
            self.stage_label.set_text(f"could not start: {exc}")
            self.win.toast(f"Could not start {title}: {exc}")
            return False
        return True

    def is_running(self) -> bool:
        return bool(self.runner and self.runner.is_running())

    def _on_cancel(self, _b: Gtk.Button) -> None:
        if self.runner and self.runner.is_running():
            self.stage_label.set_text("cancelling…")
            self.btn_cancel.set_sensitive(False)
            self.runner.cancel()

    def _on_close(self, _b: Gtk.Button) -> None:
        if not self.is_running():
            self.set_visible(False)

    # ------------------------------------------------------------- plumbing
    def _on_output(self, text: str) -> None:
        GLib.idle_add(self._apply_output, text)

    def _apply_output(self, text: str) -> bool:
        buf = self.log_view.get_buffer()
        end = buf.get_end_iter()
        tag = "dim" if text.lstrip().startswith("==>") else None
        if tag:
            buf.insert_with_tags_by_name(end, text, tag)
        else:
            buf.insert(end, text)
        self.log_view.scroll_mark_onscreen(buf.get_insert())
        self._update_stage(text)
        return False

    def _update_stage(self, text: str) -> None:
        for line in text.splitlines():
            s = line.strip()
            if not s.startswith("==>"):
                continue
            body = s.lstrip("=> ").strip()
            low = body.lower()
            for i, stage in enumerate(self._stages):
                if stage.lower() in low:
                    self._stage_idx = max(self._stage_idx, i)
                    break
            self.stage_label.set_text(body)
            if self._stages:
                frac = min(0.97, (self._stage_idx + 1) / (len(self._stages) + 1))
                self.progress.set_fraction(frac)
            self.progress.set_pulse_step(0.0)
            return

    def _on_end(self, code: int) -> None:
        GLib.idle_add(self._apply_end, code)

    def _apply_end(self, code: int) -> bool:
        self.win.state["busy"] = False
        self.btn_cancel.set_sensitive(False)
        self.btn_close.set_sensitive(True)
        elapsed = int(time.time() - self._t0)
        mins, secs = divmod(elapsed, 60)

        buf = self.log_view.get_buffer()
        end = buf.get_end_iter()
        if code == 0:
            self.stage_label.set_text(f"finished in {mins}m {secs:02d}s")
            self.progress.set_fraction(1.0)
            buf.insert_with_tags_by_name(end, f"\n[done] {self._title} finished successfully.\n", "ok")
            if self._done_note:
                self.win.toast(self._done_note)
        else:
            self.stage_label.set_text(f"failed (exit code {code})")
            buf.insert_with_tags_by_name(
                end,
                f"\n[failed] {self._title} exited with code {code} after "
                f"{mins}m {secs:02d}s. Check the log above.\n",
                "err",
            )
            self.win.toast(f"{self._title} failed (exit code {code})")
        self.log_view.scroll_mark_onscreen(buf.get_insert())

        cb = self._on_finished
        self._on_finished = None
        if cb:
            try:
                cb(code)
            except Exception:  # noqa: BLE001 - a UI callback must never crash
                pass
        return False
