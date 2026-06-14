"""Tests for the opt-in page-number strip: number parsing, the two layer signals,
the conservative (merge-only) and aggressive (whole-layer) removal, roman-numeral
preservation, prose conservation, and an end-to-end repair_epub pass."""

import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from bindery.epub import repair_epub
from bindery.pagination import (
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
            report = repair_epub(src, dst, strip_pagination=True)
            self.assertGreaterEqual(report.fixes.get("stripped_pagination", 0), 20)
            with zipfile.ZipFile(dst) as z:
                out = z.read("c1.xhtml").decode("utf-8")
            self.assertNotRegex(out, r"<p>\d+</p>")  # no bare-number paragraphs left


if __name__ == "__main__":
    unittest.main()
