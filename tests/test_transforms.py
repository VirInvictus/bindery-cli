"""Tests for the deterministic repair transforms, using the real malformations the
2026 Calibre audit turned up. Each cleaned fragment is checked for the specific fix
and, where it forms a whole document, re-parsed to prove well-formedness."""

import unittest
import xml.etree.ElementTree as ET

from bindery.transforms import (
    HTML_TRANSFORMS,
    add_img_alt,
    apply_transforms,
    css_protected_tags,
    drop_duplicate_xmlns,
    encode_url_spaces,
    escape_bare_amp,
    escape_unknown_entities,
    fix_empty_body,
    fix_id_colons,
    fix_missing_title,
    fix_named_entities,
    self_close_void,
    strip_invalid_attributes,
    strip_invalid_value,
    strip_prolog_junk,
    style_block_tags,
    unwrap_block_in_inline,
    unwrap_illegal_tags,
)


class TestAddImgAlt(unittest.TestCase):
    def test_missing_alt_added(self):
        out, n = add_img_alt('<img src="index-1_1.jpg" class="calibre1"/>')
        self.assertEqual(n, 1)
        self.assertEqual(out, '<img src="index-1_1.jpg" class="calibre1" alt=""/>')

    def test_existing_alt_untouched_either_quote_style(self):
        for frag in (
            '<img src="a.jpg" alt="a photo"/>',
            "<img src='a.jpg' alt=''/>",
            '<img alt="" src="a.jpg"/>',
        ):
            self.assertEqual(add_img_alt(frag), (frag, 0), frag)

    def test_idempotent(self):
        out, _ = add_img_alt('<img src="a.jpg">')
        self.assertEqual(add_img_alt(out), (out, 0))

    def test_open_tag_form_kept_open(self):
        # self_close_void owns the slash; this transform must not add one.
        out, n = add_img_alt('<img src="a.jpg">')
        self.assertEqual((out, n), ('<img src="a.jpg" alt="">', 1))

    def test_data_alt_is_not_alt(self):
        out, n = add_img_alt('<img src="a.jpg" data-alt="x"/>')
        self.assertEqual(n, 1)
        self.assertIn('alt=""', out)
        self.assertIn('data-alt="x"', out)

    def test_gt_inside_attribute_value(self):
        out, n = add_img_alt('<img src="a.jpg" title="a > b"/>')
        self.assertEqual(n, 1)
        self.assertEqual(out, '<img src="a.jpg" title="a > b" alt=""/>')

    def test_cdata_and_comments_untouched(self):
        doc = '<img src="a.jpg"/><!-- <img src="b.jpg"/> -->'
        out, n = add_img_alt(doc)
        self.assertEqual(n, 1)
        self.assertIn('<!-- <img src="b.jpg"/> -->', out)

    def test_result_is_well_formed(self):
        out, _ = add_img_alt('<body><img src="a.jpg"/><p>x</p></body>')
        ET.fromstring(out)


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

    def test_bare_amp_in_url_attribute_is_core(self):
        # The HTM-025 'unescaped ampersand' shape in a query string: the core
        # transform already covers attribute values (transforms run on raw text).
        out, n = escape_bare_amp('<a href="page?a=1&b=2">x</a>')
        self.assertEqual(n, 1)
        self.assertEqual(out, '<a href="page?a=1&amp;b=2">x</a>')


class TestEncodeUrlSpaces(unittest.TestCase):
    def test_double_quoted(self):
        out, n = encode_url_spaces('<a href="a b.htm">x</a>')
        self.assertEqual((out, n), ('<a href="a%20b.htm">x</a>', 1))

    def test_single_quoted(self):
        out, n = encode_url_spaces("<img src='i 1.jpg'/>")
        self.assertEqual((out, n), ("<img src='i%201.jpg'/>", 1))

    def test_namespaced_prefix_covered(self):
        out, n = encode_url_spaces('<image xlink:href="a b.png"/>')
        self.assertEqual((out, n), ('<image xlink:href="a%20b.png"/>', 1))

    def test_data_href_is_not_href(self):
        s = '<a data-href="a b.htm" href="c d.htm">x</a>'
        out, n = encode_url_spaces(s)
        self.assertEqual(n, 1)
        self.assertIn('data-href="a b.htm"', out)
        self.assertIn('href="c%20d.htm"', out)

    def test_other_attributes_untouched(self):
        s = '<a title="a b" href="c.htm">x</a>'
        self.assertEqual(encode_url_spaces(s), (s, 0))

    def test_idempotent(self):
        out, _ = encode_url_spaces('<a href="a b.htm">x</a>')
        self.assertEqual(encode_url_spaces(out), (out, 0))

    def test_protected_spans_untouched(self):
        s = '<!-- keep href="a b.htm" --><a href="c d.htm">x</a>'
        out, n = encode_url_spaces(s)
        self.assertEqual(n, 1)
        self.assertIn('<!-- keep href="a b.htm" -->', out)
        self.assertIn('href="c%20d.htm"', out)


class TestPrologAndXmlns(unittest.TestCase):
    def test_strip_bom_and_junk(self):
        out, n = strip_prolog_junk('﻿  <?xml version="1.0"?><r/>')
        self.assertEqual(n, 1)
        self.assertTrue(out.startswith("<?xml"))

    def test_clean_prolog_untouched(self):
        s = '<?xml version="1.0"?><r/>'
        out, n = strip_prolog_junk(s)
        self.assertEqual((out, n), (s, 0))

    def test_legal_leading_whitespace_is_not_a_fix(self):
        # Whitespace in the prolog is legal XML when no declaration follows it.
        # Counting it as a fix marked the document changed, which forced repair_epub's
        # decode("utf-8", "replace") round-trip on a file that had nothing wrong.
        s = '\n<!DOCTYPE html>\n<html xmlns="a"><body/></html>'
        self.assertEqual(strip_prolog_junk(s), (s, 0))

    def test_whitespace_before_xml_declaration_is_stripped(self):
        # A declaration must be the very first thing in the document, so here the
        # leading whitespace really is fatal.
        out, n = strip_prolog_junk('\n  <?xml version="1.0"?><r/>')
        self.assertEqual((out, n), ('<?xml version="1.0"?><r/>', 1))

    def test_bom_stripped_without_a_declaration(self):
        out, n = strip_prolog_junk("﻿<!DOCTYPE html><html/>")
        self.assertEqual((out, n), ("<!DOCTYPE html><html/>", 1))

    def test_junk_stripped_without_a_declaration(self):
        out, n = strip_prolog_junk("stray<html/>")
        self.assertEqual((out, n), ("<html/>", 1))

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

    def test_self_closing_slash_survives_unquoted_value(self):
        # The unquoted-value branch used to swallow the tag's own `/`, so dropping the
        # attribute left `<img>`: a well-formed self-closed tag turned into an unclosed
        # one, i.e. this fix introduced the very fatal it exists to remove.
        self.assertEqual(strip_invalid_attributes("<img 31=x/>"), ("<img/>", 1))
        self.assertEqual(strip_invalid_attributes("<p v:foo=bar/>"), ("<p/>", 1))

    def test_unquoted_value_may_end_in_a_slash(self):
        # A trailing `/` belongs to the tag only when `>` follows it; here it is part
        # of the URL and must survive alongside the dropped attribute.
        out, n = strip_invalid_attributes("<a href=http://example.com/ 31=x>t</a>")
        self.assertEqual((out, n), ("<a href=http://example.com/>t</a>", 1))

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


class TestStructuralRepairsAreOptIn(unittest.TestCase):
    """The six structural repairs must never ride along in the default pipeline.

    Until v0.17 these ran on every book via HTML_TRANSFORMS, so a plain repair
    could delete attributes, unwrap elements, and fabricate content the author
    never wrote. The default pass is well-formedness only; each structural defect
    must survive it byte-for-byte and yield zero fix counts.
    """

    CASES = {
        "empty body": "<html><head><title>t</title></head><body> </body></html>",
        "missing title": "<html><head></head><body><p>x</p></body></html>",
        "id colons": '<p id="a:b">x</p><a href="#c:d">y</a>',
        "block in inline": "<span><div>x</div></span>",
        "invalid value": '<div class="c" value="7">x</div>',
        "illegal tags": "<w>bold</w><sentence>s</sentence><pagebreak/>",
        "url spaces": '<a href="a b.htm">x</a>',
    }

    def test_default_pipeline_leaves_every_structural_defect_alone(self):
        for name, doc in self.CASES.items():
            with self.subTest(defect=name):
                out, counts = apply_transforms(doc, HTML_TRANSFORMS)
                self.assertEqual((out, counts), (doc, {}))

    def test_each_function_still_fires_when_called_directly(self):
        functions = [
            fix_empty_body,
            fix_missing_title,
            fix_id_colons,
            unwrap_block_in_inline,
            strip_invalid_value,
            lambda s: unwrap_illegal_tags(s),
            encode_url_spaces,
        ]
        for fn, (_, doc) in zip(functions, self.CASES.items(), strict=True):
            with self.subTest(
                transform=fn.__name__ if hasattr(fn, "__name__") else "unwrap"
            ):
                _, n = fn(doc)
                self.assertGreaterEqual(n, 1)


class TestCssProtectedTags(unittest.TestCase):
    def test_element_selector_protects(self):
        self.assertIn("st", css_protected_tags("st { color: red }"))
        self.assertIn("w", css_protected_tags("p st, x > w { margin: 0 }"))
        self.assertIn("o", css_protected_tags("@media print { o > sentence {} }"))
        # case-insensitive both ways
        self.assertIn("w", css_protected_tags("W:first-line { }"))

    def test_compound_selector_with_class_or_pseudo_protects(self):
        # `pagebreak.new` styles pagebreak ELEMENTS that carry class new; missing
        # this would let the unwrap destroy real formatting.
        self.assertIn("pagebreak", css_protected_tags("pagebreak.new:after {}"))

    def test_class_and_id_selectors_do_not_protect(self):
        self.assertEqual(css_protected_tags(".st { color: red }"), frozenset())
        self.assertEqual(css_protected_tags("#w { color: red }"), frozenset())
        self.assertEqual(css_protected_tags("div.st { }"), frozenset())

    def test_similar_names_do_not_match(self):
        # 'strong' contains 'st'; the selector-boundary regex must not care.
        self.assertNotIn("st", css_protected_tags("strong { font-weight: bold }"))

    def test_comments_are_ignored(self):
        self.assertEqual(css_protected_tags("/* w { } */ p { }"), frozenset())

    def test_style_block_tags_scans_inline_css(self):
        doc = "<style>w { }</style><p>hi</p>"
        self.assertIn("w", style_block_tags(doc))
        self.assertEqual(style_block_tags("<p>.w { }</p>"), frozenset())


class TestUnwrapIllegalTagsProtection(unittest.TestCase):
    def test_protected_tag_survives_unstyled_removed(self):
        doc = "<p><w>b</w></p><p><sentence>s</sentence></p>"
        out, n = unwrap_illegal_tags(doc, protected_tags=frozenset({"w"}))
        self.assertIn("<w>b</w>", out)
        self.assertNotIn("<sentence>", out)
        self.assertIn("s", out)  # inner text of removed tag survives
        self.assertEqual(n, 2)

    def test_default_removes_every_illegal_tag(self):
        out, n = unwrap_illegal_tags("<w>a</w><o>b</o>")
        self.assertEqual(out, "ab")
        self.assertEqual(n, 4)

    def test_case_insensitive_removal_and_protection(self):
        out, n = unwrap_illegal_tags("<W>x</W>")
        self.assertEqual((out, n), ("x", 2))
        out, _ = unwrap_illegal_tags("<W>x</W>", protected_tags=frozenset({"w"}))
        self.assertEqual(out, "<W>x</W>")


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
