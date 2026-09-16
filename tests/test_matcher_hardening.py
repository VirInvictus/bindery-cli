"""Regression net for the 2026-09-16 per-matcher hardening (the roadmap's
matcher-audit follow-up box, executed).

The 0.41.1 hotfix made three rewritten patterns possessive; the audit that
followed found six more greedy patterns of the same exponential class
(the quote-aware loop whose branches overlap) and five lazy patterns of
the quadratic class. The greedy loops are now possessive `*+` (byte-
identical on success: the loop is always followed by a literal `>` that
the `[^>]` branch cannot consume, so backtracking only ever happened on
failure). The lazy loops got a disjoint branch 3 (`[^>"']`: the catch-all
no longer eats quote characters), which makes re-partitioning impossible
and stops an unterminated quote inside a tag from pairing with a distant
quote and matching across markup.

A1 behavioral pins (success cases unchanged) and A2 pins (unterminated-
quote tags are now skipped rather than mis-paired) live beside the spin
guards here. The spin guard is the same shape as the 0.41.0 test: a
failing candidate over an apostrophe-rich run must finish, not hang."""

import signal
import time
import unittest

from bindery import epub, transforms
from bindery.transforms import _START_TAG_RE


def _apostrophe_run(times: int = 2000) -> str:
    return "don't can't won't it's kind of prose, isn't it " * times


class GreedyStartTagSpin(unittest.TestCase):
    """_START_TAG_RE runs under repair --all on every content doc: the same
    input class as the Physics fixture (an apostrophe-rich run with no `>`
    after the tag opener) must fail linearly."""

    def test_spin_shape_fails_fast(self):
        doc = "<p class='broken " + _apostrophe_run() + "and still no closer in sight"
        signal.alarm(20)
        try:
            t0 = time.perf_counter()
            m = _START_TAG_RE.search(doc)
            elapsed = time.perf_counter() - t0
        finally:
            signal.alarm(0)
        self.assertIsNone(m)
        self.assertLess(elapsed, 5.0)

    def test_success_shape_is_unchanged(self):
        text = "<p class='x' data-y=\"a>b\">body</p>"
        m = _START_TAG_RE.search(text)
        self.assertEqual(m.group(0), "<p class='x' data-y=\"a>b\">")
        # the `>` inside the quoted attribute stays attribute content
        self.assertNotIn("body", m.group(0))


class OpfTagMatchersSpin(unittest.TestCase):
    """The OPF matchers iterate manifest/spine/guide tags: a truncated OPF
    that ends inside an apostrophe-rich attribute run must fail linearly."""

    SPIN_PREFIX = "<item id='id1' href='text/part1.xhtml' media-type='application/xhtml+xml' extra='"

    def _assert_fast_no_match(self, regex, text):
        signal.alarm(20)
        try:
            t0 = time.perf_counter()
            m = regex.search(text)
            elapsed = time.perf_counter() - t0
        finally:
            signal.alarm(0)
        self.assertIsNone(m)
        self.assertLess(elapsed, 5.0)

    def test_item_tag_spin_shape(self):
        self._assert_fast_no_match(epub._ITEM_TAG_RE, self.SPIN_PREFIX + _apostrophe_run(500))

    def test_itemref_tag_spin_shape(self):
        text = "<itemref idref='id1' extra='" + _apostrophe_run(500)
        self._assert_fast_no_match(epub._SPINE_ITEM_RE, text)

    def test_cover_meta_spin_shape(self):
        text = '<meta name="cover" content="' + _apostrophe_run(500)
        self._assert_fast_no_match(epub._COVER_META_RE, text)

    def test_guide_reference_spin_shape(self):
        text = "<reference type='text' title='" + _apostrophe_run(500)
        self._assert_fast_no_match(epub._GUIDE_REF_RE, text)

    def test_success_shapes_unchanged(self):
        opf = (
            '<package><item id="id1" href="a.xhtml" media-type="application/xhtml+xml"/>'
            "<itemref idref='id1'/>"
            '<meta name="cover" content="cover-image"/>'
            "<reference type='text' title='t' href='a.xhtml'/></package>"
        )
        item = epub._ITEM_TAG_RE.search(opf)
        self.assertEqual(item.group(0), '<item id="id1" href="a.xhtml" media-type="application/xhtml+xml"/>')
        self.assertIsNotNone(epub._SPINE_ITEM_RE.search(opf))
        self.assertIsNotNone(epub._COVER_META_RE.search(opf))
        self.assertIsNotNone(epub._GUIDE_REF_RE.search(opf))

    def test_prune_dangling_edges_spin_shape(self):
        # prune_dangling_edges' inline spine|item|itemref matcher over a
        # truncated OPF: linear failure, no hang.
        opf = (
            '<package><spine toc="ncx"><itemref idref=\'broken ' + _apostrophe_run(500)
        )
        signal.alarm(20)
        try:
            t0 = time.perf_counter()
            epub.prune_dangling_edges(opf, {"gone"})
            elapsed = time.perf_counter() - t0
        finally:
            signal.alarm(0)
        self.assertLess(elapsed, 5.0)


class LazyMatcherDisjointBranch(unittest.TestCase):
    """The lazy matchers (_VOID_RE, the img/link/a tag matchers) now use a
    disjoint branch 3 ([^>"']): an unterminated quote inside a tag can no
    longer pair with a distant quote, so the mis-pairing match is gone.
    Every well-formed shape is byte-identical."""

    def test_void_unterminated_quote_skipped(self):
        # OLD: this matched, pairing the quote with a distant one and
        # mis-styling markup after the tag. NEW: no match, text untouched.
        text = '<br x="a> and more prose follows here'
        self.assertIsNone(transforms._VOID_RE.search(text))

    def test_void_wellformed_shapes_still_match(self):
        for tag in ("<br/>", "<br />", '<br class="x"/>', "<hr class='x' />"):
            self.assertIsNotNone(transforms._VOID_RE.search(tag), tag)

    def test_img_unterminated_quote_skipped(self):
        text = '<img src="a> more prose'
        self.assertIsNone(transforms._IMG_TAG_RE.search(text))

    def test_img_wellformed_rebuild_identical(self):
        tag = '<img src="pic.png" alt="a photo" />'
        out, count = transforms.add_img_alt('<html><body>' + tag + '</body></html>')
        self.assertEqual(count, 0)  # alt already present; tag untouched
        self.assertIn(tag, out)

    def test_prune_img_unterminated_quote_skipped(self):
        text = '<img src="a> more prose'
        self.assertIsNone(epub._PRUNE_IMG_TAG_RE.search(text))

    def test_link_and_anchor_unterminated_quote_skipped(self):
        self.assertIsNone(epub._LINK_TAG_RE.search('<link href="a> prose'))
        self.assertIsNone(epub._ANCHOR_TAG_RE.search("<a href='a> prose"))

    def test_link_and_anchor_wellformed_match(self):
        self.assertIsNotNone(epub._LINK_TAG_RE.search('<link href="a.css" rel="stylesheet"/>'))
        self.assertIsNotNone(epub._ANCHOR_TAG_RE.search('<a href="b.xhtml">text</a>'))


if __name__ == "__main__":
    unittest.main()
