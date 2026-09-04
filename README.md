# caelestia-ubuntu

**Caelestia Shell (Quickshell) + Hyprland, on Ubuntu 24.04 / Zorin OS 18 / Mint 22 / Pop!_OS 22.04 — one script, zero manual config.**

Caelestia officially supports Arch-based distributions only. This repository
backports the full experience to Ubuntu-family systems: it builds Quickshell and
the Caelestia shell from source against a pinned **Qt 6.11.2** (the same Qt
version Arch currently ships, which is what Caelestia upstream develops against),
installs every dependency, and deploys the maintainer's exact desktop — theme,
panels, animations, wallpapers, fonts, idle/lock behaviour — as a **side-by-side
session**. GNOME stays completely untouched.

```bash
git clone https://github.com/12errh/caelestia-ubuntu.git
cd caelestia-ubuntu
sudo apt install -y shellcheck   # optional, for the lint step
./setup.sh                       # interactive; --yes for unattended
```

Reboot, pick **Hyprland** in the GDM gear menu, and log in.

---

## Screenshots

This is what you get on the first login — no tweaking required.

| | |
|---|---|
| ![Desktop](assets/screenshot-desktop.png) | ![Dashboard](assets/screenshot-dashboard.png) |
| *Desktop — wallpaper, bar, audio visualiser* | *Dashboard — quick toggles, system info* |
| ![Launcher](assets/screenshot-launcher.png) | ![Session](assets/screenshot-session.png) |
| *Launcher — apps, `>` actions, `>wallpaper` picker* | *Session — logout / reboot / shutdown* |

The whole interface (bars, drawers, lock screen, OSD) recolours itself from the
wallpaper. Switch it with `>wallpaper` in the launcher or
`caelestia wallpaper -f <file>`.

## What you get

The same configuration running on the maintainer's machine:

| Area | Details |
|---|---|
| Theme | Rubik / CaskaydiaCove NF / Material Symbols Rounded, 0.55 base / 0.42 layer transparency, 1.35 rounding |
| Background | animated wallpaper layer with a blurred desktop clock (bottom-right, 1.15×) + 51-bar audio visualiser (blurred, auto-hide) |
| Bar | logo → workspaces → active window → tray → clock → status → power, hover-reveal, scroll actions |
| Animations | global duration scale 1.4, smooth bezier window motion |
| Idle / lock | 3 min lock → 5 min dpms off → 10 min suspend-then-hibernate, audio-aware inhibition, fingerprint unlock via quickshell PAM |
| Resume | shell auto-bounce + automatic lock re-acquisition after lid open (systemd-sleep hooks included) |
| Keybinds | SUPER-centred workflow incl. resize submap, hyprshot, wlogout, launcher/drawer IPC |

### Installed components

- **Qt 6.11.2** toolchain under `/opt` (via `aqtinstall`, includes
  `qtimageformats`/webp + `qtshadertools`). On re-runs an *existing* Qt that the
  deployed `qs` already links is reused — no re-download.
- **wayland 1.26** → `/usr/local` (Qt 6.11's Wayland private headers need the
  `wl_fixes` type, absent from Ubuntu 24.04's wayland 1.22; the newer wayland is
  ABI-compatible, so GNOME and system packages are unaffected).
- **Hyprland** + `hyprlock`/`hypridle`/`hyprpaper` + portals (PPA
  `ppa:cppiber/hyprland`).
- **Quickshell** (git master) built with vendored `cpptrace`, RPATH-baked so no
  global `LD_LIBRARY_PATH` is ever needed.
- **Caelestia shell** (git master) — QML tree in `~/.config/quickshell/caelestia`,
  plugin in `/lib/qt6/qml/Caelestia`.
- **m3shapes** (Material 3 shapes) + **libcava** (audio visualiser backend).
- **caelestia CLI** (via `uv`/`pipx`), all fonts, and Chrome + Bazaar flatpaks
  (best-effort, non-fatal).

## Requirements

- Ubuntu 24.04 or a derivative (Zorin 18, Mint 22, Pop!_OS 22.04), x86_64.
- ~8 GB free on `/` (Qt toolchain ≈ 1.5 GB, build caches ≈ 2 GB). CI runners
  redirect the heavy writes onto a large workspace disk — on a normal machine you
  can pass `--ignore-space` if `/` is tight and a bigger disk is mounted.
- Working internet connection, `sudo` access.
- A GPU with Mesa graphics drivers (Intel iGPU Haswell and newer, AMD, or
  NVIDIA with `mesa`/`nouveau`; proprietary NVIDIA works but is untested here).

## Usage

```bash
./setup.sh            # interactive
./setup.sh --yes      # unattended install
```

| Flag | Effect |
|---|---|
| `--yes` | no confirmation prompts |
| `--skip-apt` | skip the package stage (re-runs) |
| `--skip-qt` | Qt already installed |
| `--skip-fonts` | skip font downloads |
| `--skip-config` | build only; do not touch `~/.config` |
| `--qt-version X` | use a different Qt (must be ≥ 6.11) |
| `--ignore-space` | skip the 8 GB free-disk check |

The installer is **idempotent** — re-running it updates sources and rebuilds
only what moved, and reuses an already-installed Qt toolchain. Existing user
configs are backed up to `~/.config/caelestia-ubuntu-backup-<timestamp>` before
anything is overwritten.

> **CI note:** the GitHub Actions workflow runs `setup.sh --yes --skip-fonts` on a
> fresh Ubuntu 24.04 runner and then asserts on the build's outputs (built
> binaries, templated configs, deployed Qt modules) in
> [`scripts/ci-verify-install.sh`](scripts/ci-verify-install.sh). Hyprland itself
> is **not** launched there — aquamarine (its backend) needs a DRM render node
> for a GBM allocator, which GPU-less CI runners don't expose.

## Update

```bash
./update.sh        # check + apply (prompts before rebuild)
./update.sh --yes  # apply non-interactively
./update.sh --check   # read-only: report drift without cloning or building
```

`update.sh` reads the manifest written by `setup.sh`, compares each local
component's revision against upstream (`git ls-remote` — no local clones needed
in `--check`), rebuilds only the components that moved using the **same** Qt
toolchain, and restarts the running shell service only when something changed
*and* the service is active. Qt itself is a fixed toolchain — bump it with
`./setup.sh --qt-version X`.

> `update.sh` rebuilds *sources* only. To refresh the shipped configs/hooks
> after a repo change (e.g. the lock-restore fix), re-run the config deploy:
> `./setup.sh --skip-apt --skip-qt --skip-fonts --yes`.

| Flag | Effect |
|---|---|
| `--check` | read-only status report (no clones, no builds) |
| `--yes` | apply without prompting |
| `--force` | rebuild even when local matches upstream |
| `--no-restart` | keep the running shell service as-is |

## Uninstall

```bash
./uninstall.sh            # everything except Qt
./uninstall.sh --purge-qt # also remove /opt/qt* (~2 GB freed)
```

User configs are preserved at `~/.config/caelestia-ubuntu-uninstalled-<date>`.
GNOME is never modified by either script.

## Troubleshooting

- **Black screen at login / no bar**: check
  `systemctl --user status caelestia-shell` and
  `journalctl --user -u caelestia-shell -e`.
- **`qs` IPC binds do nothing**: ensure no global `LD_LIBRARY_PATH` points at an
  old Qt; the binary's baked RPATH resolves Qt on its own.
- **`hyprctl configerrors` looks wrong**: run `hyprctl reload`.
- **Lock screen doesn't appear after lid resume / frozen "lock screen died" screen**: the installer ships `misc.allow_session_lock_restore = true` plus the systemd-sleep hooks that re-acquire the Caelestia lock on wake — if you still hit it, the hooks were templated with the installing user's id at install time. Re-run `./setup.sh --yes` to re-deploy them if your uid isn't 1000.
- **`hyprlock` exits immediately when run manually**: the repo now ships a valid `~/.config/hypr/hyprlock.conf`, but hyprlock can't lock while Caelestia already holds the session lock (that's normal — Super+L is the Caelestia lock).
- **Missing Calculator action in the launcher**: `sudo apt install qalculate`
  (the `qalc` CLI, not just the library).
- Wayland-native Qt 6.11 apps and GNOME apps are unaffected by this install —
  only `qs` uses the `/opt/qt*` toolchain.

## Licenses

Repository scripts are MIT-licensed. Installed upstream components keep their own
licenses — see [`LICENSE`](LICENSE).
