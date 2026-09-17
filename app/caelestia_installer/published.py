"""Read maintainer-tested pins over HTTPS; never execute downloaded content."""

from __future__ import annotations

import re
from urllib.request import Request, urlopen

from . import pins

PUBLISHED_URL = (
    "https://raw.githubusercontent.com/12errh/caelestia-ubuntu/main/revisions.conf"
)
MAX_SIZE = 32768


def validate(text: str) -> dict[str, str]:
    """Require exactly the supported components and full Git commit hashes."""
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (not separator or key not in pins.COMPONENTS or key in result
                or re.fullmatch(r"[0-9a-f]{40}", value) is None):
            raise ValueError("Published revisions have an invalid entry")
        result[key] = value
    if set(result) != set(pins.COMPONENTS):
        raise ValueError("Published revisions are missing required components")
    return result


def fetch() -> dict[str, str]:
    request = Request(PUBLISHED_URL, headers={
        "User-Agent": "CaelestiaUbuntu-Installer", "Cache-Control": "no-cache",
    })
    with urlopen(request, timeout=15) as response:
        content = response.read(MAX_SIZE + 1)
    if len(content) > MAX_SIZE:
        raise ValueError("Published revision file is too large")
    return validate(content.decode("utf-8"))


def adopt(mapping: dict[str, str], destination, expected: bytes) -> None:
    """Explicit adoption, with a one-step undo snapshot and stale-check guard."""
    text = "# Maintainer-tested revisions from " + PUBLISHED_URL + "\n"
    text += "".join(f"{key}={mapping[key]}\n" for key in pins.COMPONENTS)
    validate(text)
    if destination != pins.revisions_path() or destination.read_bytes() != expected:
        raise ValueError("Local revisions changed since the check; recheck before adopting")
    if not pins.snapshot_previous():
        raise OSError("Could not back up the current revision file")
    pins.restore_snapshot(destination, text.encode("utf-8"))
