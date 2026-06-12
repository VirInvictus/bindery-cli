"""CLI-level safety tests: the gate must not be bypassed when epubcheck fails, the
single-file repair must write the exact bytes the gate accepted (flags included),
and candidate selection must refuse --only fatals without an audit."""

import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest import mock

from bindery.cli import _load_audit, main, process_book

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
        with tempfile.TemporaryDirectory() as td:
            with_header = Path(td) / "a.csv"
            with_header.write_text("fatals,errors,warnings,path\n\n2,5,1,/lib/x.epub\n")
            self.assertEqual(_load_audit(with_header), {"/lib/x.epub": (2, 5, 1)})
            # A headerless CSV must not lose its first row.
            headerless = Path(td) / "b.csv"
            headerless.write_text("1,0,0,/lib/y.epub\n")
            self.assertEqual(_load_audit(headerless), {"/lib/y.epub": (1, 0, 0)})


if __name__ == "__main__":
    unittest.main()
