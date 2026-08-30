#!/bin/bash
# Restart the Caelestia shell (quickshell) after waking from suspend/hibernate.
# Quickshell frequently ends up with dead layer surfaces after a Wayland
# session is resumed, or segfaults outright while processing the teardown
# events. Terminating it here lets the caelestia-shell.service systemd user
# unit (Restart=on-failure) start a clean instance a moment later.

if [ "$1" = "post" ]; then
    sleep 3
    if pgrep -u 1000 -f "qs -c caelestia" > /dev/null 2>&1; then
        pkill -TERM -u 1000 -f "qs -c caelestia" 2>/dev/null
        logger -t caelestia-resume "Bounced caelestia shell after wake ($2)"
    fi
fi
exit 0
