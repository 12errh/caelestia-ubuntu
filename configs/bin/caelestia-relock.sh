#!/bin/bash
# Re-acquire the Caelestia Wayland session lock after quickshell restarts
# following a lid-close/wake cycle.
#
# The systemd-sleep hook bounces qs on wake, but a fresh qs instance has no
# ext_session_lock_v1 lock, so the lock screen is missing until manually
# locked. This re-establishes it so the lock is back when the lid reopens.
#
# Runs as the logged-in user (invoked from a root-owned systemd-sleep hook
# via `setpriv --reuid`), so no privilege escalation is needed.
#
# Behaviour:
#   - If /run/user/<uid>/caelestia-resume marker exists, the qs process was
#     bounced by the sleep hook, so re-acquire the lock (mark consumed).
#   - If no marker exists, this is a cold Hyprland start — do NOTHING.
#   - While waiting, keep the display blanked (DPMS off) so the user does
#     not see the unlocked desktop between resume and lock acquisition.
#     The display is turned back on once the lock UI is up.
#
# Usage: caelestia-relock.sh [uid]   (uid defaults to current uid)

TARGET_UID="${1:-$(id -u)}"
MAX_WAIT=30
MARKER="/run/user/${TARGET_UID}/caelestia-resume"

if ! NAME=$(id -un "$TARGET_UID" 2>/dev/null); then
    echo "caelestia-relock: unknown uid $TARGET_UID" >&2
    exit 1
fi

# Cold start: no marker, do not auto-lock.
if [ ! -f "$MARKER" ]; then
    exit 0
fi

# Blank the display so the unlocked desktop is not visible while we wait
# for quickshell to come back and re-acquire the session lock.
# (Only run if Hyprland is responding; otherwise this hangs.)
if command -v hyprctl >/dev/null 2>&1; then
    HYPRLAND_INSTANCE_SIGNATURE="${HYPRLAND_INSTANCE_SIGNATURE:-}" \
    XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" \
        hyprctl dispatch dpms off >/dev/null 2>&1 || true
fi

cleanup() {
    # Restore display so the user can see the lock UI / unlocked desktop.
    # Give the WlSessionLock surface a moment to render its first frame
    # before turning the panel back on, otherwise the user briefly sees
    # an unfilled screencopy background instead of the lock UI.
    sleep 1
    if command -v hyprctl >/dev/null 2>&1; then
        HYPRLAND_INSTANCE_SIGNATURE="${HYPRLAND_INSTANCE_SIGNATURE:-}" \
        XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" \
            hyprctl dispatch dpms on >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

# Wait for quickshell to be running and IPC-ready.
deadline=$(( $(date +%s) + MAX_WAIT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    if pgrep -u "$NAME" -f "qs -c caelestia" >/dev/null 2>&1; then
        if qs -c caelestia ipc call lock isLocked >/dev/null 2>&1; then
            rm -f "$MARKER"
            qs -c caelestia ipc call lock lock >/dev/null 2>&1
            exit 0
        fi
    fi
    sleep 1
done

# Could not reacquire in time; leave marker in place for next attempt
# but still restore the display so the user is not stuck on a black screen.
echo "caelestia-relock: quickshell not ready within ${MAX_WAIT}s" >&2
exit 1
