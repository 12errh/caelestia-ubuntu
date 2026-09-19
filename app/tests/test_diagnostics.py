"""Unit tests for the About page's local diagnostic summary."""

import unittest
from unittest.mock import patch
from urllib.parse import unquote, quote

from caelestia_installer import diagnostics


STATE = {
    "installed": True, "manifest_present": True,
    "manifest": {"QT_VERSION": "6.9.2"}, "qs": True, "qt_version": "6.9.2",
    "shell_dir": True, "shell_json": True, "session_registered": True,
    "wallpaper": "",
}


class DiagnosticsTests(unittest.TestCase):
    def test_collect_lists_versions_service_and_pins(self):
        with patch("caelestia_installer.checks.installed_state",
                   return_value=dict(STATE)), \
                patch("caelestia_installer.checks.service_active",
                      return_value=False), \
                patch("caelestia_installer.pins.current",
                      return_value={"quickshell": "a" * 40}):
            text = diagnostics.collect()
        self.assertIn("Caelestia for Ubuntu installer: ", text)
        self.assertIn("Ubuntu: ", text)
        self.assertIn("Installed: yes", text)
        self.assertIn("Qt: 6.9.2", text)
        self.assertIn("Shell service: stopped", text)
        self.assertIn("Shell config (shell.json): present", text)
        self.assertIn("Pinned revisions:", text)
        self.assertIn("  quickshell: " + "a" * 40, text)
        self.assertIn("  cava: missing", text)
        self.assertIn("caelestia CLI: ", text)

    def test_collect_without_pins_or_service(self):
        with patch("caelestia_installer.checks.installed_state",
                   return_value={"installed": False, "manifest": {},
                                 "qs": False, "qt_version": "",
                                 "shell_dir": False, "shell_json": False,
                                 "session_registered": False,
                                 "wallpaper": None}), \
                patch("caelestia_installer.checks.service_active",
                      return_value=None), \
                patch("caelestia_installer.pins.current", return_value={}):
            text = diagnostics.collect()
        self.assertIn("Installed: no", text)
        self.assertIn("Shell service: unknown (not in session)", text)
        self.assertNotIn("Pinned revisions:", text)

    def test_issue_url_is_prefilled_and_escaped(self):
        url = diagnostics.issue_url("line one\nsecret")
        self.assertTrue(url.startswith(diagnostics.REPO_ISSUES_URL + "?"))
        self.assertIn("title=" + quote("Issue report"), url)
        self.assertIn("&body=", url)
        body = unquote(url.split("&body=", 1)[1])
        self.assertIn("line one\nsecret", body)
        self.assertIn("remove anything", body)
        # A newline must never appear raw in a URL query string.
        self.assertNotIn("\n", url)


if __name__ == "__main__":
    unittest.main()
