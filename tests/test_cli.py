"""CLI-level safety tests: the gate must not be bypassed when epubcheck fails or when
the lossy strip leaves fatals behind, a corrupt book must not abort a library sweep,
the single-file repair must write the exact bytes the gate accepted (flags included)
and label partial output honestly, and candidate selection must refuse --only fatals
without an audit."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from bindery import cli
from bindery.cli import _load_audit, build_parser, main, process_book
from bindery.epub import RepairReport
from bindery.validate import CheckResult

try:
    import html5lib  # noqa: F401

    HAVE_HTML5LIB = True
except ImportError:
    HAVE_HTML5LIB = False

# Unclosed <p> tags: only --reserialize can repair this document.
BROKEN_CONTENT = (
    '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
    "<p>one<p>two&nbsp;three</body></html>"
)


def build(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("OEBPS/c1.xhtml", BROKEN_CONTENT)


class TestEpubcheckFailureIsError(unittest.TestCase):
    def test_failed_epubcheck_is_error_not_unvalidated(self):
        # If epubcheck crashes or its output cannot be parsed, the gate has not
        # accepted anything, so the outcome must never look applicable.
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            build(src)
            with mock.patch("bindery.cli.run_epubcheck", return_value=None):
                o = process_book(src, Path(td), validate=True)
        self.assertEqual(o.status, "error")


class TestStripPaginationGate(unittest.TestCase):
    def _strip_verdict(self, before, after):
        report = RepairReport(fixes={"stripped_pagination": 5})
        with (
            mock.patch("bindery.cli.repair_epub", return_value=report),
            mock.patch("bindery.cli.run_epubcheck", side_effect=[before, after]),
        ):
            return process_book(
                Path("x.epub"), Path("."), validate=True, strip_pagination=True
            )

    def test_still_fatal_book_is_partial_not_accept(self):
        # Regression: no_worse used to overwrite the gate's verdict outright, so a
        # book going 3 fatals -> 1 fatal was accepted, and library --apply replaced
        # a book that still does not open.
        o = self._strip_verdict(CheckResult(3, 0, 0), CheckResult(1, 0, 0))
        self.assertEqual(o.status, "partial")

    def test_no_measurable_gain_on_clean_book_still_accepts(self):
        o = self._strip_verdict(CheckResult(0, 0, 0), CheckResult(0, 0, 0))
        self.assertEqual(o.status, "accept")

    def test_regression_still_rejects(self):
        o = self._strip_verdict(CheckResult(0, 0, 0), CheckResult(0, 2, 0))
        self.assertEqual(o.status, "reject")


class TestLibrarySurvivesCorruptEpub(unittest.TestCase):
    def test_sweep_continues_past_a_bad_zip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bad = root / "Author" / "Bad (1)" / "bad.epub"
            bad.parent.mkdir(parents=True)
            bad.write_bytes(b"not a zip")
            good = root / "Author" / "Book (2)" / "book.epub"
            good.parent.mkdir(parents=True)
            build(good)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(["library", td, "--no-validate"])
        # the bad book is reported and counted, the good one is still processed,
        # and the exit code says the sweep saw trouble
        self.assertEqual(rc, 2)
        self.assertIn("unreadable", out.getvalue())
        self.assertIn("ACCEPT", out.getvalue())

    def test_repair_reports_a_bad_zip_cleanly(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "bad.epub"
            src.write_bytes(b"not a zip")
            err = io.StringIO()
            with redirect_stderr(err):
                rc = main(["repair", str(src), "--no-validate"])
        self.assertEqual(rc, 1)
        self.assertIn("cannot read", err.getvalue())


class TestRepairPartialLabel(unittest.TestCase):
    def test_partial_output_is_labeled_not_repaired(self):
        # The file is worth writing (fewer fatals), but the old "repaired:" line read
        # as fixed when the book still had fatals.
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            dst = Path(td) / "out.epub"
            build(src)
            results = [CheckResult(3, 0, 0), CheckResult(1, 0, 0)]
            out = io.StringIO()
            with (
                mock.patch("bindery.cli.run_epubcheck", side_effect=results),
                redirect_stdout(out),
            ):
                rc = main(["repair", str(src), str(dst)])
            self.assertEqual(rc, 0)
            self.assertTrue(dst.exists())
        self.assertIn("PARTIAL", out.getvalue())
        self.assertNotIn("repaired:", out.getvalue())


class TestRepairRefusesOverwrite(unittest.TestCase):
    def test_existing_output_needs_force(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            dst = Path(td) / "out.epub"
            build(src)
            dst.write_bytes(b"precious")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(["repair", str(src), str(dst), "--no-validate"])
                self.assertEqual(rc, 1)
                self.assertEqual(dst.read_bytes(), b"precious")
                rc = main(["repair", str(src), str(dst), "--no-validate", "--force"])
            self.assertEqual(rc, 0)
            self.assertNotEqual(dst.read_bytes(), b"precious")


class TestRepairWritesGatedBytes(unittest.TestCase):
    @unittest.skipUnless(HAVE_HTML5LIB, "html5lib not installed")
    def test_opt_in_flags_reach_the_output_file(self):
        # Regression: run_repair used to re-repair src without the opt-in flags,
        # writing a file that differed from the one the gate had validated.
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            dst = Path(td) / "out.epub"
            build(src)
            rc = main(["repair", str(src), str(dst), "--no-validate", "--reserialize"])
            self.assertEqual(rc, 0)
            with zipfile.ZipFile(dst) as z:
                out = z.read("OEBPS/c1.xhtml").decode()
        ET.fromstring(out)  # reserialize ran: the document is now well-formed


class TestLibraryGuards(unittest.TestCase):
    def test_only_fatals_requires_audit(self):
        with tempfile.TemporaryDirectory() as td:
            rc = main(["library", td, "--only", "fatals", "--no-validate"])
        self.assertEqual(rc, 1)


class TestLoadAudit(unittest.TestCase):
    def test_header_blank_rows_and_headerless_files(self):
        # Paths are resolved on load, so build the keys from a symlink-free base
        # (on Fedora /lib resolves to /usr/lib, which would shift a literal path).
        with tempfile.TemporaryDirectory() as td:
            base = Path(td).resolve()
            with_header = Path(td) / "a.csv"
            with_header.write_text(
                f"fatals,errors,warnings,path\n\n2,5,1,{base}/x.epub\n"
            )
            self.assertEqual(_load_audit(with_header), {f"{base}/x.epub": (2, 5, 1)})
            # A headerless CSV must not lose its first row.
            headerless = Path(td) / "b.csv"
            headerless.write_text(f"1,0,0,{base}/y.epub\n")
            self.assertEqual(_load_audit(headerless), {f"{base}/y.epub": (1, 0, 0)})


class TestAuditPathMatching(unittest.TestCase):
    def test_zero_overlap_warns(self):
        # An audit generated from a different mount point silently matched nothing
        # and read as "library is clean"; now it warns loudly.
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "book.epub"
            build(good)
            audit = Path(td) / "audit.csv"
            audit.write_text("1,0,0,/somewhere/else/book.epub\n")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                main(["library", td, "--audit", str(audit), "--no-validate"])
        self.assertIn("no scanned book matched", err.getvalue())

    def test_relative_scan_matches_absolute_audit(self):
        # Both sides are resolved before comparing, so a relative library path still
        # hits an absolute-path audit CSV.
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "book.epub"
            build(good)
            audit = Path(td) / "audit.csv"
            audit.write_text(f"1,0,0,{good.resolve()}\n")
            cwd = os.getcwd()
            os.chdir(Path(td).parent)
            try:
                rel = os.path.relpath(td)
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    main(
                        [
                            "library",
                            rel,
                            "--only",
                            "fatals",
                            "--audit",
                            str(audit),
                            "--no-validate",
                        ]
                    )
            finally:
                os.chdir(cwd)
        self.assertIn("1 candidate book(s)", out.getvalue())
        self.assertNotIn("no scanned book matched", err.getvalue())


class TestBackupNotices(unittest.TestCase):
    def test_backup_without_apply_notes_inert(self):
        with tempfile.TemporaryDirectory() as td:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                main(["library", td, "--backup-inplace", "--no-validate"])
        self.assertIn("do nothing without --apply", err.getvalue())

    def test_lossy_apply_without_backup_warns(self):
        with tempfile.TemporaryDirectory() as td:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                main(["library", td, "--apply", "--strip-pagination", "--no-validate"])
        self.assertIn("lossy", err.getvalue())


class TestLibraryExitCode(unittest.TestCase):
    def test_reject_makes_exit_code_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            book = Path(td) / "book.epub"
            build(book)
            results = [CheckResult(0, 0, 0), CheckResult(0, 5, 0)]  # errors rose
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch("bindery.cli.epubcheck_available", return_value=True),
                mock.patch("bindery.cli.run_epubcheck", side_effect=results),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                rc = main(["library", td])
        self.assertEqual(rc, 2)
        self.assertIn("REJECT", out.getvalue())


class TestLimitIsLazy(unittest.TestCase):
    def test_limit_stops_the_scan_not_just_the_work(self):
        # --only ncx probes each archive during selection; with --limit 2 the scan
        # must stop probing after the second candidate instead of opening all five.
        with tempfile.TemporaryDirectory() as td:
            for i in range(5):
                build(Path(td) / f"book{i}.epub")
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch("bindery.cli.ncx_uid_mismatch", return_value=True) as probe,
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                rc = main(
                    ["library", td, "--only", "ncx", "--limit", "2", "--no-validate"]
                )
        self.assertEqual(rc, 0)
        self.assertEqual(probe.call_count, 2)
        self.assertIn("candidates:      2", out.getvalue())


class TestJsonAndManualList(unittest.TestCase):
    def test_json_report_and_manual_list(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "lib"
            root.mkdir()
            good = root / "good.epub"
            build(good)
            bad = root / "bad.epub"
            bad.write_bytes(b"not a zip")
            jout = Path(td) / "report.json"
            mout = Path(td) / "manual.txt"
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(
                    [
                        "library",
                        str(root),
                        "--no-validate",
                        "--json",
                        str(jout),
                        "--manual-list",
                        str(mout),
                    ]
                )
            self.assertEqual(rc, 2)
            data = json.loads(jout.read_text())
            self.assertEqual(data["summary"]["unreadable"], 1)
            self.assertEqual(data["summary"]["unvalidated"], 1)
            statuses = {b["path"]: b["status"] for b in data["books"]}
            self.assertEqual(statuses[str(bad)], "unreadable")
            self.assertEqual(statuses[str(good)], "unvalidated")
            # the unreadable book needs manual work; the unvalidated one does not
            self.assertEqual(mout.read_text().splitlines(), [str(bad)])


class TestSweepSelection(unittest.TestCase):
    def test_sweep_selects_fatals_and_reuses_before(self):
        with tempfile.TemporaryDirectory() as td:
            build(Path(td) / "a.epub")
            build(Path(td) / "b.epub")
            results = [
                CheckResult(1, 0, 0),  # sweep a: fatal, selected
                CheckResult(0, 0, 0),  # sweep b: clean, skipped
                CheckResult(0, 0, 0),  # 'after' for a (its 'before' is the sweep hit)
            ]
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch("bindery.cli.epubcheck_available", return_value=True),
                mock.patch("bindery.cli.run_epubcheck", side_effect=results) as oracle,
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                rc = main(["library", td, "--only", "fatals", "--sweep"])
        self.assertEqual(rc, 0)
        self.assertEqual(oracle.call_count, 3)  # not 4: the sweep result was reused
        self.assertIn("ACCEPT", out.getvalue())

    def test_sweep_flag_conflicts(self):
        with tempfile.TemporaryDirectory() as td:
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(main(["library", td, "--sweep", "--no-validate"]), 1)
                self.assertEqual(
                    main(["library", td, "--sweep", "--audit", "x.csv"]), 1
                )
                self.assertEqual(main(["library", td, "--sweep", "--only", "ncx"]), 1)


class TestOptInFlagWiring(unittest.TestCase):
    """Every structural repair must exist as a real flag on both subcommands,
    default off, and --all must turn each of them on."""

    NEW_FLAGS = (
        "fix_empty_body",
        "fix_missing_title",
        "fix_id_colons",
        "unwrap_block_in_inline",
        "strip_invalid_value",
        "unwrap_illegal_tags",
        "prune_missing_resources",
        "strip_broken_anchors",
        "encode_url_spaces",
    )

    def test_flags_default_off_on_both_subcommands(self):
        parser = build_parser()
        for argv in (["repair", "x.epub"], ["library", "/tmp"]):
            args = parser.parse_args(argv)
            for flag in self.NEW_FLAGS:
                self.assertFalse(getattr(args, flag), f"{argv} {flag}")

    def test_all_enables_every_new_flag_at_the_call_site(self):
        # --all ORs into each flag where process_book calls repair_epub (the same
        # wiring the pre-existing opt-ins use); argparse itself stays a plain
        # store_true, so assert at the call site.
        FLAG_KWARGS = (
            "empty_body",
            "missing_title",
            "id_colons",
            "block_in_inline",
            "invalid_value",
            "illegal_tags",
            "prune_missing",
            "strip_anchors",
            "url_spaces",
        )
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("OEBPS/c1.xhtml", "<html><body><p>x</p></body></html>")
            report = RepairReport(fixes={"self_close_void": 1})
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch("bindery.cli.repair_epub", return_value=report) as repair,
                mock.patch("bindery.cli.run_epubcheck", return_value=None),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                main(["repair", str(src), "--all"])
            kwargs = repair.call_args.kwargs
            for kw in FLAG_KWARGS:
                self.assertTrue(kwargs.get(kw), kw)


class TestAuditTagWiring(unittest.TestCase):
    """The audit --tag flag must exist on the subparser and reach
    run_audit_library. v0.18.0 documented --tag and shipped _apply_audit_tag,
    but the CLI never registered the flag, so the write path was unreachable
    from the command line."""

    def test_flag_parses_and_defaults_off(self):
        parser = build_parser()
        self.assertIsNone(parser.parse_args(["audit", "content"]).tag)
        args = parser.parse_args(["audit", "content", "--tag", "Flagged"])
        self.assertEqual(args.tag, "Flagged")

    def test_tag_reaches_run_audit_library(self):
        with mock.patch("bindery.cli.run_audit_library", return_value=0) as run:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                main(["audit", "content", "--tag", "Flagged"])
        self.assertEqual(run.call_args.kwargs.get("tag"), "Flagged")

    def test_no_tag_keeps_write_path_dormant(self):
        # Without --tag the audit stays strictly read-only: tag must be None,
        # never an empty string that could sneak past `if audit_tag:`.
        with mock.patch("bindery.cli.run_audit_library", return_value=0) as run:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                main(["audit", "content"])
        self.assertIsNone(run.call_args.kwargs.get("tag"))


class TestAuditIdWiring(unittest.TestCase):
    """The audit --id flag must exist on the console-script subparser and reach
    run_single. v0.19.0 documented --id and shipped run_single, but only the
    module's own argparse main (`python -m bindery.audit`) registered it; the
    `bindery` entry point silently lacked the flag."""

    def test_flag_parses_and_defaults_off(self):
        parser = build_parser()
        self.assertIsNone(parser.parse_args(["audit", "content"]).id)
        args = parser.parse_args(["audit", "content", "--id", "1234"])
        self.assertEqual(args.id, "1234")

    def test_id_reaches_run_single(self):
        with mock.patch("bindery.cli.run_single", return_value=0) as run:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                main(["audit", "all", "--id", "1234"])
        self.assertEqual(run.call_args.args[0], 1234)
        self.assertIsNone(run.call_args.kwargs.get("tag"))

    def test_id_rejects_a_directory_argument(self):
        with mock.patch("bindery.cli.run_single", return_value=0) as run:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(["audit", "content", "--id", "1234", "/tmp/epubs"])
        self.assertEqual(rc, 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class TestUnreadableReasons(unittest.TestCase):
    """The sweep's `unreadable` bucket splits by disease: a CRC-broken
    download (re-source) must be distinguishable from a DRM'd or truncated
    one without leaving the sweep, and zipfile's message already names the
    broken entry."""

    def test_crc_damage_is_corrupt_entry(self):
        e = zipfile.BadZipFile("Bad CRC-32 for file 'ch01.xhtml'")
        self.assertEqual(cli._unreadable_reason(e), "corrupt_entry")

    def test_not_a_zip(self):
        e = zipfile.BadZipFile("File is not a zip file")
        self.assertEqual(cli._unreadable_reason(e), "not_a_zip")

    def test_truncated(self):
        self.assertEqual(
            cli._unreadable_reason(zipfile.BadZipFile("Truncated file")), "truncated"
        )
        self.assertEqual(
            cli._unreadable_reason(EOFError("ran out of input")), "truncated"
        )

    def test_encrypted(self):
        e = RuntimeError("File ch01.xhtml is encrypted, password required")
        self.assertEqual(cli._unreadable_reason(e), "encrypted")


class TestLibraryIdScoping(unittest.TestCase):
    """library --id: comma-separated book-id scoping (phase 8). The sweep
    processes only the resolved EPUBs, one wrong id warns without sinking
    the batch."""

    def _library(self, root):
        import sqlite3
        import zipfile

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
        container = (
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>'
        )
        opf = (
            '<package xmlns="http://www.idpf.org/2007/opf">'
            '<manifest><item id="c1" href="t.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="c1"/></spine></package>'
        )
        for bid, title in [(1, "One"), (2, "Two")]:
            conn.execute(
                "INSERT INTO books (id,title,sort,path) VALUES (?,?,?,?)",
                (bid, title, title, f"A/{title} ({bid})"),
            )
            conn.execute("INSERT INTO authors (id,name) VALUES (?,?)", (bid, "Author"))
            conn.execute(
                "INSERT INTO books_authors_link (book,author) VALUES (?,1)", (bid,)
            )
            conn.execute(
                "INSERT INTO data (book,format,name) VALUES (?, 'EPUB', ?)",
                (bid, f"{title} - Author"),
            )
            book_dir = root / "A" / f"{title} ({bid})"
            book_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(book_dir / f"{title} - Author.epub", "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", container)
                z.writestr("content.opf", opf)
                z.writestr(
                    "t.xhtml", "<html><body><p>prose prose prose</p></body></html>"
                )
        conn.commit()
        conn.close()
        return root

    def test_id_scopes_the_sweep(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as td:
            root = self._library(Path(td))
            buf, err = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "bindery",
                        "library",
                        str(root),
                        "--id",
                        "1",
                        "--no-validate",
                    ],
                ),
                contextlib.redirect_stdout(buf),
                contextlib.redirect_stderr(err),
            ):
                cli.run_library(
                    build_parser().parse_args(
                        [
                            "library",
                            str(root),
                            "--id",
                            "1",
                            "--no-validate",
                        ]
                    )
                )
            out = buf.getvalue()
            everything = out + err.getvalue()
            # Without --id this tree yields 2 candidates; scoped, exactly 1 —
            # and the other book's files were never opened.
            self.assertIn("candidates:      1", out)
            self.assertIn("no change:       1", out)
            self.assertNotIn("Two - Author", everything)

    def test_unknown_id_warns_and_skips(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._library(Path(td))
            build_parser().parse_args(
                [
                    "library",
                    str(root),
                    "--id",
                    "1,99",
                    "--no-validate",
                ]
            )
            scoped = cli._epubs_for_ids(root, "1,99")
        self.assertEqual(len(scoped), 1)

    def test_id_and_audit_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._library(Path(td))
            err = io.StringIO()
            with (
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "bindery",
                        "library",
                        str(root),
                        "--id",
                        "1",
                        "--audit",
                        "x.csv",
                        "--no-validate",
                    ],
                ),
                contextlib.redirect_stderr(err),
            ):
                rc = cli.run_library(
                    build_parser().parse_args(
                        [
                            "library",
                            str(root),
                            "--id",
                            "1",
                            "--audit",
                            "x.csv",
                            "--no-validate",
                        ]
                    )
                )
        self.assertEqual(rc, 1)


class TestAuditIdCommaLists(unittest.TestCase):
    """audit --id 1,2 audits both books (phase 8: the audit and library
    modes share the comma-separated form)."""

    def _library(self, root):
        return TestLibraryIdScoping._library(self, root)

    def test_comma_list_audits_both(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as td:
            root = self._library(Path(td))
            buf = io.StringIO()
            old = os.getcwd()
            os.chdir(root)
            try:
                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            "bindery",
                            "audit",
                            "all",
                            "--id",
                            "1,2",
                            "--no-validate",
                        ],
                    ),
                    contextlib.redirect_stdout(buf),
                    contextlib.redirect_stderr(buf),
                ):
                    rc = main(["audit", "content", "--id", "1,2"])
            finally:
                os.chdir(old)
            out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("#1", out)
        self.assertIn("#2", out)


class TestAuditJsonWiring(unittest.TestCase):
    """The audit --json flag must exist on the console-script subparser and
    reach the audit runners. Same bug class as the v0.19.2 --id gap: a
    documented flag the `bindery` entry point never registered."""

    def test_flag_parses_and_defaults_off(self):
        parser = build_parser()
        self.assertIsNone(parser.parse_args(["audit", "content"]).json)
        args = parser.parse_args(["audit", "content", "--json", "out.json"])
        self.assertEqual(args.json, "out.json")

    def test_json_reaches_run_directory(self):
        # end to end: one clean book, the report lands and parses
        import zipfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with zipfile.ZipFile(root / "t.epub", "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr(
                    "META-INF/container.xml",
                    '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="content.opf" '
                    'media-type="application/oebps-package+xml"/></rootfiles></container>',
                )
                z.writestr(
                    "content.opf",
                    '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                    '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
                    '</manifest><spine><itemref idref="c1"/></spine></package>',
                )
                z.writestr(
                    "text.xhtml",
                    "<html><body><p>" + "prose here. " * 300 + "</p></body></html>",
                )
            out = root / "report.json"
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = main(["audit", "emptytext", td, "--json", str(out)])
            self.assertEqual(rc, 0)
            data = json.loads(out.read_text())
        self.assertEqual(data["mode"], "audit")
        self.assertEqual(len(data["books"]), 1)

    def test_json_reaches_run_audit_library(self):
        with mock.patch("bindery.cli.run_audit_library", return_value=0) as run:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                main(["audit", "content", "--json", "report.json"])
        self.assertEqual(run.call_args.kwargs.get("json_path"), "report.json")

    def test_json_reaches_run_single(self):
        with mock.patch("bindery.cli.run_single", return_value=0) as run:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                main(["audit", "all", "--id", "1234", "--json", "report.json"])
        self.assertEqual(run.call_args.kwargs.get("json_path"), "report.json")

    def test_json_with_multiple_ids_is_refused(self):
        # Each run_single writes the report wholesale; two ids would silently
        # leave only the second book's file behind.
        with mock.patch("bindery.cli.run_single", return_value=0) as run:
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                rc = main(["audit", "all", "--id", "1,2", "--json", "report.json"])
        self.assertEqual(rc, 2)
        self.assertIn("exactly one", err.getvalue())
        run.assert_not_called()


def _phase1_book(path: Path, broken: bool) -> None:
    """A realistic loose EPUB: proper container/OPF/NCX, >2000 chars of body
    text so the audit battery stays silent on it. `broken` leaves an unclosed
    <p> that only --reserialize can repair (epubcheck fatal, audit-clean)."""
    container = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>T</dc:title><dc:language>en</dc:language>"
        '<dc:identifier id="id">x</dc:identifier></metadata>'
        '<manifest><item id="c1" href="t.xhtml" '
        'media-type="application/xhtml+xml"/>'
        '<item id="ncx" href="t.ncx" media-type="application/x-dtbncx+xml"/>'
        '</manifest><spine toc="ncx"><itemref idref="c1"/></spine></package>'
    )
    ncx = (
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        '<head><meta name="dtb:uid" content="x"/></head>'
        "<docTitle><text>T</text></docTitle><navMap>"
        '<navPoint id="n1" playOrder="1"><navLabel><text>C1</text></navLabel>'
        '<content src="t.xhtml"/></navPoint></navMap></ncx>'
    )
    if broken:
        body = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title>'
            "</head><body><p>"
            + ("First paragraph continues here. " * 50)
            + "<p>"
            + ("Second paragraph continues here. " * 50)
            + "</body></html>"
        )
    else:
        body = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title>'
            "</head><body><p>"
            + ("A clean prose sentence stands here. " * 90)
            + "</p></body></html>"
        )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("content.opf", opf)
        z.writestr("t.ncx", ncx)
        z.writestr("t.xhtml", body)


class TestRunPhase1(unittest.TestCase):
    """run phase1: the pre-import vetting slice. Read-only without
    --apply-lossy; the lossy-strip consent is that flag and nothing else;
    consent questions surface as decisions_needed, never as prompts."""

    def test_readonly_run_decides_but_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _phase1_book(root / "broken.epub", broken=True)
            _phase1_book(root / "clean.epub", broken=False)
            jout = root / "phase1.json"
            results = [
                CheckResult(2, 0, 0),  # sweep broken: fatal, selected
                CheckResult(0, 0, 0),  # sweep clean: skipped
                CheckResult(0, 0, 0),  # 'after' for broken (before reused)
            ]
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch("bindery.cli.epubcheck_available", return_value=True),
                mock.patch("bindery.cli.run_epubcheck", side_effect=results),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                rc = main(["run", "phase1", td, "--json", str(jout)])
            data = json.loads(jout.read_text())
        self.assertEqual(rc, 0)
        self.assertEqual(data["mode"], "phase1")
        self.assertFalse(data["apply_lossy"])
        # a consent question is not a defect: both books read clean, but the
        # repairable one is held in decisions_needed for the caller
        statuses = {b["path"]: b["status"] for b in data["books"]}
        self.assertEqual(set(statuses.values()), {"clean"})
        (decision,) = data["decisions_needed"]
        self.assertEqual(decision["decision"], "apply_lossy")
        self.assertIn("broken.epub", " ".join(decision["books"]))
        self.assertIn("decisions needed", out.getvalue())

    def test_apply_lossy_is_the_consent_and_applies_with_backup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _phase1_book(root / "broken.epub", broken=True)
            _phase1_book(root / "clean.epub", broken=False)
            bdir = root / "backups"
            jout = root / "phase1.json"
            results = [
                CheckResult(2, 0, 0),
                CheckResult(0, 0, 0),
                CheckResult(0, 0, 0),
            ]
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch("bindery.cli.epubcheck_available", return_value=True),
                mock.patch("bindery.cli.run_epubcheck", side_effect=results),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                rc = main(
                    [
                        "run",
                        "phase1",
                        td,
                        "--apply-lossy",
                        "--backup",
                        str(bdir),
                        "--json",
                        str(jout),
                    ]
                )
            data = json.loads(jout.read_text())
            # the consent applied the repair and the backup captured the
            # original; read the files before the tmp context deletes them
            backup_names = [p.name for p in bdir.rglob("*.epub")]
            with zipfile.ZipFile(next(p for p in bdir.rglob("broken.epub"))) as z:
                self.assertIn(b"<p>", z.read("t.xhtml"))
            with zipfile.ZipFile(root / "broken.epub") as z:
                repaired = z.read("t.xhtml").decode()
        self.assertEqual(rc, 0)
        self.assertTrue(data["apply_lossy"])
        self.assertEqual(data["decisions_needed"], [])
        self.assertEqual(data["summary"]["repair"]["applied"], 1)
        self.assertEqual(backup_names, ["broken.epub"])
        self.assertEqual(repaired.count("<p>"), repaired.count("</p>"))

    def test_usage_errors(self):
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "empty"
            empty.mkdir()
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                self.assertEqual(main(["run", "phase1", str(empty)]), 1)
                self.assertEqual(main(["run", "phase1", str(Path(td) / "nope")]), 1)
        self.assertIn("no .epub", err.getvalue())

    def test_non_interactive_flag_wiring(self):
        args = build_parser().parse_args(["run", "phase1", "/tmp", "--non-interactive"])
        self.assertTrue(args.non_interactive)
        self.assertFalse(args.apply_lossy)
        p3 = build_parser().parse_args(
            ["run", "phase3", "--ids", "1", "--non-interactive"]
        )
        self.assertTrue(p3.non_interactive)


class TestRunPhase3(unittest.TestCase):
    """run phase3: the post-import scoped repair sweep. The phase-3 skill's
    step-10 scope warning is mechanized: no --ids, no sweep, exit 2."""

    def _library(self, root):
        return TestLibraryIdScoping._library(self, root)

    def test_unscoped_sweep_is_refused_with_exit_2(self):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(main(["run", "phase3"]), 2)
            self.assertEqual(main(["run", "phase3", "--ids", ","]), 2)
        self.assertIn("refused", err.getvalue())

    def test_missing_library_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "nowhere"
            empty.mkdir()
            old = os.getcwd()
            os.chdir(empty)
            try:
                err = io.StringIO()
                with redirect_stdout(io.StringIO()), redirect_stderr(err):
                    rc = main(["run", "phase3", "--ids", "1"])
            finally:
                os.chdir(old)
        self.assertEqual(rc, 1)
        self.assertIn("metadata.db", err.getvalue())

    def test_scoped_run_applies_into_the_library(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._library(Path(td))
            # book 1 must give the repair engine something to fix: an unclosed
            # <p> that only --reserialize repairs (a nochange book never
            # reaches validation, so it would never apply)
            broken = root / "A" / "One (1)" / "One - Author.epub"
            with zipfile.ZipFile(broken, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr(
                    "META-INF/container.xml",
                    '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="content.opf" '
                    'media-type="application/oebps-package+xml"/></rootfiles></container>',
                )
                z.writestr(
                    "content.opf",
                    '<package xmlns="http://www.idpf.org/2007/opf">'
                    '<manifest><item id="c1" href="t.xhtml" '
                    'media-type="application/xhtml+xml"/></manifest>'
                    '<spine><itemref idref="c1"/></spine></package>',
                )
                z.writestr(
                    "t.xhtml",
                    "<html><body><p>"
                    + ("prose prose prose " * 20)
                    + "<p>more</body></html>",
                )
            jout = root / "phase3.json"
            results = [
                CheckResult(1, 0, 0),  # sweep book 1: fatal, selected
                CheckResult(0, 0, 0),  # 'after' (before reused)
            ]
            out, err = io.StringIO(), io.StringIO()
            old = os.getcwd()
            os.chdir(root)
            try:
                with (
                    mock.patch("bindery.cli.epubcheck_available", return_value=True),
                    mock.patch("bindery.cli.run_epubcheck", side_effect=results),
                    redirect_stdout(out),
                    redirect_stderr(err),
                ):
                    rc = main(["run", "phase3", "--ids", "1", "--json", str(jout)])
            finally:
                os.chdir(old)
            data = json.loads(jout.read_text())
        self.assertEqual(rc, 0)
        self.assertEqual(data["mode"], "phase3")
        self.assertEqual(data["summary"]["scoped"], 1)
        self.assertEqual(data["summary"]["before"]["fatals"], 1)
        self.assertEqual(data["summary"]["after"]["fatals"], 0)
        self.assertEqual(data["summary"]["repair"]["applied"], 1)
        self.assertEqual(data["decisions_needed"], [])
        self.assertIn("PHASE 3 SUMMARY", out.getvalue())


class TestSweepWorkers(unittest.TestCase):
    """--workers N: concurrent epubcheck workers over the --sweep candidate
    pass (Phase 8 stretch). Default 1 is the serial sweep; N > 1 must select
    the same candidates in the same order, reuse the sweep result as the
    book's before-measurement, and keep --limit lazy within one window of
    overshoot."""

    def test_flag_defaults_to_one(self):
        parser = build_parser()
        self.assertEqual(parser.parse_args(["library", "/tmp"]).workers, 1)
        self.assertEqual(
            parser.parse_args(["library", "/tmp", "--workers", "4"]).workers, 4
        )

    def test_invalid_worker_count_is_a_usage_error(self):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            rc = main(["library", "/tmp", "--workers", "0", "--no-validate"])
        self.assertEqual(rc, 1)
        self.assertIn("--workers", err.getvalue())

    def test_parallel_sweep_selects_like_the_serial_one(self):
        # a path-keyed oracle: threads complete in any order, but every
        # book's result is pinned to the book, so determinism survives
        def oracle(path):
            return (
                CheckResult(1, 0, 0) if path.name == "a.epub" else CheckResult(0, 0, 0)
            )

        with tempfile.TemporaryDirectory() as td:
            for name in ("a", "b", "c"):
                build(Path(td) / f"{name}.epub")
            jout = Path(td) / "report.json"
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch("bindery.cli.epubcheck_available", return_value=True),
                mock.patch("bindery.cli.run_epubcheck", side_effect=oracle),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                rc = main(
                    [
                        "library",
                        td,
                        "--sweep",
                        "--only",
                        "fatals",
                        "--workers",
                        "3",
                        "--json",
                        str(jout),
                    ]
                )
            data = json.loads(jout.read_text())
        self.assertEqual(rc, 0)
        statuses = {Path(b["path"]).name: b["status"] for b in data["books"]}
        self.assertEqual(list(statuses), ["a.epub"])
        # the sweep result was reused as the before-measurement and the gate
        # accepted the no-op-fix candidate exactly as the serial sweep would
        self.assertEqual(statuses["a.epub"], "accept")
        self.assertEqual(data["summary"]["accepted"], 1)

    def test_limit_stays_lazy_within_one_window(self):
        checked: list[str] = []

        def oracle(path):
            checked.append(path.name)
            return CheckResult(1, 0, 0)

        with tempfile.TemporaryDirectory() as td:
            for name in ("a", "b", "c", "d", "e"):
                build(Path(td) / f"{name}.epub")
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch("bindery.cli.epubcheck_available", return_value=True),
                mock.patch("bindery.cli.run_epubcheck", side_effect=oracle),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                rc = main(
                    [
                        "library",
                        td,
                        "--sweep",
                        "--only",
                        "all",
                        "--limit",
                        "1",
                        "--workers",
                        "2",
                    ]
                )
        self.assertEqual(rc, 0)
        # the first window (a, b) is checked; the keeper limit of 1 stops the
        # sweep there: at most one window of overshoot, never the whole tree.
        # (2 sweep calls + 1 after-validation on the candidate: the fixture's
        # &nbsp; always gives the core pass a fix, so the candidate validates.)
        self.assertEqual(len(checked), 3)

    def test_serial_limit_checks_exactly_the_limit(self):
        checked: list[str] = []

        def oracle(path):
            checked.append(path.name)
            return CheckResult(1, 0, 0)

        with tempfile.TemporaryDirectory() as td:
            for name in ("a", "b", "c", "d", "e"):
                build(Path(td) / f"{name}.epub")
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch("bindery.cli.epubcheck_available", return_value=True),
                mock.patch("bindery.cli.run_epubcheck", side_effect=oracle),
                redirect_stdout(out),
                redirect_stderr(err),
            ):
                rc = main(["library", td, "--sweep", "--only", "all", "--limit", "1"])
        self.assertEqual(rc, 0)
        # 1 sweep call + 1 after-validation on the single candidate
        self.assertEqual(len(checked), 2)
