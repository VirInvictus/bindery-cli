"""Tests for the deterministic repair transforms, using the real malformations the
2026 Calibre audit turned up. Each cleaned fragment is checked for the specific fix
and, where it forms a whole document, re-parsed to prove well-formedness."""

import unittest
import xml.etree.ElementTree as ET

from bindery.transforms import (
    apply_transforms,
    drop_duplicate_xmlns,
    escape_bare_amp,
    fix_named_entities,
    self_close_void,
    strip_prolog_junk,
    HTML_TRANSFORMS,
)


class TestVoidElements(unittest.TestCase):
    def test_unclosed_link_br_img(self):
        for frag in (
            '<link href="x.css" rel="stylesheet">',
            "<br>",
            '<img src="a.jpg">',
        ):
            out, n = self_close_void(frag)
            self.assertEqual(n, 1, frag)
            self.assertTrue(out.endswith("/>"), out)

    def test_already_self_closed_untouched(self):
        for frag in ("<br/>", '<img src="a.jpg"/>', '<link href="x"/>'):
            out, n = self_close_void(frag)
            self.assertEqual(n, 0)
            self.assertEqual(out, frag)

    def test_non_void_untouched(self):
        out, n = self_close_void("<p>hi</p><div>x</div>")
        self.assertEqual(n, 0)
        self.assertEqual(out, "<p>hi</p><div>x</div>")


class TestEntities(unittest.TestCase):
    def test_nbsp_deg_eacute_to_numeric(self):
        out, n = fix_named_entities("a&nbsp;b&deg;c&eacute;d")
        self.assertEqual(n, 3)
        self.assertEqual(out, "a&#160;b&#176;c&#233;d")

    def test_predefined_entities_kept(self):
        out, n = fix_named_entities("a &amp; b &lt; c &gt; d")
        self.assertEqual(n, 0)
        self.assertEqual(out, "a &amp; b &lt; c &gt; d")

    def test_unknown_entity_left_alone(self):
        out, n = fix_named_entities("&notarealentity;")
        self.assertEqual(n, 0)
        self.assertEqual(out, "&notarealentity;")


class TestBareAmp(unittest.TestCase):
    def test_bare_amp_escaped(self):
        out, n = escape_bare_amp("Salt & Pepper")
        self.assertEqual(n, 1)
        self.assertEqual(out, "Salt &amp; Pepper")

    def test_valid_refs_not_doubled(self):
        s = "&amp; &nbsp; &#160; &#x1F600;"
        out, n = escape_bare_amp(s)
        self.assertEqual(n, 0)
        self.assertEqual(out, s)


class TestPrologAndXmlns(unittest.TestCase):
    def test_strip_bom_and_junk(self):
        out, n = strip_prolog_junk('﻿  <?xml version="1.0"?><r/>')
        self.assertEqual(n, 1)
        self.assertTrue(out.startswith("<?xml"))

    def test_clean_prolog_untouched(self):
        s = '<?xml version="1.0"?><r/>'
        out, n = strip_prolog_junk(s)
        self.assertEqual((out, n), (s, 0))

    def test_duplicate_xmlns_collapsed(self):
        s = '<html xmlns="http://www.w3.org/1999/xhtml" xmlns="http://www.w3.org/1999/xhtml"><body/></html>'
        out, n = drop_duplicate_xmlns(s)
        self.assertEqual(n, 1)
        self.assertEqual(out.count('xmlns="'), 1)
        ET.fromstring(out)  # now well-formed

    def test_namespaced_xmlns_kept(self):
        s = '<html xmlns="a" xmlns:epub="b"><body/></html>'
        out, n = drop_duplicate_xmlns(s)
        self.assertEqual(n, 0)
        self.assertEqual(out, s)


class TestPipeline(unittest.TestCase):
    def test_full_document_made_well_formed(self):
        doc = (
            '﻿<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns="http://www.w3.org/1999/xhtml">'
            '<head><link rel="stylesheet" href="s.css"></head>'
            "<body><p>Tom &amp; Jerry caf&eacute;&nbsp;time<br>done</p></body></html>"
        )
        out, counts = apply_transforms(doc, HTML_TRANSFORMS)
        self.assertIn("self_close_void", counts)
        self.assertIn("fix_named_entities", counts)
        self.assertIn("drop_duplicate_xmlns", counts)
        # The whole thing must now parse as XML.
        ET.fromstring(out)
        self.assertNotIn("&nbsp;", out)
        self.assertNotIn("&eacute;", out)


if __name__ == "__main__":
    unittest.main()
