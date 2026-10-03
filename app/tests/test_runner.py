"""The PTY runner: what the child may ask, and what it is answered.

Two halves of one contract. Only this module writes to the script's terminal,
and only to answer *sudo* — so every other prompt has to be impossible rather
than merely unlikely (:data:`~caelestia_installer.runner.UNATTENDED_ENV`), and
the one prompt that is answered must actually be recognised and answered.
"""

import threading
import time
import unittest

from caelestia_installer.runner import SUDO_PROMPT_RE, UNATTENDED_ENV, ScriptRunner

BASH = "/bin/bash"


def run(argv, *, password=None, env=None, timeout=20):
    """Run ``argv`` on the runner's PTY; return (output, exit code)."""
    collected: list[str] = []
    done = threading.Event()
    state: dict = {"code": None}

    def on_output(text: str) -> None:
        collected.append(text)

    def on_end(code: int) -> None:
        state["code"] = code
        done.set()

    runner = ScriptRunner(argv, on_output=on_output, on_end=on_end,
                          password=password, env=env)
    runner.start()
    if not done.wait(timeout):
        runner.cancel()
        raise AssertionError(f"script did not finish: {argv}")
    return "".join(collected), state["code"]


class UnattendedEnvironmentTests(unittest.TestCase):
    def test_every_variable_reaches_the_child(self):
        # A real child on a real PTY: this is the only way to prove the values
        # are exported rather than merely defined in this process.
        output, code = run([BASH, "-c", "env"])
        self.assertEqual(code, 0)
        for name, value in UNATTENDED_ENV.items():
            self.assertIn(f"{name}={value}", output.splitlines(),
                          f"{name} did not reach the script")

    def test_a_caller_cannot_re_enable_a_prompt(self):
        output, _ = run([BASH, "-c", "echo \"$DEBIAN_FRONTEND $GIT_TERMINAL_PROMPT\""],
                        env={"DEBIAN_FRONTEND": "telnet", "GIT_TERMINAL_PROMPT": "1"})
        self.assertEqual(output.strip(), "noninteractive 0")

    def test_the_list_covers_the_known_blockers(self):
        # Each of these is a prompt that has stalled an unattended install:
        # the scripts' own questions, debconf dialogs, needrestart's restart
        # question, pipx's keyring password and git's credential prompt.
        self.assertEqual(UNATTENDED_ENV["CAELESTIA_ASSUME_YES"], "1")
        self.assertEqual(UNATTENDED_ENV["DEBIAN_FRONTEND"], "noninteractive")
        self.assertEqual(UNATTENDED_ENV["NEEDRESTART_MODE"], "a")
        self.assertEqual(UNATTENDED_ENV["APT_LISTCHANGES_FRONTEND"], "none")
        self.assertEqual(UNATTENDED_ENV["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("keyring", UNATTENDED_ENV["PYTHON_KEYRING_BACKEND"])


class SudoAnswerTests(unittest.TestCase):
    """The one prompt the runner does answer, end to end."""

    SCRIPT = (
        'printf "[sudo] password for rehan: "\n'
        'IFS= read -r reply\n'
        'printf "got:%s\\n" "$reply"\n'
    )

    def test_the_password_is_written_to_the_pty(self):
        output, code = run([BASH, "-c", self.SCRIPT], password="hunter2")
        self.assertEqual(code, 0)
        self.assertIn("got:hunter2", output)

    def test_both_sudo_prompt_shapes_are_recognised(self):
        for prompt in ("[sudo] password for rehan: ",
                       "[sudo] password for rehan:",
                       "[sudo via [tty]] password for rehan: "):
            with self.subTest(prompt=prompt):
                self.assertTrue(SUDO_PROMPT_RE.search(prompt))

    def test_similar_looking_output_is_not_answered(self):
        for line in ("[sudo] password for rehan: nope", "password for rehan:",
                     "asking for a password for rehan:"):
            with self.subTest(line=line):
                self.assertIsNone(SUDO_PROMPT_RE.search(line))

    def test_a_script_is_not_answered_without_a_stored_password(self):
        # Without one it gets an empty line so sudo fails fast and the log says
        # why, rather than waiting forever on a prompt nobody can type into.
        script = 'printf "[sudo] password for rehan: "\nIFS= read -r reply\nprintf "got:[%s]\\n" "$reply"\n'
        output, code = run([BASH, "-c", script])
        self.assertEqual(code, 0)
        self.assertIn("got:[]", output)

    def test_cancelling_kills_the_process_group(self):
        collected: list[str] = []
        done = threading.Event()
        runner = ScriptRunner(
            [BASH, "-c", "echo started; sleep 30"],
            on_output=collected.append,
            on_end=lambda _code: done.set())
        runner.start()
        deadline = time.monotonic() + 5
        while "started" not in "".join(collected) and time.monotonic() < deadline:
            time.sleep(0.01)
        runner.cancel()
        self.assertTrue(done.wait(10), "cancel did not end the script")
        self.assertFalse(runner.is_running())


if __name__ == "__main__":
    unittest.main()