"""Explicit, confirmed maintenance actions used only by AdvancedPage."""

from __future__ import annotations

import os
import shlex

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import checks, paths, pins  # noqa: E402


class AdvancedActions:
    def _on_update_upstream(self, _b: Gtk.Button) -> None:
        if self.win.state.get("busy"):
            self.win.toast("A script is already running — wait for it to finish.")
            return
        dialog = Adw.AlertDialog.new(
            "Update to the latest upstream commits?",
            "This fetches the newest upstream commit of every component, "
            "rewrites revisions.conf, and rebuilds them. It deliberately jumps "
            "ahead of the known-good pins, so it may break — you can Revert "
            "afterwards. Your configs and shell.json are not touched (a copy is "
            "saved first). Rebuilding can take a while.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Update & build")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response: str) -> None:
            if response != "ok":
                return
            self.win.ensure_password(self._start_update_upstream, force=True)

        dialog.connect("response", on_response)
        dialog.present(self.win)

    def _start_update_upstream(self) -> None:
        if self.win.state.get("busy"):
            self.win.toast("A script is already running — wait for it to finish.")
            return
        try:
            argv = ["bash", paths.script("update.sh"), "--update-sources", "--yes"]
            # Capture the exact file and path for this run, not whichever repo
            # a changed manifest might resolve to after a partial update.
            revision_file = pins.revisions_path()
            original = revision_file.read_bytes()
            if not pins.snapshot_previous():
                self.win.toast("Cannot save previous revisions; build was not started.")
                return
            backup = pins.backup_shell_json()
        except OSError as exc:
            self.win.toast(f"Cannot prepare upstream build: {exc}")
            return
        self._upstream_snapshot = (revision_file, original)
        if backup is not None:
            self.win.toast(f"Saved settings backup: {backup.name}")
        started = self.panel.start(
            argv, title="update.sh --update-sources",
            password=self.win.state.get("password"),
            done_note="Latest upstream built — test in Hyprland, then Keep or Revert.",
            on_finished=self._upstream_finished,
        )
        if started:
            self._refresh_advanced()
        else:
            self._upstream_snapshot = None

    def _upstream_finished(self, code: int) -> None:
        snapshot = getattr(self, "_upstream_snapshot", None)
        self._upstream_snapshot = None
        if self.panel.cancel_requested:
            try:
                if snapshot is None:
                    raise OSError("the pre-build snapshot is unavailable")
                pins.restore_snapshot(*snapshot)
            except OSError as exc:
                message = (
                    f"Build cancelled, but revisions could not be restored: {exc}. "
                    f"The saved snapshot is at {pins.PREVIOUS}.")
            else:
                message = (
                    "Build cancelled. Pre-build revisions restored. Already installed "
                    "components are not rolled back; use Revert to rebuild the saved pins.")
            self.panel._apply_output(f"\n[recovery] {message}\n")
            self.win.toast(message)
        elif code == 0:
            self.win.toast("Latest upstream built. Test it, then Keep or Revert.")
        else:
            self.win.toast("Upstream update failed — use Revert to go back.")
        self._refresh_advanced()
        self._refresh_updates()

    # ---------------------------------------------------------- keep / revert
    def _on_keep_tested(self, _b: Gtk.Button) -> None:
        if self.win.state.get("busy"):
            self.win.toast("A script is already running — wait for it to finish.")
            return
        if pins.mark_tested_installed():
            self.win.toast("Tested revisions recorded as the known-good pins.")
        else:
            self.win.toast("Could not record the pins (is revisions.conf writable?).")
        self._refresh_advanced()
        self._refresh_updates()

    def _on_revert(self, _b: Gtk.Button) -> None:
        if self.win.state.get("busy"):
            self.win.toast("A script is already running — wait for it to finish.")
            return
        dialog = Adw.AlertDialog.new(
            "Revert to the previous pins?",
            "Restores the revisions.conf snapshot taken before the last change "
            "and rebuilds those components. Your configs and shell.json are not "
            "touched.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Revert & rebuild")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d, response: str) -> None:
            if response != "ok":
                return
            self.win.ensure_password(self._start_revert, force=True)

        dialog.connect("response", on_response)
        dialog.present(self.win)

    def _on_repair_runtime(self, _b: Gtk.Button) -> None:
        if self.win.state.get("busy"):
            self.win.toast("A script is already running — wait for it to finish.")
            return
        self.win.ensure_password(self._start_repair_runtime, force=True)

    def _start_repair_runtime(self) -> None:
        manifest = checks.read_manifest()
        qt = manifest.get("QT_PREFIX") or "/opt/qt611/6.11.2/gcc_64"
        rpath = f"{qt}/lib:$ORIGIN:$ORIGIN/../lib"
        qs = os.path.realpath(str(paths.QS_BIN))
        tmp = os.path.join(os.path.dirname(qs), ".quickshell.rpath-new")
        # The shell runs qs, so the file is "Text file busy" for in-place edits.
        # Stage a patched copy and atomically rename it over the target instead;
        # the running shell keeps its old inode until the service restarts.
        script = (
            "set -e\n"
            f"QS={shlex.quote(qs)}\n"
            f"TMP={shlex.quote(tmp)}\n"
            f"RPATH={shlex.quote(rpath)}\n"
            'echo "==> staging a patched copy of quickshell"\n'
            'sudo cp -f "$QS" "$TMP"\n'
            'sudo patchelf --force-rpath --set-rpath "$RPATH" "$TMP"\n'
            'sudo chmod 755 "$TMP"\n'
            'sudo mv -f "$TMP" "$QS"\n'
            'echo "==> rpath now: $(patchelf --print-rpath "$QS")"\n'
            'env -u LD_LIBRARY_PATH "$QS" --version\n'
            'echo "==> restarting caelestia-shell"\n'
            'systemctl --user try-restart caelestia-shell.service 2>/dev/null || true\n'
        )
        self.panel.start(
            ["bash", "-c", script], title="repair quickshell runtime",
            password=self.win.state.get("password"),
            done_note="Quickshell runtime repaired.",
            on_finished=self._repair_finished,
        )

    def _repair_finished(self, code: int) -> None:
        self._repair_result = code
        self._refresh_advanced()

    def _start_revert(self) -> None:
        if not pins.restore_previous():
            self.win.toast("Nothing to revert to, or revisions.conf is not writable.")
            self._refresh_advanced()
            return
        try:
            argv = ["bash", paths.script("update.sh"), "--yes"]
        except FileNotFoundError as exc:
            self.win.toast(f"Cannot find update.sh: {exc}")
            return
        self.panel.start(
            argv, title="update.sh (revert)", password=self.win.state.get("password"),
            done_note="Reverted to the previous pins.",
            on_finished=self._revert_finished,
        )

    def _revert_finished(self, code: int) -> None:
        self._refresh_advanced()
        self._refresh_updates()
        self.win.toast("Reverted and rebuilt." if code == 0
                       else "Revert rebuild failed — see the log.")
