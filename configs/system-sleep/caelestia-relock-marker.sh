#!/bin/bash
# Touch /run/user/__UID__/caelestia-resume on post-suspend so the user
# service drop-in knows the upcoming ExecStartPost is a post-resume
# restart (and not a cold Hyprland login).
#
# Run as root by systemd-sleep; the marker is a plain file owned by
# the user, consumed by ~/.local/bin/caelestia-relock.sh.

USER_UID=__UID__
MARKER="/run/user/$USER_UID/caelestia-resume"

logger -t caelestia-relock-marker "hook invoked: arg1=$1 arg2=$2"

case "$1" in
    post)
        install -d -m 700 -o "$USER_UID" -g "$USER_UID" "/run/user/$USER_UID" 2>/dev/null || true
        : > "$MARKER"
        chown "$USER_UID:$USER_UID" "$MARKER"
        chmod 600 "$MARKER"
        logger -t caelestia-relock-marker "marker written at $MARKER"
        # Auto-cleanup safety net
        ( sleep 120 && rm -f "$MARKER" ) &
        ;;
esac

exit 0
