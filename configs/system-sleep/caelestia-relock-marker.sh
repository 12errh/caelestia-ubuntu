#!/bin/bash
# Touch /run/user/__UID__/caelestia-resume on post-suspend so the user
# service drop-in knows the upcoming ExecStartPost is a post-resume
# restart (and not a cold Hyprland login).
#
# Also blanks the display immediately on wake so the user never sees the
# "dead lock" frame that Hyprland shows between quickshell being bounced
# and caelestia-relock.sh re-acquiring the session lock. The display is
# turned back on by caelestia-relock.sh once the lock UI is rendered.
#
# Run as root by systemd-sleep; the marker is a plain file owned by
# the user, consumed by ~/.local/bin/caelestia-relock.sh.

USER_UID=__UID__
MARKER="/run/user/$USER_UID/caelestia-resume"
RUNDIR="/run/user/$USER_UID"

logger -t caelestia-relock-marker "hook invoked: arg1=$1 arg2=$2"

case "$1" in
    post)
        install -d -m 700 -o "$USER_UID" -g "$USER_UID" "$RUNDIR" 2>/dev/null || true
        : > "$MARKER"
        chown "$USER_UID:$USER_UID" "$MARKER"
        chmod 600 "$MARKER"
        logger -t caelestia-relock-marker "marker written at $MARKER"

        # Blank the panel right away (it may still be on after wake) so the
        # dead-lock frame is not visible while quickshell restarts/re-locks.
        SIG=$(ls -d "$RUNDIR"/hypr/*/ 2>/dev/null | head -1 | sed 's#.*/##; s#/$##')
        if [ -n "$SIG" ]; then
            XDG_RUNTIME_DIR="$RUNDIR" HYPRLAND_INSTANCE_SIGNATURE="$SIG" \
                hyprctl dispatch dpms off >/dev/null 2>&1 || true
            logger -t caelestia-relock-marker "display blanked on wake"
        fi

        # Auto-cleanup safety net
        ( sleep 120 && rm -f "$MARKER" ) &
        ;;
esac

exit 0
