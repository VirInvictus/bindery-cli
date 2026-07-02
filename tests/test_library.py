"""Tests for the in-place library replacement: it must be atomic, preserve the path
and mode, and back up when asked."""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from bindery.library import atomic_replace, backup_path, iter_epubs, make_backup


class TestIterEpubs(unittest.TestCase):
    def test_uppercase_suffix_found(self):
        # rglob is case-sensitive by default; Calibre emits lowercase, but a
        # hand-added Book.EPUB must not be invisible to the scan.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.epub").write_bytes(b"x")
            (root / "b.EPUB").write_bytes(b"x")
            names = [p.name for p in iter_epubs(root)]
        self.assertEqual(names, ["a.epub", "b.EPUB"])


class TestAtomicReplace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.target = self.d / "book.epub"
        self.target.write_bytes(b"OLD CONTENT")
        os.chmod(self.target, 0o640)
        self.new = self.d / "new.epub"
        self.new.write_bytes(b"NEW REPAIRED CONTENT")

    def tearDown(self):
        self.tmp.cleanup()

    def test_replaces_contents_in_place(self):
        atomic_replace(self.target, self.new)
        self.assertEqual(self.target.read_bytes(), b"NEW REPAIRED CONTENT")
        # no stray temp file left behind
        self.assertEqual(
            sorted(p.name for p in self.d.iterdir()), ["book.epub", "new.epub"]
        )

    def test_preserves_mode(self):
        atomic_replace(self.target, self.new)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)

    def test_backup_inplace(self):
        dst = make_backup(self.target, None)
        self.assertEqual(dst, self.target.with_suffix(".epub.bak"))
        self.assertEqual(dst.read_bytes(), b"OLD CONTENT")

    def test_backup_mirrored_dir(self):
        bdir = self.d / "backups"
        dst = backup_path(self.target, bdir)
        self.assertEqual(dst, bdir / self.target.parent.name / "book.epub")
        made = make_backup(self.target, bdir)
        self.assertTrue(made.exists())
        self.assertEqual(made.read_bytes(), b"OLD CONTENT")


if __name__ == "__main__":
    unittest.main()
