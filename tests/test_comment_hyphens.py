"""The RSC-016 structural repair (--fix-comment-double-hyphen, v0.46.0):
`--` inside an XML comment is illegal XML (a fatal), and the fix replaces it
with an en-dash so the document parses. The fixture class is the 2026-09-16
Theaetetus hand-repair (split_269.html carried two `--` sites in a comment).
Comment bodies only: text nodes and CDATA sections are never touched."""

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from bindery.cli import CheckResult, process_book
from bindery.epub import RepairFlags, RepairReport, repair_epub
from bindery.transforms import EN_DASH, fix_comment_double_hyphen


class TransformTests(unittest.TestCase):
    def test_double_hyphen_becomes_en_dash(self):
        out, n = fix_comment_double_hyphen("<!-- moved -- chapters -->")
        self.assertEqual(n, 1)
        self.assertEqual(out, f"<!-- moved {EN_DASH} chapters -->")

    def test_two_sites_count_two(self):
        out, n = fix_comment_double_hyphen("<!-- a -- b -- c -->")
        self.assertEqual(n, 2)
        self.assertEqual(out, f"<!-- a {EN_DASH} b {EN_DASH} c -->")

    def test_idempotent(self):
        once, n = fix_comment_double_hyphen("<!-- a -- b -->")
        self.assertEqual(n, 1)
        again, n2 = fix_comment_double_hyphen(once)
        self.assertEqual(n2, 0)
        self.assertEqual(again, once)

    def test_text_nodes_untouched(self):
        # `--` in character data is legal XML; the fix claims only comments.
        s = "<p>a -- b</p><!-- c -- d --><p>e -- f</p>"
        out, n = fix_comment_double_hyphen(s)
        self.assertEqual(n, 1)
        self.assertIn("<p>a -- b</p>", out)
        self.assertIn("<p>e -- f</p>", out)

    def test_cdata_untouched(self):
        s = "<p><![CDATA[<!-- x -- y -->]]></p>"
        out, n = fix_comment_double_hyphen(s)
        self.assertEqual((out, n), (s, 0))

    def test_terminator_intact(self):
        out, n = fix_comment_double_hyphen("<!-- a -- b -->")
        self.assertTrue(out.endswith("-->"))

    def test_triple_hyphen_before_terminator_untouched(self):
        # The parser ends the comment at the `-->` inside `--->`; the body
        # is ` a -`, which carries no `--`, so the span is already legal.
        s = "<!-- a --->"
        self.assertEqual(fix_comment_double_hyphen(s), (s, 0))

    def test_quad_hyphen_run_fixed(self):
        # `----` in a comment: the span is ` a --` and the fix rewrites the
        # body's `--` while the terminator stays put.
        out, n = fix_comment_double_hyphen("<!-- a ---->")
        self.assertEqual(n, 1)
        self.assertEqual(out, f"<!-- a {EN_DASH}-->")

    def test_unclosed_comment_untouched(self):
        # No `-->`, no span: the unclosed comment is its own fatal class and
        # the fix does not claim it.
        s = "<p>x</p><!-- a -- b"
        self.assertEqual(fix_comment_double_hyphen(s), (s, 0))

    def test_clean_comment_untouched(self):
        s = "<!-- producer note -->"
        self.assertEqual(fix_comment_double_hyphen(s), (s, 0))

    def test_bang_close_inside_body_still_counted(self):
        # XML has no `--!>` abort form (that is HTML): the `--` inside the
        # body is the same illegal sequence and the fix rewrites it.
        out, n = fix_comment_double_hyphen("<!-- a --!> b -->")
        self.assertEqual(n, 1)
        self.assertEqual(out, f"<!-- a {EN_DASH}!> b -->")

    def test_no_marker_fast_path(self):
        s = "<p>plain -- text</p>"
        self.assertEqual(fix_comment_double_hyphen(s), (s, 0))


class _TempEpub(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bindery_comment_")
        self.addCleanup(self.tmp.cleanup)

    def _path(self, name):
        return Path(self.tmp.name) / name

    def _build(self, name, entries):
        path = self._path(name)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
            for entry, data in entries.items():
                z.writestr(entry, data)
        return path

    def _comment_book(self):
        doc = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
            "<title>p</title></head><body>"
            "<!-- split 269 -- hand note -- kept --><p>word word word word</p>"
            "</body></html>"
        )
        return self._build(
            "comments.epub",
            {
                "META-INF/container.xml": (
                    '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                    'media-type="application/oebps-package+xml"/></rootfiles></container>'
                ),
                "OEBPS/content.opf": (
                    '<?xml version="1.0"?>'
                    '<package xmlns="http://www.idpf.org/2007/opf" '
                    'unique-identifier="id">'
                    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                    "<dc:title>t</dc:title><dc:language>en</dc:language>"
                    '<dc:identifier id="id">u</dc:identifier></metadata>'
                    "<manifest>"
                    '<item id="c1" href="text/split_269.xhtml" '
                    'media-type="application/xhtml+xml"/>'
                    "</manifest>"
                    '<spine><itemref idref="c1"/></spine></package>'
                ),
                "OEBPS/text/split_269.xhtml": doc,
            },
        )


class EndToEndTests(_TempEpub):
    def test_flag_fixes_comment_and_counts(self):
        src = self._comment_book()
        dst = self._path("out.epub")
        report = repair_epub(src, dst, RepairFlags(comment_double_hyphens=True))
        self.assertEqual(report.fixes.get("fix_comment_double_hyphen"), 2)
        with zipfile.ZipFile(dst) as z:
            doc = z.read("OEBPS/text/split_269.xhtml").decode()
        self.assertIn(f"split 269 {EN_DASH} hand note {EN_DASH} kept", doc)
        self.assertNotIn(" -- ", doc)

    def test_default_pass_leaves_the_comment_alone(self):
        src = self._comment_book()
        dst = self._path("out.epub")
        report = repair_epub(src, dst)
        self.assertNotIn("fix_comment_double_hyphen", report.fixes)
        with zipfile.ZipFile(dst) as z:
            doc = z.read("OEBPS/text/split_269.xhtml").decode()
        self.assertIn("split 269 -- hand note", doc)

    def test_ncx_comment_fixed(self):
        src = self._build(
            "ncx.epub",
            {
                "META-INF/container.xml": (
                    '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="content.opf" '
                    'media-type="application/oebps-package+xml"/></rootfiles></container>'
                ),
                "content.opf": (
                    '<?xml version="1.0"?>'
                    '<package xmlns="http://www.idpf.org/2007/opf" '
                    'unique-identifier="id">'
                    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                    "<dc:title>t</dc:title><dc:language>en</dc:language>"
                    '<dc:identifier id="id">u</dc:identifier></metadata>'
                    "<manifest>"
                    '<item id="c1" href="c1.xhtml" '
                    'media-type="application/xhtml+xml"/>'
                    '<item id="ncx" href="toc.ncx" '
                    'media-type="application/x-dtbncx+xml"/>'
                    "</manifest>"
                    '<spine toc="ncx"><itemref idref="c1"/></spine></package>'
                ),
                "toc.ncx": (
                    '<?xml version="1.0"?>'
                    '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                    "<head/><docTitle><text>t</text></docTitle>"
                    '<navMap><!-- rebuilt -- partial --><navPoint id="np1" '
                    'playOrder="1"><navLabel><text>c1</text></navLabel>'
                    '<content src="c1.xhtml"/></navPoint></navMap></ncx>'
                ),
                "c1.xhtml": (
                    '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>x</p>'
                    "</body></html>"
                ),
            },
        )
        dst = self._path("out.epub")
        report = repair_epub(src, dst, RepairFlags(comment_double_hyphens=True))
        self.assertEqual(report.fixes.get("fix_comment_double_hyphen"), 1)
        with zipfile.ZipFile(dst) as z:
            ncx = z.read("toc.ncx").decode()
        self.assertIn(f"rebuilt {EN_DASH} partial", ncx)

    def test_opf_comment_fixed(self):
        src = self._build(
            "opfcomment.epub",
            {
                "META-INF/container.xml": (
                    '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="content.opf" '
                    'media-type="application/oebps-package+xml"/></rootfiles></container>'
                ),
                "content.opf": (
                    '<?xml version="1.0"?>'
                    '<package xmlns="http://www.idpf.org/2007/opf" '
                    'unique-identifier="id">'
                    "<!-- guids -- v2 -- do not edit -->"
                    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                    "<dc:title>t</dc:title><dc:language>en</dc:language>"
                    '<dc:identifier id="id">u</dc:identifier></metadata>'
                    "<manifest>"
                    '<item id="c1" href="c1.xhtml" '
                    'media-type="application/xhtml+xml"/>'
                    "</manifest>"
                    '<spine><itemref idref="c1"/></spine></package>'
                ),
                "c1.xhtml": (
                    '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>x</p>'
                    "</body></html>"
                ),
            },
        )
        dst = self._path("out.epub")
        report = repair_epub(src, dst, RepairFlags(comment_double_hyphens=True))
        self.assertEqual(report.fixes.get("fix_comment_double_hyphen"), 2)
        with zipfile.ZipFile(dst) as z:
            opf = z.read("content.opf").decode()
        self.assertIn(f"guids {EN_DASH} v2 {EN_DASH} do not edit", opf)


class GateTests(unittest.TestCase):
    """The fix is a structural repair: it flows through the normal
    improvement gate, no no_worse side door (clearing RSC-016 IS a
    measurable fatal-count gain)."""

    def _verdict(self, before, after):
        report = RepairReport(fixes={"fix_comment_double_hyphen": 1})
        with (
            mock.patch("bindery.cli.repair_epub", return_value=report),
            mock.patch("bindery.cli.run_epubcheck", side_effect=[before, after]),
        ):
            return process_book(
                Path("x.epub"),
                Path("."),
                validate=True,
                flags=RepairFlags(comment_double_hyphens=True),
            )

    def test_fatal_cleared_accepts(self):
        o = self._verdict(CheckResult(2, 0, 0), CheckResult(0, 0, 0))
        self.assertEqual(o.status, "accept")

    def test_fatal_reduced_but_still_fatal_is_partial(self):
        o = self._verdict(CheckResult(2, 0, 0), CheckResult(1, 0, 0))
        self.assertEqual(o.status, "partial")

    def test_regression_rejects(self):
        o = self._verdict(CheckResult(0, 0, 0), CheckResult(0, 2, 0))
        self.assertEqual(o.status, "reject")

    def test_no_gain_on_clean_book_is_equal(self):
        o = self._verdict(CheckResult(0, 0, 0), CheckResult(0, 0, 0))
        self.assertEqual(o.status, "equal")


if __name__ == "__main__":
    unittest.main()
