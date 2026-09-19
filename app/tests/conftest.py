"""Shared test guards.

The most important one is :func:`isolate_hypr_config`. The Keybinds page edits
``~/.config/hypr/hyprland.conf`` *and every file it sources*, so a test that
forgets to redirect ``paths.HYPRLAND_CONF`` would rewrite the developer's own
desktop configuration. This autouse fixture makes that impossible: for every
test, the config paths point into a throwaway directory, whether or not the
test sets up its own redirect.
"""

from __future__ import annotations

import pytest

from caelestia_installer import paths


@pytest.fixture(autouse=True)
def isolate_hypr_config(tmp_path_factory, monkeypatch):
    sandbox = tmp_path_factory.mktemp("hypr")
    monkeypatch.setattr(paths, "HYPR_DIR", sandbox)
    monkeypatch.setattr(paths, "HYPRLAND_CONF", sandbox / "hyprland.conf")
    return sandbox
