"""System checks and installed-state detection — no GTK imports here so the
same logic can run from a terminal (``caelestia-installer --check``) or CI."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import paths


@dataclass
class Check:
    id: str
    label: str
    detail: str = ""
    ok: bool = False
    severity: str = "error"  # "error" blocks the install, "warning" does not


def _os_release() -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                data[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return data


def _free_gb(path: str = "/") -> float:
    try:
        return shutil.disk_usage(path).free / 1e9
    except OSError:
        return 0.0


def _total_ram_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1024 / 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def _internet_ok() -> bool:
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53), ("github.com", 443)):
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            continue
    return False


def read_manifest() -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        for line in paths.MANIFEST.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                data[k.strip()] = v.strip()
    except OSError:
        pass
    return data


def collect() -> list[Check]:
    """Run every pre-flight check and return them in display order."""
    osr = _os_release()
    checks: list[Check] = []

    ident = osr.get("ID", "")
    like = osr.get("ID_LIKE", "")
    pretty = osr.get("PRETTY_NAME") or ident or "unknown"
    debian_family = ident in {"ubuntu", "zorin", "linuxmint", "pop", "neon", "debian"} \
        or "debian" in like or "ubuntu" in like
    checks.append(Check(
        "os", f"Operating system — {pretty}",
        "Tested on Ubuntu 24.04, Zorin OS 18, Mint 22 and Pop!_OS 22.04."
        if debian_family else
        "Not a Debian/Ubuntu derivative — the installer is untested here.",
        debian_family, "warning",
    ))

    arch = platform.machine()
    checks.append(Check(
        "arch", f"Architecture — {arch}",
        "x86_64 is required.", arch == "x86_64", "error",
    ))

    is_root = os.geteuid() == 0 if hasattr(os, "geteuid") else False
    checks.append(Check(
        "root", "Running as a normal user",
        "The installer must run as your user; it asks for sudo itself when needed.",
        not is_root, "error",
    ))

    free = _free_gb()
    checks.append(Check(
        "disk", f"Free disk space on / — {free:.1f} GB",
        "At least 8 GB is required (Qt toolchain ≈ 1.5 GB, build caches ≈ 2 GB).",
        free >= 8.0, "error",
    ))

    ram = _total_ram_gb()
    checks.append(Check(
        "ram", f"Memory — {ram:.1f} GB",
        "Components are built from source; 4 GB is a comfortable minimum.",
        ram >= 2.0, "error",
    ))

    checks.append(Check(
        "sudo", "sudo available",
        "Needed for package installs and writes to /opt and /usr/local.",
        shutil.which("sudo") is not None, "error",
    ))

    online = _internet_ok()
    checks.append(Check(
        "net", "Internet connection",
        "Sources are downloaded and built from pinned upstream commits.",
        online, "error",
    ))

    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "unknown")
    checks.append(Check(
        "desktop", f"Desktop session — {desktop}",
        "The installer runs from GNOME and adds Hyprland as an extra session; "
        "GNOME itself is never modified.",
        True,
    ))
    return checks


def installed_state() -> dict:
    """Detect what setup.sh previously installed (if anything)."""
    manifest = read_manifest()
    qs = paths.QS_BIN.exists()
    return {
        "installed": bool(manifest) or qs,
        "manifest_present": bool(manifest),
        "manifest": manifest,
        "qs": qs,
        "qt_version": manifest.get("QT_VERSION", ""),
        "shell_dir": paths.SHELL_DIR.is_dir(),
        "shell_json": paths.SHELL_JSON.is_file(),
        "session_registered": paths.HYPR_SESSION.exists(),
        "wallpaper": _current_wallpaper(),
    }


def _current_wallpaper() -> str:
    try:
        return paths.WALLPAPER_STATE.joinpath("path.txt").read_text().strip()
    except OSError:
        return ""


def service_active() -> bool | None:
    """True/False for the user shell service, None when systemd query fails."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", paths.SHELL_SERVICE],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() == "active"
    except (OSError, subprocess.TimeoutExpired):
        return None


def blocking(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.ok and c.severity == "error"]
