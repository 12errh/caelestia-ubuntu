#!/usr/bin/env bash
# ============================================================================
#  caelestia-ubuntu — uninstaller
#  Removes everything setup.sh installed. User configs are NEVER deleted;
#  they are moved to ~/.config/caelestia-ubuntu-uninstalled-<date> instead.
#  GNOME was never touched, so removing this returns the system to stock.
#
#  Usage: ./uninstall.sh [--purge-qt]
# ============================================================================
set -euo pipefail

PURGE_QT=0
[ "${1:-}" = "--purge-qt" ] && PURGE_QT=1

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !\033[0m %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

log "stopping services"
systemctl --user stop caelestia-shell.service 2>/dev/null || true
systemctl --user disable caelestia-shell.service 2>/dev/null || true

log "removing systemd units and sleep hooks"
rm -f "$HOME/.config/systemd/user/caelestia-shell.service"
rm -rf "$HOME/.config/systemd/user/caelestia-shell.service.d"
sudo rm -f /usr/lib/systemd/system-sleep/caelestia-relock-marker.sh \
           /usr/lib/systemd/system-sleep/caelestia-shell-restart.sh
systemctl --user daemon-reload 2>/dev/null || true

log "removing installed binaries, QML modules and libraries"
rm -f "$HOME/.local/bin/caelestia-relock.sh"
sudo rm -f /usr/local/bin/qs /usr/local/bin/quickshell
sudo rm -rf /usr/local/lib/qt6/qml/Quickshell /usr/local/share/applications/org.quickshell.desktop
sudo rm -rf /lib/qt6/qml/Caelestia /lib/qt6/qml/M3Shapes \
            /usr/lib/qt6/qml/Caelestia /usr/lib/qt6/qml/M3Shapes
sudo rm -rf /usr/lib/caelestia /etc/xdg/quickshell/caelestia
sudo rm -f /usr/local/lib/x86_64-linux-gnu/libcava* /usr/local/include/cava* 2>/dev/null || true
sudo rm -f /usr/local/lib/pkgconfig/cava.pc /usr/local/lib/x86_64-linux-gnu/pkgconfig/cava.pc 2>/dev/null || true
sudo ldconfig

if [ "$PURGE_QT" = 1 ]; then
    log "removing Qt toolchains in /opt (any /opt/qt* install)"
    for q in /opt/qt*; do
        [ -e "$q" ] && sudo rm -rf "$q"
    done
else
    warn "Qt toolchains kept in /opt (use --purge-qt to remove)"
fi

log "removing manifest directory"
rm -rf "$HOME/.local/share/caelestia-ubuntu"

log "preserving user configs"
ts=$(date +%Y%m%d-%H%M%S)
keep="$HOME/.config/caelestia-ubuntu-uninstalled-$ts"
mkdir -p "$keep"
for d in "$HOME/.config/hypr" "$HOME/.config/caelestia" "$HOME/.config/quickshell"; do
    [ -e "$d" ] && cp -a "$d" "$keep/"
done
warn "configs moved to $keep (delete manually if unwanted)"

echo
log "uninstall complete. GNOME was never modified."
log "if you no longer need the Hyprland session: sudo apt remove hyprland hypridle hyprlock hyprpaper xdg-desktop-portal-hyprland"
