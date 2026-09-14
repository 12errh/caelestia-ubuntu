"""Revision-pin snapshots for the Updates tab's advanced workflow.

The repository's ``revisions.conf`` is what ``setup.sh``/``update.sh`` build.
For the "jump to latest upstream → test → keep or revert" workflow we keep two
user-side snapshots under ``~/.local/share/caelestia-ubuntu/pins``:

  * ``known-good.conf`` — the last revision set the user marked as tested
  * ``previous.conf``   — the revision set from just before the latest change

They live outside the repo, so marking/reverting never fights git and never
touches user configuration. A snapshot of ``~/.config/caelestia/shell.json`` is
also taken before an upstream update: ``update.sh`` does not deploy configs, but
the extra copy makes recovery trivial should anything unexpected happen.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from . import paths

PINS_DIR = Path.home() / ".local/share/caelestia-ubuntu/pins"
KNOWN_GOOD = PINS_DIR / "known-good.conf"
PREVIOUS = PINS_DIR / "previous.conf"

COMPONENTS = ("quickshell", "caelestia", "m3shapes", "cava", "caelestia_cli")


def revisions_path() -> Path:
    """Path of the repository's live revisions.conf (may raise FileNotFoundError)."""
    return paths.repo_root() / "revisions.conf"


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        out[key.strip()] = value.strip()
    return out


def _read(path: Path) -> dict[str, str]:
    try:
        return parse(path.read_text())
    except OSError:
        return {}


def current() -> dict[str, str]:
    try:
        return _read(revisions_path())
    except FileNotFoundError:
        return {}


def known_good() -> dict[str, str]:
    return _read(KNOWN_GOOD)


def previous() -> dict[str, str]:
    return _read(PREVIOUS)


def seed_if_needed() -> None:
    """First run: treat the repository's current pins as the known-good set."""
    if KNOWN_GOOD.exists():
        return
    try:
        src = revisions_path()
    except FileNotFoundError:
        return
    if not src.is_file():
        return
    PINS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, KNOWN_GOOD)
    if not PREVIOUS.exists():
        shutil.copy2(src, PREVIOUS)


def snapshot_previous() -> bool:
    """Save the live revisions.conf as the one-step undo target."""
    try:
        src = revisions_path()
    except FileNotFoundError:
        return False
    if not src.is_file():
        return False
    PINS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, PREVIOUS)
    return True


def mark_tested() -> bool:
    """Record the live revisions.conf as the new known-good set."""
    try:
        src = revisions_path()
    except FileNotFoundError:
        return False
    if not src.is_file():
        return False
    PINS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, KNOWN_GOOD)
    return True


def _src_root() -> Path:
    """Build cache root recorded by setup.sh; falls back to the default."""
    manifest = Path.home() / ".local/share/caelestia-ubuntu/manifest"
    try:
        for line in manifest.read_text().splitlines():
            if line.startswith("SRC_ROOT="):
                return Path(line.split("=", 1)[1].strip())
    except OSError:
        pass
    return Path.home() / ".cache/caelestia-ubuntu-build"


def _git_head(path: Path) -> str:
    if not (path / ".git").exists():
        return ""
    try:
        r = subprocess.run(  # noqa: S603
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def installed_revisions() -> dict[str, str]:
    """The full commit SHAs actually built/checked out right now.

    setup.sh/update.sh leave each source checkout on the revision they built,
    so the git HEAD is the installed revision. The CLI has no checkout, so its
    existing pin is kept.
    """
    src = _src_root()
    result = dict(current())
    for key, path in (
        ("quickshell", src / "quickshell"),
        ("caelestia", paths.SHELL_DIR),
        ("m3shapes", src / "m3shapes"),
        ("cava", src / "cava"),
    ):
        full = _git_head(path)
        if full:
            result[key] = full
    return result


def write_pins(mapping: dict[str, str]) -> bool:
    """Update the key=value lines in the repo's revisions.conf in place."""
    try:
        dest = revisions_path()
    except FileNotFoundError:
        return False
    if not dest.is_file():
        return False
    try:
        lines = dest.read_text().splitlines()
    except OSError:
        return False
    remaining = dict(mapping)
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    try:
        dest.write_text("\n".join(out) + "\n")
    except OSError:
        return False
    return True


def mark_tested_installed() -> bool:
    """Make the currently installed revisions the new known-good pins.

    Writes them into the repository's revisions.conf *and* the known-good
    snapshot, so the Updates check reports "up to date" after testing.
    """
    if not repo_writable():
        return False
    mapping = installed_revisions()
    if not mapping:
        return False
    if not write_pins(mapping):
        return False
    try:
        PINS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(revisions_path(), KNOWN_GOOD)
    except (OSError, FileNotFoundError):
        return False
    return True


def restore_previous() -> bool:
    """Write the previous snapshot back into the repository's revisions.conf."""
    if not PREVIOUS.is_file():
        return False
    try:
        dest = revisions_path()
    except FileNotFoundError:
        return False
    try:
        shutil.copy2(PREVIOUS, dest)
    except OSError:
        return False
    return True


def repo_writable() -> bool:
    try:
        path = revisions_path()
    except FileNotFoundError:
        return False
    return path.is_file() and os.access(path, os.W_OK)


def backup_shell_json() -> Path | None:
    """Snapshot the user's shell settings before an upstream jump."""
    src = paths.SHELL_JSON
    if not src.is_file():
        return None
    PINS_DIR.mkdir(parents=True, exist_ok=True)
    dest = PINS_DIR / f"shell.json.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        shutil.copy2(src, dest)
    except OSError:
        return None
    return dest


def short(mapping: dict[str, str]) -> str:
    """A compact ``key=sha12`` line for display."""
    if not mapping:
        return "—"
    return "  ".join(f"{k}={mapping[k][:12]}" for k in COMPONENTS if k in mapping)
