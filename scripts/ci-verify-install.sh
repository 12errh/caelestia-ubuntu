#!/usr/bin/env bash
# ----------------------------------------------------------------------------
#  ci-verify-install.sh — post-install sanity checks for the e2e CI job.
#
#  Runs AFTER setup.sh has finished on a real Ubuntu 24.04 runner. It does NOT
#  launch a compositor (Hyprland cannot run headless in a GPU-less CI runner —
#  aquamarine's headless backend needs a GBM allocator / render node, which the
#  GitHub-hosted runner does not expose). Instead it proves the installer did
#  its job: every binary was built and deployed, every config was templated in
#  place, and the Qt toolchain path the unit refers to actually exists.
# ----------------------------------------------------------------------------
set -uo pipefail

PASS=0; FAIL=0
check() { # check <label> <test...>
    local label="$1"; shift
    if "$@" >/tmp/ci-check.out 2>&1; then
        printf '  \033[1;32mPASS\033[0m %s\n' "$label"
        PASS=$((PASS+1))
    else
        printf '  \033[1;31mFAIL\033[0m %s\n' "$label"
        sed 's/^/        /' /tmp/ci-check.out | head -8
        FAIL=$((FAIL+1))
    fi
}

# Derive the Qt prefix the SAME way setup.sh does, then confirm the systemd
# unit references a path that really exists (catches missing __QT__ template).
# shellcheck disable=SC2034
QT_PREFIX="$(ls -d /opt/qt*/6.*/gcc_64 2>/dev/null | sort -V | tail -1)"
UNIT="$HOME/.config/systemd/user/caelestia-shell.service"

check "quickshell binary present"      test -x /usr/local/bin/qs
check "quickshell runs (--version)"    env -u LD_LIBRARY_PATH /usr/local/bin/qs --version
check "caelestia version helper present" test -x /usr/lib/caelestia/version
check "Caelestia QML module deployed"  sh -c '[ -d /lib/qt6/qml/Caelestia ] || [ -d /usr/lib/qt6/qml/Caelestia ]'
check "M3Shapes QML module deployed"    sh -c '[ -d /lib/qt6/qml/M3Shapes ] || [ -d /usr/lib/qt6/qml/M3Shapes ]'
check "Quickshell QML module deployed" test -d /usr/local/lib/qt6/qml/Quickshell
check "libcava installed"              pkg-config --exists libcava
check "caelestia shell config present" test -d "$HOME/.config/quickshell/caelestia"
check "shell.json templated (no __HOME__ token)" sh -c '! grep -q "__HOME__" "$1"' sh "$HOME/.config/caelestia/shell.json"
check "systemd unit has no __QT__ token" sh -c '! grep -q "__QT__" "$1"' sh "$UNIT"
export QT_PREFIX
check "systemd unit references real Qt dir" sh -c '[ -n "$QT_PREFIX" ] && grep -qF "$QT_PREFIX" "$1"' sh "$UNIT"
check "hyprland.conf Qt path templated"   sh -c '! grep -q "__QT__" "$1"' sh "$HOME/.config/hypr/hyprland.conf"
check "system-sleep hook templated" sh -c 'grep -q "__UID__" /usr/lib/systemd/system-sleep/caelestia-shell-restart.sh && { echo "token __UID__ still present"; exit 1; } || echo ok'
check "Hyprland installed (on PATH)"       command -v hyprland

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ] || exit 1
