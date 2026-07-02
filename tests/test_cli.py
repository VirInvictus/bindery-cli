"""CLI-level safety tests: the gate must not be bypassed when epubcheck fails or when
the lossy strip leaves fatals behind, a corrupt book must not abort a library sweep,
the single-file repair must write the exact bytes the gate accepted (flags included)
and label partial output honestly, and candidate selection must refuse --only fatals
without an audit."""

import io
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from bindery.cli import _load_audit, main, process_book
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


if __name__ == "__main__":
    unittest.main()
