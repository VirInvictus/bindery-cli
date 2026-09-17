"""The --encode-url-spaces rename half (v0.44.0): archive entries whose
names carry raw spaces are renamed to their underscore spellings and every
reference to them is rewritten, so epubcheck's PKG-010 advisories clear.
Also the tempered-quote fix in the reference matcher: a double-quoted href
carrying an apostrophe is ordinary and must encode (the Theaetetus hole,
2026-09-16)."""

import tempfile
import unittest
import zipfile
from pathlib import Path

from bindery.epub import (
    repair_epub,
    rewrite_space_renames,
    rewrite_space_renames_css,
    space_rename_map,
)
from bindery.transforms import encode_url_spaces


class TemperedValueMatcherTests(unittest.TestCase):
    def test_apostrophe_in_double_quoted_href_encodes(self):
        # The hole: [^"']* stopped at the apostrophe, so an href whose
        # filename carried one never matched and never encoded.
        s = '<item href="text/part 1 - O\'Brien.xhtml"/>'
        out, n = encode_url_spaces(s)
        self.assertEqual(n, 1)
        self.assertIn("text/part%201%20-%20O'Brien.xhtml", out)

    def test_quote_inside_single_quoted_value(self):
        s = "<item href='a \"b\" c.xhtml'/>"
        out, n = encode_url_spaces(s)
        self.assertEqual(n, 1)
        # Only spaces encode; the embedded quotes stay verbatim.
        self.assertIn('a%20"b"%20c.xhtml', out)

    def test_ordinary_values_unchanged(self):
        s = "<a href=\"plain.htm\">x</a><img src='i.jpg'/>"
        out, n = encode_url_spaces(s)
        self.assertEqual((out, n), (s, 0))


class SpaceRenameMapTests(unittest.TestCase):
    def test_space_names_map_to_underscores(self):
        m = space_rename_map(["OEBPS/text/a b.xhtml", "OEBPS/content.opf", "mimetype"])
        self.assertEqual(m, {"OEBPS/text/a b.xhtml": "OEBPS/text/a_b.xhtml"})

    def test_target_already_existing_refuses(self):
        m = space_rename_map(["a b.xhtml", "a_b.xhtml"])
        self.assertEqual(m, {})

    def test_duplicate_entry_names_refuse(self):
        # Broken archives can carry the same name twice; renaming would
        # fold them, so the map refuses instead.
        m = space_rename_map(["a b.xhtml", "a b.xhtml"])
        self.assertEqual(m, {})

    def test_unambiguous_names_rename_despite_a_sibling_refusal(self):
        m = space_rename_map(["a b.xhtml", "a_b.xhtml", "c d.xhtml"])
        self.assertEqual(m, {"c d.xhtml": "c_d.xhtml"})


class RewriteTests(unittest.TestCase):
    def test_refs_rewrite_relative_with_fragments(self):
        resolved = {"text/a b.xhtml": "text/a_b.xhtml"}
        out, n = rewrite_space_renames(
            '<a href="a b.xhtml#chap">x</a>', "text", "text", resolved
        )
        self.assertEqual(n, 1)
        self.assertIn('href="a_b.xhtml#chap"', out)

    def test_external_and_unrelated_refs_untouched(self):
        resolved = {"text/a b.xhtml": "text/a_b.xhtml"}
        s = '<a href="http://x/a b.xhtml">e</a><img src="a_b.xhtml"/>'
        out, n = rewrite_space_renames(s, "text", "text", resolved)
        self.assertEqual((out, n), (s, 0))

    def test_parent_dir_rename_preserves_the_relative_shape(self):
        # The document itself moved (its directory renamed): the lookup
        # resolves against the old layout, the replacement is spelled
        # against the new one.
        resolved = {"OEBPS text/i 1.png": "OEBPS_text/i_1.png"}
        out, n = rewrite_space_renames(
            '<img src="../OEBPS text/i 1.png"/>', "text", "text", resolved
        )
        self.assertEqual(n, 1)
        self.assertIn('src="../OEBPS_text/i_1.png"', out)

    def test_css_quoted_and_bare_urls(self):
        resolved = {"i 1.png": "i_1.png"}
        out, n = rewrite_space_renames_css(
            'a { background: url("i 1.png") }\nb { background: url(i%201.png) }',
            ".",
            ".",
            resolved,
        )
        self.assertEqual(n, 2)
        self.assertIn('url("i_1.png")', out)
        self.assertIn("url(i_1.png)", out)


class _TempEpub(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bindery_rename_")
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

    def _spaces_book(self):
        doc = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
            "<title>p</title></head><body><p>word word word word</p></body></html>"
        )
        return self._build(
            "spaces.epub",
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
                    '<manifest><item id="c1" href="text/part 1.xhtml" '
                    'media-type="application/xhtml+xml"/></manifest>'
                    '<spine><itemref idref="c1"/></spine></package>'
                ),
                "OEBPS/text/part 1.xhtml": doc,
                "OEBPS/style/s 1.css": "p { background: url('../text/part 1.xhtml') }",
            },
        )


class RenameEndToEndTests(_TempEpub):
    def test_entries_renamed_and_every_reference_rewritten(self):
        src = self._spaces_book()
        dst = self._path("out.epub")
        report = repair_epub(src, dst, url_spaces=True)
        self.assertEqual(report.fixes.get("entries_renamed"), 2)
        with zipfile.ZipFile(dst) as z:
            names = z.namelist()
            self.assertNotIn("OEBPS/text/part 1.xhtml", names)
            self.assertIn("OEBPS/text/part_1.xhtml", names)
            self.assertNotIn("OEBPS/style/s 1.css", names)
            self.assertIn("OEBPS/style/s_1.css", names)
            opf = z.read("OEBPS/content.opf").decode()
            self.assertIn('href="text/part_1.xhtml"', opf)
            self.assertNotIn("part 1.xhtml", opf)
            css = z.read("OEBPS/style/s_1.css").decode()
            self.assertIn("url('../text/part_1.xhtml')", css)

    def test_collision_refuses_only_the_ambiguous_entry(self):
        doc = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title>'
            "</head><body><p>word word word word</p></body></html>"
        )
        src = self._build(
            "collision.epub",
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
                    '<item id="a" href="a b.xhtml" media-type="application/xhtml+xml"/>'
                    '<item id="b" href="a_b.xhtml" media-type="application/xhtml+xml"/>'
                    '<item id="c" href="c d.xhtml" media-type="application/xhtml+xml"/>'
                    "</manifest>"
                    '<spine><itemref idref="a"/><itemref idref="b"/>'
                    '<itemref idref="c"/></spine></package>'
                ),
                "OEBPS/a b.xhtml": doc,
                "OEBPS/a_b.xhtml": doc,
                "OEBPS/c d.xhtml": doc,
            },
        )
        dst = self._path("out.epub")
        report = repair_epub(src, dst, url_spaces=True)
        # "a b.xhtml" collides with the existing "a_b.xhtml" and stays;
        # "c d.xhtml" renames.
        self.assertEqual(report.fixes.get("entries_renamed"), 1)
        with zipfile.ZipFile(dst) as z:
            names = z.namelist()
            self.assertIn("OEBPS/a b.xhtml", names)
            self.assertIn("OEBPS/a_b.xhtml", names)
            self.assertIn("OEBPS/c_d.xhtml", names)
            opf = z.read("OEBPS/content.opf").decode()
            # The refused entry keeps its raw name; its reference still
            # takes the reference half's percent-encode (which epubcheck
            # decodes back to the raw name).
            self.assertIn('href="a%20b.xhtml"', opf)
            self.assertIn('href="c_d.xhtml"', opf)

    def test_renamed_opf_regenates_a_stale_container(self):
        doc = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>p</title>'
            "</head><body><p>word word word word</p></body></html>"
        )
        src = self._build(
            "opfspaces.epub",
            {
                "META-INF/container.xml": (
                    '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="OEBPS/the book.opf" '
                    'media-type="application/oebps-package+xml"/></rootfiles></container>'
                ),
                "OEBPS/the book.opf": (
                    '<?xml version="1.0"?>'
                    '<package xmlns="http://www.idpf.org/2007/opf" '
                    'unique-identifier="id">'
                    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                    "<dc:title>t</dc:title><dc:language>en</dc:language>"
                    '<dc:identifier id="id">u</dc:identifier></metadata>'
                    "<manifest>"
                    '<item id="a" href="p.xhtml" media-type="application/xhtml+xml"/>'
                    '</manifest><spine><itemref idref="a"/></spine></package>'
                ),
                "OEBPS/p.xhtml": doc,
            },
        )
        dst = self._path("out.epub")
        report = repair_epub(src, dst, url_spaces=True, fix_container=True)
        self.assertEqual(report.fixes.get("entries_renamed"), 1)
        with zipfile.ZipFile(dst) as z:
            self.assertIn("OEBPS/the_book.opf", z.namelist())
            container = z.read("META-INF/container.xml").decode()
            self.assertIn("OEBPS/the_book.opf", container)
            self.assertNotIn(
                "the book.opf", container.replace("OEBPS/the_book.opf", "")
            )


if __name__ == "__main__":
    unittest.main()
