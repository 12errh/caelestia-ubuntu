<p align="center">
  <img src="../assets/logo.png" alt="Caelestia for Ubuntu" width="400">
</p>

# Caelestia for Ubuntu — the app

A graphical installer, setup assistant and built-in user guide for the
[caelestia-ubuntu](https://github.com/12errh/caelestia-ubuntu) project.
Users never need to open a terminal: the app runs the repository's
`setup.sh` / `update.sh` / `uninstall.sh` for them, with live progress,
sudo password handling, a wallpaper picker, starter settings and the full
keybind/guide reference.

![Welcome screen](../assets/welcom.png)

## Run it

### Easiest: install from a release (.deb)

Download the `.deb` from the
[latest GitHub Release](https://github.com/12errh/caelestia-ubuntu/releases/latest)
and install it:

```bash
sudo apt install ./caelestia-installer_*_all.deb
# then launch "Caelestia for Ubuntu" from the applications menu
```

### From a clone (no install step)

```bash
./app/run.py              # GUI
./app/run.py --check      # headless system report (no GTK needed)
```

### Install script (from a clone)

Or install it into the app grid with the script (system-wide, or `--user`
for current user only):

```bash
sudo ./app/install.sh          # installs launcher + icon + repo copy
# then launch "Caelestia for Ubuntu" from the applications menu
```

Runtime requirements: `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`
(standard on Ubuntu 24.04 / Zorin 18 GNOME; `app/install.sh` and the .deb
declare them). GTK4 + libadwaita are available on the stock GNOME desktop
*before* Caelestia/Qt exists, which is why the app uses them. Full details
in [docs/INSTALL_APP.md](../docs/INSTALL_APP.md).

## What each tab does

| Tab | Purpose |
|---|---|
| **Welcome** | Status snapshot (installed or not, service running?) and quick entry points. |
| **Install** | Four-step wizard: system checks → install options → sudo password → live progress and result. Runs `setup.sh` with your chosen flags. Expert overrides live in Advanced. |
| **Setup** | Status, a paginated wallpaper gallery with background thumbnail loading, appearance controls and idle/lock settings. |
| **Updates** | Read-only revision checks and normal updates to the project's pinned commits, with a live build log. Experimental builds and recovery live exclusively in Advanced. |
| **Guides** | Expandable topics covering first login, keybinds, wallpaper, idle/lock behaviour, updating and troubleshooting. |
| **Advanced** | Installation overrides (Qt version and disk-check override), shell configuration editor and restart, latest-upstream builds, Keep/Revert, runtime repair and confirmed uninstall options. Pin snapshots live in `~/.local/share/caelestia-ubuntu/pins`. |

## App releases and tested desktop revisions

These are two separate updates:

- **Installer app:** Updates checks GitHub's latest stable release on its first
  visit each app session, and again with **Recheck now**. A newer `vMAJOR.MINOR.PATCH`
  release with an uploaded `.deb` enables **Get app update**. After confirmation,
  it opens the official release page; download the package, finish any running
  build, close the app, install the `.deb`, and relaunch. This is notification
  and manual installation, **not silent self-updating**. Offline errors do not
  prevent desktop update checks. No package is downloaded or executed by the app.
  The tag workflow attaches the package and `SHA256SUMS`; installing the package
  upgrades the app without rebuilding desktop components. Clone / `install.sh`
  users should update using their original method to avoid shadowing installs.
  Native `apt upgrade` delivery would require a signed APT repository, which is
  not configured.
- **Tested desktop pins:** Updates now also fetches `revisions.conf` from this
  project's GitHub `main` branch in the background. Commit and push your tested
  revisions there to publish them; no app release tag is needed. The check is
  read-only and separate from component/upstream status. Offline or malformed
  responses are reported, never adopted. Only full commit hashes for the five
  supported components are accepted.

When published pins differ, **Use published pins** asks for confirmation,
backs up the local file for Advanced → Revert, then saves the published set and
rechecks the component report. **Apply updates** is the separate rebuild step.
A difference is not necessarily a newer version: experimental local pins can
be ahead of the maintainer-tested set. Read-only repositories must be made
writable by their owner or updated through the package installation; the app
will report a write error rather than silently request elevated file access.

The launcher icon now uses the same mark as the in-app dock. Existing installs
need the updated `.deb` or a rerun of the same app installation method to replace
its cached icon. A user-local installation can shadow a system-wide one; avoid
mixing installation methods. If the desktop still displays its cached icon
after reinstalling, log out and in again.

## How it runs the scripts

The scripts refuse to run as root (they need your real `$HOME`), so the
app launches them on a **pseudo-terminal** as your user. When sudo
prints its `password for …:` prompt, the app writes the password you
entered on the wizard's auth page (or the password prompt shown when you
start a privileged action from Updates/Advanced) into the PTY —
exactly like typing it in a terminal. Before a privileged action the
password is validated with `sudo -S -k -v`; if sudo already has a cached
credential no prompt is needed. The password lives only in app memory: it
is never written to disk or logs. The live log shows everything the
scripts print, and stage headers (`==> stage: …`) drive the progress bar.

Settings in the Setup tab write to `~/.config/caelestia/shell.json`
(the file the Caelestia shell watches), preserving all other keys — the
shell applies changes live.

## Layout

```
app/
├── run.py                     # launcher from a clone
├── install.sh                 # system/user install of the app itself
├── data/
│   ├── io.github.CaelestiaUbuntu.Installer.desktop
│   └── io.github.CaelestiaUbuntu.Installer.png
└── caelestia_installer/
    ├── main.py                # window shell + floating dock navigation
    ├── cli.py                 # --check headless mode
    ├── checks.py              # pre-flight checks + installed-state detection
    ├── runner.py              # PTY script runner with sudo auto-answer
    ├── paths.py               # repo/script/config path resolution
    ├── releases.py            # stable app-release detection
    ├── published.py           # read-only published desktop-pin checks
    ├── ui.py                  # shared GTK helpers
    └── pages/
        ├── welcome.py         # status + entry points
        ├── install.py         # gated install wizard
        ├── setup.py           # paginated wallpaper gallery + desktop settings
        ├── updates.py         # app releases, published pins + component updates
        ├── guides.py          # built-in user guide content
        ├── advanced.py        # expert controls + recovery tools
        ├── advanced_actions.py # confirmed maintenance actions
        └── script_panel.py    # shared live-log/progress widget
```

## Development notes

- No third-party Python packages — stdlib + PyGObject only.
- `paths.repo_root()` finds the repo when running from a clone, from the
  installed layout (`/usr/local/share/caelestia-installer/repo`), or via
  the `CAEL_REPO_DIR` environment variable (what `install.sh`'s launcher
  sets and CI can use).
- The installer is **idempotent**: "Repair / reinstall" is a supported
  re-run; existing user configs are backed up by `setup.sh` itself.
