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
    escape_unknown_entities,
    fix_named_entities,
    self_close_void,
    strip_invalid_attributes,
    strip_prolog_junk,
)


class TestEscapeUnknownEntities(unittest.TestCase):
    def test_unknown_escaped_known_untouched(self):
        out, n = escape_unknown_entities("<p>&foo; &nbsp; &amp; &#160; &bar;</p>")
        self.assertEqual(n, 2)
        self.assertIn("&amp;foo;", out)
        self.assertIn("&amp;bar;", out)
        self.assertIn("&nbsp;", out)  # known: left for fix_named_entities
        self.assertIn("&#160;", out)  # numeric refs are never entity names
        # idempotent: the &amp; it produced is predefined and stays put
        self.assertEqual(escape_unknown_entities(out), (out, 0))

    def test_internal_subset_skips_whole_document(self):
        # An internal DTD subset can declare the entity, making it legitimate; the
        # escape would then change visible text, so such documents are left alone.
        doc = '<!DOCTYPE html [<!ENTITY foo "bar">]><html><p>&foo;</p></html>'
        self.assertEqual(escape_unknown_entities(doc), (doc, 0))

    def test_plain_doctype_does_not_skip(self):
        doc = "<!DOCTYPE html><html><p>&foo;</p></html>"
        out, n = escape_unknown_entities(doc)
        self.assertEqual(n, 1)
        self.assertIn("&amp;foo;", out)

    def test_cdata_and_comments_untouched(self):
        doc = "<p>&foo;</p><![CDATA[&foo;]]><!-- &foo; -->"
        out, n = escape_unknown_entities(doc)
        self.assertEqual(n, 1)
        self.assertIn("<![CDATA[&foo;]]>", out)
        self.assertIn("<!-- &foo; -->", out)


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

    def test_orphaned_end_tag_swallowed(self):
        out, n = self_close_void('<img src="a.jpg"></img>')
        self.assertEqual(n, 2)
        self.assertEqual(out, '<img src="a.jpg"/>')

        out, n = self_close_void("<br></br>")
        self.assertEqual(n, 2)
        self.assertEqual(out, "<br/>")

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

    def test_hyphenated_custom_element_untouched(self):
        # `-`, `:`, `.` are XML name characters but not \w, so the old `\b` boundary
        # still matched `<col` inside `<col-group>` and self-closed it.
        for frag in (
            "<col-group><p>a</p></col-group>",
            "<source-list>s</source-list>",
            "<img.caption>t</img.caption>",
        ):
            out, n = self_close_void(frag)
            self.assertEqual(n, 0, frag)
            self.assertEqual(out, frag)


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

    def test_multi_codepoint_entity_expanded(self):
        # &NotEqualTilde; is U+2242 U+0338: one reference per codepoint, same glyph.
        out, n = fix_named_entities("a&NotEqualTilde;b")
        self.assertEqual(n, 1)
        self.assertEqual(out, "a&#8770;&#824;b")


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

    def test_epub3_prefix_kept(self):
        s = '<html xmlns:epub="http://www.idpf.org/2007/ops" epub:prefix="math: http://www.w3.org/1998/Math/MathML z3998: http://www.daisy.org/"><body z3998:role="section"><math:math display="block"/></body></html>'
        out, n = strip_invalid_attributes(s)
        self.assertEqual(n, 0)
        self.assertEqual(out, s)


class TestProtectedSpans(unittest.TestCase):
    # Inside CDATA and comments a bare `&`, an entity name, or a `<br>` is literal,
    # legal content; rewriting it would change what renders.

    def test_cdata_left_alone(self):
        cdata = '<script><![CDATA[ if (a & b) say("<br>&nbsp;") ]]></script>'
        s = f"<p>Salt & Pepper</p>{cdata}"
        out, _ = apply_transforms(s, HTML_TRANSFORMS)
        self.assertIn("<p>Salt &amp; Pepper</p>", out)  # outside: still fixed
        self.assertIn(cdata, out)  # inside: byte-for-byte

    def test_comment_left_alone(self):
        comment = "<!-- legal & comment with <hr> and &nbsp; -->"
        s = f"{comment}<p>x<br></p>"
        out, _ = apply_transforms(s, HTML_TRANSFORMS)
        self.assertIn(comment, out)
        self.assertIn("<br/>", out)

    def test_invalid_attrs_in_comment_kept(self):
        s = '<!-- <img v:shapes="x"> --><img v:shapes="y" src="a.jpg"/>'
        out, n = strip_invalid_attributes(s)
        self.assertEqual(n, 1)
        self.assertIn('<!-- <img v:shapes="x"> -->', out)
        self.assertNotIn('v:shapes="y"', out)


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
