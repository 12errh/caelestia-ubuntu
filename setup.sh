#!/usr/bin/env bash
# ============================================================================
#  caelestia-ubuntu — one-shot installer
#  Caelestia Shell (Quickshell) + Hyprland on Ubuntu 24.04 / Zorin 18 / Debian
#  derivatives, built from source against a pinned Qt (6.11.2), with the
#  maintainer's exact theme, panels, animations and wallpapers.
#
#  Everything GNOME remains untouched: this installs Hyprland as an ADDITIONAL
#  session you pick from the GDM gear menu at login.
#
#  Usage:
#    ./setup.sh                 # full install (asks before config deploy)
#    ./setup.sh --yes           # no prompts
#    ./setup.sh --skip-apt      # skip package installation
#    ./setup.sh --skip-qt       # skip Qt download (already installed)
#    ./setup.sh --skip-config   # build only, do not deploy configs/theme
#    ./setup.sh --skip-fonts    # skip font downloads
#    ./setup.sh --qt-version X  # override pinned Qt version (default 6.11.2)
# ============================================================================
set -euo pipefail

QT_VERSION="6.11.2"
QT_MODULES=(qtimageformats qtshadertools)
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_ROOT="$HOME/.cache/caelestia-ubuntu-build"
QT_ROOT=""
ASSUME_YES=0
SKIP_APT=0; SKIP_QT=0; SKIP_FONTS=0; SKIP_CONFIG=0; IGNORE_SPACE=0
QT_REQUESTED=0

# Known-good upstream revisions (see revisions.conf). Empty = track the repo's
# default branch (legacy behaviour). setup.sh checks these exact commits out so
# a fresh install is reproducible; bump them deliberately with:
#     ./update.sh --update-sources
PIN_FILE="$REPO_DIR/revisions.conf"
PIN_QS=""; PIN_CAEL=""; PIN_M3S=""; PIN_CAVA=""; PIN_CLI=""
load_pins() {
    [ -f "$PIN_FILE" ] || return 0
    while IFS='=' read -r k v; do
        case "$k" in
            ''|\#*) continue ;;
            quickshell)    PIN_QS="$v" ;;
            caelestia)     PIN_CAEL="$v" ;;
            m3shapes)      PIN_M3S="$v" ;;
            cava)          PIN_CAVA="$v" ;;
            caelestia_cli) PIN_CLI="$v" ;;
        esac
    done < "$PIN_FILE"
}
# Ensure <sha> exists locally (fetch it / unshallow if not), so a checkout can
# never fail on a shallow or stale clone.
ensure_commit() {  # $1=dir $2=sha
    git -C "$1" cat-file -e "$2^{commit}" >/dev/null 2>&1 && return 0
    git -C "$1" fetch --quiet origin "$2" 2>/dev/null && return 0
    git -C "$1" fetch --quiet --unshallow origin 2>/dev/null && return 0
    git -C "$1" fetch --quiet origin 2>/dev/null || true
    git -C "$1" cat-file -e "$2^{commit}" >/dev/null 2>&1
}

# ----------------------------------------------------------------------------
log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✔\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m ✘ %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
ask()  { [ "$ASSUME_YES" = 1 ] && return 0; read -r -p "$1 [y/N] " a; [[ "${a,,}" == y* ]]; }

while [ $# -gt 0 ]; do
    case "$1" in
        --yes)         ASSUME_YES=1 ;;
        --skip-apt)    SKIP_APT=1 ;;
        --skip-qt)     SKIP_QT=1 ;;
        --skip-fonts)  SKIP_FONTS=1 ;;
        --skip-config) SKIP_CONFIG=1 ;;
        --ignore-space) IGNORE_SPACE=1 ;;
        --qt-version)
            [ -n "${2:-}" ] || die "--qt-version needs a value (e.g. 6.11.2)"
            QT_VERSION="$2"; QT_REQUESTED=1; shift ;;
        -h|--help)     grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
    shift
done

# ----------------------------------------------------------------------------
preflight() {
    [ "$(id -u)" = 0 ] && die "run as a normal user (script uses sudo internally)"
    . /etc/os-release
    case "${ID_LIKE:-} ${ID:-}" in
        *debian*|*ubuntu*) log "detected: ${PRETTY_NAME:-unknown}" ;;
        *) warn "not a Debian/Ubuntu derivative — continuing anyway" ;;
    esac
    [ "$(uname -m)" = "x86_64" ] || die "x86_64 required"
    if [ "$IGNORE_SPACE" = 1 ]; then
        warn "skipping free-disk check (--ignore-space)"
    else
        local avail_gb
        avail_gb=$(df --output=avail -BG / | tail -1 | tr -dc '0-9')
        [ "$avail_gb" -lt 8 ] && die "need at least 8 GB free on / (have ${avail_gb}G)"
    fi

    # Must include a Qt >= 6.11: Caelestia master uses QtQuick.Controls
    # DoubleSpinBox (introduced in Qt 6.11). 6.11.2 is what Arch currently
    # ships, i.e. what upstream develops/tests against.
    case "$QT_VERSION" in
        6.11*|6.1[2-9]*) : ;;
        *) die "Qt $QT_VERSION is too old — Caelestia master needs >= 6.11 (DoubleSpinBox)" ;;
    esac
    QT_ROOT="/opt/qt${QT_VERSION%%.*}"
    # Reuse the Qt toolchain the existing quickshell was built against when the
    # user didn't explicitly pick a version — avoids re-downloading Qt on re-runs
    # and ignores stale /opt/qt6/6.x dirs left by previous installers.
    if [ "$QT_REQUESTED" = 0 ] && [ -x /usr/local/bin/qs ]; then
        local _qtdir
        _qtdir=$(ldd /usr/local/bin/qs 2>/dev/null | grep -oE '/opt/qt[0-9]+/[0-9.]+/gcc_64' | head -1 || true)
        if [ -n "$_qtdir" ] && [ -x "$_qtdir/bin/qmake6" ]; then
            QT_ROOT="${_qtdir%/*/*}"               # -> /opt/qt611
            QT_VERSION="${_qtdir#*/opt/qt*/}"      # -> 6.11.2/gcc_64
            QT_VERSION="${QT_VERSION%%/*}"         # -> 6.11.2
            ok "reusing Qt $QT_VERSION at $QT_ROOT (from ldd qs)"
        fi
    fi
    [ "$SKIP_QT" = 1 ] && [ ! -x "$QT_ROOT/$QT_VERSION/gcc_64/bin/qmake6" ] && \
        die "--skip-qt given but Qt $QT_VERSION is not installed at $QT_ROOT"

    log "sudo password required for package installs and /opt, /usr/local writes"
    sudo -v
    ask "Install everything? (Qt $QT_VERSION, Hyprland+PPA, quickshell, caelestia, theme)" || die "aborted"
    mkdir -p "$SRC_ROOT"
}

# ----------------------------------------------------------------------------
stage_apt() {
    [ "$SKIP_APT" = 1 ] && { warn "skipping apt stage"; return 0; }
    log "stage: apt packages"
    sudo apt-get update -y

    # hyprland lives in the cppiber PPA, not the default Ubuntu repos, so the
    # PPA must be present before we can verify the package is installable.
    if ! grep -rq 'cppiber/hyprland' /etc/apt/sources.list.d/ 2>/dev/null; then
        sudo apt-get install -y software-properties-common
        sudo add-apt-repository -y ppa:cppiber/hyprland
        sudo apt-get update -y
    fi
    if ! apt-cache show hyprland >/dev/null 2>&1; then
        die "hyprland not available after adding PPA — apt/PPA broken"
    fi

    # Core: every one of these must install for the build to succeed.
    sudo apt-get install -y \
        build-essential cmake ninja-build git curl wget pkg-config patchelf unzip \
        libgl1-mesa-dev libdrm-dev libgbm-dev libwayland-dev wayland-protocols libxkbcommon-dev \
        libunwind-dev \
        libpipewire-0.3-dev libspa-0.2-dev libaubio-dev \
        libqalculate-dev libsensors-dev \
        libasound2-dev libpulse-dev libfftw3-dev libinih-dev libiniparser-dev \
        autoconf automake libtool meson libffi-dev libexpat1-dev libxml2-dev libcli11-dev \
        hyprland hypridle hyprlock hyprpaper xdg-desktop-portal-hyprland \
        xdg-desktop-portal-gtk network-manager \
        dbus

    # Quickshell build deps: VulkanHeaders (Vulkan), X11, glib, polkit, jemalloc,
    # and PAM (the pam service plugin ships against libpam). These are all
    # REQUIRED by default, so the -dev packages must be present or configure fails.
    sudo apt-get install -y \
        libvulkan-dev libxcb1-dev libglib2.0-dev \
        libpolkit-agent-1-dev libpolkit-gobject-1-dev \
        libjemalloc-dev libpam0g-dev

    # Nice-to-haves: install individually so one missing package on an older
    # release never fails the whole stage.
    local p
    for p in foot wlogout brightnessctl ddcutil lm-sensors swappy policykit-1-gnome qalculate; do
        sudo apt-get install -y "$p" >/dev/null 2>&1 || warn "optional package unavailable on this release: $p"
    done

    # Best-effort flatpak apps referenced by the default binds
    if have flatpak; then
        flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo || true
        flatpak install -y --noninteractive flathub com.google.Chrome || warn "Chrome flatpak not installed"
        flatpak install -y --noninteractive flathub io.github.kolunmi.Bazaar || warn "Bazaar flatpak not installed"
    fi
    ok "apt stage done"
}

# ----------------------------------------------------------------------------
stage_qt() {
    [ "$SKIP_QT" = 1 ] && { warn "skipping Qt stage"; return 0; }
    log "stage: Qt $QT_VERSION (aqtinstall) -> $QT_ROOT"
    if [ -x "$QT_ROOT/$QT_VERSION/gcc_64/bin/qmake6" ]; then
        ok "Qt $QT_VERSION already installed"
        return 0
    fi
    if ! have aqt; then
        if have uv; then uv tool install --force aqtinstall
        elif have pipx; then pipx install --force aqtinstall
        else python3 -m pip install --user --break-system-packages aqtinstall
        fi
    fi
    AQT="$(command -v aqt || echo "$HOME/.local/bin/aqt")"

    sudo mkdir -p "$QT_ROOT"
    sudo chown "$USER:$USER" "$QT_ROOT"
    # aqt >=3.2 renamed the linux desktop arch to linux_gcc_64; fall back for older aqt
    "$AQT" install-qt linux desktop "$QT_VERSION" linux_gcc_64 -O "$QT_ROOT" -m "${QT_MODULES[@]}" \
        || "$AQT" install-qt linux desktop "$QT_VERSION" gcc_64 -O "$QT_ROOT" -m "${QT_MODULES[@]}"

    [ -f "$QT_ROOT/$QT_VERSION/gcc_64/plugins/imageformats/libqwebp.so" ] \
        || die "Qt installed but qtimageformats (webp) missing — remove $QT_ROOT and rerun"
    ok "Qt $QT_VERSION ready (webp included)"
}

# ----------------------------------------------------------------------------
stage_wayland() {
    # Qt 6.11's QtWaylandClient private headers reference `wl_fixes`, which
    # only exists in wayland >= 1.24. Ubuntu 24.04 ships 1.22 — build a
    # current wayland into /usr/local (same soname, ABI-compatible; system
    # packages and GNOME keep working).
    log "stage: wayland 1.26 -> /usr/local"
    if grep -q wl_fixes /usr/local/include/wayland-client-protocol.h 2>/dev/null; then
        ok "wayland headers with wl_fixes already present"
        return 0
    fi
    if [ ! -d "$SRC_ROOT/wayland" ]; then
        git clone --depth 1 --branch 1.26.0 \
            https://gitlab.freedesktop.org/wayland/wayland "$SRC_ROOT/wayland"
    fi
    ( cd "$SRC_ROOT/wayland" && rm -rf build && \
      meson setup build --prefix=/usr/local \
            -Ddocumentation=false -Ddtd_validation=false -Dtests=false \
      && ninja -C build \
      && sudo ninja -C build install \
      && sudo ldconfig )
    grep -q wl_fixes /usr/local/include/wayland-client-protocol.h \
        || die "wayland installed but wl_fixes still missing"
    ok "wayland 1.26 installed to /usr/local"
}

stage_cava() {
    log "stage: libcava (visualiser backend)"
    export PKG_CONFIG_PATH="/usr/local/lib/x86_64-linux-gnu/pkgconfig:${PKG_CONFIG_PATH:-}"
    if pkg-config --exists libcava 2>/dev/null && [ -z "$PIN_CAVA" ]; then
        ok "libcava already present ($(pkg-config --modversion libcava))"
        return 0
    fi
    if [ ! -d "$SRC_ROOT/cava" ]; then
        git clone https://github.com/LukashonakV/cava "$SRC_ROOT/cava"
    else
        git -C "$SRC_ROOT/cava" fetch --quiet origin 2>/dev/null || true
    fi
    if [ -n "$PIN_CAVA" ]; then
        ensure_commit "$SRC_ROOT/cava" "$PIN_CAVA" || die "cava: pinned revision $PIN_CAVA not fetchable"
        git -C "$SRC_ROOT/cava" reset --hard --quiet "$PIN_CAVA"
    fi
    ( cd "$SRC_ROOT/cava" \
      && ./autogen.sh \
      && ./configure --prefix=/usr/local \
      && make -j"$(nproc)" \
      && sudo make install \
      && sudo ldconfig )
    ok "libcava installed to /usr/local"
}

# ----------------------------------------------------------------------------
build_cmake() {  # $1=srcdir $2=prefix $3=extra-args... ; runs configure+build+install
    local src="$1" prefix="$2"; shift 2
    ( cd "$src" && rm -rf build && \
      env -u LD_LIBRARY_PATH -u QML_IMPORT_PATH \
          PKG_CONFIG_PATH="/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig" \
      cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo \
            -DCMAKE_INSTALL_PREFIX="$prefix" \
            -DCMAKE_PREFIX_PATH="$QT_ROOT/$QT_VERSION/gcc_64${prefix:+;/usr/local}" \
            "$@" > /tmp/caelestia-setup-cmake.log 2>&1 ) \
      || { grep -B2 -A8 -iE 'error' /tmp/caelestia-setup-cmake.log | head -40; die "cmake configure failed for $src (see /tmp/caelestia-setup-cmake.log)"; }
    ( cd "$src" && env -u LD_LIBRARY_PATH -u QML_IMPORT_PATH \
          PKG_CONFIG_PATH="/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig" \
      cmake --build build -j"$(nproc)" ) || die "build failed for $src"
}

stage_quickshell() {
    log "stage: quickshell (pinned revision or master)"
    # Primary clone host is the upstream self-hosted forgejo; fall back to the
    # GitHub mirror so a common user is never blocked by one host being down.
    if [ ! -d "$SRC_ROOT/quickshell" ]; then
        git clone https://git.outfoxxed.me/quickshell/quickshell "$SRC_ROOT/quickshell" 2>/dev/null \
            || { warn "git.outfoxxed.me unreachable — cloning GitHub mirror"; \
                 git clone https://github.com/outfoxxed/quickshell "$SRC_ROOT/quickshell" \
                    || die "quickshell clone failed (both upstream and mirror)"; }
    else
        git -C "$SRC_ROOT/quickshell" fetch --quiet origin 2>/dev/null \
            || { warn "quickshell upstream unreachable — switching remote to GitHub mirror"; \
                 git -C "$SRC_ROOT/quickshell" remote set-url origin https://github.com/outfoxxed/quickshell; \
                 git -C "$SRC_ROOT/quickshell" fetch --quiet origin 2>/dev/null || true; }
    fi
    # Build the pinned known-good revision (revisions.conf) so a fresh install
    # is reproducible instead of tracking whatever master is today.
    if [ -n "$PIN_QS" ]; then
        ensure_commit "$SRC_ROOT/quickshell" "$PIN_QS" || die "quickshell: pinned revision $PIN_QS not fetchable"
        git -C "$SRC_ROOT/quickshell" checkout --quiet "$PIN_QS" || die "quickshell: cannot check out pinned revision $PIN_QS"
        ok "quickshell at pinned revision $(git -C "$SRC_ROOT/quickshell" rev-parse --short=12 HEAD)"
    fi
    # -DCMAKE_INSTALL_LIBDIR=lib keeps quickshell's own libs under /usr/local/lib
    # so qs's $ORIGIN/../lib RPATH resolves them (multiarch libdir isn't on RPATH).
    # -DINSTALL_QMLDIR=/usr/local/lib/qt6/qml: quickshell gates QML-module
    # installation behind INSTALL_QMLDIR (see cmake/install-qml-module.cmake) and
    # installs NOTHING if it's unset. Qt's install_qml_module also ignores the
    # project libdir, so GNUInstallDirs would otherwise route the Quickshell qml
    # module to the multiarch /usr/local/lib/x86_64-linux-gnu/qt6/qml — which Qt's
    # default QML2 search paths do NOT scan. Pointing INSTALL_QMLDIR at Qt's
    # canonical qml path fixes both CI (ci-verify checks /usr/local/lib/qt6/qml/Quickshell)
    # and fresh-Ubuntu runtime (Quickshell.* types resolve without help).
    build_cmake "$SRC_ROOT/quickshell" "/usr/local" \
        -DVENDOR_CPPTRACE=ON \
        -DCMAKE_INSTALL_LIBDIR=lib \
        -DINSTALL_QMLDIR=/usr/local/lib/qt6/qml \
        "-DCMAKE_INSTALL_RPATH=$QT_ROOT/$QT_VERSION/gcc_64/lib;\$ORIGIN;\$ORIGIN/../lib"
    sudo cmake --install "$SRC_ROOT/quickshell/build" >/dev/null
    # Bake the Qt lib path in (force-rpath = DT_RPATH so transitive Qt libs resolve);
    # keeps `qs` IPC calls working with NO session-wide LD_LIBRARY_PATH. Patching the
    # real ELF (qs is a symlink to it) makes this idempotent and reliable.
    sudo patchelf --force-rpath \
        --set-rpath "$QT_ROOT/$QT_VERSION/gcc_64/lib:\$ORIGIN:\$ORIGIN/../lib" \
        /usr/local/bin/quickshell 2>/dev/null || true
    env -u LD_LIBRARY_PATH /usr/local/bin/qs --version >/dev/null \
        || die "quickshell installed but does not run"
    ok "quickshell $(env -u LD_LIBRARY_PATH /usr/local/bin/qs --version 2>/dev/null | head -1)"
}

stage_m3shapes() {
    log "stage: m3shapes (Material 3 shape QML module)"
    if [ ! -d "$SRC_ROOT/m3shapes" ]; then
        git clone https://github.com/soramanew/m3shapes "$SRC_ROOT/m3shapes"
    else
        git -C "$SRC_ROOT/m3shapes" fetch --quiet origin 2>/dev/null || true
    fi
    if [ -n "$PIN_M3S" ]; then
        ensure_commit "$SRC_ROOT/m3shapes" "$PIN_M3S" || die "m3shapes: pinned revision $PIN_M3S not fetchable"
        git -C "$SRC_ROOT/m3shapes" reset --hard --quiet "$PIN_M3S"
    fi
    build_cmake "$SRC_ROOT/m3shapes" "/" -DCMAKE_INSTALL_LIBDIR=lib
    sudo cmake --install "$SRC_ROOT/m3shapes/build" >/dev/null
    [ -d /lib/qt6/qml/M3Shapes ] || [ -d /usr/lib/qt6/qml/M3Shapes ] || die "m3shapes not installed where expected"
    ok "m3shapes installed"
}

stage_shell() {
    log "stage: caelestia shell -> ~/.config/quickshell/caelestia"
    local dir="$HOME/.config/quickshell/caelestia"
    mkdir -p "$HOME/.config/quickshell"
    if [ -d "$dir/.git" ]; then
        if [ -n "$(git -C "$dir" status --porcelain 2>/dev/null | head -1)" ]; then
            git -C "$dir" branch -f backup-local-changes
            # also preserve untracked files (git clean -fd would remove them)
            local ub="$HOME/.cache/caelestia-shell-untracked-backup-$(date +%Y%m%d-%H%M%S)"
            mkdir -p "$ub"
            git -C "$dir" status --porcelain | grep '^??' | sed 's/^?? //' | while read -r f; do
                cp -a "$dir/$f" "$ub/" 2>/dev/null || true
            done
            warn "local changes found in $dir — tracked changes saved to branch backup-local-changes, untracked files copied to $ub"
        fi
        git -C "$dir" fetch origin
    elif [ -d "$dir" ]; then
        sudo rm -rf "$dir"
        git clone https://github.com/caelestia-dots/shell.git "$dir"
    else
        git clone https://github.com/caelestia-dots/shell.git "$dir"
    fi
    # Build the pinned known-good revision (revisions.conf). Empty PIN_CAEL
    # keeps the upstream default branch (legacy behaviour).
    if [ -n "$PIN_CAEL" ]; then
        ensure_commit "$dir" "$PIN_CAEL" || die "caelestia: pinned revision $PIN_CAEL not fetchable"
        git -C "$dir" reset --hard "$PIN_CAEL" >/dev/null || die "caelestia: cannot reset to pinned revision $PIN_CAEL"
        git -C "$dir" clean -fd >/dev/null 2>&1 || true
        ok "caelestia shell at pinned revision $(git -C "$dir" rev-parse --short=12 HEAD)"
    else
        git -C "$dir" reset --hard origin/main >/dev/null 2>&1 || true
        git -C "$dir" clean -fd >/dev/null 2>&1 || true
    fi
    build_cmake "$dir" "/" -DCMAKE_INSTALL_LIBDIR=lib
    sudo cmake --install "$dir/build" >/dev/null
    local v
    v=$(/usr/lib/caelestia/version -s 2>/dev/null || echo "unknown")
    [ -x /usr/lib/caelestia/version ] || die "caelestia shell install incomplete"
    ok "caelestia shell $v installed (no Qt patches needed on Qt $QT_VERSION)"
}

stage_cli() {
    log "stage: caelestia CLI (best effort)"
    local spec="git+https://github.com/caelestia-dots/cli${PIN_CLI:+@$PIN_CLI}"
    if have uv; then
        uv tool install --force "$spec" \
            && ok "caelestia CLI via uv (pinned ${PIN_CLI:0:12})" && return 0
    elif have pipx; then
        pipx install --force "caelestia-cli @ $spec" \
            && ok "caelestia CLI via pipx (pinned ${PIN_CLI:0:12})" && return 0
    fi
    warn "caelestia CLI not installed (uv/pipx missing) — shell still works; CLI adds 'caelestia shell -d' autostart, theme CLI and wallpaper commands"
}

stage_fonts() {
    [ "$SKIP_FONTS" = 1 ] && { warn "skipping fonts"; return 0; }
    log "stage: fonts"
    local fd="$HOME/.local/share/fonts/caelestia"
    mkdir -p "$fd"
    dl() { [ -f "$2" ] || curl -fL --retry 3 -o "$2" "$1" || warn "font download failed: $1"; }
    dl "https://github.com/google/material-design-icons/raw/master/variablefont/MaterialSymbolsRounded%5BFILL,GRAD,opsz,wght%5D.ttf" \
       "$fd/MaterialSymbolsRounded.ttf"
    dl "https://github.com/google/fonts/raw/main/ofl/rubik/Rubik%5Bwght%5D.ttf" "$fd/Rubik.ttf"
    dl "https://github.com/google/fonts/raw/main/ofl/rubik/Rubik-Italic%5Bwght%5D.ttf" "$fd/Rubik-Italic.ttf"
    if [ ! -f "$fd/CaskaydiaCoveNerdFont-Regular.ttf" ]; then
        curl -fL --retry 3 -o /tmp/CascadiaCode.zip \
            https://github.com/ryanoasis/nerd-fonts/releases/latest/download/CascadiaCode.zip \
            && (mkdir -p /tmp/cascadia && unzip -oq /tmp/CascadiaCode.zip -d /tmp/cascadia \
                && cp /tmp/cascadia/*.ttf "$fd/") \
            || warn "CascadiaCode NF download failed"
        rm -f /tmp/CascadiaCode.zip
    fi
    fc-cache -f >/dev/null 2>&1
    ok "fonts installed (Material Symbols Rounded, Rubik, CaskaydiaCove NF)"
}

# ----------------------------------------------------------------------------
stage_config() {
    [ "$SKIP_CONFIG" = 1 ] && { warn "skipping config deploy"; return 0; }
    log "stage: deploying theme + configs"

    if ask "Deploy the maintainer's theme to ~/.config (backs up anything existing first)?" || [ "$ASSUME_YES" = 1 ]; then
        local ts bdir
        ts=$(date +%Y%m%d-%H%M%S)
        bdir="$HOME/.config/caelestia-ubuntu-backup-$ts"
        for d in "$HOME/.config/hypr" "$HOME/.config/caelestia" \
                 "$HOME/.config/systemd/user/caelestia-shell.service" \
                 "$HOME/.config/systemd/user/caelestia-shell.service.d"; do
            [ -e "$d" ] && mkdir -p "$bdir/$(dirname "$d")" && cp -a "$d" "$bdir/"
        done
        [ -d "$bdir" ] && warn "existing configs backed up to $bdir"

        mkdir -p "$HOME/.config/hypr" "$HOME/.config/caelestia/monitors/eDP-1" \
                 "$HOME/.config/quickshell" "$HOME/.local/bin" \
                 "$HOME/.config/systemd/user" \
                 "$HOME/Pictures/Wallpapers" "$HOME/Pictures/wallpapers"

        local qt_prefix="$QT_ROOT/$QT_VERSION/gcc_64"
        local run_uid; run_uid="$(id -u)"
        # Deploy templated configs: __HOME__ -> user home, __QT__ -> Qt prefix,
        # __UID__ -> invoking uid (system-sleep hooks run as root). %h/%U in the
        # systemd drop-in resolve per-user at runtime.
        template() { sed "s|__HOME__|$HOME|g; s|__QT__|$qt_prefix|g"; }

        sed "s|__HOME__|$HOME|g; s|__QT__|$qt_prefix|g" \
            "$REPO_DIR/configs/hypr/hyprland.conf"              > "$HOME/.config/hypr/hyprland.conf"
        cp "$REPO_DIR/configs/hypr/perf-overrides.conf"         "$HOME/.config/hypr/"
        template < "$REPO_DIR/configs/hypr/optional/hypridle.conf"    > "$HOME/.config/hypr/hypridle.conf"
        template < "$REPO_DIR/configs/hypr/optional/hyprpaper.conf"    > "$HOME/.config/hypr/hyprpaper.conf"
        template < "$REPO_DIR/configs/hypr/hyprlock.conf"             > "$HOME/.config/hypr/hyprlock.conf"
        template < "$REPO_DIR/configs/caelestia/shell.json"           > "$HOME/.config/caelestia/shell.json"
        echo '{ }' > "$HOME/.config/caelestia/monitors/eDP-1/shell.json"

        template < "$REPO_DIR/configs/systemd/user/caelestia-shell.service" \
            > "$HOME/.config/systemd/user/caelestia-shell.service"
        mkdir -p "$HOME/.config/systemd/user/caelestia-shell.service.d"
        cp "$REPO_DIR/configs/systemd/user/caelestia-shell.service.d/"*.conf \
            "$HOME/.config/systemd/user/caelestia-shell.service.d/"
        cp "$REPO_DIR/configs/bin/caelestia-relock.sh" "$HOME/.local/bin/" \
            && chmod +x "$HOME/.local/bin/caelestia-relock.sh"

        # system-sleep hooks run as root; bake the invoking user's uid in.
        sed "s|__UID__|$run_uid|g" \
            "$REPO_DIR/configs/system-sleep/caelestia-shell-restart.sh" \
            | sudo tee /usr/lib/systemd/system-sleep/caelestia-shell-restart.sh >/dev/null
        sed "s|__UID__|$run_uid|g" \
            "$REPO_DIR/configs/system-sleep/caelestia-relock-marker.sh" \
            | sudo tee /usr/lib/systemd/system-sleep/caelestia-relock-marker.sh >/dev/null
        sudo chmod +x /usr/lib/systemd/system-sleep/caelestia-shell-restart.sh \
                          /usr/lib/systemd/system-sleep/caelestia-relock-marker.sh

        cp "$REPO_DIR/configs/wallpapers/wallpaper.jpg" "$HOME/Pictures/Wallpapers/wallpaper.jpg"
        cp "$REPO_DIR/configs/wallpapers/wallpaper.jpg" "$HOME/Pictures/wallpapers/wallpaper.jpg"

        # Pre-seed the shell's wallpaper state so the maintainer's wallpaper and
        # its dynamic colour scheme are active from the very first login (the
        # shell normally writes this itself when a wallpaper is chosen).
        mkdir -p "$HOME/.local/state/caelestia/wallpaper"
        printf '%s\n' "$HOME/Pictures/wallpapers/wallpaper.jpg" > "$HOME/.local/state/caelestia/wallpaper/path.txt"
        ln -sf "$HOME/Pictures/wallpapers/wallpaper.jpg" "$HOME/.local/state/caelestia/wallpaper/current"
        # If the shell is already running (re-run of this script), apply live too.
        if systemctl --user is-active --quiet caelestia-shell.service 2>/dev/null && have caelestia; then
            caelestia wallpaper -f "$HOME/Pictures/wallpapers/wallpaper.jpg" 2>/dev/null \
                || warn "could not apply wallpaper via CLI — it will be used on next login"
        fi

        # polkit agent path used by hyprland.conf
        if [ ! -x /usr/libexec/polkit-gnome-authentication-agent-1 ]; then
            local agent
            agent=$(dpkg -L policykit-1-gnome 2>/dev/null | grep -m1 'authentication-agent' || true)
            if [ -n "$agent" ] && [ -x "$agent" ]; then
                sudo mkdir -p /usr/libexec
                sudo ln -sf "$agent" /usr/libexec/polkit-gnome-authentication-agent-1
            else
                warn "polkit gnome agent not found — auth prompts may need another agent"
            fi
        fi

        # hypridle must NOT run user-wide: Caelestia IdleMonitors owns idle/lock/suspend
        sudo systemctl --global disable hypridle.service 2>/dev/null || true

        # GNOME's portal would claim the xdg-desktop-portal role inside the
        # Hyprland session and fight the hyprland/gtk portals (screencast, file
        # dialogs). Mask it and let the session portals own the role — this is
        # what the maintainer's working setup does. GNOME itself is unaffected
        # because only one graphical session per user is active at a time.
        systemctl --user mask xdg-desktop-portal-gnome 2>/dev/null || true
        systemctl --user daemon-reload 2>/dev/null || true
        systemctl --user restart xdg-desktop-portal xdg-desktop-portal-hyprland \
            xdg-desktop-portal-gtk 2>/dev/null || true
    fi
    ok "config stage done"
}

# ----------------------------------------------------------------------------
stage_verify() {
    log "stage: post-install verification"
    local fail=0
    env -u LD_LIBRARY_PATH /usr/local/bin/qs --version >/dev/null 2>&1 \
        && ok "quickshell runs" || { warn "quickshell does not run"; fail=1; }
    [ -x /usr/lib/caelestia/version ] \
        && ok "caelestia shell $(/usr/lib/caelestia/version -s 2>/dev/null)" || { warn "caelestia version helper missing"; fail=1; }
    [ -d /lib/qt6/qml/Caelestia ] || [ -d /usr/lib/qt6/qml/Caelestia ] \
        && ok "Caelestia QML module present" || { warn "Caelestia QML module missing"; fail=1; }
    [ -d /lib/qt6/qml/M3Shapes ] || [ -d /usr/lib/qt6/qml/M3Shapes ] \
        && ok "M3Shapes module present" || { warn "M3Shapes missing"; fail=1; }
    [ -f "$QT_ROOT/$QT_VERSION/gcc_64/plugins/imageformats/libqwebp.so" ] \
        && ok "Qt webp support present" || { warn "webp plugin missing"; fail=1; }
    grep -q wl_fixes /usr/local/include/wayland-client-protocol.h 2>/dev/null \
        && ok "wayland headers ok (wl_fixes)" || { warn "wayland headers incomplete"; fail=1; }
    if [ "$SKIP_FONTS" = 1 ]; then
        warn "fonts skipped (--skip-fonts), skipping font checks"
    else
        fc-list 2>/dev/null | grep -qiE 'Material Symbols' \
            && ok "Material Symbols font present" || { warn "Material Symbols font missing — icons will be blank"; fail=1; }
    fi
    if [ "$SKIP_CONFIG" = 1 ]; then
        warn "config deploy skipped (--skip-config), skipping wallpaper checks"
    else
        [ -f "$HOME/Pictures/wallpapers/wallpaper.jpg" ] \
            && ok "wallpaper deployed" || { warn "wallpaper missing"; fail=1; }
    fi
    hyprctl version >/dev/null 2>&1 \
        && ok "hyprland on PATH" || ok "hyprland installed (session entry appears after re-login)"
    [ "$fail" = 0 ] && ok "ALL CHECKS PASSED" || die "one or more checks failed — see the ! lines above"
}

# ----------------------------------------------------------------------------
stage_manifest() {
    # Record what was installed so update.sh can detect drift without rebuilding
    # the whole toolchain. Re-runs overwrite the file (idempotent).
    local manifest_dir="$HOME/.local/share/caelestia-ubuntu"
    local manifest="$manifest_dir/manifest"
    local rev
    mkdir -p "$manifest_dir"
    : > "$manifest"
    {
        echo "QT_VERSION=$QT_VERSION"
        echo "QT_ROOT=$QT_ROOT"
        echo "QT_PREFIX=$QT_ROOT/$QT_VERSION/gcc_64"
        echo "SRC_ROOT=$SRC_ROOT"
        echo "INSTALLED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        rev=$(git -C "$SRC_ROOT/quickshell" rev-parse --short=12 HEAD 2>/dev/null || true); echo "quickshell=$rev"
        rev=$(git -C "$HOME/.config/quickshell/caelestia" rev-parse --short=12 HEAD 2>/dev/null || true); echo "caelestia=$rev"
        rev=$(git -C "$SRC_ROOT/m3shapes" rev-parse --short=12 HEAD 2>/dev/null || true); echo "m3shapes=$rev"
        rev=$(git -C "$SRC_ROOT/cava" rev-parse --short=12 HEAD 2>/dev/null || true); echo "cava=$rev"
        if have uv; then rev=$(uv tool list 2>/dev/null | awk '/caelestia-cli/{print $2}' || true); echo "caelestia_cli=$rev"
        elif have pipx; then rev=$(pipx list --format=json 2>/dev/null | jq -r '.installed."caelestia-cli".version' 2>/dev/null || true); echo "caelestia_cli=$rev"; fi
    } >> "$manifest"
    ok "manifest written to $manifest (run update.sh to check for upgrades)"
}

# ----------------------------------------------------------------------------
main() {
    load_pins
    preflight
    stage_apt
    stage_qt
    stage_wayland
    stage_cava
    stage_quickshell
    stage_m3shapes
    stage_shell
    stage_cli
    stage_fonts
    stage_config
    sudo ldconfig
    stage_verify
    stage_manifest

    echo
    log "install complete. next steps:"
    echo "  1. reboot  (or log out)"
    echo "  2. at the GDM login screen pick your user, click the gear icon, choose 'Hyprland'"
    echo "  3. log in — the Caelestia shell starts automatically"
    echo
    echo "  verify once inside Hyprland:"
    echo "    hyprctl configerrors                       # should print nothing"
    echo "    systemctl --user status caelestia-shell    # should be active"
    echo
    echo "  GNOME stays untouched — pick 'Zorin'/'Ubuntu' in GDM any time."
    echo "  Qt $QT_VERSION lives in $QT_ROOT (safe to delete only after uninstalling this setup)."
    echo "  update the shell anytime with: $(dirname "$0")/update.sh"
    echo "  uninstall any time with: $(dirname "$0")/uninstall.sh"
}

main "$@"
