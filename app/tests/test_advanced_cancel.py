"""Exercise Advanced cancellation with a real PTY process, never the updater."""

import shlex
import time
from unittest.mock import Mock, patch

from caelestia_installer import pins
from caelestia_installer.pages.advanced import AdvancedPage
from gi.repository import Adw, GLib


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Build/cancel did not finish before timeout")


def test_build_cancel_restores_exact_prebuild_pins(tmp_path):
    Adw.init()
    revision_file = tmp_path / "revisions.conf"
    original = b"# preserve comments and formatting\r\nquickshell=before\r\n"
    revision_file.write_bytes(original)
    revision_file.chmod(0o640)
    updater = tmp_path / "update.sh"
    updater.write_text(
        f"printf 'quickshell=after\\n' > {shlex.quote(str(revision_file))}\n"
        "echo 'substitute updater ready'\n"
        "exec sleep 30\n"
    )
    win = Adw.Window()
    win.state = {"busy": False, "installed": True, "password": ""}
    win.toast = Mock()
    with patch.object(pins, "revisions_path", return_value=revision_file), \
            patch.object(pins, "PINS_DIR", tmp_path / "pins"), \
            patch.object(pins, "PREVIOUS", tmp_path / "pins" / "previous.conf"), \
            patch.object(pins, "backup_shell_json", return_value=None), \
            patch("caelestia_installer.pages.advanced_actions.paths.script",
                  return_value=str(updater)), \
            patch.object(AdvancedPage, "_refresh_advanced"), \
            patch.object(AdvancedPage, "_refresh_updates"):
        page = AdvancedPage(win)
        win.set_content(page)
        win.present()
        try:
            page._start_update_upstream()
            wait_for(lambda: revision_file.read_bytes() == b"quickshell=after\n")
            assert win.state["busy"]
            assert pins.PREVIOUS.read_bytes() == original
            page.panel.btn_cancel.emit("clicked")
            wait_for(lambda: not win.state["busy"])
            assert page.panel.cancel_requested
            assert page.panel.runner.proc.poll() is not None
            assert revision_file.read_bytes() == original
            assert revision_file.stat().st_mode & 0o777 == 0o640
            assert page._upstream_snapshot is None
            assert "cancelled" in page.panel.stage_label.get_text()
            buf = page.panel.log_view.get_buffer()
            log = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            assert "Pre-build revisions restored" in log
            assert "Already installed components are not rolled back" in log
        finally:
            if page.panel.is_running():
                page.panel._on_cancel(None)
                wait_for(lambda: not win.state["busy"])
            win.destroy()


def test_cancel_reports_restore_failure(tmp_path):
    from caelestia_installer.pages.advanced_actions import AdvancedActions

    page = Mock()
    page.panel.cancel_requested = True
    page._upstream_snapshot = (tmp_path / "revisions.conf", b"original")
    with patch.object(pins, "restore_snapshot", side_effect=OSError("read-only")):
        AdvancedActions._upstream_finished(page, 1)
    message = page.win.toast.call_args.args[0]
    assert "could not be restored" in message
    assert "read-only" in message
    assert str(pins.PREVIOUS) in message
    page.panel._apply_output.assert_called_once()
    page._refresh_advanced.assert_called_once()
    page._refresh_updates.assert_called_once()


def test_success_keeps_new_pins():
    from caelestia_installer.pages.advanced_actions import AdvancedActions

    page = Mock()
    page.panel.cancel_requested = False
    page._upstream_snapshot = (None, b"original")
    with patch.object(pins, "restore_snapshot") as restore:
        AdvancedActions._upstream_finished(page, 0)
    restore.assert_not_called()
    assert page._upstream_snapshot is None
    assert "Latest upstream built" in page.win.toast.call_args.args[0]
