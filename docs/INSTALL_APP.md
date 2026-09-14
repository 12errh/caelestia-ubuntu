# Installing the Caelestia for Ubuntu app

The repository ships two ways to run the graphical app. Neither uses any
third-party Python packages — the app is pure Python + PyGObject
(GTK4/libadwaita).

> The app installs and manages the **Caelestia desktop** (Hyprland +
> Quickshell). This document is only about installing *the app itself*; the
> desktop is built from within the app (Install tab → `setup.sh`).

## Requirements

The runtime dependencies are the standard GTK4 bindings, already present on
Ubuntu 24.04, Zorin OS 18, Linux Mint 22 and Pop!_OS 22.04 GNOME desktops:

| Package | Why |
|---|---|
| `python3` (≥ 3.10) | runs the app |
| `python3-gi` | Python GObject bindings |
| `gir1.2-gtk-4.0` | GTK4 |
| `gir1.2-adw-1` | libadwaita |
| `sudo` | drives the install scripts |
| `xdg-utils` (recommended) | "Open shell.json in editor" |

`./packaging/build-deb.sh` declares these as `Depends`, so `apt` installs
them automatically.

## Option A — .deb package (recommended)

```bash
git clone https://github.com/12errh/caelestia-ubuntu.git
cd caelestia-ubuntu
./packaging/build-deb.sh
sudo apt install ./dist/caelestia-installer_*_all.deb
```

Then launch **Caelestia for Ubuntu** from the applications menu, or run:

```bash
caelestia-installer
```

### What the package installs

```
/usr/bin/caelestia-installer                                   # launcher
/usr/share/caelestia-installer/app/                            # the Python app
/usr/share/caelestia-installer/repo/                           # setup/update/uninstall + configs
/usr/share/applications/io.github.CaelestiaUbuntu.Installer.desktop
/usr/share/icons/hicolor/scalable/apps/io.github.CaelestiaUbuntu.Installer.svg
```

On first launch the launcher copies the repo scripts/configs to a
**user-writable** location:

```
~/.local/share/caelestia-installer/repo/
```

and points `CAEL_REPO_DIR` at it. This is deliberate: the Updates →
Advanced workflow writes `revisions.conf`, and `/usr/share` is root-owned.
Keeping the working copy in your home means **Keep / Revert / Update
latest upstream** work without root, and the user's tested pins are never
clobbered on package upgrades (scripts and configs refresh; `revisions.conf`
is preserved).

### Uninstall

```bash
sudo apt remove caelestia-installer
# remove the app's user data too, if you like:
rm -rf ~/.local/share/caelestia-installer
```

## Option B — install script (from a clone)

```bash
sudo ./app/install.sh        # system-wide (/usr/local), appears in the app grid
./app/install.sh --user      # current user only (~/.local)
```

This copies the app and repo to `/usr/local/share/caelestia-installer`
(or `~/.local/share/...`) and installs a `caelestia-installer` launcher.

## Option C — run straight from a clone (no install)

```bash
./app/run.py            # GUI
./app/run.py --check    # headless system report, no GUI needed
```

## Other formats

An **AppImage** would require bundling PyGObject, GI typelibs, libadwaita
and Gio modules, which is fragile and large; **Flatpak** cannot run host
`setup.sh`/`sudo`/`systemctl`, which the installer needs. For these
reasons the project ships a native `.deb` for the Ubuntu family only. RPMs
are not provided (the project targets Ubuntu-family distributions).
