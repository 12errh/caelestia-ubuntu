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
/usr/share/caelestia-installer/caelestia-legacy-cleanup.sh     # removes a shadowing copy
/usr/share/applications/io.github.CaelestiaUbuntu.Installer.desktop
/usr/share/icons/hicolor/128x128/apps/io.github.CaelestiaUbuntu.Installer.png
```

`postinst` runs the cleanup script against `/usr/local` (and, when installing
with `sudo`, the invoking user's `~/.local`). See
[Don't mix install methods](#dont-mix-install-methods).

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
installs, update the source and rerun the same installation command.

### Don't mix install methods

Installing **and** then running `sudo ./app/install.sh` looks like an upgrade and
gets you the opposite: the *older* app, with the *older* icon. `/usr/local` and
`~/.local` are both searched before `/usr` in `$PATH` and in `$XDG_DATA_DIRS`, so
the clone install wins over the package no matter which one is newer:

| Symptom | Cause |
|---|---|
| `caelestia-installer` starts an older version (check **About**) | `/usr/local/bin/caelestia-installer` shadows `/usr/bin/caelestia-installer` and execs the older copy under `/usr/local/share` |
| The app grid keeps the old icon | A leftover scalable SVG or `icon-theme.cache` in `/usr/local/share/icons` outranks the packaged 128px PNG |
| The grid entry launches the old app | `/usr/local/share/applications/….desktop` shadows the packaged entry |

Since **v1.2.1** this cannot happen: `app/install.sh` refuses to run while the
package is installed, the package's `postinst` removes a shadowing leftover, and
the packaged desktop entry launches `/usr/bin/caelestia-installer` by absolute
path instead of relying on `$PATH` order.

To repair a machine that is already in this state (for example one installed
from a package older than v1.2.1), reinstall the current package or run the
cleanup directly:

```bash
sudo /usr/share/caelestia-installer/caelestia-legacy-cleanup.sh
# nothing to clean? it says so and exits 0. Preview what it found first:
sudo /usr/share/caelestia-installer/caelestia-legacy-cleanup.sh --help
```

It removes only files it positively identifies as this project's — the
`install.sh` launcher, its payload directory, its desktop entry, and its icons —
and refreshes the desktop/icon caches afterwards. A hand-written launcher or an
unrelated `/usr/local` file is left alone. Restart the shell session (log out and
back in, or restart the desktop) to clear the icon the running shell has cached.

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

It refuses to run while the `.deb` is installed, because mixing the two makes
the older copy win (see [Don't mix install
methods](#dont-mix-install-methods)). Remove the package first:

```bash
sudo apt remove caelestia-installer
sudo ./app/install.sh
```

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
