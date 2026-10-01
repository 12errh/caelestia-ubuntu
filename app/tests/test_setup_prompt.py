"""setup.sh must never block on a question the GUI cannot answer.

The installer page runs setup.sh in a PTY that only ever replies to *sudo*
prompts (runner.ScriptRunner). setup.sh used to gate the whole install behind
``ask "Install everything? …"``, a plain ``read``: in the GUI nothing could ever
answer it, so the build sat there forever on a question with no visible way to
reply. The install now starts straight away, and the GUI passes ``--yes`` so
the one remaining interactive question (deploy the theme) is answered too.

The test runs setup.sh's own ``preflight`` — never ``main`` — so no package is
installed and nothing on the machine is touched.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

#: The clone under test. Not paths.repo_root(): on a machine that has already
#: installed, that resolves to the *installed* copy of the repo (via the
#: manifest's PIN_FILE), which would silently test the wrong setup.sh.
REPO = Path(__file__).resolve().parents[2]


def preflight_harness(tmp: Path) -> Path:
    """setup.sh truncated just before ``main``, with ``preflight`` called.

    Cutting at the ``main`` call rather than rewriting it means a failure here
    runs *nothing* instead of kicking off a real installation.
    """
    lines = (REPO / "setup.sh").read_text().splitlines()
    for index, line in enumerate(lines):
        if line.startswith("main "):
            script = tmp / "setup-preflight.sh"
            script.write_text("\n".join(lines[:index])
                              + '\npreflight\nprintf "PREFLIGHT-DONE\\n"\n')
            script.chmod(0o755)
            return script
    raise AssertionError("setup.sh no longer calls main(); update this harness")


class SetupPromptTests(unittest.TestCase):
    def test_preflight_installs_without_asking(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("setup.sh refuses to run as root")
        if not os.path.exists("/bin/bash"):
            self.skipTest("bash is not installed")

        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            script = preflight_harness(tmp)
            # sudo is the only external thing preflight calls; stub it so the
            # test never touches (or waits on) a real password prompt.
            stub = tmp / "bin"
            stub.mkdir()
            sudo = stub / "sudo"
            sudo.write_text("#!/bin/sh\nexit 0\n")
            sudo.chmod(0o755)
            env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}")

            # stdin closed, and deliberately *no* --yes: this is the shape that
            # used to hang. Reaching the end proves nothing reads stdin.
            result = subprocess.run(
                ["bash", str(script), "--ignore-space"],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                env=env, timeout=120, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PREFLIGHT-DONE", result.stdout)
        self.assertNotIn("Install everything?", result.stdout)
        self.assertNotIn("[y/N]", result.stdout)


if __name__ == "__main__":
    unittest.main()