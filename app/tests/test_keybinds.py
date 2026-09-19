"""Keybinding editor: parsing, conflicts, safe writes and the capture pad."""

import contextlib
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from caelestia_installer import keybinds, paths
from caelestia_installer.pages.keybinds import KeybindEditor, KeybindsPage, _ShortcutCapture
from gi.repository import Adw, Gdk

REPO_CONF = Path(__file__).resolve().parents[2] / "configs/hypr/hyprland.conf"

SAMPLE = """\
# Hyprland test config
$mainMod = SUPER

# --- Keybindings ---

# Applications
bind = $mainMod SHIFT, L, exec, qs -c caelestia ipc call lock lock
bind = $mainMod, Return, exec, gnome-terminal
bind = $mainMod, SPACE, togglefloating
# Uses a letter key since the laptop has no Print key
bind = $mainMod SHIFT, S, exec, hyprshot -m region

# Workspaces
bind = $mainMod, 1, workspace, 1
bind = $mainMod SHIFT, 1, movetoworkspace, 1

# Resize submap
bind = $mainMod, R, submap, resize
submap = resize
    bind = , h, resizeactive, -20 0
    bind = , escape, submap, reset
submap = reset

# Mouse
bindm = $mainMod, mouse:Left, movewindow
"""


def test_parse_resolves_variables_sections_and_submaps():
    doc = keybinds.parse(SAMPLE)
    assert doc.variables == {"mainMod": "SUPER"}
    assert doc.main_mod_variable() == "mainMod"
    assert len(doc.binds) == 10

    lock = doc.binds[0]
    assert lock.mods == ("SUPER", "SHIFT")
    assert lock.key == "L"
    assert lock.dispatcher == "exec"
    assert lock.args == "qs -c caelestia ipc call lock lock"
    assert lock.group_title == "Applications"
    assert lock.combo == "Super + Shift + L"

    # A one-line note above a bind must not replace the section heading.
    assert doc.binds[3].group_title == "Applications"
    # Submap binds are grouped by the submap they belong to.
    assert doc.binds[7].group_title == "resize submap"
    assert doc.binds[7].mods == ()
    assert doc.binds[8].key == "escape"
    # The mouse bind is recognised as such.
    assert doc.binds[-1].is_mouse


def test_sections_cover_the_shipped_config():
    if not REPO_CONF.is_file():
        pytest.skip("repository config not available")
    doc = keybinds.load(REPO_CONF)
    titles = {b.group_title for b in doc.binds}
    assert {"Applications", "Workspaces", "Screenshot shortcuts (hyprshot)"} <= titles
    assert doc.main_mod_variable() == "mainMod"
    assert len(doc.binds) > 40


def test_conflicts_ignore_order_and_case():
    doc = keybinds.parse(SAMPLE)
    assert [b.combo for b in doc.conflicts(["SUPER", "SHIFT"], "l")] == \
        ["Super + Shift + L"]
    assert doc.conflicts(["SHIFT", "SUPER"], "L")[0].key == "L"
    assert doc.conflicts(["SUPER"], "Return")[0].dispatcher == "exec"
    assert doc.conflicts(["SUPER"], "Z") == []
    # Editing a bind must not report the bind itself.
    bind = doc.binds[0]
    assert doc.conflicts(bind.mods, bind.key) != []
    assert doc.conflicts(bind.mods, bind.key, exclude=bind) == []


def test_plus_separated_modifiers_are_understood():
    # Hand-written and tool-generated configs sometimes write SUPER+CTRL;
    # Hyprland's own form is space-separated, so both must be understood.
    doc = keybinds.parse(
        "$mainMod = SUPER\n"
        "bind = $mainMod+CTRL, S, exec, flatpak run com.spotify.Client\n"
        "bind = SUPER+CTRL+SHIFT, T, exec, true\n")
    assert doc.binds[0].mods == ("SUPER", "CTRL")
    assert doc.binds[0].combo == "Super + Ctrl + S"
    assert doc.binds[0].canonical == keybinds.canonical(["CTRL", "SUPER"], "s")
    assert doc.binds[1].mods == ("SUPER", "CTRL", "SHIFT")
    # Rewriting normalises to the documented space-separated form.
    assert keybinds.render_bind(doc.binds[0].spec(), "mainMod") == \
        "bind = $mainMod CTRL, S, exec, flatpak run com.spotify.Client"


@pytest.mark.parametrize("text,expected", [
    ("Super+Shift+L", (["SUPER", "SHIFT"], "L")),
    ("SUPER SHIFT L", (["SUPER", "SHIFT"], "L")),
    ("mainMod SHIFT l", (["SHIFT"], "L")),      # only known modifiers count
    ("Ctrl Alt T", (["CTRL", "ALT"], "T")),
    ("SUPER mouse:Left", (["SUPER"], "mouse:Left")),
    ("esc", ([], "escape")),
])
def test_parse_combo(text, expected):
    assert keybinds.parse_combo(text) == expected


def test_key_names_from_captured_keyvals():
    assert keybinds.key_from_keyval(Gdk.KEY_l) == "L"
    assert keybinds.key_from_keyval(Gdk.KEY_1) == "1"
    # Shift + 1 arrives as "exclam"; a bind is written with the unshifted name.
    assert keybinds.key_from_keyval(Gdk.KEY_exclam) == "1"
    assert keybinds.key_from_keyval(Gdk.KEY_underscore) == "minus"
    assert keybinds.key_from_keyval(Gdk.KEY_space) == "SPACE"
    assert keybinds.key_from_keyval(Gdk.KEY_Return) == "Return"
    assert keybinds.key_from_keyval(Gdk.KEY_Print) == "Print"
    assert keybinds.key_from_keyval(Gdk.KEY_F5) == "F5"


def test_mods_and_combo_formatting():
    state = Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.SHIFT_MASK
    assert keybinds.mods_from_state(state) == ["SUPER", "SHIFT"]
    assert keybinds.mods_from_state(Gdk.ModifierType.LOCK_MASK) == []
    assert keybinds.format_combo(["SHIFT", "SUPER"], "l") == "Super + Shift + L"


def test_render_uses_the_configured_main_modifier():
    spec = keybinds.BindSpec.build(["SUPER", "SHIFT"], "T", "exec", "gnome-terminal")
    assert keybinds.render_bind(spec, "mainMod") == \
        "bind = $mainMod SHIFT, T, exec, gnome-terminal"
    assert keybinds.render_bind(spec) == "bind = SUPER SHIFT, T, exec, gnome-terminal"


def test_apply_changes_edits_deletes_and_appends():
    doc = keybinds.parse(SAMPLE)
    edited = keybinds.BindSpec.build(["SUPER"], "Y", "exec", "firefox")
    deleted = doc.binds[1]
    added = keybinds.BindSpec.build(["SUPER", "ALT"], "G", "exec",
                                    keybinds.launch_command("/tmp/mail.desktop"))

    text = keybinds.apply_changes(
        SAMPLE,
        [keybinds.Change(doc.binds[0].line, edited),
         keybinds.Change(deleted.line, None)],
        [added], doc.main_mod_variable())

    lines = text.split("\n")
    assert "bind = $mainMod, Y, exec, firefox" in lines
    assert deleted.raw not in text              # removed
    assert doc.binds[2].raw in lines            # untouched
    assert doc.binds[5].raw in lines            # untouched
    assert keybinds.SECTION_HEADER in text
    assert "bind = $mainMod ALT, G, exec, gio launch /tmp/mail.desktop" in lines
    # The submap's indentation survives an in-place edit.
    sub = keybinds.parse(text).binds
    assert any(b.group_title == "resize submap" for b in sub)


SOURCED_MAIN = """\
$mainMod = SUPER

# Applications
bind = $mainMod, T, exec, gnome-terminal

# Custom keybinds
source = ~/.config/hypr/keybinds.conf
"""

SOURCED_KEYBINDS = """\
# Keybinds managed by caelestia-settings
# Hand edits are fine, but the settings app may rewrite this file.

bind = $mainMod SHIFT, S, exec, firefox

# Clipboard
bind = SUPER, V, global, quickshell:celestia-memory:toggle
"""


def test_load_config_follows_source_directives(tmp_path):
    home = tmp_path / "home"
    (home / ".config/hypr").mkdir(parents=True)
    main = home / ".config/hypr/hyprland.conf"
    sourced = home / ".config/hypr/keybinds.conf"
    main.write_text(SOURCED_MAIN)
    sourced.write_text(SOURCED_KEYBINDS)

    with patch.dict(os.environ, {"HOME": str(home)}):
        config = keybinds.load_config(main)

        assert [d.path for d in config.documents] == [main, sourced]
        assert [b.combo for b in config.binds] == [
            "Super + T", "Super + Shift + S", "Super + V"]
        # Variables from the main file resolve inside the sourced one.
        assert config.binds[1].mods == ("SUPER", "SHIFT")
        # The sourced keybinds file is where new shortcuts belong.
        assert config.managed_target() == sourced
        # Conflicts are checked across the whole tree.
        assert config.conflicts(["SUPER"], "V")[0].file == sourced

        # A missing sourced file is still remembered as a write target.
        sourced.unlink()
        config = keybinds.load_config(main)
        assert config.managed_target() == sourced
        assert config.document_for(sourced) is None


def test_duplicate_combinations_are_detected(tmp_path):
    main = tmp_path / "hyprland.conf"
    main.write_text("$mainMod = SUPER\n"
                    "bind = $mainMod, V, togglefloating\n"
                    "bind = SUPER, V, global, app:toggle\n"
                    "bind = $mainMod, Q, killactive\n")
    config = keybinds.load_config(main)
    duplicates = config.duplicates()
    assert len(duplicates) == 1
    assert all(b.canonical in duplicates for b in config.binds if b.key == "V")
    assert all(b.canonical not in duplicates for b in config.binds if b.key == "Q")


def test_validate_rejects_injection_and_bare_keys():
    with pytest.raises(ValueError, match="modifier"):
        keybinds.validate_spec(
            keybinds.BindSpec.build([], "K", "exec", "true"))
    with pytest.raises(ValueError, match="Press a key"):
        keybinds.validate_spec(keybinds.BindSpec.build(["SUPER"], "", "exec", "true"))
    with pytest.raises(ValueError, match="Choose what"):
        keybinds.validate_spec(keybinds.BindSpec.build(["SUPER"], "K", ""))
    with pytest.raises(ValueError):
        keybinds.validate_spec(
            keybinds.BindSpec.build(["SUPER"], "K", "exec", "echo hi # comment"))
    with pytest.raises(ValueError):
        keybinds.validate_spec(
            keybinds.BindSpec.build(["SUPER"], "K", "exec", "true\nbind = , x, exit"))


def test_save_is_atomic_with_a_rolling_backup():
    with tempfile.TemporaryDirectory() as directory:
        conf = Path(directory) / "hyprland.conf"
        conf.write_text("original\n")
        backup = keybinds.save(conf, "first change\n")
        assert backup == keybinds.backup_path(conf)
        assert backup.read_text() == "original\n"
        assert conf.read_text() == "first change\n"
        assert not (Path(directory) / "hyprland.conf.tmp").exists()

        assert keybinds.restore_backup(conf) is True
        assert conf.read_text() == "original\n"
        # restore must not clobber the backup with the reverted content
        assert backup.read_text() == "original\n"
        backup.unlink()
        assert keybinds.restore_backup(conf) is False


def test_launch_command_quoting_and_summaries():
    assert keybinds.launch_command("/usr/share/applications/a.desktop") == \
        "gio launch /usr/share/applications/a.desktop"
    assert keybinds.launch_command("/opt/My Apps/b.desktop").startswith('gio launch "')
    names = {"/usr/share/applications/a.desktop": "Aardvark"}
    args = "gio launch /usr/share/applications/a.desktop"
    assert keybinds.action_summary("exec", args, names) == "Launch Aardvark"
    assert keybinds.action_summary("killactive", "") == "Close the active window"
    assert keybinds.action_summary("workspace", "3") == "Go to workspace 3"
    assert keybinds.action_summary("exec", "htop") == "Run: htop"
    assert len(keybinds.ACTIONS) > 10


def test_list_apps_returns_launchable_entries():
    apps = keybinds.list_apps()
    assert all(a.name and a.desktop_file for a in apps)
    assert all(a.command().startswith("gio launch") for a in apps)
    assert [a.name for a in apps] == sorted((a.name for a in apps), key=str.casefold)


def test_capture_pad_turns_presses_into_shortcuts():
    Adw.init()
    captured = []
    pad = _ShortcutCapture(lambda mods, key: captured.append((mods, key)))
    super_shift = Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.SHIFT_MASK
    # A modifier on its own only previews; it never completes a shortcut.
    assert pad.handle_key(Gdk.KEY_Shift_L, Gdk.ModifierType.SHIFT_MASK) is True
    assert captured == []
    assert pad.handle_key(Gdk.KEY_l, super_shift) is True
    assert captured == [(["SUPER", "SHIFT"], "L")]
    # An unmodified Escape is left for the dialog to handle.
    assert pad.handle_key(Gdk.KEY_Escape, Gdk.ModifierType.LOCK_MASK) is False


# ---------------------------------------------------------------------------
# Page + editor
# ---------------------------------------------------------------------------

class FakeWin:
    def __init__(self):
        self.state = {"busy": False, "installed": True}
        self.toasts = []

    def toast(self, message):
        self.toasts.append(message)


@contextlib.contextmanager
def page_with(text=SAMPLE):
    """A KeybindsPage pointed at a throwaway config — never the real one."""
    Adw.init()
    with tempfile.TemporaryDirectory() as directory:
        conf = Path(directory) / "hyprland.conf"
        conf.write_text(text)
        page = KeybindsPage(FakeWin())
        page._reload_hyprland = Mock()  # never talk to a real Hyprland session
        with patch.object(paths, "HYPRLAND_CONF", conf):
            page.refresh()
            yield page, conf


def test_page_lists_binds_grouped_by_section():
    with page_with() as (page, conf):
        assert page.config is not None
        titles = [g.get_title() for g in page.reloadable
                  if isinstance(g, Adw.PreferencesGroup)]
        assert titles[0] == "Applications"
        assert "resize submap" in titles
        page.search.set_text("hyprshot")
        page._on_filter_changed()
        assert len(page._rows) == 1
        assert "hyprshot" in page._rows[0].get_subtitle()


def test_page_reports_a_missing_config():
    Adw.init()
    with tempfile.TemporaryDirectory() as directory:
        conf = Path(directory) / "missing.conf"
        page = KeybindsPage(FakeWin())
        with patch.object(paths, "HYPRLAND_CONF", conf):
            page.refresh()
        assert page.config is None
        assert page.btn_add.get_sensitive() is False
        assert "not found" in page.row_config.get_subtitle()


def test_page_adds_refuses_conflicts_and_undoes():
    with page_with() as (page, conf):
        original = conf.read_text()
        spec = keybinds.BindSpec.build(["SUPER"], "T", "exec", "gnome-terminal")

        assert page.apply_bind(None, spec) is True
        assert conf.read_text() != original
        assert "bind = $mainMod, T, exec, gnome-terminal" in conf.read_text()
        assert keybinds.backup_path(conf).read_text() == original
        assert any("Super + T" in t and "added" in t for t in page.win.toasts)
        assert any(b.combo == "Super + T" for b in page.config.binds)

        # Re-adding the same combination is refused by the live document.
        assert page.apply_bind(None, spec) is False
        assert "already used" in page.win.toasts[-1]

        # Undo restores exactly the pre-edit file.
        assert keybinds.restore_backup(conf) is True
        page.refresh()
        assert conf.read_text() == original
        assert all(b.combo != "Super + T" for b in page.config.binds)


def test_editor_blocks_a_taken_combination():
    with page_with() as (page, _conf):
        editor = KeybindEditor(page, None)
        assert editor.btn_save.get_sensitive() is False

        editor.keys_entry.set_text("SUPER SHIFT L")   # taken by the lock binding
        assert editor.btn_save.get_sensitive() is False
        assert "Already in use by" in editor.status.get_label()
        assert editor.status.get_css_classes() == ["keybind-taken"]

        editor.keys_entry.set_text("SUPER ALT P")     # free
        assert editor.btn_save.get_sensitive() is False   # …but no action yet
        editor.kind_dropdown.set_selected(1)              # Run a command
        editor.command_entry.set_text("pavucontrol")
        assert "Ready" in editor.status.get_label()
        assert editor.btn_save.get_sensitive() is True

        spec = editor._current_spec()
        assert spec is not None and spec.dispatcher == "exec"
        assert spec.args == "pavucontrol"
        assert spec.mods == ("SUPER", "ALT") and spec.key == "P"


def test_editor_prefills_an_existing_app_binding():
    app = keybinds.AppEntry("Nautilus", "org.gnome.Nautilus.desktop",
                            "/usr/share/applications/org.gnome.Nautilus.desktop")
    with page_with() as (page, conf):
        spec = keybinds.BindSpec.build(["SUPER"], "E", "exec", app.command())
        with patch.object(keybinds, "list_apps", return_value=[app]):
            page.apply_bind(None, spec)
            bind = next(b for b in page.config.binds if b.combo == "Super + E")
            editor = KeybindEditor(page, bind)
        assert editor.kind_dropdown.get_selected() == 0
        assert editor._chosen_app is not None
        assert editor._chosen_app.name == "Nautilus"
        assert editor.keys_entry.get_text() == "SUPER E"
        assert editor.btn_save.get_sensitive() is True


SOURCED_MAIN_RELATIVE = """\
$mainMod = SUPER

# Applications
bind = $mainMod, T, exec, gnome-terminal

# Custom keybinds
source = keybinds.conf
"""


def test_page_shows_and_extends_a_sourced_keybinds_file(tmp_path):
    Adw.init()
    main = tmp_path / "hyprland.conf"
    main.write_text(SOURCED_MAIN_RELATIVE)
    sourced = tmp_path / "keybinds.conf"
    sourced.write_text(SOURCED_KEYBINDS)

    page = KeybindsPage(FakeWin())
    page._reload_hyprland = Mock()
    with patch.object(paths, "HYPRLAND_CONF", main):
        page.refresh()
        # Both files are shown, and they are labelled by file name.
        assert {b.combo for b in page.config.binds} == {
            "Super + T", "Super + Shift + S", "Super + V"}
        descriptions = [g.get_description() for g in page.reloadable
                        if isinstance(g, Adw.PreferencesGroup)]
        assert "keybinds.conf" in descriptions

        spec = keybinds.BindSpec.build(["SUPER"], "Y", "exec", "gnome-terminal")
        assert page.apply_bind(None, spec) is True
        assert any("keybinds.conf" in t for t in page.win.toasts)

    # The sourced file's own style is kept: a literal modifier, not $mainMod,
    # which is only guaranteed to be defined inside its defining file.
    assert "bind = SUPER, Y, exec, gnome-terminal" in sourced.read_text()
    assert main.read_text() == SOURCED_MAIN_RELATIVE   # never touched
    assert keybinds.backup_path(sourced).is_file()


def test_page_flags_duplicate_combinations(tmp_path):
    Adw.init()
    main = tmp_path / "hyprland.conf"
    main.write_text("$mainMod = SUPER\n\n# Apps\n"
                    "bind = $mainMod, V, togglefloating\n"
                    "bind = SUPER, V, global, app:toggle\n")
    page = KeybindsPage(FakeWin())
    with patch.object(paths, "HYPRLAND_CONF", main):
        page.refresh()
    assert len(page._rows) == 2
    assert all("Same keys as another shortcut" in r.get_subtitle()
               for r in page._rows)
    assert "duplicate combination" in page.row_count.get_subtitle()


def test_editor_allows_plain_keys_inside_a_submap():
    with page_with() as (page, _conf):
        submap_bind = next(b for b in page.config.binds if b.submap)
        editor = KeybindEditor(page, submap_bind)
        # The submap already isolates the key, so no modifier is required.
        assert editor.bare_keys_allowed is True
        assert editor.btn_save.get_sensitive() is True
        assert editor.status.get_label().startswith("Ready")

        # A brand-new binding is still held to the modifier rule.
        fresh = KeybindEditor(page, None)
        fresh.keys_entry.set_text("J")
        assert fresh.btn_save.get_sensitive() is False
        assert "add a modifier" in fresh.status.get_label()


def test_app_picker_marks_the_chosen_application():
    with page_with() as (page, _conf):
        editor = KeybindEditor(page, None)
        if not editor._apps:
            pytest.skip("no installed applications to list")
        editor.keys_entry.set_text("SUPER ALT K")
        editor.kind_dropdown.set_selected(0)
        chosen_app = editor._apps[0]
        editor._on_app_activated(None, chosen_app)
        assert editor._chosen_app is chosen_app
        icons = {a.app_id: icon.get_icon_name() for _r, a, icon in editor._app_rows}
        assert icons[chosen_app.app_id] == "object-select-symbolic"
        assert set(icons.values()) - {"object-select-symbolic"} == {
            "application-x-executable-symbolic"}
        assert editor.btn_save.get_sensitive() is True


def test_browsing_the_page_is_strictly_read_only():
    """Opening, refreshing, filtering or previewing must never write a file."""
    Adw.init()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        main = root / "hyprland.conf"
        main.write_text(SOURCED_MAIN_RELATIVE)
        sourced = root / "keybinds.conf"
        sourced.write_text(SOURCED_KEYBINDS)
        before = (main.read_bytes(), sourced.read_bytes())

        page = KeybindsPage(FakeWin())
        page._reload_hyprland = Mock()
        with patch.object(paths, "HYPRLAND_CONF", main):
            for _ in range(3):
                page.refresh()
                page.search.set_text("fire")
                page._on_filter_changed()
                KeybindEditor(page, page.config.binds[0])
                page._set_actions_enabled(True)

        assert (main.read_bytes(), sourced.read_bytes()) == before
        assert not keybinds.backup_path(main).exists()
        assert not keybinds.backup_path(sourced).exists()


def test_page_reload_is_skipped_outside_hyprland():
    with page_with() as (page, _conf):
        with patch.dict("os.environ", {}, clear=True):
            # Bypass the test's stub to exercise the real method.
            KeybindsPage._reload_hyprland(page, announce=True)
        assert "Not in a Hyprland session" in page.win.toasts[-1]
