"""Headless entry point.

``caelestia-installer --check`` prints the same pre-flight checks and the
installed-state report the GUI uses, without touching GTK — handy for CI and
for debugging a machine over ssh.
"""

from __future__ import annotations

import argparse
import sys

from . import VERSION, checks, paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="caelestia-installer")
    parser.add_argument("--check", action="store_true",
                        help="run system checks and print the installed state, then exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args(argv)

    if not args.check:
        # GUI mode — import GTK only now so --check stays display-free.
        try:
            from .main import run_gui  # noqa: PLC0415
        except ImportError as exc:
            print(f"GUI unavailable ({exc}). GTK4 + libadwaita are required:\n"
                  "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1", file=sys.stderr)
            return 1
        return run_gui()

    try:
        repo = paths.repo_root()
    except FileNotFoundError as exc:
        repo = f"NOT FOUND — {exc}"

    print(f"{paths.APP_NAME} {VERSION}")
    print(f"repo:      {repo}")
    print(f"manifest:  {paths.MANIFEST} ({'present' if paths.MANIFEST.exists() else 'absent'})")
    print()
    print("pre-flight checks:")
    failed = False
    for c in checks.collect():
        mark = "ok " if c.ok else ("WARN" if c.severity == "warning" else "FAIL")
        if not c.ok and c.severity == "error":
            failed = True
        print(f"  [{mark}] {c.label}" + (f" — {c.detail}" if c.detail else ""))
    state = checks.installed_state()
    print()
    print("installed state:")
    print(f"  installed:         {state['installed']}")
    print(f"  quickshell binary: {state['qs']}")
    print(f"  Qt version:        {state['qt_version'] or '—'}")
    print(f"  shell sources:     {state['shell_dir']}")
    print(f"  Hyprland session:  {state['session_registered']}")
    print(f"  shell service:     {checks.service_active()}")
    print()
    if failed:
        print("BLOCKING PROBLEMS FOUND — resolve the [FAIL] items above before installing.")
        return 2
    print("No blocking problems — ready to install." if not state["installed"]
          else "Already installed — use the Setup tab or ./update.sh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
