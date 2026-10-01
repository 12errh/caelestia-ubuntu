"""The self-update download path: URLs, checksums and refusals.

Nothing here touches the network — every fetch is redirected at a ``file://``
URL built from a temporary directory — or the system: :func:`download` only
writes into a directory it is handed.
"""

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from caelestia_installer import releases, updater


def _publish(tmp: Path, payload: bytes, name: str = None, listed: bool = True) -> Path:
    """Write a fake release: the package, its SHA256SUMS entry, and the file."""
    package = tmp / (name or updater.asset_name("1.2.3"))
    package.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    line = f"{digest}  {package.name}\n" if listed else ""
    (tmp / updater.CHECKSUMS_NAME).write_text(
        line + f"{'0' * 64}  some-other-file.txt\n")
    return package


def _serve(tmp: Path):
    """Make the constructed URLs resolve to the temporary directory."""
    base = f"file://{tmp}"
    return (
        patch.object(updater, "checksums_url", side_effect=lambda _v: f"{base}/{updater.CHECKSUMS_NAME}"),
        patch.object(updater, "package_url", side_effect=lambda _v: f"{base}/{updater.asset_name('1.2.3')}"),
    )


class UpdaterUrlTests(unittest.TestCase):
    def test_urls_are_constructed_from_the_validated_version(self):
        self.assertEqual(
            updater.package_url("1.2.3"),
            f"{releases.REPOSITORY}/releases/download/v1.2.3/caelestia-installer_1.2.3_all.deb")
        self.assertEqual(
            updater.checksums_url("1.2.3"),
            f"{releases.REPOSITORY}/releases/download/v1.2.3/SHA256SUMS")

    def test_hostile_versions_never_reach_a_url(self):
        for version in ("../1.2.3", "1.2.3/../../etc", "01.2.3", "1.2.3-rc1",
                        "1.2", "", "1.2.3 && rm -rf /"):
            with self.subTest(version=version):
                for builder in (updater.package_url, updater.checksums_url,
                                updater.asset_name):
                    with self.assertRaises(ValueError):
                        builder(version)


class ChecksumTests(unittest.TestCase):
    #: A digest the parser must accept, and the corruption it must not match.
    GOOD = "a" * 64
    OTHER = "b" * 64
    NAME = "caelestia-installer_1.2.3_all.deb"

    def test_parses_the_exact_entry(self):
        text = (f"{self.GOOD}  {self.NAME}\n"
                f"{self.OTHER}  other.deb\n")
        self.assertEqual(updater.parse_checksums(text, self.NAME), self.GOOD)

    def test_accepts_binary_mode_marker(self):
        # `sha256sum -b` writes "digest *name"; the marker is not part of the name.
        self.assertEqual(
            updater.parse_checksums(f"{self.GOOD} *{self.NAME}\n", self.NAME),
            self.GOOD)

    def test_rejects_a_missing_or_malformed_entry(self):
        for text in ("",
                     f"{self.GOOD}  other.deb\n",
                     f"nothex  {self.NAME}\n",
                     f"{'a' * 63}  {self.NAME}\n",
                     f"{self.GOOD}  {self.NAME}.bak\n"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    updater.parse_checksums(text, self.NAME)

    def test_a_name_prefix_cannot_stand_in(self):
        # A line for a longer, similarly named file must not satisfy the package.
        with self.assertRaises(ValueError):
            updater.parse_checksums(f"{self.GOOD}  {self.NAME}.asc\n", self.NAME)


class DownloadTests(unittest.TestCase):
    def test_verified_package_is_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            _publish(tmp, b"a plausible deb")
            sums, package = _serve(tmp)
            with sums, package, tempfile.TemporaryDirectory() as out:
                path = updater.download("1.2.3", directory=Path(out))
                self.assertEqual(path.read_bytes(), b"a plausible deb")
                self.assertEqual(path.name, "caelestia-installer_1.2.3_all.deb")

    def test_corrupt_package_is_discarded_and_nothing_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            _publish(tmp, b"the real bytes")
            # Serve different bytes than the listing vouches for.
            (tmp / updater.asset_name("1.2.3")).write_bytes(b"substituted")
            sums, package = _serve(tmp)
            with sums, package, tempfile.TemporaryDirectory() as out:
                with self.assertRaises(ValueError) as caught:
                    updater.download("1.2.3", directory=Path(out))
                self.assertIn("checksum", str(caught.exception))
                self.assertEqual(list(Path(out).iterdir()), [],
                                 "an unverified download must not be left behind")

    def test_unlisted_package_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            _publish(tmp, b"bytes", listed=False)
            sums, package = _serve(tmp)
            with sums, package:
                with self.assertRaises(ValueError):
                    updater.download("1.2.3")

    def test_oversized_package_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            _publish(tmp, b"x" * 4096)
            sums, package = _serve(tmp)
            with sums, package, patch.object(updater, "MAX_PACKAGE", 1024), \
                    tempfile.TemporaryDirectory() as out:
                with self.assertRaises(ValueError):
                    updater.download("1.2.3", directory=Path(out))

    def test_install_command_resolves_dependencies(self):
        # dpkg -i would leave python3-gi / gir1.2-gtk-4.0 unresolved on a machine
        # that installed the app from source; apt-get install does not.
        argv = updater.install_command(Path("/tmp/caelestia-installer_1.2.3_all.deb"))
        self.assertEqual(argv[0], "sudo")
        self.assertEqual(argv[1], "apt-get")
        self.assertIn("install", argv)
        self.assertEqual(argv[-1], "/tmp/caelestia-installer_1.2.3_all.deb")
        self.assertNotIn("-i", argv)

    def test_remove_is_best_effort(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pkg.deb"
            path.write_bytes(b"x")
            updater.remove(path)
            self.assertFalse(path.exists())
            updater.remove(path)          # already gone: no exception


if __name__ == "__main__":
    unittest.main()