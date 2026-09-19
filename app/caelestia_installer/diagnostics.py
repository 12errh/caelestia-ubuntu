"""Local, user-visible diagnostic summary for the About page's issue reporter.

Everything here is read-only and local: no network, no sudo, and the sudo
password is never touched. The collected text is exactly what the user sees
and copies into a GitHub issue — nothing is sent anywhere by the app itself.
"""

from __future__ import annotations

import platform
import shutil

from . import VERSION, checks, pins

REPO_ISSUES_URL = "https://github.com/12errh/caelestia-ubuntu/issues/new"


def _service_text() -> str:
    state = {True: "running", False: "stopped", None: "unknown (not in session)"}
    return state[checks.service_active()]


def collect() -> str:
    """Build the plain-text report the user reviews before filing an issue."""
    state = checks.installed_state()
    manifest = state.get("manifest") or {}
    lines = [
        f"Caelestia for Ubuntu installer: {VERSION}",
        f"Ubuntu: {platform.freedesktop_os_release().get('PRETTY_NAME', 'unknown')}",
        f"Desktop session: {_session_type()}",
        f"Installed: {'yes' if state['installed'] else 'no'}",
        f"Qt: {manifest.get('QT_VERSION', 'not recorded')}",
        f"Quickshell binary: {'present' if state['qs'] else 'missing'}",
        f"Shell service: {_service_text()}",
        f"Hyprland session registered: {'yes' if state['session_registered'] else 'no'}",
        f"Shell config (shell.json): {'present' if state['shell_json'] else 'missing'}",
    ]
    pins_now = pins.current()
    if pins_now:
        lines.append("Pinned revisions:")
        lines.extend(
            f"  {name}: {pins_now.get(name, 'missing')}"
            for name in pins.COMPONENTS
        )
    lines.append(f"caelestia CLI: {'present' if shutil.which('caelestia') else 'missing'}")
    return "\n".join(lines)


def _session_type() -> str:
    import os
    return os.environ.get("XDG_SESSION_TYPE") or "unknown"


def issue_url(summary: str) -> str:
    """Pre-filled GitHub issue URL; the user reviews and submits it in a browser."""
    from urllib.parse import quote
    body = (
        "**Describe the problem**\n\n\n\n"
        "**Steps to reproduce**\n\n1. \n\n"
        "**Diagnostic summary (added by the installer app — remove anything "
        "you would rather not share)**\n\n```\n" + summary + "\n```\n"
    )
    return f"{REPO_ISSUES_URL}?title={quote('Issue report')}&" \
           f"body={quote(body)}"
