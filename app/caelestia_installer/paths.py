"""Locate the caelestia-ubuntu repository scripts and well-known install paths.

The app must work in two layouts:
  * running straight from a clone:  <repo>/app/caelestia_installer/…
  * installed system-wide:          <prefix>/share/caelestia-installer/app/…
                                    with the repo copied next to it in
                                    <prefix>/share/caelestia-installer/repo/
CAEL_REPO_DIR overrides both (used by tests and unusual setups).
"""

from __future__ import annotations

import os
from pathlib import Path

APP_ID = "io.github.CaelestiaUbuntu.Installer"
APP_NAME = "Caelestia for Ubuntu"
VERSION = "1.2.1"

# Brand mark shown in the middle of the dock. This is a trimmed, 96px copy of
# assets/logo.png kept *inside the package* so it ships with the app: install.sh
# copies caelestia_installer/ (plus the scripts/configs) but not assets/, so
# reading the repository original would leave an installed app without a logo.
# Only the source file's alpha is used - the dock tints it like a symbolic icon.
BRAND_LOGO = Path(__file__).resolve().parent / "data" / "logo.png"

_INSTALLED_REPO_CANDIDATES = (
    Path("/usr/local/share/caelestia-installer/repo"),
    Path("/usr/share/caelestia-installer/repo"),
    Path.home() / ".local/share/caelestia-installer/repo",
)


def _looks_like_repo(p: Path) -> bool:
    return (p / "setup.sh").is_file() and (p / "configs").is_dir()


def _find_upwards(start: Path) -> Path | None:
    for p in (start, *start.parents):
        if _looks_like_repo(p):
            return p
    return None


def _repo_from_manifest() -> Path | None:
    """The repo whose revisions.conf the installer/updater actually used.

    setup.sh records ``PIN_FILE=`` in the manifest. Preferring its repo keeps
    the app consistent no matter where it is launched from (a clone vs the
    installed copy), so the Updates tab can never offer to "update" back to an
    older pin held by an unrelated clone.
    """
    try:
        for line in MANIFEST.read_text().splitlines():
            if line.startswith("PIN_FILE="):
                pin = Path(line.split("=", 1)[1].strip()).expanduser()
                if _looks_like_repo(pin.parent):
                    return pin.parent
    except OSError:
        pass
    return None


def repo_root() -> Path:
    """Directory containing setup.sh / update.sh / uninstall.sh / configs/."""
    env = os.environ.get("CAEL_REPO_DIR", "")
    if env:
        p = Path(env).expanduser().resolve()
        if _looks_like_repo(p):
            return p
    manifest_repo = _repo_from_manifest()
    if manifest_repo is not None:
        return manifest_repo
    here = Path(__file__).resolve()
    found = _find_upwards(here.parent) or _find_upwards(here.parent.parent)
    if found:
        return found
    for cand in _INSTALLED_REPO_CANDIDATES:
        if _looks_like_repo(cand):
            return cand
    raise FileNotFoundError(
        "Cannot locate the caelestia-ubuntu repository (setup.sh + configs/). "
        "Run the app from a clone of the repo, or install it with app/install.sh, "
        "or set CAEL_REPO_DIR=/path/to/caelestia-ubuntu"
    )


def script(name: str) -> str:
    """Absolute path of a repository script (setup.sh / update.sh / uninstall.sh)."""
    return str(repo_root() / name)


# Well-known paths created by setup.sh --------------------------------------
MANIFEST = Path.home() / ".local/share/caelestia-ubuntu/manifest"
QS_BIN = Path("/usr/local/bin/qs")
SHELL_DIR = Path.home() / ".config/quickshell/caelestia"
SHELL_JSON = Path.home() / ".config/caelestia/shell.json"
HYPR_DIR = Path.home() / ".config/hypr"
HYPRLAND_CONF = HYPR_DIR / "hyprland.conf"  # where the shell's keybinds live
HYPR_SESSION = Path("/usr/share/wayland-sessions/hyprland.desktop")
SHELL_SERVICE = "caelestia-shell.service"
WALLPAPER_STATE = Path.home() / ".local/state/caelestia/wallpaper"
WALLPAPER_DIRS = (
    Path.home() / "Pictures/wallpapers",
    Path.home() / "Pictures/Wallpapers",
)
