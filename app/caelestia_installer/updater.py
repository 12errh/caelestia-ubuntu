"""Download, verify and install the app's own package update.

The app is distributed as a ``.deb`` on a GitHub release. This module fetches
that package, checks it against the release's own ``SHA256SUMS``, and hands back
the command that installs it — everything up to (but not including) the moment a
password is needed, so it can run off the GTK thread and be tested without a
display.

Trust model, matching :mod:`caelestia_installer.releases`
--------------------------------------------------------
Nothing the API returns is ever used as a destination. The package URL is
*constructed* from the validated version, exactly like the release page URL:

    https://github.com/12errh/caelestia-ubuntu/releases/download/v1.2.2/caelestia-installer_1.2.2_all.deb

The download is only ever handed on once it hashes to the digest published
beside it in ``SHA256SUMS``, and only within a size bound. A truncated,
substituted or half-uploaded asset therefore fails before anything is executed,
and the user is asked before any of it starts.

Why ``apt-get install`` and not ``dpkg -i``
-------------------------------------------
``dpkg -i`` leaves dependencies unresolved; the package depends on
``python3-gi``/``gir1.2-gtk-4.0``/``sudo``, which may not be on a machine that
installed the app some other way. ``apt-get install -y <file>`` resolves them,
which is also what the README tells users to type by hand.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile
from typing import Callable
from urllib.request import Request, urlopen

from . import releases

#: Upper bound on the package we are willing to download. The real thing is
#: ~160 kB; anything an order of magnitude larger is not this project's
#: package, and refusing it early keeps a hostile or broken endpoint from
#: filling the disk.
MAX_PACKAGE = 8 * 1024 * 1024

#: Published beside the package by the release workflow (.github/workflows/deb.yml).
CHECKSUMS_NAME = "SHA256SUMS"

#: The checksum listing is a couple of lines of text; anything larger is not it.
MAX_CHECKSUMS = 64 * 1024

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_READ_CHUNK = 64 * 1024


def asset_name(version: str) -> str:
    """The one asset name this project publishes for ``version``."""
    releases.version_tuple(version)          # rejects "../1.2.3", "01.2.3", …
    return f"caelestia-installer_{version}_all.deb"


def package_url(version: str) -> str:
    """The download URL, constructed rather than taken from the API."""
    return f"{releases.REPOSITORY}/releases/download/v{version}/{asset_name(version)}"


def checksums_url(version: str) -> str:
    releases.version_tuple(version)
    return f"{releases.REPOSITORY}/releases/download/v{version}/{CHECKSUMS_NAME}"


def parse_checksums(text: str, wanted: str) -> str:
    """Extract ``wanted``'s digest from ``SHA256SUMS`` text.

    The format is one ``<digest>  <name>`` line per file. The name must match
    exactly: no globs, no basename matching, so a line for some other file
    cannot stand in for the package's.
    """
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == wanted:
            digest = parts[0].lower()
            if _DIGEST_RE.fullmatch(digest):
                return digest
    raise ValueError(f"{CHECKSUMS_NAME} has no entry for {wanted}")


def file_digest(path: Path) -> str:
    """SHA-256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch(url: str, limit: int) -> bytes:
    """Read a small text resource, refusing anything over ``limit`` bytes."""
    request = Request(url, headers={
        "User-Agent": "CaelestiaUbuntu-Installer",
        "Cache-Control": "no-cache",
    })
    with urlopen(request, timeout=30) as response:
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError(f"{CHECKSUMS_NAME} is larger than expected")
    return content


def _download(url: str, destination: Path, limit: int,
              on_progress: Callable[[int], None] | None) -> None:
    request = Request(url, headers={
        "User-Agent": "CaelestiaUbuntu-Installer",
        "Accept": "application/octet-stream",
        "Cache-Control": "no-cache",
    })
    written = 0
    with urlopen(request, timeout=60) as response, open(destination, "wb") as handle:
        while True:
            chunk = response.read(_READ_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                raise ValueError("The update package is larger than expected")
            handle.write(chunk)
            if on_progress is not None:
                on_progress(written)
    if written == 0:
        raise ValueError("The update package is empty")


def download(version: str, *, directory: Path | None = None,
             on_progress: Callable[[int], None] | None = None) -> Path:
    """Fetch and verify the package for ``version``; return the local file.

    Raises ``ValueError`` if the published digest is missing, if the download is
    empty or oversized, or if the bytes do not hash to the published digest.
    The verified file lives in ``directory`` (a private temporary directory by
    default) and is left there for the caller to install.
    """
    name = asset_name(version)
    expected = parse_checksums(
        _fetch(checksums_url(version), MAX_CHECKSUMS).decode("utf-8", "replace"),
        name)

    target_dir = directory or Path(tempfile.mkdtemp(prefix="caelestia-installer-"))
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / name
    _download(package_url(version), destination, MAX_PACKAGE, on_progress)

    if file_digest(destination) != expected:
        destination.unlink(missing_ok=True)
        raise ValueError(
            "The downloaded package does not match the release checksum and was "
            "discarded. Nothing has been installed.")
    return destination


def install_command(package: Path) -> list[str]:
    """The argv that installs a verified package, resolving dependencies."""
    return ["sudo", "apt-get", "install", "-y", str(package)]


def installed_from_package() -> bool:
    """True when dpkg has this app installed as a package.

    Only then may the app install its own update: the .deb's ``postinst``
    deliberately removes a ``/usr/local`` or ``~/.local`` copy of the app
    (docs/INSTALL_APP.md, "Don't mix install methods"), so offering to install
    it while the user is running one of those would switch their install method
    underneath them. Those users get instructions instead.
    """
    try:
        import subprocess
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["dpkg-query", "-W", "-f=${db:Status-Status}", "caelestia-installer"],
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip() == "installed"


def remove(path: Path) -> None:
    """Delete a downloaded package (best effort)."""
    try:
        os.unlink(path)
    except OSError:
        pass