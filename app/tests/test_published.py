"""Published revision checks are read-only until explicitly accepted."""

import time
from unittest.mock import Mock, patch

import pytest

from caelestia_installer import pins, published
from caelestia_installer.pages.updates import UpdatesPage
from gi.repository import Adw, GLib


def text(char="a"):
    return "".join(f"{key}={char * 40}\n" for key in pins.COMPONENTS)


@pytest.mark.parametrize("invalid", [
    "quickshell=abc\n", text() + "unknown=" + "a" * 40,
    text() + "quickshell=" + "a" * 40,
    text().replace("a" * 40, "$(echo test)", 1),
])
def test_reject_invalid_published_pins(invalid):
    with pytest.raises(ValueError):
        published.validate(invalid)


def test_fetch_is_bounded_and_validated():
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = text().encode()
    with patch.object(published, "urlopen", return_value=response) as fetch:
        assert published.fetch() == published.validate(text())
    assert fetch.call_args.kwargs["timeout"] == 15
    response.read.assert_called_once_with(published.MAX_SIZE + 1)
    response.read.return_value = b"x" * (published.MAX_SIZE + 1)
    with patch.object(published, "urlopen", return_value=response):
        with pytest.raises(ValueError, match="too large"):
            published.fetch()


def test_adopt_backups_and_stale_guard(tmp_path):
    dest = tmp_path / "revisions.conf"
    original = ("# original formatting\r\n" + text()).encode()
    dest.write_bytes(original)
    backup = tmp_path / "pins" / "previous.conf"
    with patch.object(pins, "revisions_path", return_value=dest), \
            patch.object(pins, "PINS_DIR", backup.parent), \
            patch.object(pins, "PREVIOUS", backup):
        published.adopt(published.validate(text("b")), dest, original)
        assert backup.read_bytes() == original
        assert pins.current() == published.validate(text("b"))
        with pytest.raises(ValueError, match="changed since"):
            published.adopt(published.validate(text("c")), dest, original)
        assert backup.read_bytes() == original


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("Published check did not finish")


def test_page_check_is_readonly_offline_and_matching(tmp_path):
    Adw.init()
    dest = tmp_path / "revisions.conf"
    dest.write_text(text())
    original = dest.read_bytes()
    page = UpdatesPage(Mock(state={"busy": False}))
    with patch.object(pins, "revisions_path", return_value=dest), \
            patch.object(published, "fetch", return_value=published.validate(text("b"))):
        page._check_published_async()
        wait_for(lambda: not page._published_checking)
    assert dest.read_bytes() == original
    assert page.btn_adopt.get_sensitive()
    assert "differ" in page.published_row.get_subtitle()
    with patch.object(pins, "revisions_path", return_value=dest), \
            patch.object(published, "fetch", side_effect=OSError("offline")):
        page._check_published_async()
        wait_for(lambda: not page._published_checking)
    assert not page.btn_adopt.get_sensitive()
    assert "offline" in page.published_row.get_subtitle()
    with patch.object(pins, "revisions_path", return_value=dest), \
            patch.object(published, "fetch", return_value=published.validate(text())):
        page._check_published_async()
        wait_for(lambda: not page._published_checking)
    assert not page.btn_adopt.get_sensitive()
    assert "match" in page.published_row.get_subtitle()
    assert dest.read_bytes() == original


def test_recheck_also_checks_published_when_not_installed():
    Adw.init()
    page = UpdatesPage(Mock(state={"busy": False}))
    with patch.object(page, "_check_published_async") as check, \
            patch.object(page, "_check_app_async"), \
            patch("caelestia_installer.pages.updates.checks.installed_state",
                  return_value={"installed": False}):
        page.refresh_async(force=True)
    check.assert_called_once()
