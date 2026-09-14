#!/usr/bin/env bash
# ============================================================================
#  build-deb.sh — build the caelestia-installer .deb (no debhelper required).
#
#  Usage:  ./packaging/build-deb.sh
#  Output: dist/caelestia-installer_<version>_all.deb
#
#  Installs the app to /usr/share/caelestia-installer (app + a read-only copy
#  of the repo scripts/configs) and a launcher in /usr/bin. The launcher seeds
#  a user-writable repo copy under ~/.local/share so pin keep/revert works
#  without root.
# ============================================================================
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"

command -v dpkg-deb >/dev/null 2>&1 || { echo "dpkg-deb not found (install dpkg-dev)" >&2; exit 1; }

VERSION="${CAELESTIA_INSTALLER_VERSION:-}"
if [ -z "$VERSION" ]; then
    VERSION="$(python3 -c "import sys; sys.path.insert(0, '$repo/app'); from caelestia_installer import VERSION; print(VERSION)")"
fi
PKG="caelestia-installer"
OUT="$repo/dist"
STAGE="$OUT/${PKG}_${VERSION}_all"

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/usr/bin" \
         "$STAGE/usr/share/caelestia-installer/app" \
         "$STAGE/usr/share/caelestia-installer/repo" \
         "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps"

# --- payload ---------------------------------------------------------------
install -m 0755 "$here/deb/caelestia-installer" "$STAGE/usr/bin/caelestia-installer"

cp -a "$repo/app/caelestia_installer" "$STAGE/usr/share/caelestia-installer/app/"
cp -a "$repo/app/run.py"             "$STAGE/usr/share/caelestia-installer/app/"

cp -a "$repo/setup.sh" "$repo/update.sh" "$repo/uninstall.sh" \
      "$repo/revisions.conf" "$STAGE/usr/share/caelestia-installer/repo/"
cp -a "$repo/configs" "$STAGE/usr/share/caelestia-installer/repo/"

install -m 0644 "$repo/app/data/io.github.CaelestiaUbuntu.Installer.desktop" \
    "$STAGE/usr/share/applications/io.github.CaelestiaUbuntu.Installer.desktop"
install -m 0644 "$repo/app/data/io.github.CaelestiaUbuntu.Installer.svg" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/io.github.CaelestiaUbuntu.Installer.svg"

find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$STAGE" -name '*.pyc' -delete

# --- control ---------------------------------------------------------------
size_kb="$(du -sk "$STAGE" | cut -f1)"
sed -e "s/@VERSION@/$VERSION/" -e "s/@SIZE@/$size_kb/" \
    "$here/deb/control.in" > "$STAGE/DEBIAN/control"
install -m 0755 "$here/deb/postinst" "$STAGE/DEBIAN/postinst"
install -m 0755 "$here/deb/postrm"   "$STAGE/DEBIAN/postrm"

mkdir -p "$OUT"
deb="$OUT/${PKG}_${VERSION}_all.deb"
dpkg-deb --root-owner-group --build "$STAGE" "$deb"

echo
echo "built: $deb"
dpkg-deb --info "$deb" | sed 's/^/  /'
