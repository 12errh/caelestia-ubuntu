#!/usr/bin/env bash
# ============================================================================
#  caelestia-ubuntu updater
#
#  Compares what setup.sh installed against the PINNED upstream revisions and
#  rebuilds only what differs — reusing your existing Qt toolchain and system
#  packages. System packages (Hyprland, wayland, fonts, …) stay current via
#  `sudo apt update && sudo apt upgrade`; Qt itself is a fixed toolchain
#  (bump it with `./setup.sh --qt-version X`).
#
#  Revision pinning: every built component (quickshell, caelestia shell,
#  m3shapes, libcava, caelestia CLI) is built from an exact commit listed in
#  revisions.conf — the "known good" revision the maintainer tested. update.sh
#  therefore NEVER silently tracks moving upstream. To deliberately track
#  newer upstream (test it, then commit the bump):
#      ./update.sh --update-sources
#
#  Usage:
#    ./update.sh                  # check + apply (rebuild to pinned revisions)
#    ./update.sh --check          # read-only status report (no clones/builds)
#    ./update.sh --yes            # apply non-interactively
#    ./update.sh --force          # rebuild pinned revisions even if already installed
#    ./update.sh --update-sources # bump revisions.conf to latest upstream, then rebuild
#    ./update.sh --no-restart     # do not restart the running shell service
# ============================================================================
set -euo pipefail

ACTION=apply
FORCE=0; ASSUME_YES=0; RESTART=1; UPDATE_SOURCES=0
while [ $# -gt 0 ]; do
    case "$1" in
        --check)          ACTION=check ;;
        --yes)            ASSUME_YES=1 ;;
        --force)          FORCE=1 ;;
        --update-sources) UPDATE_SOURCES=1 ;;
        --no-restart)     RESTART=0 ;;
        -h|--help)        grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "error: unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIN_FILE="$SCRIPT_DIR/revisions.conf"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✔\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m ✘ %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
ask()  { [ "$ASSUME_YES" = 1 ] && return 0; read -r -p "$1 [y/N] " a; [[ "${a,,}" == y* ]]; }

# ---------------------------------------------------------------------------
# Revision pins
# ---------------------------------------------------------------------------
declare -A PIN=()
PIN_ORDER=(quickshell caelestia m3shapes cava caelestia_cli)

load_pins() {
    PIN=()
    [ -f "$PIN_FILE" ] || return 0
    local k v
    while IFS='=' read -r k v; do
        case "$k" in
            ''|\#*) continue ;;
            *) PIN["$k"]="$v" ;;
        esac
    done < "$PIN_FILE"
}

pinned_short() { local v="${PIN[$1]:-}"; printf '%s' "${v:0:12}"; }

component_url() {  # $1=component  -> url ("" for cli, handled separately)
    case "$1" in
        quickshell) echo "$QS_REPO" ;;
        caelestia)  echo "$CAEL_REPO" ;;
        m3shapes)   echo "$M3S_REPO" ;;
        cava)       echo "$CAVA_REPO" ;;
    esac
}

# ---------------------------------------------------------------------------
# Qt toolchain discovery (the one quickshell was built against).
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
QS_MIRROR="https://github.com/outfoxxed/quickshell"
CAEL_REPO="https://github.com/caelestia-dots/shell";           CAEL_BRANCH="main"
M3S_REPO="https://github.com/soramanew/m3shapes";              M3S_BRANCH="main"
CAVA_REPO="https://github.com/LukashonakV/cava";               CAVA_BRANCH="master"
CLI_REPO="https://github.com/caelestia-dots/cli";              CLI_BRANCH="main"

load_pins

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

# --- revision probes --------------------------------------------------------
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

rev_remote() {  # $1=url
    git ls-remote "$1" HEAD 2>/dev/null | awk '{print $1}' | cut -c1-12
}

# Ensure <sha> exists locally (fetch it / unshallow if not).
ensure_commit() {  # $1=dir $2=full sha
    git -C "$1" cat-file -e "$2^{commit}" >/dev/null 2>&1 && return 0
    git -C "$1" fetch --quiet origin "$2" 2>/dev/null && return 0
    git -C "$1" fetch --quiet --unshallow origin 2>/dev/null && return 0
    git -C "$1" fetch --quiet origin 2>/dev/null || true
    git -C "$1" cat-file -e "$2^{commit}" >/dev/null 2>&1
}

# Prepare a clone for building: clone if absent (with mirror fallback), else
# fetch. Check mode never mutates. Echoes the local HEAD rev.
prepare_clone() {  # $1=dir $2=url
    local dir="$1" url="$2"
    if [ ! -d "$dir/.git" ]; then
        [ "$ACTION" = "check" ] && { echo ""; return 0; }
        log "cloning $(basename "$dir")"
        if ! git clone "$url" "$dir" >/dev/null 2>&1; then
            if [ "$url" = "$QS_REPO" ]; then
                warn "quickshell upstream unreachable — trying GitHub mirror"
                git clone "$QS_MIRROR" "$dir" >/dev/null 2>&1 || die "clone failed: $QS_MIRROR"
            else
                die "clone failed: $url"
            fi
        fi
    elif [ "$ACTION" = "apply" ]; then
        git -C "$dir" fetch --quiet origin >/dev/null 2>&1 || true
    fi
    git -C "$dir" rev-parse HEAD 2>/dev/null || echo ""
}

# Align a source dir to its pinned revision (or, when unpinned, its default
# branch). Returns the revision now checked out (12-char).
align_to_pin() {  # $1=dir $2=component $3=full-pin
    local dir="$1" comp="$2" pin="$3" branch
    if [ -n "$pin" ]; then
        ensure_commit "$dir" "$pin" || die "$comp: pinned revision $pin not fetchable"
        git -C "$dir" reset --hard "$pin" >/dev/null 2>&1 || die "$comp: cannot reset to pinned revision $pin"
    else
        case "$comp" in
            quickshell) branch="$QS_BRANCH" ;; caelestia) branch="$CAEL_BRANCH" ;;
            m3shapes) branch="$M3S_BRANCH" ;; cava) branch="$CAVA_BRANCH" ;;
        esac
        git -C "$dir" fetch --quiet origin 2>/dev/null || true
        git -C "$dir" reset --hard "origin/$branch" >/dev/null 2>&1 || true
    fi
    git -C "$dir" rev-parse --short=12 HEAD 2>/dev/null || echo ""
}

record() { NEW_MANIFEST+="$(printf '%s=%s\n' "$1" "$2")"; }

# rebuild <key> <dir> <url> <prefix> <extra cmake args...>
rebuild() {
    local key="$1" dir="$2" url="$3" prefix="$4"; shift 4
    local localRev remoteRev pinned fullPin targetLabel targetRev
    localRev="$(rev_local "$key")"
    remoteRev="$(rev_remote "$url")"
    fullPin="${PIN[$key]:-}"
    pinned="$(pinned_short "$key")"

    if [ -z "$remoteRev" ]; then
        warn "$key: cannot reach upstream (offline)"
        return 0
    fi
    if [ -z "$pinned" ]; then
        targetLabel="upstream ($remoteRev)"
        targetRev="$remoteRev"
    else
        targetLabel="pinned $pinned"
        targetRev="$pinned"
    fi

    if [ "$ACTION" = "check" ]; then
        printf '  %-12s local %s | pinned %s | upstream %s\n' "$key" "${localRev:-none}" "${pinned:-none}" "${remoteRev:-none}"
        if [ -n "$pinned" ] && [ "$pinned" != "$remoteRev" ]; then
            printf '     -> upstream moved (%s); run ./update.sh --update-sources to track it\n' "$remoteRev"
        fi
        return 0
    fi

    # Nothing to do when the installed revision already matches the target.
    if [ "$FORCE" = 0 ] && [ -n "$localRev" ] && [ "$localRev" = "$targetRev" ]; then
        ok "$key up to date ($targetLabel)"
        record "$key" "$targetRev"
        return 0
    fi

    log "$key: rebuilding (installed ${localRev:-none} -> $targetLabel)"
    prepare_clone "$dir" "$url" >/dev/null
    targetRev="$(align_to_pin "$dir" "$key" "$fullPin")"
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
    record "$key" "$(git -C "$dir" rev-parse --short=12 HEAD 2>/dev/null || echo "$targetRev")"
}

print_status() {
    printf '\n%-14s %-12s %-10s %s\n' "component" "installed" "pinned" "upstream"
    printf '%-14s %-12s %-10s %s\n' "quickshell" "$(rev_local quickshell)" "$(pinned_short quickshell)" "$(rev_remote "$QS_REPO")"
    printf '%-14s %-12s %-10s %s\n' "caelestia shell" "$(rev_local caelestia)" "$(pinned_short caelestia)" "$(rev_remote "$CAEL_REPO")"
    printf '%-14s %-12s %-10s %s\n' "m3shapes" "$(rev_local m3shapes)" "$(pinned_short m3shapes)" "$(rev_remote "$M3S_REPO")"
    printf '%-14s %-12s %-10s %s\n' "libcava" "$(rev_local cava)" "$(pinned_short cava)" "$(rev_remote "$CAVA_REPO")"
    printf '%-14s %-12s %-10s %s\n' "Qt $QT_VERSION" "installed" "fixed" "(use setup.sh --qt-version to bump)"
}

# Bump revisions.conf to the latest default-branch commits (--update-sources).
bump_pins() {
    log "fetching latest upstream revisions"
    local changed=0 k url head
    for k in "${PIN_ORDER[@]}"; do
        case "$k" in
            quickshell) url="$QS_REPO" ;; caelestia) url="$CAEL_REPO" ;;
            m3shapes) url="$M3S_REPO" ;; cava) url="$CAVA_REPO" ;;
            caelestia_cli) url="$CLI_REPO" ;;
        esac
        head=$(git ls-remote "$url" HEAD 2>/dev/null | awk '{print $1}')
        if [ -n "$head" ]; then
            [ "${PIN[$k]:-}" = "$head" ] || changed=1
            PIN["$k"]="$head"
            log "  $k -> $head"
        else
            warn "  $k: cannot reach upstream — keeping ${PIN[$k]:-none}"
        fi
    done
    if [ "$ACTION" = "check" ]; then
        log "check-only: not writing $PIN_FILE"
        return 0
    fi
    {
        cat <<'EOF'
# Known-good upstream revisions pinned by the caelestia-ubuntu installer.
#
# setup.sh checks these commits out verbatim, and update.sh rebuilds to them,
# so a fresh install is reproducible and can never silently break when an
# upstream repo moves. A fresh clone always builds exactly what the maintainer
# tested.
#
# To deliberately track newer upstream (after testing it yourself), run:
#     ./update.sh --update-sources
# which fetches the latest default-branch commit of every component, rewrites
# this file, and rebuilds to the new pins. Commit the bump afterwards.
#
# Format: <component>=<full 40-character git commit SHA on its default branch>
EOF
        for k in "${PIN_ORDER[@]}"; do
            printf '%s=%s\n' "$k" "${PIN[$k]}"
        done
    } > "$PIN_FILE"
    log "wrote updated pins to $PIN_FILE"
}

# ---------------------------------------------------------------------------
main() {
    log "updater — Qt prefix: $QT_PREFIX"
    print_status

    if [ "$ACTION" = "check" ]; then
        log "check-only: no changes will be applied"
        log "components:"
        rebuild quickshell  "$SRC_ROOT/quickshell" "$QS_REPO" "/usr/local"
        rebuild caelestia   "$SHELL_DIR"            "$CAEL_REPO" "/"
        rebuild m3shapes    "$SRC_ROOT/m3shapes"    "$M3S_REPO" "/"
        rebuild cava        "$SRC_ROOT/cava"        "$CAVA_REPO" ""
        exit 0
    fi

    # --- deliberate pin bump ---------------------------------------------
    if [ "$UPDATE_SOURCES" = 1 ]; then
        if [ -f "$PIN_FILE" ] && [ ! -w "$PIN_FILE" ]; then
            die "$PIN_FILE is not writable"
        fi
        bump_pins
    fi

    # --- apply -----------------------------------------------------------
    # caelestia shell: preserve local config changes (backup branch), then
    # align to its pinned revision so the rebuild is reproducible.
    if [ -n "$(git -C "$SHELL_DIR" status --porcelain 2>/dev/null | head -1)" ]; then
        git -C "$SHELL_DIR" branch -f backup-local-changes 2>/dev/null || true
        local ub; ub="$HOME/.cache/caelestia-shell-untracked-backup-$(date +%Y%m%d-%H%M%S)"
        mkdir -p "$ub"
        git -C "$SHELL_DIR" status --porcelain | grep '^??' | sed 's/^?? //' \
            | while read -r f; do cp -a "$SHELL_DIR/$f" "$ub/" 2>/dev/null || true; done
        warn "local config changes backed up (tracked->branch backup-local-changes, untracked->$ub)"
    fi

    # quickshell (system Qt-based binary)
    rebuild "quickshell" "$SRC_ROOT/quickshell" "$QS_REPO" "/usr/local" "-DVENDOR_CPPTRACE=ON"
    sudo patchelf --force-rpath --set-rpath "$QT_PREFIX/lib:\$ORIGIN:\$ORIGIN/../lib" \
        /usr/local/bin/qs /usr/local/bin/quickshell 2>/dev/null || warn "quickshell rpath patch skipped (non-fatal)"
    env -u LD_LIBRARY_PATH /usr/local/bin/qs --version >/dev/null 2>&1 || warn "quickshell does not run after update"

    # caelestia shell (QML module + shell config repo)
    rebuild "caelestia" "$SHELL_DIR" "$CAEL_REPO" "/" "-DCMAKE_INSTALL_LIBDIR=lib"

    # m3shapes (Qt QML module)
    rebuild "m3shapes" "$SRC_ROOT/m3shapes" "$M3S_REPO" "/" "-DCMAKE_INSTALL_LIBDIR=lib"

    # libcava (autotools, no cmake)
    rebuild "cava" "$SRC_ROOT/cava" "$CAVA_REPO" ""

    # caelestia CLI (best effort)
    local cli_pin="${PIN[caelestia_cli]:-}"
    if have uv || have pipx; then
        if ask "Update the caelestia CLI (to ${cli_pin:0:12})?"; then
            local cli_spec="git+$CLI_REPO${cli_pin:+@$cli_pin}"
            if have uv; then
                ( uv tool install --force "$cli_spec" >/dev/null 2>&1 ) \
                    && ok "caelestia CLI updated (uv)" || warn "caelestia CLI upgrade failed"
            else
                ( pipx install --force "caelestia-cli @ $cli_spec" >/dev/null 2>&1 ) \
                    && ok "caelestia CLI updated (pipx)" || warn "caelestia CLI upgrade failed"
            fi
        fi
        record caelestia_cli "${cli_pin:0:12}"
    else
        warn "neither uv nor pipx found — skip caelestia CLI update (shell still works)"
    fi

    sudo ldconfig

    {
        printf 'QT_VERSION=%s\n' "$QT_VERSION"
        printf 'QT_PREFIX=%s\n' "$QT_PREFIX"
        printf 'SRC_ROOT=%s\n' "$SRC_ROOT"
        printf 'UPDATED_AT=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'PIN_FILE=%s\n' "$PIN_FILE"
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
    [ "$did_anything" = 1 ] && ok "updates applied." || ok "nothing to update (pinned revisions already installed)."
    log "manifest written to $MANIFEST (run ./update.sh --check to verify)"
}

main "$@"
