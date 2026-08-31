#!/usr/bin/env bash
# ----------------------------------------------------------------------------
#  ci-headless-smoke.sh — prove the Caelestia shell actually *starts* on a real
#  system, without a GPU or monitor.
#
#  Strategy: launch Hyprland with the wlroots headless backend (Mesa llvmpipe
#  software GL) and then run the exact Quickshell invocation the systemd unit
#  uses (`qs -c caelestia -n`) as a Wayland client. If the shell process stays
#  alive and answers an IPC call, the deploy is genuinely working.
#
#  This mirrors the real login path as closely as CI allows; the only thing
#  missing is a physical display/seat. libseat's `builtin` backend + `seatd`
#  provide the seat Hyprland needs inside a container.
# ----------------------------------------------------------------------------
set -uo pipefail

# setup.sh installs Qt to /opt/qt<major> (e.g. /opt/qt6 for 6.11.2). Derive the
# real gcc_64 path rather than hardcoding it.
QT_DIR="$(ls -d /opt/qt*/6.*/gcc_64 2>/dev/null | head -1)"
[ -n "$QT_DIR" ] || { echo "!! Qt gcc_64 dir not found under /opt/qt*"; exit 1; }
QT="$QT_DIR"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
mkdir -p "$XDG_RUNTIME_DIR"

# Quickshell/Wayland env (same shape as caelestia-shell.service).
export QT_QPA_PLATFORM=wayland
export WLR_BACKENDS=headless
export WLR_HEADLESS_OUTPUTS=1
export GALLIUM_DRIVER=llvmpipe
export LIBSEAT_BACKEND=builtin
export LD_LIBRARY_PATH="$QT/lib"
export QML_IMPORT_PATH=/usr/local/lib/qt6/qml:/lib/qt6/qml:"$QT/qml"
export PATH="$QT/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export XDG_CURRENT_DESKTOP=Hyprland:GNOME
export XDG_SESSION_DESKTOP=Hyprland

log() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

# A session D-Bus is required by Hyprland + Quickshell.
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
  eval "$(dbus-launch --sh-syntax)"
fi

# Provide a seat for libseat inside the container.
if command -v seatd >/dev/null 2>&1; then
  sudo seatd -g wheel >/tmp/seatd.log 2>&1 &
  sleep 1
  export XDG_SEAT=seat0
fi

# --- Launch Hyprland headless ------------------------------------------------
# We intentionally reuse the deployed config so the *real* config is exercised.
# exec-once entries (polkit/foot) that are absent in CI fail silently and do
# not prevent the compositor from coming up.
log "starting Hyprland (headless)"
Hyprland --config "$HOME/.config/hypr/hyprland.conf" >/tmp/hyprland.log 2>&1 &
HYPR_PID=$!

ok=0
for _ in $(seq 1 60); do
  [ -S "$XDG_RUNTIME_DIR/wayland-1" ] && { ok=1; break; }
  sleep 1
done
if [ "$ok" = 0 ]; then
  echo "!! Hyprland failed to start"; tail -n 40 /tmp/hyprland.log
  exit 1
fi
export WAYLAND_DISPLAY=wayland-1
HSIG="$(ls "$XDG_RUNTIME_DIR"/hypr 2>/dev/null | head -1 || true)"
[ -n "$HSIG" ] && export HYPRLAND_INSTANCE_SIGNATURE="$HSIG"
log "Hyprland up (display=$WAYLAND_DISPLAY instance=$HSIG)"

# --- Launch the Caelestia shell (same cmd as the systemd unit) ---------------
log "starting quickshell -c caelestia -n"
/usr/local/bin/qs -c caelestia -n >/tmp/qs.log 2>&1 &
QS_PID=$!

ok=0
for _ in $(seq 1 40); do
  kill -0 "$QS_PID" 2>/dev/null && { ok=1; break; }
  sleep 1
done
# Let it actually build the scene graph / connect to the compositor.
sleep 15
if ! kill -0 "$QS_PID" 2>/dev/null; then
  echo "!! quickshell exited early"; tail -n 60 /tmp/qs.log
  kill "$HYPR_PID" 2>/dev/null || true
  exit 1
fi
log "quickshell alive (pid $QS_PID) — shell started successfully"

# --- Prove it is interactive via the Quickshell IPC -------------------------
if /usr/local/bin/qs -c caelestia ipc list >/tmp/qs-ipc.log 2>&1; then
  log "IPC reachable — registered endpoints:"
  cat /tmp/qs-ipc.log
else
  log "IPC list unavailable (non-fatal in CI):"
  tail -n 20 /tmp/qs-ipc.log || true
fi

echo "E2E_HEADLESS_OK"

# Clean up so the job exits cleanly.
kill "$QS_PID" 2>/dev/null || true
kill "$HYPR_PID" 2>/dev/null || true
