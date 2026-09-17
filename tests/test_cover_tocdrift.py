"""The v0.44.0 audit analyzers: cover wiring (the EPUB3 half of the ruled
hybrid) and the NCX<->nav drift detector. Both advisory by contract: they
never move an exit code, they report the shape a human judges."""

import pathlib
import tempfile
import unittest
import zipfile

from bindery import audit
from bindery.audit import analyze_cover, analyze_tocdrift, load_book

CONTAINER = (
    '<?xml version="1.0"?><container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
    '<rootfile full-path="content.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles></container>'
)

DOC = (
    '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
    "<head><title>t</title></head><body><p>chapter prose here</p></body></html>"
)


def _epub(tmp, opf, files, name="t.epub"):
    p = pathlib.Path(tmp) / name
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("content.opf", opf)
        for entry, data in files.items():
            z.writestr(entry, data)
    return p


def _opf(manifest, spine_ref="c1", meta="", guide=""):
    return (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>t</dc:title><dc:language>en</dc:language>"
        f'<dc:identifier id="id">u</dc:identifier>{meta}</metadata>'
        f"<manifest>{manifest}</manifest>"
        f'<spine><itemref idref="{spine_ref}"/></spine>{guide}</package>'
    )


class CoverAnalyzerTests(unittest.TestCase):
    def test_epub3_property_present_and_file_present_is_clean(self):
        opf = _opf(
            '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ci" href="cover.jpg" media-type="image/jpeg" '
            'properties="cover-image"/>'
        )
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, {"text.xhtml": DOC, "cover.jpg": "JPG"}))
        r = analyze_cover(book)
        self.assertTrue(r["epub3_declared"])
        self.assertEqual(r["epub3_item"], "ci")
        self.assertFalse(r["cover_file_absent"])
        self.assertEqual(audit._cover_dir(r)[1], "OK")

    def test_epub3_property_without_a_file_is_advisory(self):
        opf = _opf(
            '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ci" href="gone.jpg" media-type="image/jpeg" '
            'properties="cover-image"/>'
        )
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, {"text.xhtml": DOC}))
        r = analyze_cover(book)
        self.assertTrue(r["cover_file_absent"])
        self.assertEqual(audit._cover_dir(r)[1], "ADVISORY")

    def test_dangling_epub2_meta_is_advisory(self):
        opf = _opf(
            '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>',
            meta='<meta name="cover" content="no-such-id"/>',
        )
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, {"text.xhtml": DOC}))
        r = analyze_cover(book)
        self.assertTrue(r["epub2_dangling"])
        self.assertEqual(r["epub2_meta"], "no-such-id")
        self.assertEqual(audit._cover_dir(r)[1], "ADVISORY")

    def test_resolved_epub2_meta_names_the_cover_file(self):
        opf = _opf(
            '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="img" href="cover.jpg" media-type="image/jpeg"/>',
            meta='<meta name="cover" content="img"/>',
        )
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, {"text.xhtml": DOC, "cover.jpg": "JPG"}))
        r = analyze_cover(book)
        self.assertFalse(r["epub2_dangling"])
        self.assertEqual(r["cover_file"], "cover.jpg")
        self.assertEqual(audit._cover_dir(r)[1], "OK")

    def test_no_wiring_at_all_is_advisory(self):
        opf = _opf(
            '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
        )
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, {"text.xhtml": DOC}))
        self.assertEqual(audit._cover_dir(analyze_cover(book))[1], "ADVISORY")


class TocDriftTests(unittest.TestCase):
    NCX = (
        '<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">'
        "<head/><docTitle><text>t</text></docTitle><navMap>"
        '<navPoint id="n1" playOrder="1"><navLabel><text>One</text></navLabel>'
        '<content src="text.xhtml"/></navPoint>'
        '<navPoint id="n2" playOrder="2"><navLabel><text>Two</text></navLabel>'
        '<content src="text.xhtml#two"/></navPoint>'
        "</navMap></ncx>"
    )

    def _files(self, nav_links):
        lis = "".join(
            f'<li><a href="{href}">{label}</a></li>' for label, href in nav_links
        )
        nav = (
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>t</title></head>'
            '<body><nav epub:type="toc"><ol>' + lis + "</ol></nav></body></html>"
        )
        return {"text.xhtml": DOC, "nav.xhtml": nav, "toc.ncx": self.NCX}

    def _opf_with_nav(self):
        return _opf(
            '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ncx" href="toc.ncx" '
            'media-type="application/x-dtbncx+xml"/>'
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
            'properties="nav"/>'
        )

    def test_matching_tocs_are_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(
                _epub(
                    tmp,
                    self._opf_with_nav(),
                    self._files([("One", "text.xhtml"), ("Two", "text.xhtml#two")]),
                )
            )
        r = analyze_tocdrift(book)
        self.assertEqual(r["ncx_points"], 2)
        self.assertEqual(r["nav_entries"], 2)
        self.assertEqual(audit._tocdrift_dir(r)[1], "OK")

    def test_missing_nav_entry_is_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(
                _epub(tmp, self._opf_with_nav(), self._files([("One", "text.xhtml")]))
            )
        r = analyze_tocdrift(book)
        self.assertEqual(len(r["missing_in_nav"]), 1)
        self.assertEqual(r["missing_in_nav"][0][0], "Two")
        self.assertEqual(audit._tocdrift_dir(r)[1], "ADVISORY")

    def test_label_mismatch_is_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(
                _epub(
                    tmp,
                    self._opf_with_nav(),
                    self._files([("One", "text.xhtml"), ("Deux", "text.xhtml#two")]),
                )
            )
        r = analyze_tocdrift(book)
        self.assertEqual(len(r["label_mismatches"]), 1)
        self.assertEqual(r["label_mismatches"][0][1], "Two")
        self.assertEqual(r["label_mismatches"][0][2], "Deux")

    def test_epub2_only_book_has_no_comparison(self):
        opf = _opf(
            '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ncx" href="toc.ncx" '
            'media-type="application/x-dtbncx+xml"/>'
        )
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, {"text.xhtml": DOC, "toc.ncx": self.NCX}))
        r = analyze_tocdrift(book)
        self.assertEqual(r["nav_entries"], 0)
        self.assertEqual(audit._tocdrift_dir(r)[1], "OK")

    def test_advisory_never_reports_problem(self):
        # the contract: both verdict dirs always answer problem=False
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(
                _epub(tmp, self._opf_with_nav(), self._files([("One", "text.xhtml")]))
            )
        for analyzer, verdict in (
            (analyze_cover, audit._cover_dir),
            (analyze_tocdrift, audit._tocdrift_dir),
        ):
            r = analyzer(book)
            self.assertFalse(verdict(r)[0])


if __name__ == "__main__":
    unittest.main()
