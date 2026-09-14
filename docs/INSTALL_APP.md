# Installing the Caelestia for Ubuntu app

The repository ships a graphical installer/setup/updates app
(GTK4/libadwaita, pure Python + PyGObject). This document is only about
installing *the app itself*; the Caelestia desktop is built from within the
app (Install tab → `setup.sh`).

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

The `.deb` declares these as `Depends`, so `apt` installs them
automatically.

## Install from a release (.deb)

Grab the latest `.deb` from the
[GitHub Releases](https://github.com/12errh/caelestia-ubuntu/releases)
and install it:

```bash
sudo apt install ./caelestia-installer_*_all.deb
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

## Other ways to run the app

### From a clone (no install)

```bash
./app/run.py            # GUI
./app/run.py --check    # headless system report, no GUI needed
```

### Install script (from a clone)

```bash
sudo ./app/install.sh        # system-wide (/usr/local), appears in the app grid
./app/install.sh --user      # current user only (~/.local)
```

This copies the app and repo to `/usr/local/share/caelestia-installer`
(or `~/.local/share/...`) and installs a `caelestia-installer` launcher.

## Building the package (maintainers)

If you want to build the `.deb` yourself instead of downloading from
Releases:

```bash
git clone https://github.com/12errh/caelestia-ubuntu.git
cd caelestia-ubuntu
./packaging/build-deb.sh                     # -> dist/caelestia-installer_<ver>_all.deb
sudo apt install ./dist/caelestia-installer_*_all.deb
```

The CI workflow (`.github/workflows/deb.yml`) automates this and attaches
the package to every tagged release.

## Other formats

An **AppImage** would require bundling PyGObject, GI typelibs, libadwaita
and Gio modules, which is fragile and large; **Flatpak** cannot run host
`setup.sh`/`sudo`/`systemctl`, which the installer needs. For these
reasons the project ships a native `.deb` for the Ubuntu family only. RPMs
are not provided (the project targets Ubuntu-family distributions).
