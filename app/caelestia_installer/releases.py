"""Stable GitHub app releases, separate from desktop revision pins."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPOSITORY = "https://github.com/12errh/caelestia-ubuntu"
API_URL = "https://api.github.com/repos/12errh/caelestia-ubuntu/releases/latest"
MAX_SIZE = 1024 * 1024


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value):
        raise ValueError("Expected a stable version in MAJOR.MINOR.PATCH format")
    return tuple(int(part) for part in value.split("."))


@dataclass(frozen=True)
class Release:
    version: str

    @property
    def url(self) -> str:
        # Construct the trusted destination, rather than opening API-supplied URLs.
        version_tuple(self.version)
        return f"{REPOSITORY}/releases/tag/v{self.version}"

    def newer_than(self, installed: str) -> bool:
        return version_tuple(self.version) > version_tuple(installed)


def parse_release(data) -> Release:
    if not isinstance(data, dict) or data.get("draft") is not False or data.get("prerelease") is not False:
        raise ValueError("Expected a published stable release")
    tag = data.get("tag_name", "")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ValueError("Release tag must start with v")
    version = tag[1:]
    version_tuple(version)
    assets = data.get("assets")
    if not isinstance(assets, list) or not any(
        isinstance(asset, dict)
        and asset.get("name") == f"caelestia-installer_{version}_all.deb"
        and asset.get("state") == "uploaded"
        and isinstance(asset.get("size"), int) and asset["size"] > 0
        for asset in assets
    ):
        raise ValueError("The release package is not available yet; try again later")
    return Release(version)


def fetch_latest() -> Release | None:
    request = Request(API_URL, headers={
        "User-Agent": "CaelestiaUbuntu-Installer",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Cache-Control": "no-cache",
    })
    try:
        with urlopen(request, timeout=15) as response:
            content = response.read(MAX_SIZE + 1)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if len(content) > MAX_SIZE:
        raise ValueError("Release response is too large")
    return parse_release(json.loads(content.decode("utf-8")))
