#!/usr/bin/env bash
# ============================================================================
#  caelestia-ubuntu updater
#
#  Compares what setup.sh installed against upstream and rebuilds only the
#  components that moved — reusing your existing Qt toolchain and system
#  packages. System packages (Hyprland, wayland, fonts, …) stay current via
#  `sudo apt update && sudo apt upgrade`; Qt itself is a fixed toolchain
#  (bump it with `./setup.sh --qt-version X`).
#
#  Built components tracked: quickshell, caelestia shell, m3shapes, libcava,
#  and the caelestia CLI tool.
#
#  Usage:
#    ./update.sh                 # check + apply updates (prompts before apply)
#    ./update.sh --check         # only report (read-only, no clones/builds)
#    ./update.sh --yes           # apply non-interactively
#    ./update.sh --force         # rebuild even if upstream matches local
#    ./update.sh --no-restart    # do not restart the running shell service
# ============================================================================
set -euo pipefail

ACTION=apply
FORCE=0; ASSUME_YES=0; RESTART=1
while [ $# -gt 0 ]; do
    case "$1" in
        --check)      ACTION=check ;;
        --yes)        ASSUME_YES=1 ;;
        --force)      FORCE=1 ;;
        --no-restart) RESTART=0 ;;
        -h|--help)    grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "error: unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✔\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m ✘ %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
ask()  { [ "$ASSUME_YES" = 1 ] && return 0; read -r -p "$1 [y/N] " a; [[ "${a,,}" == y* ]]; }

# ---------------------------------------------------------------------------
# Qt toolchain discovery (the one quickshell was built against).
#   1. deployed systemd unit (real absolute path setup.sh used)
#   2. manifest written by setup.sh
#   3. ldd on the installed `qs` RPATH
# ---------------------------------------------------------------------------
detect_qt_prefix() {
    local unit="$HOME/.config/systemd/user/caelestia-shell.service" rev
    if [ -f "$unit" ]; then
        rev=$(grep -oE '/opt/qt[0-9]+/[0-9.]+/gcc_64' "$unit" | head -1 || true)
        [ -n "$rev" ] && { echo "$rev"; return 0; }
    fi
    if [ -f "$HOME/.local/share/caelestia-ubuntu/manifest" ]; then
        # shellcheck disable=SC1091
        . "$HOME/.local/share/caelestia-ubuntu/manifest" 2>/dev/null || true
        [ -n "${QT_PREFIX:-}" ] && [ -d "$QT_PREFIX" ] && { echo "$QT_PREFIX"; return 0; }
    fi
    rev=$(ldd "$(command -v qs 2>/dev/null)" 2>/dev/null \
        | grep -oE '/opt/qt[0-9]+/[0-9.]+/gcc_64/lib' | head -1 | sed 's|/lib$||' || true)
    echo "${rev:-}"
}

QT_PREFIX="$(detect_qt_prefix)"
[ -n "$QT_PREFIX" ] || die "quickshell not installed — run ./setup.sh first"
QT_VERSION="$(awk -F/ '{print $4}' <<<"$QT_PREFIX")"
SRC_ROOT="${SRC_ROOT:-$HOME/.cache/caelestia-ubuntu-build}"
SHELL_DIR="$HOME/.config/quickshell/caelestia"
MANIFEST_DIR="$HOME/.local/share/caelestia-ubuntu"; mkdir -p "$MANIFEST_DIR"
MANIFEST="$MANIFEST_DIR/manifest"

QS_REPO="https://git.outfoxxed.me/quickshell/quickshell";     QS_BRANCH="master"
CAEL_REPO="https://github.com/caelestia-dots/shell";           CAEL_BRANCH="main"
M3S_REPO="https://github.com/soramanew/m3shapes";              M3S_BRANCH="main"
CAVA_REPO="https://github.com/LukashonakV/cava";               CAVA_BRANCH="master"

NEW_MANIFEST=""; need_restart=0; did_anything=0

# Mirror setup.sh's build_cmake exactly (configure+build+install).
build_cmake() {  # $1=srcdir  $2=prefix  $3..=extra cmake args
    local src="$1" prefix="$2"; shift 2
    [ -d "$src/.git" ] || die "missing source tree: $src"
    ( cd "$src" && rm -rf build && \
        env -u LD_LIBRARY_PATH -u QML_IMPORT_PATH \
            PKG_CONFIG_PATH="/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig" \
        cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo \
              -DCMAKE_INSTALL_PREFIX="$prefix" \
              -DCMAKE_PREFIX_PATH="$QT_PREFIX;/usr/local" \
              "$@" > /tmp/caelestia-update-cmake.log 2>&1 ) \
      || { echo "=== cmake configure failed for $src ===" >&2; \
          sed -n '/error/I,$p' /tmp/caelestia-update-cmake.log | head -30 >&2; return 1; }
    ( cd "$src" && env -u LD_LIBRARY_PATH -u QML_IMPORT_PATH \
            PKG_CONFIG_PATH="/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig" \
        cmake --build build -j"$(nproc)" ) || return 1
    sudo cmake --install "$src/build" >/dev/null 2>&1 || return 1
}

# --- read-only revision probes (no cloning) --------------------------------
# quickshell and caelestia bake their git rev into the shipped binaries, so a
# status check needs no source tree. m3shapes/libcava have no such helper, so
# their cache-clone HEAD is used when present.

rev_local() {  # $1=quickshell|caelestia|m3shapes|cava
    case "$1" in
        quickshell)  env -u LD_LIBRARY_PATH qs --version 2>/dev/null \
                        | sed -n 's/.*revision \([0-9a-f]*\).*/\1/p' | cut -c1-12 ;;
        caelestia)   /usr/lib/caelestia/version -s 2>/dev/null \
                        | sed -n 's/.*revision \([0-9a-f]*\).*/\1/p' | cut -c1-12 ;;
        m3shapes)    git -C "$SRC_ROOT/m3shapes" rev-parse --short=12 HEAD 2>/dev/null || echo "" ;;
        cava)        git -C "$SRC_ROOT/cava" rev-parse --short=12 HEAD 2>/dev/null || echo "" ;;
    esac
}

rev_remote() {  # $1=url $2=branch
    git ls-remote "$1" "refs/heads/$2" 2>/dev/null | awk '{print $1}' | cut -c1-12
}

status_row() {  # $1=key $2=url $3=branch
    local key="$1" localRev remoteRev
    localRev="$(rev_local "$key")"
    remoteRev="$(rev_remote "$2" "$3")"
    if [ -z "$remoteRev" ]; then printf '  %-18s %s\n' "$key" "unknown (offline?)"; return; fi
    if [ -z "$localRev" ]; then printf '  %-18s %s\n' "$key" "not installed (need setup.sh)"; return; fi
    if [ "$localRev" = "$remoteRev" ] && [ "$FORCE" = 0 ]; then printf '  %-18s %s\n' "$key" "up to date"; return; fi
    printf '  %-18s %s\n' "$key" "UPDATE AVAILABLE (local ${localRev:-?} -> ${remoteRev:-?})"
}

# Prepare a clone for rebuilding: clone if absent, fetch+reset in apply mode
# only (check mode never mutates). Echoes the local HEAD rev.
prepare_clone() {  # $1=dir $2=url $3=branch
    local dir="$1" url="$2" branch="$3"
    if [ ! -d "$dir/.git" ]; then
        [ "$ACTION" = "check" ] && { echo ""; return 0; }
        log "cloning $(basename "$dir")"
        git clone "$url" "$dir" >/dev/null 2>&1 || die "clone failed: $url"
    elif [ "$ACTION" = "apply" ]; then
        git -C "$dir" fetch origin >/dev/null 2>&1 || warn "git fetch failed for $dir"
        git -C "$dir" checkout "$branch" >/dev/null 2>&1 || true
        git -C "$dir" reset --hard "origin/$branch" >/dev/null 2>&1 || true
    fi
    git -C "$dir" rev-parse --short=12 HEAD 2>/dev/null || echo ""
}

record() { NEW_MANIFEST+="$(printf '%s=%s\n' "$1" "$2")"; }

# rebuild <key> <dir> <url> <branch> <prefix> <extra cmake args...>
rebuild() {
    local key="$1" dir="$2" url="$3" branch="$4" prefix="$5"; shift 5
    local localRev remoteRev
    localRev="$(rev_local "$key")"
    remoteRev="$(rev_remote "$url" "$branch")"
    if [ -z "$remoteRev" ]; then warn "$key: cannot reach upstream (offline)"; return 0; fi
    if [ "$FORCE" = 0 ] && [ "$localRev" = "$remoteRev" ]; then
        ok "$key up to date"; record "$key" "$localRev"; return 0
    fi
    if [ "$ACTION" = "check" ]; then
        printf '  %-18s would rebuild (local ${localRev:-none} -> remote %s)\n' "$key" "$remoteRev"
        did_anything=1; return 0
    fi
    log "$key: rebuilding (local ${localRev:-none} -> remote $remoteRev)"
    prepare_clone "$dir" "$url" "$branch" >/dev/null
    case "$key" in
        cava)
            ( cd "$dir" && ./autogen.sh >/dev/null 2>&1 && \
                PKG_CONFIG_PATH="/usr/local/lib/x86_64-linux-gnu/pkgconfig" ./configure --prefix=/usr/local >/dev/null 2>&1 \
                && make -j"$(nproc)" >/dev/null 2>&1 && sudo make install >/dev/null 2>&1 && sudo ldconfig ) \
              || die "$key build failed"
            ;;
        *)
            build_cmake "$dir" "$prefix" "$@" || die "$key build failed"
            ;;
    esac
    did_anything=1; need_restart=1
    record "$key" "$(git -C "$dir" rev-parse --short=12 HEAD 2>/dev/null || echo "$remoteRev")"
}

print_status() {
    printf '\n%-16s %-14s %s\n' "component" "local" "upstream"
    printf '%-16s %-14s %s\n' "quickshell" "$(rev_local quickshell)" "$(rev_remote "$QS_REPO" "$QS_BRANCH")"
    printf '%-16s %-14s %s\n' "caelestia shell" "$(rev_local caelestia)" "$(rev_remote "$CAEL_REPO" "$CAEL_BRANCH")"
    printf '%-16s %-14s %s\n' "m3shapes" "$(rev_local m3shapes)" "$(rev_remote "$M3S_REPO" "$M3S_BRANCH")"
    printf '%-16s %-14s %s\n' "libcava" "$(rev_local cava)" "$(rev_remote "$CAVA_REPO" "$CAVA_BRANCH")"
    printf '%-16s %-14s %s\n' "Qt $QT_VERSION" "installed" "fixed (use setup.sh --qt-version to bump)"
}

main() {
    log "updater — Qt prefix: $QT_PREFIX"
    print_status
    if [ "$ACTION" = "check" ]; then
        log "check-only: no changes will be applied"
        printf '\n%s\n' "Components with updates:"
        status_row quickshell "$QS_REPO" "$QS_BRANCH"
        status_row caelestia "$CAEL_REPO" "$CAEL_BRANCH"
        status_row m3shapes "$M3S_REPO" "$M3S_BRANCH"
        status_row cava "$CAVA_REPO" "$CAVA_BRANCH"
        exit 0
    fi

    # --- apply -------------------------------------------------------------
    # caelestia shell: preserve local config changes (backup branch), then
    # hard-reset to upstream so the rebuild is reproducible.
    if [ -n "$(git -C "$SHELL_DIR" status --porcelain 2>/dev/null | head -1)" ]; then
        git -C "$SHELL_DIR" branch -f backup-local-changes 2>/dev/null || true
        local ub; ub="$HOME/.cache/caelestia-shell-untracked-backup-$(date +%Y%m%d-%H%M%S)"
        mkdir -p "$ub"
        git -C "$SHELL_DIR" status --porcelain | grep '^??' | sed 's/^?? //' \
            | while read -r f; do cp -a "$SHELL_DIR/$f" "$ub/" 2>/dev/null || true; done
        warn "local config changes backed up (tracked->branch backup-local-changes, untracked->$ub)"
    fi

    # quickshell (system Qt-based binary)
    rebuild "quickshell" "$SRC_ROOT/quickshell" "$QS_REPO" "$QS_BRANCH" "/usr/local" "-DVENDOR_CPPTRACE=ON"
    sudo patchelf --force-rpath --set-rpath "$QT_PREFIX/lib:\$ORIGIN:\$ORIGIN/../lib" \
        /usr/local/bin/qs /usr/local/bin/quickshell 2>/dev/null || warn "quickshell rpath patch skipped (non-fatal)"
    env -u LD_LIBRARY_PATH /usr/local/bin/qs --version >/dev/null 2>&1 || warn "quickshell does not run after update"

    # caelestia shell (QML module + shell config repo)
    rebuild "caelestia" "$SHELL_DIR" "$CAEL_REPO" "$CAEL_BRANCH" "/" "-DCMAKE_INSTALL_LIBDIR=lib"

    # m3shapes (Qt QML module)
    rebuild "m3shapes" "$SRC_ROOT/m3shapes" "$M3S_REPO" "$M3S_BRANCH" "/" "-DCMAKE_INSTALL_LIBDIR=lib"

    # libcava (autotools, no cmake)
    rebuild "cava" "$SRC_ROOT/cava" "$CAVA_REPO" "$CAVA_BRANCH" ""

    # caelestia CLI (best effort)
    if have uv; then
        if ask "Update the caelestia CLI (git main)?"; then
            ( uv tool upgrade --force git+https://github.com/caelestia-dots/cli >/dev/null 2>&1 \
                || uv tool install --force git+https://github.com/caelestia-dots/cli >/dev/null 2>&1 ) \
              && ok "caelestia CLI updated" || warn "caelestia CLI upgrade failed"
        fi
        record caelestia_cli "$(uv tool list 2>/dev/null | awk '/caelestia-cli/{print $2}' || echo unknown)"
    elif have pipx; then
        if ask "Update the caelestia CLI (git main)?"; then
            pipx upgrade "caelestia-cli @ git+https://github.com/caelestia-dots/cli" >/dev/null 2>&1 \
                && ok "caelestia CLI updated" || warn "caelestia CLI upgrade failed"
        fi
        record caelestia_cli "$(pipx list --format=json 2>/dev/null | jq -r '.installed."caelestia-cli".version' 2>/dev/null || echo unknown)"
    else
        warn "neither uv nor pipx found — skip caelestia CLI update (shell still works)"
    fi

    sudo ldconfig

    {
        printf 'QT_VERSION=%s\n' "$QT_VERSION"
        printf 'QT_PREFIX=%s\n' "$QT_PREFIX"
        printf 'SRC_ROOT=%s\n' "$SRC_ROOT"
        printf 'UPDATED_AT=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '%s' "$NEW_MANIFEST"
    } > "$MANIFEST"

    if [ "$need_restart" = 1 ] && [ "$RESTART" = 1 ] && \
       systemctl --user list-units --state=active --no-legend caelestia-shell.service 2>/dev/null | grep -q caelestia-shell; then
        log "restarting the running Caelestia shell service"
        systemctl --user daemon-reload 2>/dev/null || true
        systemctl --user restart caelestia-shell.service 2>/dev/null && ok "caelestia-shell restarted" \
            || warn "restart via systemd failed (not in a Hyprland session?) — restart after next login"
    elif [ "$need_restart" = 1 ]; then
        log "restart after re-login: systemctl --user restart caelestia-shell.service"
    fi
    [ "$did_anything" = 1 ] && ok "updates applied." || ok "nothing to update."
    log "manifest written to $MANIFEST (run ./update.sh --check to verify)"
}

main "$@"
