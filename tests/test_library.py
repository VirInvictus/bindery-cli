"""Tests for the in-place library replacement: it must be atomic, preserve the path
and mode, and back up when asked."""

import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bindery.library import (
    CalibreIdResolver,
    atomic_replace,
    backup_path,
    calibredb_replace,
    guess_calibre_id,
    iter_epubs,
    make_backup,
)


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


def make_library(root: Path, dir_id: int = 1) -> Path:
    """A minimal real metadata.db plus one catalogued EPUB (book id 1).

    `dir_id` is the number that appears in the physical directory name, so
    tests can prove the id comes from metadata.db rather than the directory
    fragment (set dir_id=9 and the legacy regex guesses the WRONG book).
    """
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "metadata.db")
    conn.executescript(
        """
        CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT,
            author_sort TEXT, timestamp TEXT, pubdate TEXT, has_cover INT,
            last_modified TEXT, series_index REAL DEFAULT 1.0, path TEXT, uuid TEXT);
        CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
        CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INT, author INT);
        CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
        CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INT, series INT);
        CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
        CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INT, publisher INT);
        CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, link TEXT);
        CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INT, tag INT);
        CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT, link TEXT);
        CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code INT);
        CREATE TABLE ratings (id INTEGER PRIMARY KEY, rating INT, link TEXT DEFAULT '');
        CREATE TABLE books_ratings_link (id INTEGER PRIMARY KEY, book INT, rating INT);
        CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT,
            name TEXT, uncompressed_size INT);
        CREATE TABLE identifiers (book INT, type TEXT, val TEXT);
        """
    )
    conn.execute(
        "INSERT INTO books (id,title,sort,path) VALUES (1,'Title','Title',?)",
        (f"Author/Title ({dir_id})",),
    )
    conn.execute("INSERT INTO authors (id,name,sort) VALUES (1,'Author','Author')")
    conn.execute("INSERT INTO books_authors_link (book,author) VALUES (1,1)")
    conn.execute(
        "INSERT INTO data (book,format,name,uncompressed_size) "
        "VALUES (1,'EPUB','Title - Author',10)"
    )
    conn.commit()
    conn.close()
    book_dir = root / "Author" / f"Title ({dir_id})"
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "Title - Author.epub").write_bytes(b"EPUB")
    return root


class TestCalibreIdResolver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_library(Path(self.tmp.name))
        self.epub = self.root / "Author" / "Title (1)" / "Title - Author.epub"

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolves_catalogued_epub(self):
        self.assertEqual(CalibreIdResolver(self.root).id_for(self.epub), 1)

    def test_id_comes_from_db_not_directory_name(self):
        # Catalogued as book 1 but living in a "(9)" directory: the resolver
        # answers 1 where the legacy regex would have guessed 9.
        root = make_library(Path(self.tmp.name + "_9"), dir_id=9)
        epub = root / "Author" / "Title (9)" / "Title - Author.epub"
        self.assertEqual(CalibreIdResolver(root).id_for(epub), 1)
        self.assertEqual(guess_calibre_id(epub), "9")  # the wrong answer

    def test_unknown_file_is_none(self):
        stray = self.root / "not-in-library.epub"
        stray.write_bytes(b"x")
        self.assertIsNone(CalibreIdResolver(self.root).id_for(stray))

    def test_missing_metadata_db_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            stray = Path(td) / "book.epub"
            stray.write_bytes(b"x")
            self.assertIsNone(CalibreIdResolver(Path(td)).id_for(stray))


class TestGuessCalibreId(unittest.TestCase):
    def test_extracts_directory_id(self):
        self.assertEqual(guess_calibre_id(Path("/lib/A/Title (12)/t.epub")), "12")

    def test_no_directory_id_is_none(self):
        self.assertIsNone(guess_calibre_id(Path("/lib/loose.epub")))


class TestCalibredbReplaceRouting(unittest.TestCase):
    """calibredb_replace prefers the cquarry-resolved id over the name guess,
    and falls back to an atomic in-place save when no id can be found."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_library(Path(self.tmp.name), dir_id=9)
        self.epub = self.root / "Author" / "Title (9)" / "Title - Author.epub"
        self.new = self.root / "repaired.epub"
        self.new.write_bytes(b"REPAIRED")

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolver_id_wins_over_directory_guess(self):
        with mock.patch("bindery.library.subprocess.run") as run:
            calibredb_replace(self.epub, self.new, CalibreIdResolver(self.root))
        run.assert_called_once()
        # the DB says book 1 even though the directory screams (9)
        self.assertEqual(run.call_args.args[0][2], "1")
        self.assertNotIn("--replace", run.call_args.args[0])
        self.assertEqual(self.epub.read_bytes(), b"EPUB")  # untouched in place

    def test_add_format_gets_no_replace_flag(self):
        # calibredb add_format has no --replace flag (replacement is its
        # default; --dont-replace opts out). Passing it crashed
        # --install-to-calibre with a usage error on 2026-08-31; the command
        # stays exactly this four-argument form.
        with mock.patch("bindery.library.subprocess.run") as run:
            calibredb_replace(self.epub, self.new, CalibreIdResolver(self.root))
        self.assertEqual(
            run.call_args.args[0], ["calibredb", "add_format", "1", str(self.new)]
        )

    def test_regex_fallback_when_no_resolver(self):
        with mock.patch("bindery.library.subprocess.run") as run:
            calibredb_replace(self.epub, self.new)  # legacy behaviour
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][2], "9")

    def test_no_id_falls_back_to_atomic_replace(self):
        import contextlib
        import io

        loose = self.root / "loose.epub"
        loose.write_bytes(b"OLD")
        with mock.patch("bindery.library.subprocess.run") as run:
            with contextlib.redirect_stderr(io.StringIO()):
                calibredb_replace(loose, self.new)
        run.assert_not_called()
        self.assertEqual(loose.read_bytes(), b"REPAIRED")


if __name__ == "__main__":
    unittest.main()
