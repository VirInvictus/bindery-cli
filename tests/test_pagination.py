"""Tests for the opt-in page-number strip: number parsing, the two layer signals,
the conservative (merge-only) and aggressive (whole-layer) removal, roman-numeral
preservation, prose conservation, and an end-to-end repair_epub pass."""

import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from bindery.epub import RepairFlags, repair_epub
from bindery.pagination import (
    _P_RE,
    _Block,
    _safe,
    collect_runheads,
    detect_page_layer,
    number_value,
    strip_pagination_doc,
)

# Must exceed PROSE_MIN (120 chars) so neighbours count as real prose paragraphs.
LONG = (
    "the quick brown fox jumped over the lazy dog and then kept on running for many "
    "miles across the wide green fields under a bright and cloudless summer sky"
)


def _doc(body: str) -> str:
    return f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'


def _texts(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


class TestNumberValue(unittest.TestCase):
    def test_arabic_roman_years_junk(self):
        self.assertEqual(number_value("42"), 42)
        self.assertEqual(number_value("xiv"), 14)
        self.assertIsNone(number_value("1999"))  # year range excluded
        self.assertIsNone(number_value("Chapter"))
        self.assertIsNone(number_value("12345"))  # > 4 digits
        self.assertIsNone(number_value("i"))  # lone i too noisy

    def test_word_romans_are_not_numbers(self):
        # reported 2026-09-08: [ivxlcdm]{2,7} read ordinary English words built
        # from roman letters as page numbers (number_value("mid") = 1499)
        for w in (
            "mid",
            "dim",
            "mix",
            "lid",
            "civil",
            "mild",
            "mil",
            "ill",
            "civ",
            "div",
        ):
            self.assertIsNone(number_value(w), w)

    def test_strict_roman_grammar_still_parses_numerals(self):
        for w, v in (
            ("ii", 2),
            ("iv", 4),
            ("ix", 9),
            ("xiv", 14),
            ("xl", 40),
            ("xciv", 94),
        ):
            self.assertEqual(number_value(w), v, w)

    def test_roman_values_are_capped_at_page_range(self):
        # well-formed numerals over 99 are words or section marks in prose
        # (mix = 1009), not page numbers; front-matter pages stay under 100
        self.assertIsNone(number_value("mcmxcix"))
        self.assertIsNone(number_value("MMXXIV"))
        self.assertEqual(number_value("xcix"), 99)


class TestRunheadsAndLayer(unittest.TestCase):
    def test_runhead_detected_when_repeated(self):
        html = "".join(f"<p>REDEMPTION ARK</p><p>{LONG} {i}.</p>" for i in range(10))
        self.assertIn("REDEMPTION ARK", collect_runheads([html]))

    def test_layer_true_needs_numbers_and_interrupts(self):
        # 20+ arabic numbers, several wedged mid-sentence (lowercase continuation)
        body = "".join(
            f"<p>{LONG} and</p><p>{n}</p><p>then {LONG}</p>" for n in range(1, 26)
        )
        self.assertTrue(detect_page_layer([_doc(body)], set()))

    def test_layer_false_for_chapter_numbers(self):
        # numbers open chapters (capital next), so zero interrupts -> not a page layer
        body = "".join(f"<p>{n}</p><p>Chapter prose. {LONG}.</p>" for n in range(1, 30))
        self.assertFalse(detect_page_layer([_doc(body)], set()))


class TestStripConservative(unittest.TestCase):
    def test_lowercase_continuation_merges(self):
        body = f"<p>{LONG} and</p><p>7</p><p>sometimes {LONG}.</p>"
        out, n = strip_pagination_doc(_doc(body), set())
        self.assertEqual(n, 1)
        self.assertNotIn(">7<", out)
        self.assertIn("and sometimes", _texts(out))  # merged across the removed number

    def test_word_split_closes_hyphen(self):
        body = f"<p>{LONG} compli-</p><p>338</p><p>mentary {LONG}.</p>"
        out, n = strip_pagination_doc(_doc(body), set())
        self.assertEqual(n, 1)
        self.assertIn("complimentary", _texts(out))

    def test_clean_chapter_number_left_alone(self):
        # number between two complete sentences, capital next: not baked, not removed
        body = f"<p>{LONG}.</p><p>4</p><p>The next thing. {LONG}.</p>"
        out, n = strip_pagination_doc(_doc(body), set())
        self.assertEqual(n, 0)
        self.assertEqual(out, _doc(body))

    def test_word_roman_not_deleted_as_page_number(self):
        # reported 2026-09-08: <p>mid</p> before a lowercase-starting paragraph
        # was a confident interrupt, and the word was deleted and merged away
        body = f"<p>{LONG} and</p><p>mid</p><p>sometimes {LONG}.</p>"
        out, n = strip_pagination_doc(_doc(body), set())
        self.assertEqual(n, 0)
        self.assertIn(">mid<", out)
        self.assertNotIn("and sometimes", _texts(out))  # no merge across the word

    def test_lowercase_roman_still_merges_on_confident_interrupt(self):
        # the legitimate shape the strict grammar must keep working: front-matter
        # roman page numbers interrupting prose are still stripped and merged
        body = f"<p>{LONG} and</p><p>xiv</p><p>sometimes {LONG}.</p>"
        out, n = strip_pagination_doc(_doc(body), set())
        self.assertEqual(n, 1)
        self.assertIn("and sometimes", _texts(out))


class TestStripAggressive(unittest.TestCase):
    def test_between_paragraph_numbers_deleted_in_layer(self):
        body = f"<p>{LONG}.</p><p>4</p><p>The next thing. {LONG}.</p>"
        out, n = strip_pagination_doc(_doc(body), set(), delete_layer=True)
        self.assertEqual(n, 1)
        self.assertNotIn(">4<", out)
        # both sentences survive intact (delete-only, no merge between complete sentences)
        self.assertIn("The next thing", _texts(out))

    def test_roman_preserved_even_in_layer(self):
        body = f"<p>{LONG}.</p><p>IV</p><p>The next thing. {LONG}.</p>"
        out, n = strip_pagination_doc(_doc(body), set(), delete_layer=True)
        self.assertEqual(n, 0)
        self.assertIn(">IV<", out)

    def test_self_closing_p_does_not_abort(self):
        # Real EPUBs carry self-closing <p/>; the tag-balance net must not read those
        # as an imbalance and revert the whole document (it once did).
        nums = "".join(
            f"<p>{LONG} and</p><p>{n}</p><p>more {LONG}.</p>" for n in range(1, 6)
        )
        body = nums + '<p class="ornament"/>'
        out, n = strip_pagination_doc(_doc(body), set(), delete_layer=True)
        self.assertGreaterEqual(n, 5)

    def test_prose_conserved(self):
        body = "".join(
            f"<p>{LONG} and</p><p>{n}</p><p>more {LONG}.</p>" for n in range(1, 6)
        )
        out, _ = strip_pagination_doc(_doc(body), set(), delete_layer=True)
        letters_in = re.sub(r"[^a-z]", "", _texts(_doc(body)).lower())
        letters_out = re.sub(r"[^a-z]", "", _texts(out).lower())
        self.assertEqual(letters_in, letters_out)  # not one letter of prose lost


class TestIdPreservation(unittest.TestCase):
    def test_p_id_survives_merge(self):
        # An id on the removed <p> itself (a common page-anchor shape) used to vanish
        # with the block, breaking page-list and internal links; it must be hoisted
        # into the merged paragraph as an empty anchor.
        body = f'<p>{LONG} and</p><p id="page7">7</p><p>sometimes {LONG}.</p>'
        out, n = strip_pagination_doc(_doc(body), set())
        self.assertEqual(n, 1)
        self.assertNotIn(">7<", out)
        self.assertIn('id="page7"', out)

    def test_p_id_survives_delete_only(self):
        # Non-confident removal (capital next) in a layer book takes the delete-only
        # path; an emptied <p id=...></p> shell must remain, not nothing.
        body = f'<p>{LONG}.</p><p id="page7">7</p><p>The next thing. {LONG}.</p>'
        out, n = strip_pagination_doc(_doc(body), set(), delete_layer=True)
        self.assertEqual(n, 1)
        self.assertNotIn(">7<", out)
        self.assertIn('id="page7"', out)

    def test_single_quoted_inner_anchor_hoisted(self):
        body = f"<p>{LONG}.</p><p><a id='pg7'/>7</p><p>The next thing. {LONG}.</p>"
        out, n = strip_pagination_doc(_doc(body), set(), delete_layer=True)
        self.assertEqual(n, 1)
        self.assertNotIn(">7<", out)
        self.assertIn("id='pg7'", out)


class TestEndToEnd(unittest.TestCase):
    OPF = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="b">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="b">u</dc:identifier></metadata>'
        '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="c1"/></spine></package>'
    )

    def test_repair_epub_strips_pagination(self):
        body = "".join(
            f"<p>{LONG} and</p><p>{n}</p><p>kept {LONG}.</p>" for n in range(1, 25)
        )
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            dst = Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("content.opf", self.OPF)
                z.writestr("c1.xhtml", _doc(body))
            report = repair_epub(src, dst, RepairFlags(strip_pagination=True))
            self.assertGreaterEqual(report.fixes.get("stripped_pagination", 0), 20)
            with zipfile.ZipFile(dst) as z:
                out = z.read("c1.xhtml").decode("utf-8")
            self.assertNotRegex(out, r"<p>\d+</p>")  # no bare-number paragraphs left


if __name__ == "__main__":
    unittest.main()


class TestSelfClosingParagraphs(unittest.TestCase):
    """issue #31: a self-closing <p .../> has no </p>; a block match starting
    there ran forward to the NEXT paragraph's close tag and welded both into
    one "paragraph" whose text could read as a page number, stripping the
    divider markup (and anything between) with it."""

    def test_divider_before_number_strips_only_the_number(self):
        body = (
            f'<p class="divider"/>\n<p>1</p>\n<p>{LONG}.</p>'
            f'<p class="divider"/>\n<p>2</p>\n<p>Then. {LONG}.</p>'
        )
        out, n = strip_pagination_doc(_doc(body), set(), delete_layer=True)
        self.assertGreaterEqual(n, 2)
        self.assertEqual(out.count('<p class="divider"/>'), 2)
        self.assertIn(LONG, out)

    def test_self_closing_p_tolerated_in_conservative_path(self):
        body = (
            f'<p class="divider"/>\n<p>{LONG} and</p><p>7</p><p>sometimes {LONG}.</p>'
        )
        out, n = strip_pagination_doc(_doc(body), set())
        self.assertEqual(n, 1)
        self.assertIn('<p class="divider"/>', out)
        self.assertIn("and sometimes", _texts(out))


class TestIssue34DataIdProtection(unittest.TestCase):
    """issue #34: pagination's id guards carried the bare `\\bid=` class --
    `data-id="page7"` (arbitrary data no reader can resolve) over-protected
    a number block from the strip. The anchor is the #30 class generalized
    to xml:id: `xml:id` IS a real XML id and stays protected, `data-id`
    does not."""

    def test_data_id_block_is_strippable(self):
        body = f'<p>{LONG}.</p><p data-id="page7">7</p><p>The next thing. {LONG}.</p>'
        out, n = strip_pagination_doc(_doc(body), set(), delete_layer=True)
        self.assertEqual(n, 1)
        self.assertNotIn("data-id", out)
        self.assertNotIn(">7<", out)

    def test_real_id_block_still_protected(self):
        body = f'<p>{LONG}.</p><p id="page7">7</p><p>The next thing. {LONG}.</p>'
        out, n = strip_pagination_doc(_doc(body), set(), delete_layer=True)
        self.assertEqual(n, 1)
        self.assertNotIn(">7<", out)
        self.assertIn('id="page7"', out)

    def test_xml_id_anchor_still_hoisted(self):
        body = f"<p>{LONG}.</p><p><a xml:id='pg7'/>7</p><p>The next thing. {LONG}.</p>"
        out, n = strip_pagination_doc(_doc(body), set(), delete_layer=True)
        self.assertEqual(n, 1)
        self.assertNotIn(">7<", out)
        self.assertIn("xml:id='pg7'", out)


class TestProseFlowGates(unittest.TestCase):
    """v1.2.5, the All About Cake cookbook fixture: the strip's text-shape signals
    merged a prose paragraph across a <p class="rec_line"/><table><td> boundary,
    discarded the table openers, and orphaned the rest of the table behind the
    merged paragraph (an RSC-016 fatal the gate correctly rejected); under a
    licensed page layer the same text-shape signals swept 110 ingredient-quantity
    cells as "page numbers" and harvested their cell text ("1 tsp", "large eggs")
    as running headers. Every destructive decision now requires prose flow: no
    splice may consume markup the block model does not own, and a digit enclosed
    by foreign markup on both sides is layout content, never page furniture."""

    # The PRH recipe-card shape from the real book (c03.xhtml): the self-closing
    # rec_line paragraph and the table openers are invisible to _P_RE, so the
    # corridor between the prose paragraph and the first ingredient cell is
    # nothing the block model can see.
    COOKBOOK_ROW = (
        '<p class="rec_line"/><table>\r\n\t\t\t\t<tr class="grayline">\r\n'
        "\t\t\t\t\t<td>\r\n\t\t\t\t\t\t"
        '<p class="rilf">\t5</p>\r\n\t\t\t\t\t</td>\r\n\t\t\t\t\t<td>\r\n'
        "\t\t\t\t\t\t"
        '<p class="rilb">starlight peppermint candies</p>\r\n\t\t\t\t\t</td>\r\n'
        "\t\t\t\t\t<td>\r\n\t\t\t\t\t\t"
        '<p class="ril"/></td>\r\n\t\t\t\t</tr>\r\n\t\t\t</table>'
    )

    def _cookbook_body(self) -> str:
        return f'<p class="bodymain">{LONG} and I believe</p>{self.COOKBOOK_ROW}'

    def test_cell_digit_never_merges_across_the_table_boundary(self):
        # the RSC-016 shape: the lowercase cell after the table used to read as a
        # confident continuation of the prose paragraph, and the merge consumed
        # the table openers it cannot model
        out, n = strip_pagination_doc(_doc(self._cookbook_body()), set())
        self.assertEqual(n, 0)
        self.assertEqual(out, _doc(self._cookbook_body()))

    def test_cell_digits_are_never_page_layer_furniture(self):
        out, n = strip_pagination_doc(
            _doc(self._cookbook_body()), set(), delete_layer=True
        )
        self.assertEqual(n, 0)
        self.assertIn("starlight peppermint candies", out)
        self.assertEqual(out.count("<td>"), 3)

    def test_table_cell_digits_do_not_license_the_page_layer(self):
        rows = "".join(
            f'<p class="rec_line"/><table><tr><td><p class="rilf">\t{n}</p></td>'
            f'<td><p class="rilb">finely ground almonds</p></td></tr></table>'
            for n in range(1, 26)
        )
        html = _doc(f'<p class="bodymain">{LONG}</p>{rows}')
        self.assertFalse(detect_page_layer([html], set()))

    def test_divider_adjacent_number_still_swept_but_never_merged(self):
        # one-sided furniture stays in scope (the #31 contract), but the merge
        # span owns the whole corridor and must not swallow the divider: the
        # merge is refused and the number degrades to delete-only
        body = (
            f'<p>{LONG} and</p><p>7</p><p class="divider"/>\n<p>sometimes {LONG}.</p>'
        )
        out, n = strip_pagination_doc(_doc(body), set())
        self.assertEqual(n, 1)
        self.assertNotIn(">7<", out)
        self.assertIn('<p class="divider"/>', out)
        # the continuation paragraph survives as its own element (a merged
        # output would swallow it into the pp paragraph)
        self.assertIn("<p>sometimes", out)

    def test_runhead_cascade_stays_inside_the_prose_run(self):
        # a runhead cell across a table boundary is not the removed number's
        # furniture: the cascade must not cross the cell markup to reach it
        body = (
            f"<p>{LONG} and</p><p>7</p>"
            f"<table><tr><td><p>1 tsp</p></td><td><p>{LONG}.</p></td></tr></table>"
        )
        out, n = strip_pagination_doc(_doc(body), {"1 tsp"}, delete_layer=True)
        self.assertEqual(n, 1)
        self.assertNotIn(">7<", out)
        self.assertIn("1 tsp", out)

    def test_flat_runhead_still_cascades(self):
        body = f"<p>{LONG} and</p><p>7</p><p>1 tsp</p><p>{LONG}.</p>"
        out, n = strip_pagination_doc(_doc(body), {"1 tsp"}, delete_layer=True)
        self.assertEqual(n, 1)
        self.assertNotIn("1 tsp", out)


class TestTagDeltaNet(unittest.TestCase):
    """v1.2.5: the fourth safety net. The p/a balance net reads only two names;
    the All About Cake merge discarded a <table>/<tr>/<td> opener run the p/a
    net cannot see, and the orphaned closers rode through as an epubcheck fatal
    on the candidate. The splice may not change ANY element's open/close balance."""

    def test_changed_foreign_tag_balance_is_refused(self):
        before = _doc("<table><tr><td><p>word</p></td></tr></table>")
        after = _doc("</td></tr></table>")  # opener run lost, closers orphaned
        blocks = [_Block(m) for m in _P_RE.finditer(before)]
        self.assertFalse(_safe(before, after, blocks, {0}))

    def test_pre_existing_stray_close_is_not_a_splice_error(self):
        # a messy source with an unbalanced close keeps its balance untouched:
        # the net compares deltas, not absolute counts, so the strip still runs
        before = _doc("<table><tr><td></td></tr></table></td><p>word</p>")
        after = _doc("<table><tr><td></td></tr></table></td>")
        blocks = [_Block(m) for m in _P_RE.finditer(before)]
        self.assertTrue(_safe(before, after, blocks, {0}))

    def test_legitimate_merge_keeps_every_elements_balance(self):
        before = _doc(f"<p>{LONG} and</p><p>7</p><p>sometimes {LONG}.</p>")
        after = _doc(f"<p>{LONG} and sometimes {LONG}.</p>")
        blocks = [_Block(m) for m in _P_RE.finditer(before)]
        self.assertTrue(_safe(before, after, blocks, {1}))


_COOKBOOK_FIXTURE = Path(__file__).resolve().parent.parent / (
    "testing_facility/books_to_fix/all-about-cake-pagination-table-cell-strip.epub"
)
HAVE_COOKBOOK_FIXTURE = _COOKBOOK_FIXTURE.exists()


@unittest.skipUnless(HAVE_COOKBOOK_FIXTURE, "local issue fixture not present")
class TestPaginationCookbookFixture(unittest.TestCase):
    """The real All About Cake cookbook (added to the facility 2026-11-20). It
    carries NO baked page numbers (the PRH pagebreaks are epub:type spans inside
    heading paragraphs), so --strip-pagination must find nothing to remove."""

    def test_strip_pagination_finds_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            dst = Path(td) / "out.epub"
            shutil.copyfile(_COOKBOOK_FIXTURE, src)
            report = repair_epub(src, dst, RepairFlags(strip_pagination=True))
            self.assertEqual(report.fixes.get("stripped_pagination", 0), 0)
