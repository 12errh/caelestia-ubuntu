"""Keybinds page — see, add, edit and remove the shell's Hyprland keybindings.

The page edits the user's own ``~/.config/hypr/hyprland.conf``: it lists every
``bind`` line grouped by the section comments already in that file, opens an
editor for any of them and can add new ones. Everything is written back through
:mod:`caelestia_installer.keybinds`, which validates each value, writes
atomically and keeps a rolling backup the page's Undo button restores.

The editor is built around capturing a real key press: click the pad, press the
combination and it appears. The captured combination is checked against every
existing bind before it can be saved, so two shortcuts can never fight over the
same keys. After the keys come the action — launch an installed application
(picked from a searchable list), run a command, or a ready-made desktop action.
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from .. import keybinds, paths  # noqa: E402
from ..page_style import build_page  # noqa: E402
from ..runner import run_capture  # noqa: E402

#: Action kinds, in the order the editor's selector lists them.
KIND_APP, KIND_COMMAND, KIND_ACTION = 0, 1, 2
KIND_LABELS = (
    "Launch an application",
    "Run a command",
    "Desktop action",
)


class _ShortcutCapture(Gtk.Box):
    """A focusable pad that turns a key press into a shortcut.

    Modifier keys update the live preview; the first non-modifier key completes
    the combination. :meth:`handle_key` holds the logic (and is what the tests
    drive), while :meth:`_on_key_pressed` is only the GTK signal adapter.
    """

    def __init__(self, on_capture) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._on_capture = on_capture
        self.set_focusable(True)
        self.set_halign(Gtk.Align.FILL)
        self.set_hexpand(True)
        self.add_css_class("keybind-capture")

        self.combo = Gtk.Label(label="Press your shortcut")
        self.combo.add_css_class("keybind-combo")
        self.hint = Gtk.Label(
            label="Click here, then press the keys — include at least one "
                  "modifier (Super, Ctrl, Alt…).")
        self.hint.add_css_class("keybind-hint")
        self.hint.set_wrap(True)
        self.hint.set_justify(Gtk.Justification.CENTER)
        self.combo.set_wrap(True)
        self.append(self.combo)
        self.append(self.hint)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: self.grab_focus())
        self.add_controller(click)
        self.connect("notify::has-focus", self._on_focus_changed)

    # -- logic (test-friendly) ---------------------------------------------
    def handle_key(self, keyval, state) -> bool:
        """Return True when the press was consumed by the capture."""
        mods = keybinds.mods_from_state(state)
        if keyval in keybinds.MODIFIER_KEYVALS:
            self.show_mods(mods)
            return True
        if keyval == Gdk.KEY_Escape and not mods:
            return False  # let the dialog's own Escape handling close it
        key = keybinds.key_from_keyval(keyval)
        if not key:
            return True
        self._on_capture(mods, key)
        return True

    def show_mods(self, mods) -> None:
        label = keybinds.format_combo(mods, "")
        self.combo.set_label(label if label else "Press your shortcut")

    def show_combo(self, mods, key) -> None:
        self.combo.set_label(keybinds.format_combo(mods, key))
        self.hint.set_label(
            "Press again to replace it — the field below accepts typed keys too.")

    def show_idle(self) -> None:
        self.combo.set_label("Press your shortcut")
        self.hint.set_label(
            "Click here, then press the keys — include at least one modifier "
            "(Super, Ctrl, Alt…).")

    # -- GTK ----------------------------------------------------------------
    def _on_key_pressed(self, _controller, keyval, _code, state) -> bool:
        return self.handle_key(keyval, state)

    def _on_focus_changed(self, _widget, _pspec) -> None:
        state = "focused" if self.has_focus() else "idle"
        if state == "focused":
            self.add_css_class("capturing")
        else:
            self.remove_css_class("capturing")


class KeybindEditor(Adw.Dialog):
    """Add or edit one keybinding: capture the keys, then choose the action."""

    def __init__(self, page, bind: keybinds.Keybind | None = None) -> None:
        super().__init__()
        self.page = page
        self.bind = bind
        self.config = page.config
        self.set_title("Edit keybinding" if bind else "Add a keybinding")
        self.set_content_width(560)
        self.set_content_height(640)

        self._apps: list[keybinds.AppEntry] = []
        # (row, app, leading icon) — the icon is kept because Adw.ActionRow's
        # first child is an internal box, not the prefix we added.
        self._app_rows: list[tuple[Gtk.ListBoxRow, keybinds.AppEntry, Gtk.Image]] = []
        self._chosen_app: keybinds.AppEntry | None = None
        #: Plain keys are valid inside a submap, so editing one is allowed.
        self.bare_keys_allowed = bool(bind is not None and bind.submap)
        self._loading = True

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        for edge in ("top", "bottom", "start", "end"):
            getattr(content, f"set_margin_{edge}")(18)

        self.capture = _ShortcutCapture(self._on_capture)
        content.append(self.capture)

        # -- shortcut text --------------------------------------------------
        shortcut = Adw.PreferencesGroup(
            title="Shortcut",
            description="Captured above, or typed as e.g. SUPER SHIFT L.")
        self.keys_entry = Gtk.Entry(valign=Gtk.Align.CENTER)
        self.keys_entry.set_placeholder_text("SUPER SHIFT L")
        self.keys_entry.set_width_chars(18)
        self.keys_entry.connect("changed", self._on_keys_changed)
        row_keys = Adw.ActionRow(title="Keys")
        row_keys.add_suffix(self.keys_entry)
        shortcut.add(row_keys)
        content.append(shortcut)

        self.status = Gtk.Label(xalign=0.0, wrap=True)
        self.status.add_css_class("keybind-hint")
        content.append(self.status)

        # -- action ---------------------------------------------------------
        action_group = Adw.PreferencesGroup(
            title="Action",
            description="What the shortcut should do when it is pressed.")
        self.kind_dropdown = Gtk.DropDown.new_from_strings(list(KIND_LABELS))
        self.kind_dropdown.set_valign(Gtk.Align.CENTER)
        self.kind_dropdown.connect("notify::selected", self._on_kind_changed)
        row_kind = Adw.ActionRow(title="Do this")
        row_kind.add_suffix(self.kind_dropdown)
        action_group.add(row_kind)
        content.append(action_group)

        self.stack = Gtk.Stack(vexpand=False, hhomogeneous=True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._build_app_page(), "app")
        self.stack.add_named(self._build_command_page(), "command")
        self.stack.add_named(self._build_action_page(), "action")
        content.append(self.stack)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(content)
        root.append(scroll)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for edge in ("top", "bottom", "start", "end"):
            getattr(bar, f"set_margin_{edge}")(12)
        bar.append(Gtk.Box(hexpand=True))
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        self.btn_save = Gtk.Button(label="Save" if bind else "Add keybinding")
        self.btn_save.add_css_class("suggested-action")
        self.btn_save.connect("clicked", self._on_save)
        bar.append(cancel)
        bar.append(self.btn_save)
        root.append(Gtk.Separator())
        root.append(bar)
        self.set_child(root)

        self._prefill()
        self._loading = False
        self._filter_apps()
        self._refresh_status()
        # grab_focus() before the dialog is mapped does nothing, so arm the
        # capture pad the moment it appears: type the shortcut straight away.
        self.connect("map", lambda *_: self.capture.grab_focus())

    # ------------------------------------------------------------------ pages
    def _build_app_page(self) -> Gtk.Widget:
        self._apps = keybinds.list_apps()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.app_search = Gtk.SearchEntry()
        self.app_search.set_placeholder_text("Search installed applications…")
        self.app_search.connect("search-changed", lambda *_: self._filter_apps())
        box.append(self.app_search)

        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        listbox.set_valign(Gtk.Align.START)
        for app in self._apps:
            # App names are user data (they may contain '&'), so markup must be
            # off *before* the title is set or GTK warns while parsing it.
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(app.name)
            row.set_subtitle(app.desktop_file)
            row.set_activatable(True)
            icon = Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
            row.add_prefix(icon)
            row.connect("activated", self._on_app_activated, app)
            listbox.append(row)
            self._app_rows.append((row, app, icon))

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(220)
        scroller.set_child(listbox)
        box.append(scroller)

        self.app_none = Gtk.Label(label="No installed application matches that search.")
        self.app_none.add_css_class("keybind-hint")
        self.app_none.set_visible(False)
        box.append(self.app_none)
        return box

    def _build_command_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        frame = Adw.PreferencesGroup(
            title="Command",
            description="Runs through Hyprland's exec, exactly as typed. "
                        "The app never runs it for you.")
        self.command_entry = Gtk.Entry(valign=Gtk.Align.CENTER)
        self.command_entry.set_placeholder_text("e.g. gnome-terminal")
        self.command_entry.connect("changed", lambda *_: self._refresh_status())
        row = Adw.ActionRow(title="Command")
        row.add_suffix(self.command_entry)
        frame.add(row)
        box.append(frame)
        return box

    def _build_action_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        frame = Adw.PreferencesGroup(
            title="Desktop action",
            description="Ready-made actions for the Caelestia desktop.")
        self.action_dropdown = Gtk.DropDown.new_from_strings(
            [self._action_label(a) for a in keybinds.ACTIONS])
        self.action_dropdown.set_valign(Gtk.Align.CENTER)
        self.action_dropdown.set_hexpand(True)
        self.action_dropdown.connect("notify::selected", lambda *_: self._refresh_status())
        row = Adw.ActionRow(title="Action")
        row.add_suffix(self.action_dropdown)
        frame.add(row)
        box.append(frame)

        raw = Adw.PreferencesGroup(
            title="Or type a raw action",
            description="A Hyprland dispatcher and its arguments — this wins "
                        "over the choice above.")
        self.custom_entry = Gtk.Entry(valign=Gtk.Align.CENTER)
        self.custom_entry.set_placeholder_text("e.g. submap resize")
        self.custom_entry.connect("changed", lambda *_: self._refresh_status())
        row_raw = Adw.ActionRow(title="Action")
        row_raw.add_suffix(self.custom_entry)
        raw.add(row_raw)
        box.append(raw)
        return box

    @staticmethod
    def _action_label(action: keybinds.DesktopAction) -> str:
        if action.description:
            return f"{action.label} — {action.description}"
        return action.label

    # --------------------------------------------------------------- prefill
    def _prefill(self) -> None:
        bind = self.bind
        if bind is None:
            return
        self.keys_entry.set_text(keybinds.format_combo(bind.mods, bind.key,
                                                       friendly=False))
        self.capture.show_combo(bind.mods, bind.key)
        if bind.dispatcher == "exec" and bind.args.strip().startswith("gio launch"):
            # Match on the desktop file itself — an app's display name can
            # differ from what a previous check recorded for it.
            target = keybinds.launch_target(bind.args)
            recorded = keybinds.app_name_from_command(
                bind.args, self.page.app_names())
            self._chosen_app = next(
                (a for a in self._apps if a.desktop_file == target), None)
            if self._chosen_app is None:
                self._chosen_app = next(
                    (a for a in self._apps if a.name == recorded), None)
            self.kind_dropdown.set_selected(KIND_APP)
        elif bind.dispatcher == "exec":
            self.command_entry.set_text(bind.args)
            self.kind_dropdown.set_selected(KIND_COMMAND)
        else:
            self.kind_dropdown.set_selected(KIND_ACTION)
            match = next((a for a in keybinds.ACTIONS
                          if a.dispatcher == bind.dispatcher and a.args == bind.args),
                         None)
            if match is not None:
                self.action_dropdown.set_selected(keybinds.ACTIONS.index(match))
            else:
                self.custom_entry.set_text(
                    f"{bind.dispatcher} {bind.args}".strip())
        self._sync_app_selection()
        self._on_kind_changed()

    def _sync_app_selection(self) -> None:
        wanted = self._chosen_app.desktop_file if self._chosen_app else ""
        for _row, app, icon in self._app_rows:
            icon.set_from_icon_name(
                "object-select-symbolic" if app.desktop_file == wanted
                else "application-x-executable-symbolic")

    # ------------------------------------------------------------- callbacks
    def _on_capture(self, mods, key) -> None:
        text = keybinds.format_combo(mods, key, friendly=False)
        self.keys_entry.set_text(text)
        self.capture.show_combo(mods, key)

    def _on_keys_changed(self, _entry) -> None:
        if self._loading:
            return
        mods, key = keybinds.parse_combo(self.keys_entry.get_text())
        if key:
            self.capture.show_combo(mods, key)
        elif mods:
            self.capture.show_mods(mods)
        else:
            self.capture.show_idle()
        self._refresh_status()

    def _on_kind_changed(self, *_args) -> None:
        selected = self.kind_dropdown.get_selected()
        name = ("app", "command", "action")[selected] if selected < 3 else "app"
        self.stack.set_visible_child_name(name)
        if not self._loading:
            self._refresh_status()

    def _filter_apps(self) -> None:
        needle = self.app_search.get_text().strip().casefold()
        visible = 0
        for row, app, _icon in self._app_rows:
            match = (not needle
                     or needle in app.name.casefold()
                     or needle in app.app_id.casefold()
                     or needle in app.desktop_file.casefold())
            row.set_visible(match)
            visible += int(match)
        self.app_none.set_visible(visible == 0 and bool(self._app_rows))

    def _on_app_activated(self, _row, app) -> None:
        self._chosen_app = app
        self._sync_app_selection()
        self._refresh_status()

    # ------------------------------------------------------------- validation
    def _selected_action(self) -> keybinds.DesktopAction | None:
        index = self.action_dropdown.get_selected()
        if 0 <= index < len(keybinds.ACTIONS):
            return keybinds.ACTIONS[index]
        return None

    def _action_pair(self) -> tuple[str | None, str]:
        kind = self.kind_dropdown.get_selected()
        if kind == KIND_APP:
            if self._chosen_app is None:
                return None, ""
            return "exec", self._chosen_app.command()
        if kind == KIND_COMMAND:
            command = self.command_entry.get_text().strip()
            return ("exec", command) if command else (None, "")
        custom = self.custom_entry.get_text().strip()
        if custom:
            dispatcher, _, args = custom.partition(" ")
            return dispatcher.strip(), args.strip()
        action = self._selected_action()
        if action is None:
            return None, ""
        return action.dispatcher, action.args

    def _current_spec(self) -> keybinds.BindSpec | None:
        mods, key = keybinds.parse_combo(self.keys_entry.get_text())
        dispatcher, args = self._action_pair()
        if not key or dispatcher is None:
            return None
        return keybinds.BindSpec.build(
            mods, key, dispatcher, args,
            mouse=key.casefold().startswith("mouse:"))

    def _refresh_status(self) -> None:
        mods, key = keybinds.parse_combo(self.keys_entry.get_text())

        def problem(message: str, css: str) -> None:
            self.status.set_label(message)
            self.status.set_css_classes([css])
            self.btn_save.set_sensitive(False)

        if not key:
            problem("Press the keys you want to use, or type them.", "keybind-hint")
            return
        if (not mods and not self.bare_keys_allowed
                and not key.casefold().startswith("mouse:")):
            problem(f"{key} on its own would swallow normal typing — add a modifier.",
                    "keybind-problem")
            return
        conflicts = self.config.conflicts(mods, key, self.bind) if self.config else []
        if conflicts:
            existing = conflicts[0]
            problem(f"Already in use by “{existing.combo}” — "
                    f"{existing.summary(self.page.app_names())}. "
                    "Choose different keys.", "keybind-taken")
            return

        dispatcher, args = self._action_pair()
        if dispatcher is None:
            problem("Now choose what the shortcut should do.",
                    "keybind-hint")
            return
        try:
            candidate = keybinds.validate_spec(
                keybinds.BindSpec.build(mods, key, dispatcher, args),
                require_modifier=not self.bare_keys_allowed)
        except ValueError as exc:
            problem(str(exc), "keybind-problem")
            return
        self.status.set_label(f"Ready — {keybinds.format_combo(candidate.mods, candidate.key)}")
        self.status.set_css_classes(["keybind-ok"])
        self.btn_save.set_sensitive(True)

    # ------------------------------------------------------------------- save
    def _on_save(self, _button) -> None:
        spec = self._current_spec()
        if spec is None:
            self._refresh_status()
            return
        if self.page.apply_bind(self.bind, spec):
            self.close()


class KeybindsPage(Adw.Bin):
    """The Keybinds tab: the full list plus add / edit / undo."""

    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self.config: keybinds.Config | None = None
        self._load_error = ""
        self._app_name_cache: dict[str, str] | None = None
        self._filter = ""
        self._rows: list[Adw.ActionRow] = []  # rows of the current render
        self._last_written: Path | None = None  # file the last edit went to

        box = build_page(
            self, "KEYBINDS  /  YOUR SHORTCUTS", "Every shortcut, in one place.",
            "See what each key does, change it, or add your own — the config is "
            "validated and backed up before anything is written.")

        self._build_status(box)
        self._build_toolbar(box)
        self._build_filter(box)
        self.reloadable = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.append(self.reloadable)

    # ------------------------------------------------------------------ build
    def _build_status(self, parent) -> None:
        group = Adw.PreferencesGroup(title="Hyprland configuration")
        self.row_config = Adw.ActionRow(title="Keybindings file", use_markup=False)
        self.row_config.set_subtitle_lines(0)
        self.row_config.add_prefix(Gtk.Image.new_from_icon_name("input-keyboard-symbolic"))
        group.add(self.row_config)

        self.row_count = Adw.ActionRow(title="Shortcuts", subtitle="Not loaded yet",
                                       use_markup=False)
        self.row_count.set_subtitle_lines(0)
        group.add(self.row_count)
        parent.append(group)

    def _build_toolbar(self, parent) -> None:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_add = Gtk.Button(label="Add a keybinding")
        self.btn_add.set_css_classes(["suggested-action", "page-primary"])
        self.btn_add.set_tooltip_text("Capture a new shortcut and choose its action")
        self.btn_add.connect("clicked", lambda *_: self._open_editor(None))
        bar.append(self.btn_add)
        bar.append(Gtk.Box(hexpand=True))

        self.btn_undo = Gtk.Button(label="Undo last change")
        self.btn_undo.add_css_class("pill")
        self.btn_undo.set_tooltip_text(
            "Restore the config file from the backup taken before the last edit")
        self.btn_undo.connect("clicked", lambda *_: self._confirm_undo())
        bar.append(self.btn_undo)

        self.btn_reload = Gtk.Button(label="Reload Hyprland")
        self.btn_reload.add_css_class("pill")
        self.btn_reload.set_tooltip_text("Run hyprctl reload so changes apply now")
        self.btn_reload.connect("clicked", lambda *_: self._reload_hyprland(True))
        bar.append(self.btn_reload)
        parent.append(bar)

    def _build_filter(self, parent) -> None:
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Filter shortcuts by keys or action…")
        self.search.connect("search-changed", lambda *_: self._on_filter_changed())
        parent.append(self.search)

    # --------------------------------------------------------------- helpers
    def app_names(self) -> dict[str, str]:
        """desktop-file path -> app name, built once (used in action summaries)."""
        if self._app_name_cache is None:
            self._app_name_cache = {
                app.desktop_file: app.name for app in keybinds.list_apps()}
        return self._app_name_cache

    # ------------------------------------------------------------------- load
    def on_navigate_to(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        path = paths.HYPRLAND_CONF
        try:
            self.config = keybinds.load_config(path)
            self._load_error = ""
        except OSError as exc:
            self.config = None
            self._load_error = str(exc)
        self._render()

    def _on_filter_changed(self) -> None:
        self._filter = self.search.get_text().strip().casefold()
        self._render()

    def _render(self) -> None:
        while child := self.reloadable.get_first_child():
            self.reloadable.remove(child)

        installed = bool(self.win.state.get("installed", True))
        if self.config is None:
            self.row_config.set_subtitle(f"{paths.HYPRLAND_CONF} — not found")
            self.row_count.set_subtitle(
                "Install Caelestia first, or copy the config into place."
                if not installed else self._load_error or "Could not read the file.")
            self._set_actions_enabled(False)
            self.reloadable.append(self._notice(
                "No Hyprland configuration yet",
                f"{paths.HYPRLAND_CONF} does not exist. Run the Install tab first; "
                "keybindings become editable once the config is deployed."))
            return

        documents = self.config.documents
        sourced = documents[1:]
        main_mod = self.config.main_mod_variable()
        shown = f"${main_mod}" if main_mod else "SUPER"
        extra = (f"  ·  +{len(sourced)} sourced file"
                 f"{'s' if len(sourced) != 1 else ''}") if sourced else ""
        self.row_config.set_subtitle(
            f"{paths.HYPRLAND_CONF}  ·  main modifier {shown}{extra}")
        self.row_config.set_tooltip_text(
            "\n".join(str(d.path) for d in documents))
        binds = self.config.binds
        duplicates = self.config.duplicates()
        self.row_count.set_subtitle(
            f"{len(binds)} shortcut{'s' if len(binds) != 1 else ''} "
            f"across {len({(b.file, b.group_title) for b in binds})} groups"
            + (f"  ·  {len(duplicates)} duplicate combination"
               f"{'s' if len(duplicates) != 1 else ''}" if duplicates else ""))
        self._set_actions_enabled(True)

        visible = [b for b in binds if self._matches(b)]
        if not visible:
            self.reloadable.append(self._notice(
                "Nothing to show" if self._filter else "No shortcuts yet",
                "No shortcut matches that filter." if self._filter
                else "Use “Add a keybinding” to capture your first shortcut."))
            return

        self._rows = []
        groups: dict[tuple, list[keybinds.Keybind]] = {}
        for bind in visible:
            groups.setdefault((bind.file, bind.group_title), []).append(bind)
        for (file, title), group_binds in groups.items():
            group = Adw.PreferencesGroup(title=title)
            # With more than one file, say which on-disk file a group lives in.
            if len(documents) > 1 and file is not None:
                group.set_description(file.name)
            for bind in group_binds:
                row = self._bind_row(bind, bind.canonical in duplicates)
                self._rows.append(row)
                group.add(row)
            self.reloadable.append(group)

    def _matches(self, bind: keybinds.Keybind) -> bool:
        if not self._filter:
            return True
        haystack = " ".join((bind.combo, bind.summary(self.app_names()),
                             bind.dispatcher, bind.args, bind.group_title))
        return self._filter in haystack.casefold()

    def _bind_row(self, bind: keybinds.Keybind,
                  duplicated: bool = False) -> Adw.ActionRow:
        summary = bind.summary(self.app_names())
        if duplicated:
            summary = f"Same keys as another shortcut · {summary}"
        row = Adw.ActionRow()
        row.set_use_markup(False)
        row.set_title(bind.combo)
        row.set_subtitle(summary)
        row.set_activatable(True)
        row.set_tooltip_text(f"{bind.combo} — click to edit")
        icon = Gtk.Image.new_from_icon_name(
            "dialog-warning-symbolic" if duplicated else "input-keyboard-symbolic")
        if duplicated:
            icon.add_css_class("warning")
            icon.set_tooltip_text("Another shortcut uses these same keys")
        row.add_prefix(icon)
        row.connect("activated", lambda *_: self._open_editor(bind))

        edit = Gtk.Button(label="Edit", valign=Gtk.Align.CENTER)
        edit.add_css_class("flat")
        edit.connect("clicked", lambda *_: self._open_editor(bind))
        row.add_suffix(edit)

        remove = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        remove.set_valign(Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.set_tooltip_text("Remove this keybinding")
        remove.connect("clicked", lambda *_: self._confirm_delete(bind))
        row.add_suffix(remove)
        return row

    @staticmethod
    def _notice(title: str, body: str) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("page-panel")
        heading = Gtk.Label(label=title, xalign=0.0)
        heading.add_css_class("page-section-title")
        detail = Gtk.Label(label=body, xalign=0.0, wrap=True)
        detail.add_css_class("page-caption")
        box.append(heading)
        box.append(detail)
        return box

    def _backup_candidates(self) -> list[Path]:
        """Files this page has edited that still have a restorable backup."""
        if self.config is None:
            return []
        candidates = [d.path for d in self.config.documents]
        if self._last_written is not None and self._last_written not in candidates:
            candidates.insert(0, self._last_written)
        return [p for p in candidates if keybinds.backup_path(p).is_file()]

    def _set_actions_enabled(self, enabled: bool) -> None:
        busy = bool(self.win.state.get("busy"))
        self.btn_add.set_sensitive(enabled and not busy)
        self.btn_undo.set_sensitive(enabled and bool(self._backup_candidates()))
        self.btn_reload.set_sensitive(
            enabled and bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")))

    # ------------------------------------------------------------------ edits
    def _open_editor(self, bind: keybinds.Keybind | None) -> None:
        if self.config is None:
            self.win.toast("No Hyprland configuration to edit yet.")
            return
        KeybindEditor(self, bind).present(self.win)

    def apply_bind(self, bind: keybinds.Keybind | None,
                   spec: keybinds.BindSpec) -> bool:
        """Validate against the live config, write, and refresh. True on success."""
        if self.config is None:
            self.win.toast("No Hyprland configuration to edit yet.")
            return False
        try:
            # Bindings inside a submap use plain keys by design, so only those
            # are allowed to skip the modifier rule.
            spec = keybinds.validate_spec(
                spec, require_modifier=not (bind is not None and bind.submap))
        except ValueError as exc:
            self.win.toast(str(exc))
            return False

        conflicts = self.config.conflicts(spec.mods, spec.key, bind)
        if conflicts:
            self.win.toast(
                f"{keybinds.format_combo(spec.mods, spec.key)} is already used by "
                f"{conflicts[0].combo} ({conflicts[0].summary(self.app_names())}).")
            return False

        if bind is None:
            # New bindings join the sourced keybinds file when the config has
            # one (the conventional home for user shortcuts), else the main one.
            target = self.config.managed_target()
            document = self.config.document_for(target)
            text = keybinds.apply_changes(
                document.text if document is not None else "", [], [spec],
                self._variable_name(target))
            action = f"added to {target.name}"
        else:
            target = bind.file or self.config.path
            document = self.config.document_for(target)
            if document is None:
                self.win.toast(f"Could not find {target} any more.")
                return False
            text = keybinds.apply_changes(
                document.text, [keybinds.Change(bind.line, spec)], [],
                self._variable_name(target))
            action = "updated"
        try:
            keybinds.save(target, text)
        except OSError as exc:
            self.win.toast(f"Could not write the keybindings: {exc}")
            return False

        self._last_written = target
        self.refresh()
        combo = keybinds.format_combo(spec.mods, spec.key)
        self.win.toast(f"{combo} {action}.")
        self._reload_hyprland(False)
        return True

    def _variable_name(self, target: Path) -> str | None:
        """``$mainMod`` is safe to reuse only inside the file that defines it.

        A sourced file might be included before the variable is set (Hyprland
        expands ``source`` where it appears), so binds written there use the
        literal modifier — which is also how sourced keybind files are usually
        written by hand.
        """
        if self.config is None or target != self.config.path:
            return None
        return self.config.main_mod_variable()

    def _confirm_delete(self, bind: keybinds.Keybind) -> None:
        target = bind.file or (self.config.path if self.config else None)
        document = self.config.document_for(target) if self.config else None
        if document is None:
            self.win.toast("That keybinding is no longer in the config.")
            return
        dialog = Adw.AlertDialog.new(
            f"Remove {bind.combo}?",
            f"This deletes the {bind.combo} keybinding "
            f"({bind.summary(self.app_names())}) from {target}. The previous "
            "file is kept as a backup, so Undo last change can bring it back.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Remove")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_dialog, choice: str) -> None:
            if choice != "delete":
                return
            text = keybinds.apply_changes(
                document.text, [keybinds.Change(bind.line, None)], [],
                self.config.main_mod_variable() if self.config else None)
            try:
                keybinds.save(target, text)
            except OSError as exc:
                self.win.toast(f"Could not write the keybindings: {exc}")
                return
            self._last_written = target
            self.refresh()
            self.win.toast(f"{bind.combo} removed.")
            self._reload_hyprland(False)

        dialog.connect("response", response)
        dialog.present(self.win)

    def _confirm_undo(self) -> None:
        candidates = self._backup_candidates()
        if not candidates:
            self.win.toast("There is no backup to restore yet.")
            return
        target = (self._last_written if self._last_written in candidates
                  else candidates[0])
        dialog = Adw.AlertDialog.new(
            "Undo the last change?",
            f"Restores {target} from the backup taken before the last edit. "
            "Anything changed in that file since then is replaced.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("undo", "Restore backup")
        dialog.set_response_appearance("undo", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("undo")
        dialog.set_close_response("cancel")

        def response(_dialog, choice: str) -> None:
            if choice != "undo":
                return
            try:
                restored = keybinds.restore_backup(target)
            except OSError as exc:
                self.win.toast(f"Could not restore the backup: {exc}")
                return
            self.refresh()
            self.win.toast("Backup restored." if restored
                           else "There is no backup to restore yet.")

        dialog.connect("response", response)
        dialog.present(self.win)

    # ---------------------------------------------------------------- reload
    def _reload_hyprland(self, announce: bool) -> None:
        """Ask a running Hyprland to reload the config (never needed elsewhere)."""
        if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            if announce:
                self.win.toast("Not in a Hyprland session — changes apply next login.")
            return None
        if shutil.which("hyprctl") is None:
            if announce:
                self.win.toast("hyprctl not found — changes apply next login.")
            return None

        def worker() -> None:
            code, output = run_capture(["hyprctl", "reload"], timeout=15)
            GLib.idle_add(self._reload_done, code, output, announce)

        threading.Thread(target=worker, daemon=True).start()
        return None

    def _reload_done(self, code: int, output: str, announce: bool) -> bool:
        if announce:
            if code == 0:
                self.win.toast("Hyprland reloaded.")
            else:
                last = (output.strip().splitlines() or [""])[-1][:80]
                self.win.toast(f"Hyprland reload failed{': ' + last if last else ''}.")
        return False
