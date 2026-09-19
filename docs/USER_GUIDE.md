# Caelestia for Ubuntu — Complete User Guide

This guide covers everything a user needs, with or without the app. The
same content ships **inside the app** under *Guides*, so you never have
to leave the desktop to look something up.

---

## 1. Installing (no terminal required)

1. Download the project (ZIP from GitHub, or `git clone` if you prefer).
2. Launch the app:
   - from a clone: run `app/run.py` (double-click in the file manager works too), or
   - installed: open **Caelestia for Ubuntu** from the applications menu
     (`sudo ./app/install.sh` once, to put it in the app grid).
3. **Install tab → Step 1** shows system checks. Everything with a red
   icon must be fixed first (usually just free disk space or internet).
   Warnings don't block anything.
4. **Step 2** — leave the defaults. Uncheck "system packages" or "Qt"
   only when re-running after a failure, to skip stages that already
   succeeded. "Deploy theme and configs" is what makes your desktop
   look like the screenshots — keep it on.
5. **Step 3** — type your sudo password. It's used only to answer sudo
   prompts inside the installer and is never saved anywhere.
6. **Step 4** — watch the log (or make coffee; builds take 10–40 min
   depending on your CPU). Cancel is safe: re-running later simply
   continues where things left off.
7. **Step 5** — reboot → at the login screen pick your user → click the
   **gear icon** (bottom-right) → choose **Hyprland** → log in.

Prefer the terminal? The old flow still works exactly as documented in
the top-level README: `./setup.sh`, `./update.sh`, `./uninstall.sh`.

### What the installer does (in one breath)

Adds the Hyprland PPA and build packages → installs a pinned Qt 6.11.2
toolchain under `/opt` → builds wayland 1.26, libcava, Quickshell,
m3shapes and the Caelestia shell from exact known-good commits →
installs fonts → deploys the theme, configs, systemd service and sleep
hooks → verifies every piece. GNOME is never modified; Hyprland appears
as an **additional** session at the login screen.

---

## 2. First login & the 30-second tour

- The **top bar**: workspaces on the left, active window title, tray,
  clock, status icons, power button. It hides on fullscreen.
- **Super+D** opens the **launcher** — start typing to search apps.
- **Super+N** opens the **drawer** (notifications + quick settings).
- Click the wallpaper clock or press the drawer button for the
  **dashboard**: music, system info, toggles.
- **Super+M** is the power menu (logout / reboot / shutdown).
- Everything — bar, drawers, lock screen — is **transparent and
  blurred** and recolours itself to match your wallpaper.

---

## 3. Keybinds (complete list)

These are the ones shipped in `configs/hypr/hyprland.conf`.

### Everyday
| Keys | Action |
|---|---|
| `Super+D` | Launcher (apps, `>calc`, `>wallpaper`, `>scheme`, `>variant`) |
| `Super+N` | Drawer / notifications sidebar |
| `Super+Return` | Terminal |
| `Super+E` | Files (nautilus) |
| `Super+B` | Chrome · `Super+Shift+B` | Bazaar (app store) |
| `Super+Q` | Close window |
| `Super+M` | Power menu (wlogout) |
| `Super+L` | Lock screen (fingerprint works) |
| `Super+V` / `Super+Space` | Toggle floating |
| `Super+F` | Fullscreen |

### Windows & workspaces
| Keys | Action |
|---|---|
| `Super+1…0` | Go to workspace 1–10 |
| `Super+Shift+1…0` | Move window to workspace |
| `Super+H/J/K/L` | Move window left/down/up/right |
| `Super+R` | Resize submap (arrows; `Esc` exits) |
| `Super+mouse drag` | Move / resize with the mouse |

### Screenshots (hyprshot)
| Keys | Action |
|---|---|
| `Super+Shift+S` | Select region |
| `Super+Shift+P` | Active window |
| `Super+Shift+A` | Whole screen |

Saved to `~/Pictures/Screenshots`.

### Change or add shortcuts

The app's **Keybinds tab** lists every shortcut above — including any the
config pulls in with `source =` (for example `~/.config/hypr/keybinds.conf`) —
grouped by section.

- Pick a shortcut to change its keys or what it does, or remove it.
- **Add a keybinding**: press the combination and it is captured for you. It
  is checked against every existing binding first, so the same keys can never
  be assigned twice without you seeing which shortcut already owns them.
- For the action, launch any installed app from a searchable list, run your own
  command, or choose a ready-made desktop action.
- Every change is validated, written atomically and backed up;
  **Undo last change** restores the previous file. Nothing here needs sudo.

---

## 4. Wallpaper & looks

**Change it — three ways:**

1. This app: **Setup tab → Wallpaper** — click a thumbnail (images from
   `~/Pictures/wallpapers`), or *Add image…* to pick any picture.
2. In Hyprland: `Super+D` → type `>wallpaper` → pick one.
3. Terminal: `caelestia wallpaper -f ~/Pictures/wallpapers/file.jpg`.

**Dynamic colours:** the entire interface recolours from the wallpaper
automatically. Change the palette variant with `>scheme` / `>variant`
in the launcher.

**Tweaks (Setup tab):** transparency on/off, corner rounding,
animation speed, font scale. These write straight into
`~/.config/caelestia/shell.json` and apply live. For anything deeper,
"Open shell.json in editor" hands you the whole config.

---

## 5. Idle, lock & power

Defaults (all adjustable in **Setup → Idle & lock**):

| Idle time | Action |
|---|---|
| 3 min | Lock screen |
| 5 min | Screen off (DPMS) |
| 10 min | Suspend-then-hibernate |

- **Audio playing keeps the machine awake** — toggle in Setup.
- **Lid close** suspends; on wake the shell restarts cleanly and the
  lock screen is re-acquired automatically (that's what the installed
  systemd-sleep hooks do).
- `hyprlock` can't lock while Caelestia holds the session lock — that's
  normal. Use `Super+L` (the Caelestia lock) instead.

---

## 6. Updating

- **App:** open the **Updates** tab. It compares three things per component:
  - **installed** — what your machine built,
  - **pinned** — the known-good commit in `revisions.conf`,
  - **upstream** — the newest commit upstream.

  *Up to date* means installed matches the **pin** — the installer never
  silently follows upstream, so a moved upstream commit is informational,
  not an app-updatable change. *Apply updates* (enabled only when a
  component differs from its pin) runs `update.sh --yes`.

  **Advanced — latest upstream** (for when you want the newest code):
  1. **Update to latest upstream** — fetches the newest upstream commit of
     every component, rewrites `revisions.conf`, and rebuilds. Your configs
     and `shell.json` are never overwritten (a copy of `shell.json` is saved
     under `~/.local/share/caelestia-ubuntu/pins/` first).
  2. Test it: log into Hyprland and check the shell.
  3. **Mark tested & keep** records those revisions as your known-good pin,
     or **Revert to previous** restores the snapshot taken before the change
     and rebuilds back to it.

  The pin snapshots live outside the repo (`.../pins/known-good.conf` and
  `.../pins/previous.conf`), so keeping or reverting never fights git and
  never touches your settings.
- **Terminal:** `./update.sh` (check + apply), `./update.sh --check`
  (read-only report), `./update.sh --update-sources` (deliberately move
  to latest upstream after testing it).
- System packages (Hyprland, drivers, …) update normally with apt.
- Every component builds from an **exact pinned commit**
  (`revisions.conf`), so updates can't silently break. Qt is a fixed
  toolchain; bump deliberately with `./setup.sh --qt-version X`.

---

## 7. Uninstalling

- **App:** Setup → *Uninstall* (or Advanced → uninstall with `--purge-qt`
  to also free the ~2 GB Qt toolchain in `/opt`).
- **Terminal:** `./uninstall.sh [--purge-qt]`.

Your configs are archived to
`~/.config/caelestia-ubuntu-uninstalled-<date>`. GNOME was never
touched, so removing this returns the machine to stock.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Black screen / no bar at login | Log into GNOME → `systemctl --user status caelestia-shell` and `journalctl --user -u caelestia-shell -e`; then `systemctl --user restart caelestia-shell` or reboot. |
| Frozen "lock screen died" after lid resume | The shipped sleep hooks re-acquire the lock; if you still hit it, re-run the installer (Repair) to re-deploy hooks with your uid. |
| `hyprctl configerrors` complains | `hyprctl reload`. |
| Launcher Calculator missing | `sudo apt install qalculate` (the CLI, not just the library). |
| `qs` IPC binds do nothing | Ensure no global `LD_LIBRARY_PATH` — the binary's baked RPATH resolves Qt itself. |
| Installer says "need 8 GB free" | Free space on `/`, or tick "Skip the 8 GB check" in Step 2 if a bigger disk is mounted elsewhere. |
| Blank icons | Fonts stage was skipped — re-run the installer with fonts enabled. |

**Get help:** open an issue at
<https://github.com/12errh/caelestia-ubuntu/issues> with the app's
install log (Install tab) or `journalctl --user -u caelestia-shell -e`
output.
