"""Read, edit and safely persist Hyprland keybindings.

Keybindings are ``bind`` lines in ``~/.config/hypr/hyprland.conf``::

    bind = $mainMod SHIFT, L, exec, qs -c caelestia ipc call lock lock
    bindm = $mainMod, mouse:Left, movewindow

This module is deliberately GLib-only (no GTK widgets): parsing, rendering,
conflict detection, the validation rules and the atomic writer are plain
functions so they can be reasoned about and tested without a display. The two
helpers that translate a captured key press (:func:`mods_from_state`,
:func:`key_from_keyval`) and the installed-application list
(:func:`list_apps`) are the only things that need Gdk/Gio.

Security posture — this module is a *config editor*, so it matters:

* only the user's own file is ever written; nothing here uses sudo and no
  root-owned path is touched;
* every write is atomic (temp file + ``rename``) and keeps a rolling backup,
  so a bad edit is always recoverable through the page's Undo action;
* generated values are rejected when they contain a newline, NUL or ``#`` —
  those would break out of the generated line and either inject extra config
  or silently truncate the command;
* nothing here executes a command: commands are only *stored* for Hyprland to
  run after the user's own ``hyprctl reload``.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, Gio  # noqa: E402

#: Marker comment introducing the block of binds this app appends.
SECTION_HEADER = "# --- Custom keybinds (managed by Caelestia for Ubuntu) ---"

# ``bind``, ``bindm``, ``bindr``, ``binde``, ``bindn`` … The one-letter flags
# are combinable, so accept any ``bind`` + letters keyword.
_BIND_RE = re.compile(r"^(?P<indent>\s*)(?P<keyword>bind[a-z]*)\s*=\s*(?P<value>.*)$")
_VAR_RE = re.compile(r"^\s*\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$")
_SUBMAP_RE = re.compile(r"^\s*submap\s*=\s*(?P<name>\S+)")
# ``source = ~/.config/hypr/keybinds.conf`` — Hyprland's include directive.
_SOURCE_RE = re.compile(r"^\s*source\s*=\s*(?P<path>[^#\s].*)$")

#: Characters that would break out of a generated configuration line.
_FORBIDDEN = ("\n", "\r", "\x00", "#")

#: Modifier tokens we understand, in the order they are written back.
MOD_ORDER = ("SUPER", "CTRL", "ALT", "SHIFT")

_MOD_ALIASES = {
    "SUPER": "SUPER", "MOD4": "SUPER", "META": "SUPER", "WIN": "SUPER",
    "CTRL": "CTRL", "CONTROL": "CTRL",
    "ALT": "ALT", "MOD1": "ALT",
    "SHIFT": "SHIFT",
}

#: Modifier keyvals — pressing one of these moves the "is a modifier held"
#: state without completing a shortcut.
MODIFIER_KEYVALS = frozenset({
    Gdk.KEY_Shift_L, Gdk.KEY_Shift_R,
    Gdk.KEY_Control_L, Gdk.KEY_Control_R,
    Gdk.KEY_Alt_L, Gdk.KEY_Alt_R,
    Gdk.KEY_Super_L, Gdk.KEY_Super_R,
    Gdk.KEY_Meta_L, Gdk.KEY_Meta_R,
    Gdk.KEY_Hyper_L, Gdk.KEY_Hyper_R,
    Gdk.KEY_Caps_Lock, Gdk.KEY_Num_Lock,
})

_MOD_MASKS = (
    (Gdk.ModifierType.SUPER_MASK, "SUPER"),
    (Gdk.ModifierType.CONTROL_MASK, "CTRL"),
    (Gdk.ModifierType.ALT_MASK, "ALT"),
    (Gdk.ModifierType.SHIFT_MASK, "SHIFT"),
)

#: Shifted symbols map back to the key that produces them, because a bind for
#: ``SUPER SHIFT 1`` is written with the *unshifted* name (the shipped config
#: does exactly that).
_SHIFTED_SYMBOLS = {
    "exclam": "1", "at": "2", "numbersign": "3", "dollar": "4", "percent": "5",
    "asciicircum": "6", "ampersand": "7", "asterisk": "8", "parenleft": "9",
    "parenright": "0", "underscore": "minus", "plus": "equal",
    "braceleft": "bracketleft", "braceright": "bracketright",
    "bar": "backslash", "colon": "semicolon", "quotedbl": "apostrophe",
    "less": "comma", "greater": "period", "question": "slash",
    "asciitilde": "grave",
}

#: Keyval names whose Hyprland spelling differs from GDK's.
_SPECIAL_KEYS = {
    "space": "SPACE",
    "Return": "Return",
    "KP_Enter": "Return",
    "Escape": "escape",
    "ISO_Left_Tab": "Tab",
    "Page_Up": "Page_Up",
    "Page_Down": "Page_Down",
}

#: Friendly labels for the dispatchers shown in the action column.
_DISPATCHER_LABELS = {
    "exec": "Run",
    "killactive": "Close the active window",
    "togglefloating": "Toggle floating",
    "fullscreen": "Toggle fullscreen",
    "workspace": "Go to workspace",
    "movetoworkspace": "Move the window to workspace",
    "moveToWorkspace": "Move the window to workspace",
    "moveactive": "Move the active window",
    "resizeactive": "Resize the active window",
    "submap": "Enter the submap",
    "movewindow": "Move the window with the mouse",
    "resizewindow": "Resize the window with the mouse",
    "exit": "Quit Hyprland",
}


# ---------------------------------------------------------------------------
# Key press -> Hyprland notation
# ---------------------------------------------------------------------------

def mods_from_state(state) -> list[str]:
    """Canonical modifier list for a Gdk.ModifierType ``state``."""
    mods = [name for mask, name in _MOD_MASKS if state & mask]
    return sort_mods(mods)


def sort_mods(mods) -> list[str]:
    """Deduplicate and order modifiers the way the config writes them."""
    wanted = {m.upper() for m in mods}
    return [name for name in MOD_ORDER if name in wanted]


def key_from_keyval(keyval) -> str | None:
    """Hyprland key name for a captured keyval, or ``None`` if unmappable."""
    name = Gdk.keyval_name(keyval)
    if not name:
        return None
    name = _SHIFTED_SYMBOLS.get(name, name)
    if len(name) == 1:
        return name.upper() if name.isalpha() else name
    return _SPECIAL_KEYS.get(name, name)


#: Keys whose friendly name is worth spelling out in the list.
_KEY_LABELS = {
    "SPACE": "Space", "Return": "Enter", "escape": "Esc", "Tab": "Tab",
    "BackSpace": "Backspace", "Delete": "Delete", "Print": "Print",
    "mouse:Left": "Left click", "mouse:Right": "Right click",
    "mouse:Middle": "Middle click",
}

_MOD_LABELS = {"SUPER": "Super", "CTRL": "Ctrl", "ALT": "Alt", "SHIFT": "Shift"}


def normalize_key_name(key: str) -> str:
    """Canonical spelling for a key typed by hand or captured from a press."""
    key = key.strip()
    if not key:
        return ""
    if key.casefold() == "esc":
        return "escape"
    if key.casefold() in ("enter", "return"):
        return "Return"
    if key.casefold() == "space":
        return "SPACE"
    if key.casefold() == "tab":
        return "Tab"
    lowered = _SHIFTED_SYMBOLS.get(key.casefold(), key)
    if len(lowered) == 1 and lowered.isalpha():
        return lowered.upper()
    return lowered


def format_combo(mods, key: str, friendly: bool = True) -> str:
    """``["SUPER", "SHIFT"], "L"`` -> ``"Super + Shift + L"``.

    ``friendly=False`` produces the configuration spelling (``SUPER SHIFT L``),
    which is what the editor stores and :func:`parse_combo` reads back.
    """
    parts = [(_MOD_LABELS.get(m, m) if friendly else m) for m in sort_mods(mods)]
    key = normalize_key_name(key)
    if key:
        parts.append((_KEY_LABELS.get(key, key) if friendly else key))
    return (" + " if friendly else " ").join(parts)


def parse_mods(text: str) -> tuple[str, ...]:
    """Modifier tokens from a bind's modifier field.

    Hyprland's own configs separate modifiers with spaces (``SUPER SHIFT``),
    but hand-written and tool-generated files in the wild also use ``+`` — so
    accept both and keep only the tokens that name a real modifier.
    """
    mods: list[str] = []
    for token in re.split(r"[+\s]+", text):
        mapped = _MOD_ALIASES.get(token.upper())
        if mapped and mapped not in mods:
            mods.append(mapped)
    return tuple(sort_mods(mods))


def parse_combo(text: str) -> tuple[list[str], str]:
    """Parse ``"Super+Shift+L"`` / ``"SUPER SHIFT L"`` into (mods, key).

    The last token that is not a recognised modifier is the key, so a
    ``mouse:Left`` key survives the round trip.
    """
    tokens = [t for t in re.split(r"[+\s]+", text.strip()) if t]
    mods: list[str] = []
    key = ""
    for token in tokens:
        mapped = _MOD_ALIASES.get(token.upper())
        # A lone modifier-only string is not a shortcut; treat it as the key
        # so validation can reject it with a helpful message.
        if mapped and len(tokens) > 1:
            if mapped not in mods:
                mods.append(mapped)
        else:
            key = token
    return sort_mods(mods), normalize_key_name(key)


def canonical(mods, key: str) -> tuple[frozenset, str]:
    """Comparison key for conflict detection (order- and case-insensitive)."""
    return frozenset(sort_mods(mods)), normalize_key_name(key).casefold()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BindSpec:
    """A keybinding ready to be written."""

    mods: tuple[str, ...]
    key: str
    dispatcher: str
    args: str = ""
    keyword: str = "bind"

    @classmethod
    def build(cls, mods, key: str, dispatcher: str, args: str = "",
              mouse: bool = False) -> "BindSpec":
        return cls(mods=tuple(sort_mods(mods)), key=normalize_key_name(key),
                   dispatcher=dispatcher.strip(), args=args.strip(),
                   keyword="bindm" if mouse else "bind")


@dataclass
class Keybind:
    """One parsed ``bind`` line, addressed by line number *within its file*."""

    line: int
    keyword: str
    mods: tuple[str, ...]
    key: str
    dispatcher: str
    args: str
    section: str = ""
    submap: str = ""
    raw: str = ""
    file: Path | None = None

    @property
    def is_mouse(self) -> bool:
        return self.key.casefold().startswith("mouse:") or self.keyword == "bindm"

    @property
    def canonical(self) -> tuple[frozenset, str]:
        return canonical(self.mods, self.key)

    @property
    def combo(self) -> str:
        return format_combo(self.mods, self.key)

    @property
    def group_title(self) -> str:
        if self.submap:
            return f"{self.submap} submap"
        return self.section or "Keybindings"

    def summary(self, app_names: dict[str, str] | None = None) -> str:
        return action_summary(self.dispatcher, self.args, app_names)

    def spec(self) -> BindSpec:
        return BindSpec(mods=self.mods, key=self.key, dispatcher=self.dispatcher,
                        args=self.args, keyword=self.keyword)


@dataclass
class Document:
    """One parsed configuration file (``hyprland.conf`` or a sourced file)."""

    path: Path
    text: str
    variables: dict[str, str] = field(default_factory=dict)
    binds: list[Keybind] = field(default_factory=list)

    def main_mod_variable(self) -> str | None:
        """Name of the variable that stands in for ``SUPER`` (usually mainMod)."""
        for name, value in self.variables.items():
            if resolve_variables(value, self.variables).strip().upper() == "SUPER":
                return name
        return None

    def conflicts(self, mods, key: str,
                  exclude: Keybind | None = None) -> list[Keybind]:
        target = canonical(mods, key)
        return [b for b in self.binds
                if b is not exclude and b.canonical == target]


@dataclass
class Config:
    """``hyprland.conf`` plus every file it ``source``-includes.

    Hyprland configs routinely keep the actual binds in a sourced file (the
    test machine's setup keeps them in ``keybinds.conf``). Reading the whole
    tree is what makes the page show *every* shortcut, and each edit is written
    back to the file the binding came from.
    """

    path: Path
    documents: list[Document] = field(default_factory=list)
    #: Every path referenced by a ``source`` line, existing or not.
    sources: list[Path] = field(default_factory=list)

    @property
    def main(self) -> Document:
        return self.documents[0]

    @property
    def binds(self) -> list[Keybind]:
        return [b for doc in self.documents for b in doc.binds]

    def main_mod_variable(self) -> str | None:
        return self.main.main_mod_variable()

    def document_for(self, path: Path | None) -> Document | None:
        if path is None:
            return None
        wanted = Path(path).expanduser()
        return next((d for d in self.documents if d.path == wanted), None)

    def conflicts(self, mods, key: str,
                  exclude: Keybind | None = None) -> list[Keybind]:
        target = canonical(mods, key)
        return [b for b in self.binds
                if b is not exclude and b.canonical == target]

    def duplicates(self) -> set[tuple[frozenset, str]]:
        """Canonical keys bound more than once — the file already has a clash."""
        seen: dict[tuple, int] = {}
        for bind in self.binds:
            seen[bind.canonical] = seen.get(bind.canonical, 0) + 1
        return {key for key, count in seen.items() if count > 1}

    def managed_target(self) -> Path:
        """Where a newly added keybinding belongs.

        A sourced file whose name says "keybind" is the conventional home for
        user shortcuts, so new ones join it (creating it if it does not exist
        yet); otherwise the main file is used.
        """
        for candidate in self.sources:
            if "keybind" in Path(candidate).name.casefold():
                return candidate
        return self.path


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def resolve_variables(value: str, variables: dict[str, str],
                      depth: int = 5) -> str:
    """Expand ``$name`` references inside ``value``."""
    out = value
    for _ in range(depth):
        if "$" not in out:
            break
        changed = False

        def swap(match: re.Match) -> str:
            nonlocal changed
            name = match.group(1)
            if name in variables:
                changed = True
                return variables[name]
            return match.group(0)

        out = re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", swap, out)
        if not changed:
            break
    return out


def _clean_section(comment: str) -> str:
    text = comment.lstrip("#").strip()
    text = text.strip("-").strip()
    return re.sub(r"\s+", " ", text)


def _is_blank(line: str) -> bool:
    return not line.strip()


def parse(text: str, path: Path | None = None,
          variables: dict[str, str] | None = None) -> Document:
    """Parse ``text`` into a :class:`Document` (binds, variables, sections).

    A comment introduces a *section* only when it starts the file or follows a
    blank line, so a one-line note sitting directly above a bind ("# Uses
    letter keys…") does not replace the heading that groups it.

    ``variables`` lets a sourced file share the variables the main config
    defined (Hyprland's ``source`` runs in the same context, so ``$mainMod``
    resolves inside an included file too).
    """
    variables = variables if variables is not None else {}
    binds: list[Keybind] = []
    section = ""
    submap = ""
    previous_blank = True

    for index, line in enumerate(text.split("\n")):
        var = _VAR_RE.match(line)
        if var:
            variables[var.group("name")] = var.group("value").strip()
            previous_blank = _is_blank(line)
            continue

        sub = _SUBMAP_RE.match(line)
        if sub:
            submap = "" if sub.group("name") == "reset" else sub.group("name")
            previous_blank = _is_blank(line)
            continue

        bind = _BIND_RE.match(line)
        if not bind:
            stripped = line.strip()
            if stripped.startswith("#") and previous_blank:
                cleaned = _clean_section(stripped)
                if cleaned:
                    section = cleaned
            previous_blank = _is_blank(line)
            continue

        previous_blank = _is_blank(line)

        fields = bind.group("value").split(",", 3)
        raw_mods = fields[0].strip() if fields else ""
        mods = parse_mods(resolve_variables(raw_mods, variables))
        key = fields[1].strip() if len(fields) > 1 else ""
        dispatcher = fields[2].strip() if len(fields) > 2 else ""
        args = fields[3].strip() if len(fields) > 3 else ""
        if not key:
            continue
        binds.append(Keybind(
            line=index, keyword=bind.group("keyword"), mods=tuple(mods), key=key,
            dispatcher=dispatcher, args=args, section=section, submap=submap,
            raw=line, file=path,
        ))

    return Document(path=path or Path("hyprland.conf"), text=text,
                    variables=dict(variables), binds=binds)


# ---------------------------------------------------------------------------
# Validating and rendering
# ---------------------------------------------------------------------------

def _reject_forbidden(value: str, what: str) -> str:
    for char in _FORBIDDEN:
        if char in value:
            shown = "a newline" if char in ("\n", "\r") else repr(char)
            raise ValueError(f"{what} cannot contain {shown}.")
    return value.strip()


def validate_spec(spec: BindSpec, require_modifier: bool = True) -> BindSpec:
    """Return a cleaned spec, or raise :class:`ValueError` with a reason.

    ``require_modifier=False`` is for bindings inside a submap, where plain
    keys are the point (the submap already isolates them from normal typing).
    """
    key = normalize_key_name(spec.key)
    if not key:
        raise ValueError("Press a key combination first.")
    if (require_modifier and not spec.mods
            and not key.casefold().startswith("mouse:")):
        raise ValueError("A keybinding needs at least one modifier (Super, Ctrl, Alt…).")
    if not spec.dispatcher:
        raise ValueError("Choose what the keybinding should do.")
    _reject_forbidden(key, "The key")
    _reject_forbidden(spec.dispatcher, "The action")
    _reject_forbidden(spec.args, "The action details")
    return BindSpec(mods=tuple(sort_mods(spec.mods)), key=key,
                    dispatcher=spec.dispatcher.strip(), args=spec.args.strip(),
                    keyword=spec.keyword if spec.keyword.startswith("bind") else "bind")


def format_mods(mods, main_mod: str | None = None) -> str:
    """Modifiers as written into the file — ``$mainMod`` when it is available."""
    tokens = []
    for name in sort_mods(mods):
        if name == "SUPER" and main_mod:
            tokens.append(f"${main_mod}")
        else:
            tokens.append(name)
    return " ".join(tokens)


def render_bind(spec: BindSpec, main_mod: str | None = None) -> str:
    """The configuration line for ``spec``.

    Modifiers are always written space-separated, which is the documented
    Hyprland form (reading ``+`` is supported for compatibility, below).
    """
    parts = [format_mods(spec.mods, main_mod), spec.key, spec.dispatcher]
    if spec.args:
        parts.append(spec.args)
    return f"{spec.keyword} = " + ", ".join(parts)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

@dataclass
class Change:
    """Replace ``line`` with ``spec`` — or delete it when ``spec`` is ``None``."""

    line: int
    spec: BindSpec | None


def apply_changes(text: str, changes: list[Change],
                  additions: list[BindSpec] | None = None,
                  main_mod: str | None = None) -> str:
    """Rewrite ``text`` with the edits applied, preserving every other line."""
    by_line = {c.line: c for c in changes}
    out: list[str] = []
    for index, line in enumerate(text.split("\n")):
        change = by_line.get(index)
        if change is None:
            out.append(line)
            continue
        if change.spec is None:
            continue  # deleted
        indent = line[:len(line) - len(line.lstrip())]
        out.append(indent + render_bind(change.spec, main_mod))

    result = "\n".join(out)
    if additions:
        if not result.endswith("\n"):
            result += "\n"
        if SECTION_HEADER not in result:
            result += f"\n{SECTION_HEADER}\n"
        for spec in additions:
            result += render_bind(spec, main_mod) + "\n"
    return result


def backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def save(path: Path, text: str, keep_backup: bool = True) -> Path | None:
    """Atomically write ``text`` to ``path``, backing the old file up first.

    The write goes to a sibling temp file and is then renamed into place, so an
    interrupted write can never leave a half-written config behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    backup = None
    if keep_backup and path.exists():
        backup = backup_path(path)
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.chmod(mode)
    tmp.replace(path)
    return backup


def load(path: Path) -> Document:
    """Read and parse a single file."""
    return parse(path.read_text(), path)


def source_paths(text: str, base: Path) -> list[Path]:
    """Every file a config ``source``-includes, resolved and in order."""
    found: list[Path] = []
    for line in text.split("\n"):
        match = _SOURCE_RE.match(line)
        if not match:
            continue
        raw = match.group("path").strip().strip('"').strip("'")
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = base / path
        found.append(path)
    return found


def load_config(path: Path) -> Config:
    """Read ``hyprland.conf`` and every file it sources (recursively).

    Raises :class:`OSError` when the main file itself is missing; sourced files
    that are absent are simply skipped, and their paths are still recorded so a
    new keybinding block can be created in them.
    """
    path = Path(path).expanduser()
    variables: dict[str, str] = {}
    documents = [parse(path.read_text(), path, variables)]
    sources: list[Path] = []
    seen = {path}
    queue = source_paths(documents[0].text, path.parent)
    while queue:
        candidate = queue.pop(0)
        sources.append(candidate)
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        try:
            document = parse(candidate.read_text(), candidate, variables)
        except OSError:
            continue
        documents.append(document)
        queue.extend(source_paths(document.text, candidate.parent))
    return Config(path=path, documents=documents, sources=sources)


def restore_backup(path: Path) -> bool:
    """Put the rolling backup back in place. Returns ``False`` if there is none."""
    backup = backup_path(path)
    if not backup.is_file():
        return False
    save(path, backup.read_text(), keep_backup=False)
    return True


# ---------------------------------------------------------------------------
# Actions and installed applications
# ---------------------------------------------------------------------------

def launch_target(args: str) -> str:
    """The desktop-file path inside a ``gio launch <file>`` command."""
    target = args.strip()
    if target.startswith("gio launch "):
        target = target[len("gio launch "):].strip()
    return target.strip('"').strip("'")


def app_name_from_command(args: str, app_names: dict[str, str] | None = None) -> str:
    """Friendly app name for a ``gio launch <desktop file>`` command."""
    target = launch_target(args)
    if app_names and target in app_names:
        return app_names[target]
    return Path(target).name.removesuffix(".desktop")


def action_summary(dispatcher: str, args: str,
                   app_names: dict[str, str] | None = None) -> str:
    """Human sentence for a dispatcher + args pair."""
    if not dispatcher:
        return "No action"
    if dispatcher == "exec":
        if args.strip().startswith("gio launch"):
            return f"Launch {app_name_from_command(args, app_names)}"
        return f"Run: {args}" if args else "Run a command"
    label = _DISPATCHER_LABELS.get(dispatcher)
    if label is None:
        label = dispatcher
    return f"{label} {args}".strip() if args else label


@dataclass(frozen=True)
class DesktopAction:
    """A curated, ready-made Hyprland action."""

    label: str
    dispatcher: str
    args: str = ""
    description: str = ""

    def summary(self, app_names: dict[str, str] | None = None) -> str:
        return action_summary(self.dispatcher, self.args, app_names)


def _actions() -> tuple[DesktopAction, ...]:
    items = [
        DesktopAction("Open the launcher", "exec",
                      "qs -c caelestia ipc call drawers toggle launcher",
                      "Apps, >calc, >wallpaper and >scheme"),
        DesktopAction("Toggle the sidebar", "exec",
                      "qs -c caelestia ipc call drawers toggle sidebar",
                      "Notifications and quick controls"),
        DesktopAction("Lock the screen", "exec",
                      "qs -c caelestia ipc call lock lock",
                      "Caelestia lock — fingerprint works"),
        DesktopAction("Open the power menu", "exec", "wlogout",
                      "Logout, reboot, shutdown"),
        DesktopAction("Close the active window", "killactive"),
        DesktopAction("Toggle floating", "togglefloating"),
        DesktopAction("Toggle fullscreen", "fullscreen"),
        DesktopAction("Move the window left", "moveactive", "-20 0"),
        DesktopAction("Move the window right", "moveactive", "20 0"),
        DesktopAction("Move the window up", "moveactive", "0 -20"),
        DesktopAction("Move the window down", "moveactive", "0 20"),
        DesktopAction("Screenshot a region", "exec",
                      "hyprshot -m region -o ~/Pictures/Screenshots"),
        DesktopAction("Screenshot the active window", "exec",
                      "hyprshot -m window -o ~/Pictures/Screenshots"),
        DesktopAction("Screenshot the whole screen", "exec",
                      "hyprshot -m output -o ~/Pictures/Screenshots"),
    ]
    for number in range(1, 11):
        items.append(DesktopAction(f"Go to workspace {number}", "workspace",
                                   str(number)))
        items.append(DesktopAction(
            f"Move the window to workspace {number}", "movetoworkspace",
            str(number)))
    return tuple(items)


#: Curated actions offered by the editor's "Desktop action" picker.
ACTIONS: tuple[DesktopAction, ...] = _actions()


@dataclass(frozen=True)
class AppEntry:
    """An installed application, ready to become a keybinding action."""

    name: str
    app_id: str
    desktop_file: str

    def command(self) -> str:
        return launch_command(self.desktop_file)


def launch_command(desktop_file: str) -> str:
    """``gio launch`` command for a desktop file — safe for every app type.

    ``gio`` (GLib) resolves flatpak apps and desktop-file field codes exactly
    like the application menu does, which a raw ``Exec`` line copied out of a
    ``.desktop`` file does not.
    """
    if re.search(r"[^A-Za-z0-9@%_+=:,./-]", desktop_file):
        return f'gio launch "{desktop_file}"'
    return f"gio launch {desktop_file}"


def list_apps() -> list[AppEntry]:
    """Every installed application the app menu would show, name-sorted."""
    found: dict[str, AppEntry] = {}
    try:
        infos = Gio.AppInfo.get_all()
    except Exception:  # noqa: BLE001 - never let enumeration break the page
        return []
    for info in infos:
        try:
            if not info.should_show():
                continue
            desktop_file = info.get_filename()
            if not desktop_file:
                continue
            app_id = info.get_id() or Path(desktop_file).name
            name = (info.get_display_name() or info.get_name()
                    or Path(desktop_file).stem)
        except Exception:  # noqa: BLE001
            continue
        if app_id in found:
            continue
        found[app_id] = AppEntry(name=name.strip(), app_id=app_id,
                                 desktop_file=desktop_file)
    return sorted(found.values(), key=lambda a: a.name.casefold())
