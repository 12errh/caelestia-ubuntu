"""The .deb's legacy-install cleanup must remove only what it positively owns.

`sudo ./app/install.sh` writes into /usr/local, which `/usr` loses to in both
$PATH and $XDG_DATA_DIRS — so a leftover copy keeps launching the older app and
keeps the older icon. The cleanup runs from `postinst` as root, so the blast
radius of a mistake is the whole prefix; these tests pin down that it touches
nothing but this project's own files.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLEANUP = REPO / "packaging/deb/caelestia-legacy-cleanup.sh"
APP_ID = "io.github.CaelestiaUbuntu.Installer"


def run_cleanup(prefix: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(CLEANUP), "--prefix", str(prefix), *extra],
        capture_output=True, text=True, check=False,
    )


def make_legacy_install(prefix: Path) -> None:
    """Recreate exactly what `app/install.sh` used to leave behind."""
    (prefix / "bin").mkdir(parents=True)
    launcher = prefix / "bin/caelestia-installer"
    launcher.write_text(
        "#!/usr/bin/env bash\n"
        f'export CAEL_REPO_DIR="{prefix}/share/caelestia-installer/repo"\n'
        f'exec python3 "{prefix}/share/caelestia-installer/app/run.py" "$@"\n')
    launcher.chmod(0o755)
    share = prefix / "share/caelestia-installer"
    (share / "app").mkdir(parents=True)
    (share / "app/run.py").write_text("# app\n")
    (share / "repo").mkdir()
    (prefix / "share/applications").mkdir(parents=True)
    (prefix / f"share/applications/{APP_ID}.desktop").write_text(
        "[Desktop Entry]\nName=Caelestia for Ubuntu\nExec=caelestia-installer\n")
    # The retired glyph: a scalable SVG outranks the PNG at every size except
    # exactly 128px, which is why the app grid kept showing it.
    for size, name, body in (("scalable", f"{APP_ID}.svg", "<svg/>"),
                             ("128x128", f"{APP_ID}.png", "png")):
        directory = prefix / f"share/icons/hicolor/{size}/apps"
        directory.mkdir(parents=True)
        (directory / name).write_text(body)


def test_cleanup_removes_the_shadowing_legacy_install(tmp_path):
    make_legacy_install(tmp_path)

    result = run_cleanup(tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "bin/caelestia-installer").exists()
    assert not (tmp_path / "share/caelestia-installer").exists()
    assert not (tmp_path / f"share/applications/{APP_ID}.desktop").exists()
    assert not list((tmp_path / "share/icons").rglob(f"{APP_ID}.*"))
    assert "removed legacy icon" in result.stdout
    # The theme directory itself stays: other local apps may live there.
    assert (tmp_path / "share/icons/hicolor").is_dir()


def test_cleanup_never_touches_unrelated_files(tmp_path):
    (tmp_path / "bin").mkdir(parents=True)
    other_tool = tmp_path / "bin/other-tool"
    other_tool.write_text("#!/bin/sh\ntrue\n")
    # Not install.sh's launcher: it does not exec the copy under this prefix.
    hand_written = tmp_path / "bin/caelestia-installer"
    hand_written.write_text("#!/bin/sh\nexec /opt/mine\n")
    (tmp_path / "share/applications").mkdir(parents=True)
    other_entry = tmp_path / f"share/applications/{APP_ID}.desktop"
    other_entry.write_text("[Desktop Entry]\nName=Something else\n")
    icon_dir = tmp_path / "share/icons/hicolor/128x128/apps"
    icon_dir.mkdir(parents=True)
    (icon_dir / "other.png").write_text("png")
    (tmp_path / "share/caelestia-installer").mkdir(parents=True)
    (tmp_path / "share/caelestia-installer/notes.txt").write_text("not ours")

    result = run_cleanup(tmp_path)

    assert result.returncode == 0, result.stderr
    assert other_tool.exists()
    assert hand_written.exists()
    assert other_entry.exists()
    assert (icon_dir / "other.png").exists()
    assert (tmp_path / "share/caelestia-installer/notes.txt").exists()
    assert "no legacy install found" in result.stdout


def test_cleanup_is_idempotent_for_any_prefix(tmp_path):
    make_legacy_install(tmp_path)

    assert run_cleanup(tmp_path).returncode == 0
    second = run_cleanup(tmp_path)
    assert second.returncode == 0
    assert "no legacy install found" in second.stdout


def test_cleanup_rejects_unknown_arguments():
    result = subprocess.run(["bash", str(CLEANUP), "--nope"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "unknown argument" in result.stderr
