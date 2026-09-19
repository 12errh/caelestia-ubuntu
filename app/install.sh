#!/usr/bin/env bash
# ============================================================================
#  caelestia-installer (GUI) — system installer for the graphical app itself.
#  Installs the Python/GTK app into /usr/local so it appears in the app grid,
#  and copies the repository scripts+configs next to it so the app works
#  even if the clone is later deleted.
#
#  Usage:  sudo ./app/install.sh          (from the repository root)
#          ./app/install.sh --user        (no sudo: current user only)
# ============================================================================
set -euo pipefail

MODE="system"
[ "${1:-}" = "--user" ] && MODE="user"

APP_ID="io.github.CaelestiaUbuntu.Installer"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"     # .../app
REPO="$(cd "$SRC/.." && pwd)"                            # repo root

log() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

# Refuse to shadow the packaged app. /usr/local (and ~/.local) are searched
# before /usr in $PATH and $XDG_DATA_DIRS, so this install would silently win
# over the .deb: the grid would launch whichever copy it found first and keep
# the other one's cached icon. Mixing methods is the one thing that breaks it,
# so make it impossible rather than merely documented.
if command -v dpkg-query >/dev/null 2>&1 && \
   dpkg-query -W -f='${Status}' caelestia-installer 2>/dev/null | grep -q 'install ok installed'; then
    pkgver=$(dpkg-query -W -f='${Version}' caelestia-installer 2>/dev/null || echo unknown)
    cat >&2 <<EOF
==> The caelestia-installer package is already installed (version $pkgver).

    Installing from source as well would shadow it: /usr/local and ~/.local are
    searched before /usr, so whichever copy is not the package would keep
    launching the older app and its old icon.

    Use one method:
      # keep the package (recommended)
      sudo apt install ./caelestia-installer_VERSION_all.deb

      # or drop the package first, then install from this clone
      sudo apt remove caelestia-installer
      sudo ./app/install.sh
EOF
    exit 1
fi

if [ "$MODE" = "system" ]; then
    PREFIX="/usr/local"
    BINDIR="$PREFIX/bin"
    SHARE="$PREFIX/share/caelestia-installer"
else
    PREFIX="$HOME/.local"
    BINDIR="$PREFIX/bin"
    SHARE="$PREFIX/share/caelestia-installer"
fi

log "installing caelestia-installer ($MODE) from $REPO"

# 1. the app package ---------------------------------------------------------
mkdir -p "$SHARE"
rm -rf "$SHARE/app"
mkdir -p "$SHARE/app"
cp -a "$SRC/caelestia_installer" "$SHARE/app/"
cp -a "$SRC/run.py" "$SHARE/app/"
rm -rf "$SHARE/repo"
mkdir -p "$SHARE/repo"
cp -a "$REPO/setup.sh" "$REPO/update.sh" "$REPO/uninstall.sh" \
      "$REPO/revisions.conf" "$SHARE/repo/"
# Copy the *contents* of configs/ into repo/configs (previously this nested a
# second configs/ directory, which broke setup.sh when run from the install).
cp -a "$REPO/configs" "$SHARE/repo/"

# 2. launcher + icon ----------------------------------------------------------
mkdir -p "$BINDIR" "$PREFIX/share/applications" "$PREFIX/share/icons/hicolor/128x128/apps"
cat > "$BINDIR/caelestia-installer" <<EOF
#!/usr/bin/env bash
export CAEL_REPO_DIR="$SHARE/repo"
exec python3 "$SHARE/app/run.py" "\$@"
EOF
chmod +x "$BINDIR/caelestia-installer"
cp "$SRC/data/$APP_ID.desktop" "$PREFIX/share/applications/"
# Older versions of this script installed a scalable SVG that outranks the PNG
# for every size except exactly 128px, so a stale copy would keep the retired
# glyph in the app grid. Clear every variant before installing the current one.
rm -f "$PREFIX"/share/icons/hicolor/*/apps/"$APP_ID".*
cp "$SRC/data/$APP_ID.png" "$PREFIX/share/icons/hicolor/128x128/apps/"
# Depend on this prefix's launcher, not on $PATH order.
sed -i "s|^Exec=.*|Exec=$BINDIR/caelestia-installer|" \
    "$PREFIX/share/applications/$APP_ID.desktop"

# 3. runtime deps for the GUI itself -------------------------------------------
if [ "$MODE" = "system" ] && command -v apt-get >/dev/null 2>&1; then
    log "ensuring GTK4/libadwaita runtime (python3-gi, gir bindings)"
    sudo apt-get install -y python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 || \
        log "could not apt-install GUI runtime — install it manually if the app won't start"
fi

# 4. refresh caches ------------------------------------------------------------
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$PREFIX/share/applications" || true
command -v gtk4-update-icon-cache >/dev/null 2>&1 && gtk4-update-icon-cache -qtf "$PREFIX/share/icons/hicolor" || true

log "done. launch 'Caelestia for Ubuntu' from the app grid, or run: caelestia-installer"
