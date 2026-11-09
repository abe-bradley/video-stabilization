"""Focused checks for private settings, export integrity, and overwrite behavior."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from export_release import export_archive
from local_settings import load_settings


class ReleaseExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "kerkyra-stabilizer-v0.1.1.zip"
        prefix = self.archive.stem + "/"
        content = b"example release document\n"
        checksum = hashlib.sha256(content).hexdigest()
        with zipfile.ZipFile(self.archive, "w") as bundle:
            bundle.writestr(prefix + "README.md", content)
            bundle.writestr(prefix + "SHA256SUMS.txt", checksum + "  README.md\n")
        self.sidecar = self.archive.with_name(self.archive.name + ".sha256")
        self.sidecar.write_text(hashlib.sha256(self.archive.read_bytes()).hexdigest() + "  " + self.archive.name + "\n")
        self.destination = self.root / "cloud export"

    def test_relative_settings_resolve_from_file_location(self):
        settings_dir = self.root / "Local settings"
        settings_dir.mkdir()
        path = settings_dir / "video-stabilizer.json"
        path.write_text(json.dumps({"schema_version": 1, "release_directory": "../Releases", "export_directory": "../Drive"}))
        settings = load_settings(path)
        self.assertEqual(settings.release_directory, self.root / "Releases")
        self.assertEqual(settings.export_directory, self.root / "Drive")

    def test_dry_run_creates_nothing(self):
        messages = export_archive(self.archive, self.destination, dry_run=True)
        self.assertEqual(len(messages), 2)
        self.assertFalse(self.destination.exists())

    def test_verified_copy_and_repeat(self):
        self.assertEqual(len(export_archive(self.archive, self.destination)), 2)
        self.assertEqual((self.destination / self.archive.name).read_bytes(), self.archive.read_bytes())
        self.assertEqual((self.destination / self.sidecar.name).read_bytes(), self.sidecar.read_bytes())
        self.assertIn("Already matches", export_archive(self.archive, self.destination)[0])

    def test_conflict_keeps_existing_file(self):
        self.destination.mkdir()
        target = self.destination / self.archive.name
        target.write_bytes(b"existing export")
        with self.assertRaisesRegex(ValueError, "different existing"):
            export_archive(self.archive, self.destination)
        self.assertEqual(target.read_bytes(), b"existing export")
        self.assertFalse((self.destination / self.sidecar.name).exists())

    def test_bad_outer_checksum_creates_nothing(self):
        self.sidecar.write_text("0" * 64 + "  " + self.archive.name + "\n")
        with self.assertRaisesRegex(ValueError, "ZIP checksum mismatch"):
            export_archive(self.archive, self.destination)
        self.assertFalse(self.destination.exists())

    def test_bad_internal_checksum_creates_nothing(self):
        with zipfile.ZipFile(self.archive, "w") as bundle:
            prefix = self.archive.stem + "/"
            bundle.writestr(prefix + "README.md", b"changed")
            bundle.writestr(prefix + "SHA256SUMS.txt", "0" * 64 + "  README.md\n")
        self.sidecar.write_text(hashlib.sha256(self.archive.read_bytes()).hexdigest() + "  " + self.archive.name + "\n")
        with self.assertRaisesRegex(ValueError, "Internal release checksum"):
            export_archive(self.archive, self.destination)
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
