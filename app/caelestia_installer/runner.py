"""Run repository scripts (setup.sh, update.sh, uninstall.sh) from the GUI.

The scripts call ``sudo`` internally many times, and they *refuse* to run as
root themselves (they need your real $HOME/$USER). The trick used here:

  * the child process runs on a pseudo-terminal (PTY) so its internal ``sudo``
    calls can read the password exactly like they would in a real terminal;
  * we watch the stream and, whenever sudo prints its ``password for`` prompt,
    we surface it in the GUI (pre-filled from the password captured on the
    auth page) and write it into the PTY automatically.

No global LD_LIBRARY_PATH games, no root shell — the script keeps full
control and the log shows everything it prints.
"""

from __future__ import annotations

import os
import pty
import re
import subprocess
import threading
from typing import Callable

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
SUDO_PROMPT_RE = re.compile(
    r"\[(?:sudo|sudo via \[[^\]]+\])\] password for [^\]]*:\s*$"
)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


class ScriptRunner:
    """Spawn ``argv`` on a PTY and stream its output through callbacks.

    Callbacks run on the reader thread — wrap them with GLib.idle_add in the
    GUI layer. ``on_output`` receives decoded chunks; ``on_end`` receives the
    exit code. ``password`` (optional) is written automatically when a sudo
    prompt is detected.
    """

    def __init__(
        self,
        argv: list[str],
        on_output: Callable[[str], None],
        on_end: Callable[[int], None],
        password: str | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.argv = argv
        self.on_output = on_output
        self.on_end = on_end
        self.password = password
        self.env = env
        self.cwd = cwd

        self._master = -1
        self.proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cancelled = False
        self._attempts = 0
        self._no_password_warned = False

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        master, slave = pty.openpty()
        self._master = master

        def child_prep() -> None:  # runs in the forked child before exec
            os.setsid()
            try:
                import fcntl
                import termios
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)  # make pty the controlling tty
            except OSError:
                pass

        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        if self.env:
            env.update(self.env)

        self.proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            self.argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            preexec_fn=child_prep,
            env=env,
            cwd=self.cwd,
            close_fds=True,
        )
        os.close(slave)  # child owns its copy now

        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Kill the whole process group (scripts spawn compilers etc.)."""
        self._cancelled = True
        if self.proc and self.proc.poll() is None:
            try:
                import signal
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    import signal
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def write_line(self, line: str) -> None:
        """Send a line to the child's stdin (e.g. a sudo password)."""
        with self._lock:
            try:
                os.write(self._master, (line + "\n").encode())
            except OSError:
                pass

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # -- internals -----------------------------------------------------------
    def _read_loop(self) -> None:
        tail = ""
        try:
            while True:
                try:
                    data = os.read(self._master, 8192)
                except OSError:
                    break
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                text = strip_ansi(text).replace("\r\n", "\n").replace("\r", "\n")
                self._safe(self.on_output, text)

                tail = (tail + text)[-512:]
                match = SUDO_PROMPT_RE.search(tail)
                if match:
                    if self.password is None:
                        # No password to answer with: send one empty line so sudo
                        # fails fast and the script aborts, instead of hanging.
                        if not self._no_password_warned:
                            self._no_password_warned = True
                            self._safe(
                                self.on_output,
                                "\n[gui] sudo is asking for a password but none "
                                "is available — re-run this action and enter it "
                                "when prompted.\n",
                            )
                            self.write_line("")
                    else:
                        self._attempts += 1
                        if self._attempts > 1:
                            self._safe(
                                self.on_output,
                                "\n[gui] sudo rejected the password "
                                f"(attempt {self._attempts}) — check it and run "
                                "the action again.\n",
                            )
                        self.write_line(self.password)
                    tail = ""  # consumed; a later prompt re-triggers
        finally:
            rc = 1 if self._cancelled else (self.proc.wait() if self.proc else 1)
            self._safe(self.on_end, rc)

    def _safe(self, cb: Callable, arg) -> None:
        try:
            cb(arg)
        except Exception:  # noqa: BLE001 - a UI callback must never kill the reader
            pass

    def __del__(self) -> None:
        try:
            if self._master >= 0:
                os.close(self._master)
        except OSError:
            pass


def run_capture(argv: list[str], timeout: int = 30) -> tuple[int, str]:
    """Small helper for quick, non-interactive commands (status probes).

    Only surrounding blank lines are removed — leading indentation is
    preserved because some callers parse column-aligned output.
    """
    try:
        r = subprocess.run(  # noqa: S603
            argv, capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, (r.stdout + r.stderr).strip("\n")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def sudo_ready() -> bool:
    """True when sudo can run without prompting (cached timestamp or NOPASSWD)."""
    try:
        r = subprocess.run(
            ["sudo", "-n", "true"], capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def verify_sudo_password(password: str) -> bool:
    """Validate ``password`` against sudo without running a command.

    ``-k`` clears any cached credential first so the password is genuinely
    checked, then ``-v`` refreshes the sudo timestamp on success.
    """
    if not password:
        return False
    try:
        r = subprocess.run(  # noqa: S603
            ["sudo", "-S", "-k", "-v"],
            input=password + "\n",
            capture_output=True,
            text=True,
            timeout=30,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
