# Installing the Caelestia for Ubuntu app

The repository ships a graphical installer/setup/updates app
(GTK4/libadwaita, pure Python + PyGObject). This document is only about
installing *the app itself*; the Caelestia desktop is built from within the
app (Install tab → `setup.sh`).

## Requirements

The GUI requires **GTK ≥ 4.14 and libadwaita ≥ 1.5**, as supplied by Ubuntu
24.04 and compatible derivatives (for example Zorin OS 18 and Mint 22).
Stock Ubuntu/Pop!_OS 22.04 has older libraries and cannot run this GUI without
upgrading them; the command-line scripts have separate requirements.

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
/usr/share/icons/hicolor/128x128/apps/io.github.CaelestiaUbuntu.Installer.png
```

On first launch the launcher copies the repo scripts/configs to a
**user-writable** location:

```
~/.local/share/caelestia-installer/repo/
```

and points `CAEL_REPO_DIR` at it. This is deliberate: the
Advanced tab writes `revisions.conf`, and `/usr/share` is root-owned.
Keeping the working copy in your home means **Keep / Revert / Update
latest upstream** work without root, and the user's tested pins are never
clobbered on package upgrades (scripts and configs refresh; `revisions.conf`
is preserved).

### Updating the app

Open **Updates**: the first visit per app session checks GitHub for a newer stable
`vMAJOR.MINOR.PATCH` release with an uploaded package. **Recheck now** retries,
including after offline or rate-limit errors. **Get app update** asks before
opening the official release page. Download the `.deb`, finish any desktop build,
close the app, install that package with your software installer or `sudo apt
install /absolute/path/to/package.deb`, and relaunch.

This is **update notification plus manual installation**, not automatic package
installation or an APT repository. It does not rebuild desktop components.
Published desktop revisions are checked separately; **Use published pins** and
**Apply updates** are explicit steps. Existing local pins survive app upgrades.

If running from a clone, update that checkout and restart. For `install.sh`
installs, update the source and rerun the same installation command. Do not mix
user-local, `/usr/local`, and `.deb` installs: older launchers/icons can shadow the
updated package. Versions without this checker need one manual upgrade first.

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

The CI workflow (`.github/workflows/deb.yml`) builds on main pushes and pull
requests, and attaches a package plus `SHA256SUMS` to `vMAJOR.MINOR.PATCH` tags.
Other version formats are rejected by the package builder.

### Release checklist

1. On a supported GTK desktop, run from the repository root:
   `G_DEBUG=fatal-warnings PYTHONPATH=app /usr/bin/python3 -m pytest app/tests -q`.
   Tests require pytest and the GUI dependencies above; build tests use harmless
   substitute processes and temporary files rather than installing the desktop.
2. Run `ruff check app/caelestia_installer app/tests`, `git diff --check`, and
   `bash packaging/build-deb.sh`. Build outputs in `dist/` are ignored by Git.
3. Set `VERSION` in `app/caelestia_installer/paths.py` to the intended stable
   version, review and commit all source/assets/tests/docs, then push main.
4. Create and push the matching `vMAJOR.MINOR.PATCH` tag on that commit. Wait for
   the package workflow to succeed and verify the release contains its `.deb`
   and `SHA256SUMS`. Tag builds stamp the package's runtime version automatically
   without editing the source checkout.
5. Keep the release published and stable (not draft/prerelease). Existing apps
   with the checker offer it on the next Updates visit or **Recheck now**, once
   GitHub's latest-release API reports it and the package asset is uploaded.

The app does not poll continuously, install packages automatically, or subscribe
users to an APT repository. Older app versions without the checker require one
manual upgrade. A local build validates packaging, not installation on a fresh
Ubuntu machine; test that separately before declaring a release supported.


## Other formats

An **AppImage** would require bundling PyGObject, GI typelibs, libadwaita
and Gio modules, which is fragile and large; **Flatpak** cannot run host
`setup.sh`/`sudo`/`systemctl`, which the installer needs. For these
reasons the project ships a native `.deb` for the Ubuntu family only. RPMs
are not provided (the project targets Ubuntu-family distributions).
