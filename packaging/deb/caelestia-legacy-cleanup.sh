#!/usr/bin/env bash
# ============================================================================
#  caelestia-legacy-cleanup.sh — remove an `app/install.sh` install that
#  shadows the packaged one.
#
#  Why this exists: `sudo ./app/install.sh` installs the app into /usr/local,
#  and /usr/local is searched *before* /usr in both $PATH and $XDG_DATA_DIRS.
#  A .deb install therefore loses to it —
#
#    * `Exec=caelestia-installer` resolves to /usr/local/bin, which runs the
#      older copy of the app under /usr/local/share, and
#    * the older install's desktop entry and icons shadow the packaged ones
#      (the pre-1.1.1 scalable SVG kept winning the theme lookup at every size
#      except exactly 128px, so the app grid showed the retired glyph).
#
#  Only files this project's install.sh creates are removed, and each one is
#  positively identified first, so an unrelated /usr/local file is never
#  touched. Running it when there is nothing to clean is a no-op.
#
#  Usage:  caelestia-legacy-cleanup.sh [--prefix /usr/local] [--quiet]
# ============================================================================
set -euo pipefail

APP_ID="io.github.CaelestiaUbuntu.Installer"
PREFIX="/usr/local"
QUIET=0

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX="${2:?--prefix needs a directory}"; shift 2 ;;
        --quiet)  QUIET=1; shift ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "caelestia-legacy-cleanup: unknown argument: $1" >&2; exit 2 ;;
    esac
done

say() { [ "$QUIET" = 1 ] || echo "  $*"; }
REMOVED=0

BIN="$PREFIX/bin/caelestia-installer"
SHARE="$PREFIX/share/caelestia-installer"
DESKTOP="$PREFIX/share/applications/$APP_ID.desktop"

# 1. The launcher — only when it is install.sh's (it execs the app under the
#    same prefix). A launcher the user wrote themselves is left alone.
if [ -f "$BIN" ] && grep -q 'caelestia-installer/app/run.py' "$BIN" 2>/dev/null; then
    rm -f "$BIN"
    say "removed legacy launcher: $BIN"
    REMOVED=1
fi

# 2. The payload directory — only when it is clearly this project's copy.
if [ -f "$SHARE/app/run.py" ] && [ -d "$SHARE/repo" ]; then
    rm -rf "$SHARE"
    say "removed legacy app copy: $SHARE"
    REMOVED=1
fi

# 3. The desktop entry that shadows the packaged one.
if [ -f "$DESKTOP" ] && grep -q '^Name=Caelestia for Ubuntu$' "$DESKTOP" 2>/dev/null; then
    rm -f "$DESKTOP"
    say "removed legacy desktop entry: $DESKTOP"
    REMOVED=1
fi

# 4. Icons at every size and format — including the retired scalable SVG that
#    wins the theme lookup at the sizes the shell actually renders.
for icon in "$PREFIX"/share/icons/hicolor/*/apps/"$APP_ID".*; do
    [ -e "$icon" ] || continue
    rm -f "$icon"
    say "removed legacy icon: $icon"
    REMOVED=1
done

# 5. Refresh the caches of the prefix just touched, so a stale icon-theme.cache
#    cannot keep serving a removed icon.
if [ -d "$PREFIX/share/icons/hicolor" ]; then
    if command -v gtk4-update-icon-cache >/dev/null 2>&1; then
        gtk4-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" 2>/dev/null || true
    elif command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" 2>/dev/null || true
    fi
fi
if [ -d "$PREFIX/share/applications" ] && command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q "$PREFIX/share/applications" 2>/dev/null || true
fi

if [ "$REMOVED" = 0 ]; then
    say "no legacy install found under $PREFIX"
fi
exit 0
