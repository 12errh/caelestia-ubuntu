"""App release checks never install packages or use untrusted download URLs."""

import json
import time
from urllib.error import HTTPError
from unittest.mock import Mock, patch

import pytest

from caelestia_installer import paths, releases
from caelestia_installer.pages.updates import UpdatesPage
from gi.repository import Adw, GLib


def payload(version="1.2.3"):
    return {"tag_name": f"v{version}", "draft": False, "prerelease": False,
            "html_url": "https://untrusted.invalid/",
            "assets": [{"name": f"caelestia-installer_{version}_all.deb",
                        "state": "uploaded", "size": 1024}]}


def test_versions_and_trusted_destination():
    release = releases.parse_release(payload("1.10.0"))
    assert release.newer_than("1.9.9")
    assert not release.newer_than("1.10.0")
    assert not release.newer_than("2.0.0")
    assert release.url == releases.REPOSITORY + "/releases/tag/v1.10.0"


@pytest.mark.parametrize("version", ["1.2", "01.2.3", "1.2.3-rc1", "../1.2.3", None])
def test_invalid_versions(version):
    with pytest.raises(ValueError):
        releases.version_tuple(version)


@pytest.mark.parametrize("change", [
    {"draft": True}, {"prerelease": True}, {"tag_name": "1.2.3"},
    {"assets": []}, {"assets": [{}]}, {"assets": "not a list"},
])
def test_unpublished_or_incomplete_release(change):
    data = payload()
    data.update(change)
    with pytest.raises(ValueError):
        releases.parse_release(data)


def test_fetch_bounded_errors_and_no_release():
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps(payload()).encode()
    with patch.object(releases, "urlopen", return_value=response) as fetch:
        assert releases.fetch_latest().version == "1.2.3"
    assert fetch.call_args.kwargs["timeout"] == 15
    response.read.assert_called_once_with(releases.MAX_SIZE + 1)
    response.read.return_value = b"x" * (releases.MAX_SIZE + 1)
    with patch.object(releases, "urlopen", return_value=response):
        with pytest.raises(ValueError, match="too large"):
            releases.fetch_latest()
    for code in (404, 403):
        error = HTTPError(releases.API_URL, code, "test", {}, None)
        with patch.object(releases, "urlopen", side_effect=error):
            if code == 404:
                assert releases.fetch_latest() is None
            else:
                with pytest.raises(HTTPError):
                    releases.fetch_latest()


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("App check did not finish")


def test_async_check_button_busy_and_offline():
    Adw.init()
    win = Mock(state={"busy": False})
    page = UpdatesPage(win)
    with patch.object(releases, "fetch_latest", return_value=releases.Release("99.0.0")):
        page._check_app_async()
        wait_for(lambda: not page._app_checking)
    assert page.btn_app_update.get_sensitive()
    assert "99.0.0" in page.app_row.get_subtitle()
    win.state["busy"] = True
    page._refresh_apply_button()
    assert not page.btn_app_update.get_sensitive()
    win.state["busy"] = False
    with patch.object(releases, "fetch_latest", side_effect=OSError("offline")):
        page._check_app_async()
        wait_for(lambda: not page._app_checking)
    assert "offline" in page.app_row.get_subtitle()
    assert page._app_release is None
    assert not page.btn_app_update.get_sensitive()
    with patch.object(releases, "fetch_latest", return_value=releases.Release(paths.VERSION)):
        page._check_app_async()
        wait_for(lambda: not page._app_checking)
    assert "No newer" in page.app_row.get_subtitle()
    assert not page.btn_app_update.get_sensitive()


def test_release_link_requires_confirmation():
    Adw.init()
    page = UpdatesPage(Mock(state={"busy": False}))
    page._app_result(releases.Release("99.0.0"), "")
    dialog = Mock()
    with patch("caelestia_installer.pages.updates.Adw.AlertDialog.new",
               return_value=dialog), \
            patch("caelestia_installer.pages.updates.Gtk.UriLauncher.new") as launcher:
        page.btn_app_update.emit("clicked")
        callback = dialog.connect.call_args.args[1]
        launcher.assert_not_called()
        callback(dialog, "cancel")
        launcher.assert_not_called()
        callback(dialog, "open")
        launcher.assert_called_once_with(releases.Release("99.0.0").url)
        launcher.return_value.launch.assert_called_once()


def test_recheck_checks_app_even_without_desktop():
    Adw.init()
    page = UpdatesPage(Mock(state={"busy": False}))
    with patch.object(page, "_check_app_async") as check, \
            patch.object(page, "_check_published_async"), \
            patch("caelestia_installer.pages.updates.checks.installed_state",
                  return_value={"installed": False}):
        page.refresh_async(force=True)
    check.assert_called_once()
