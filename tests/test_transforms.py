"""Tests for the deterministic repair transforms, using the real malformations the
2026 Calibre audit turned up. Each cleaned fragment is checked for the specific fix
and, where it forms a whole document, re-parsed to prove well-formedness."""

import unittest
import xml.etree.ElementTree as ET

from bindery.transforms import (
    HTML_TRANSFORMS,
    apply_transforms,
    drop_duplicate_xmlns,
    escape_bare_amp,
    fix_named_entities,
    self_close_void,
    strip_invalid_attributes,
    strip_prolog_junk,
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

    def test_void_name_as_prefix_of_other_tag_untouched(self):
        # Regression: `<col` must not match inside `<colgroup>` (the Purr bug, which
        # self-closed <colgroup> and orphaned its </colgroup>). Same for meta/source/etc.
        for frag in (
            "<colgroup><col/></colgroup>",
            "<metadata>x</metadata>",
            "<sourcefile>y</sourcefile>",
            "<embedded>z</embedded>",
        ):
            out, n = self_close_void(frag)
            self.assertEqual(n, 0, frag)
            self.assertEqual(out, frag)

    def test_gt_inside_attribute_value(self):
        # A `>` inside a quoted attribute must not end the tag early.
        out, n = self_close_void('<img alt="2 > 1" src="x.jpg">')
        self.assertEqual(n, 1)
        self.assertEqual(out, '<img alt="2 > 1" src="x.jpg"/>')

    def test_self_closed_with_space_untouched(self):
        out, n = self_close_void("<br />")
        self.assertEqual(n, 0)
        self.assertEqual(out, "<br />")


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


class TestStripInvalidAttributes(unittest.TestCase):
    def test_digit_led_attribute_dropped(self):
        # the real Rustonomicon case: a mangled `31=""`
        out, n = strip_invalid_attributes('<circle stroke-dasharray="31" 31="" r="7"/>')
        self.assertEqual(n, 1)
        self.assertEqual(out, '<circle stroke-dasharray="31" r="7"/>')

    def test_unbound_prefix_attribute_dropped(self):
        # the real Selfish Gene case: Office VML v:shapes with no xmlns:v
        out, n = strip_invalid_attributes(
            '<img src="a.jpg" v:shapes="Picture_356" class="x"/>'
        )
        self.assertEqual(n, 1)
        self.assertNotIn("v:shapes", out)
        self.assertIn('src="a.jpg"', out)
        self.assertIn('class="x"', out)

    def test_declared_prefix_kept(self):
        s = '<svg xmlns:xlink="http://www.w3.org/1999/xlink"><image xlink:href="c.png"/></svg>'
        out, n = strip_invalid_attributes(s)
        self.assertEqual(n, 0)
        self.assertEqual(out, s)

    def test_well_formed_untouched(self):
        s = '<p class="a" id="b">text</p><br/>'
        out, n = strip_invalid_attributes(s)
        self.assertEqual((out, n), (s, 0))


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
