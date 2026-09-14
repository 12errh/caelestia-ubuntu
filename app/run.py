#!/usr/bin/env python3
"""Launcher for the Caelestia for Ubuntu installer app."""

import os
import sys
from pathlib import Path

# Running straight from a clone: app/run.py
sys.path.insert(0, str(Path(__file__).resolve().parent))

from caelestia_installer.cli import main  # noqa: E402

if __name__ == "__main__":
    os.environ.setdefault("TERM", "xterm-256color")
    raise SystemExit(main(sys.argv[1:]))
