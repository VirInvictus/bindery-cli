"""The v0.50.0 contamination analyzer (issue #8) and the shared directory/
single-mode dispatch it rides in on. Advisory by contract: fan-bundle
contamination is the judge-and-import class, so the analyzer never flags a
book and never moves an exit code. The fixtures mirror the 2026-10-14
phase-1 wave: the MDZS fan bundles (another series' name stamped into every
content doc's head <title>, plus a whole Jnovels/Zerobooks ad page whose own
head title names a third series) and Hothouse's pasted-Amazon-review front
matter. The dispatch pin also guards the v0.44.0 misroute it fixes:
run_directory's old if/elif chain reported ocr verdicts under the cover and
tocdrift keys."""

import contextlib as cl
import io
import pathlib
import tempfile
import unittest
import zipfile

from bindery import audit
from bindery.audit import analyze_contamination, load_book

CONTAINER = (
    '<?xml version="1.0"?><container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
    '<rootfile full-path="content.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles></container>'
)

PROSE = "<p>" + ("Some ordinary prose here. " * 30) + "</p>"


def _epub(tmp, opf, files, name="t.epub"):
    p = pathlib.Path(tmp) / name
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("content.opf", opf)
        for entry, data in files.items():
            z.writestr(entry, data)
    return p


def _opf(items, title, creator="An Author", publisher="A Publisher"):
    manifest = "".join(
        f'<item id="{iid}" href="{href}" media-type="application/xhtml+xml"/>'
        for iid, href in items
    )
    spine = "".join(f'<itemref idref="{iid}"/>' for iid, _ in items)
    return (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{title}</dc:title><dc:language>en</dc:language>"
        f"<dc:creator>{creator}</dc:creator>"
        f"<dc:publisher>{publisher}</dc:publisher>"
        '<dc:identifier id="id">u</dc:identifier></metadata>'
        f"<manifest>{manifest}</manifest>"
        f"<spine>{spine}</spine></package>"
    )


def _doc(title, body=PROSE):
    return (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
        f"<head><title>{title}</title></head><body>{body}</body></html>"
    )


class ForeignTitleTests(unittest.TestCase):
    """Sweep (a): another series' name where the book's own identity belongs."""

    def _mdzs_files(self):
        # the MDZS vol-2 shape: every content doc's head <title> is the
        # template's original book, never the real one
        return {
            "s1.xhtml": _doc("Full Metal Panic! Volume 5"),
            "s2.xhtml": _doc("Full Metal Panic! Volume 5"),
            "s3.xhtml": _doc("Full Metal Panic! Volume 5"),
            "s4.xhtml": _doc("Full Metal Panic! Volume 5"),
        }

    def test_head_title_template_reuse_is_foreign(self):
        opf = _opf(
            [(f"s{i}", f"s{i}.xhtml") for i in range(1, 5)],
            "Grandmaster of Demonic Cultivation: Mo Dao Zu Shi Vol. 2",
            creator="Mo Xiang Tong Xiu",
            publisher="Seven Seas Entertainment",
        )
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, self._mdzs_files()))
        r = analyze_contamination(book)
        titles = {f["title"]: f["count"] for f in r["foreign_titles"]}
        self.assertEqual(titles.get("Full Metal Panic! Volume 5"), 4)
        self.assertEqual(audit._contamination_dir(r)[1], "ADVISORY")

    def test_singleton_marker_head_title_is_foreign(self):
        # the ad-page shape: one doc whose head title names a third series
        files = {
            "s1.xhtml": _doc("A Real Chapter"),
            "ad.xhtml": _doc(
                "Kokoro Connect Volume 4: Michi Random",
                body="<p>Download all your Favorite Light Novels Jnovels.com</p>",
            ),
        }
        opf = _opf([("s1", "s1.xhtml"), ("ad", "ad.xhtml")], "A Real Book")
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, files))
        r = analyze_contamination(book)
        titles = {f["title"] for f in r["foreign_titles"]}
        self.assertIn("Kokoro Connect Volume 4: Michi Random", titles)

    def test_own_title_stamped_on_every_doc_is_clean(self):
        # the legitimate shape: the book's own title on every head
        files = {f"s{i}.xhtml": _doc("A Real Book") for i in range(1, 5)}
        opf = _opf([(f"s{i}", f"s{i}.xhtml") for i in range(1, 5)], "A Real Book")
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, files))
        r = analyze_contamination(book)
        self.assertEqual(r["foreign_titles"], [])
        self.assertEqual(audit._contamination_dir(r)[1], "OK")

    def test_own_title_with_bare_volume_number_covers_volume_spelling(self):
        # dc:title "Series 3", running head "Series Volume 3": the
        # marker-stripped comparison keeps the book's own volume header out
        files = {f"s{i}.xhtml": _doc("Wandering Inn Volume 3") for i in range(1, 5)}
        opf = _opf([(f"s{i}", f"s{i}.xhtml") for i in range(1, 5)], "Wandering Inn 3")
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, files))
        r = analyze_contamination(book)
        self.assertEqual(r["foreign_titles"], [])

    def test_leading_part_label_never_flags(self):
        # part labels are furniture, not foreign titles: the spelled-out
        # "BOOK ONE" never matches the marker grammar, and the roman "BOOK
        # II" matches with nothing named in front of it (the Lord of the
        # Rings shape)
        body = (
            "<h1>BOOK ONE: THE RING SETS OUT</h1>"
            + "<p>"
            + ("Nine fingers and the ring. " * 30)
            + "</p>"
        )
        body_roman = (
            "<h1>BOOK II: THE RING GOES SOUTH</h1>"
            + "<p>"
            + ("Nine fingers and the ring. " * 30)
            + "</p>"
        )
        files = {
            "s1.xhtml": _doc("Chapter 1", body=body),
            "s2.xhtml": _doc("Chapter 2", body=body_roman),
            "s3.xhtml": _doc("Chapter 3", body=body),
        }
        opf = _opf(
            [("s1", "s1.xhtml"), ("s2", "s2.xhtml"), ("s3", "s3.xhtml")],
            "The Fellowship of the Ring",
            creator="J. R. R. Tolkien",
        )
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, files))
        r = analyze_contamination(book)
        self.assertEqual(r["foreign_titles"], [])

    def test_foreign_running_header_in_visible_blocks_flags(self):
        # the running-text shape: a repeated visible header naming another
        # series with its volume marker
        body = '<div class="rh">Full Metal Panic! Volume 5</div>' + PROSE
        files = {f"s{i}.xhtml": _doc(f"Chapter {i}", body=body) for i in range(1, 4)}
        opf = _opf([(f"s{i}", f"s{i}.xhtml") for i in range(1, 4)], "A Real Book")
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, files))
        r = analyze_contamination(book)
        titles = {f["title"] for f in r["foreign_titles"]}
        self.assertIn("Full Metal Panic! Volume 5", titles)

    def test_shell_book_is_skipped(self):
        # no package metadata: no "own" to judge foreignness against
        files = self._mdzs_files()
        opf = _opf([(f"s{i}", f"s{i}.xhtml") for i in range(1, 5)], "A Real Book")
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, files))
        book.opf = None
        r = analyze_contamination(book)
        self.assertEqual(r, {"foreign_titles": [], "ads": [], "reviews": []})


class AdSignatureTests(unittest.TestCase):
    """Sweep (b): known fan-bundle site/ad signatures in visible text."""

    def _book_with_ad(self, ad_text):
        files = {
            "s1.xhtml": _doc("Chapter One"),
            "ad.xhtml": _doc("Advertisement", body=f"<p>{ad_text}</p>"),
        }
        opf = _opf([("s1", "s1.xhtml"), ("ad", "ad.xhtml")], "A Real Book")
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, files))
        return analyze_contamination(book)

    def test_jnovels_and_zerobooks_plug_flags(self):
        # the observed ad page, nearly verbatim
        r = self._book_with_ad(
            "Stay up to date On Light Novels by Downloading our mobile App "
            "Zerobooks Universal Download all your Favorite Light Novels "
            "Jnovels.com Join our Discord and meet Thousands of LN readers"
        )
        sigs = {a["sig"] for a in r["ads"]}
        self.assertIn("jnovels", sigs)
        self.assertIn("zerobooks", sigs)
        self.assertIn("light-novel plug", sigs)
        self.assertEqual(audit._contamination_dir(r)[1], "ADVISORY")

    def test_kokoro_connect_in_body_flags(self):
        r = self._book_with_ad("Read more at Kokoro Connect fan wiki today")
        self.assertEqual(r["ads"][0]["sig"], "kokoro connect")

    def test_own_series_signature_is_exempt(self):
        # a legitimate Kokoro Connect volume names its own series in body
        # prose on every doc: a signature matching the book's own metadata
        # is skipped outright
        files = {
            f"s{i}.xhtml": _doc("Kokoro Connect Volume 4", body=PROSE)
            for i in range(1, 4)
        }
        opf = _opf(
            [(f"s{i}", f"s{i}.xhtml") for i in range(1, 4)],
            "Kokoro Connect Vol. 4: Michi Random",
            creator="Suganuma Yuya",
        )
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, files))
        r = analyze_contamination(book)
        self.assertEqual(r["ads"], [])

    def test_clean_book_has_no_ads(self):
        files = {"s1.xhtml": _doc("Chapter One", body=PROSE)}
        opf = _opf([("s1", "s1.xhtml")], "A Real Book")
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, files))
        r = analyze_contamination(book)
        self.assertEqual(r["ads"], [])
        self.assertEqual(audit._contamination_dir(r)[1], "OK")


class ReviewPasteTests(unittest.TestCase):
    """Sweep (c): review-paste front matter in the first few spine docs."""

    REVIEW_BODY = (
        "<p>Imaginative, but not engaging. , July 16, 2000 Reviewer: A customer "
        "This story of far future Earth is full of very imaginative landscapes. "
        "Was this review helpful to you?</p>"
        "<p>3 of 4 people found the following review helpful: Deep and "
        "challenging , January 26, 1999 Reviewer: Another customer</p>"
    )

    def _book(self, review_body, review_doc_index=1, ndocs=4):
        files = {}
        items = []
        for i in range(1, ndocs + 1):
            name = f"s{i}.xhtml"
            items.append((f"s{i}", name))
            if i == review_doc_index:
                files[name] = _doc("Reviews", body=review_body)
            else:
                files[name] = _doc(f"Front {i}" if i == 1 else f"Chapter {i}")
        opf = _opf(items, "Hothouse", creator="Brian W. Aldiss", publisher="Baen")
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, files))
        return analyze_contamination(book)

    def test_amazon_review_paste_flags(self):
        r = self._book(self.REVIEW_BODY)
        sigs = {rv["sig"] for rv in r["reviews"]}
        self.assertIn("helpful count", sigs)
        self.assertIn("helpful prompt", sigs)
        self.assertEqual(audit._contamination_dir(r)[1], "ADVISORY")

    def test_lone_weak_fingerprint_stays_silent(self):
        # a lone "Reviewer:" can be legitimate attribution
        r = self._book("<p>Reviewer: The Times Literary Supplement</p>")
        self.assertEqual(r["reviews"], [])
        self.assertEqual(audit._contamination_dir(r)[1], "OK")

    def test_review_text_beyond_the_front_docs_stays_silent(self):
        r = self._book(self.REVIEW_BODY, review_doc_index=6, ndocs=6)
        self.assertEqual(r["reviews"], [])

    def test_two_weak_fingerprints_corroborate(self):
        r = self._book("<p>Top reviews</p><p>Reviewer: someone</p>", review_doc_index=2)
        self.assertTrue(r["reviews"])

    def test_review_paste_off_a_broken_spine_flags(self):
        # the Hothouse shape: the manifest lists only the title page, so the
        # content docs resolve to a spine of one and the pasted reviews sit
        # entirely off the spine; the archive-wide corpus still sees them
        opf = _opf(
            [("tp", "titlepage.xhtml")], "Hothouse", creator="Brian W. Aldiss"
        ).replace(
            '<spine><itemref idref="tp"/></spine>',
            '<spine><itemref idref="tp"/>'
            '<itemref idref="ghost1"/><itemref idref="ghost2"/></spine>',
        )
        files = {
            "titlepage.xhtml": _doc("Hothouse"),
            "review.xhtml": _doc("Reviews", body=self.REVIEW_BODY),
            "s1.xhtml": _doc("Chapter One"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, files))
            self.assertEqual(len(book.spine), 1)
            self.assertIn("review.xhtml", book.extra_docs)
            r = analyze_contamination(book)
        sigs = {rv["sig"] for rv in r["reviews"]}
        self.assertIn("helpful count", sigs)

    def test_extra_corpus_retention_is_capped(self):
        opf = _opf([("tp", "titlepage.xhtml")], "Hothouse", creator="Brian W. Aldiss")
        files = {
            "titlepage.xhtml": _doc("Hothouse"),
            "review.xhtml": _doc("Reviews", body=self.REVIEW_BODY),
        }
        with tempfile.TemporaryDirectory() as tmp:
            p = _epub(tmp, opf, files)
            real = audit._MAX_EXTRA_HTML_BYTES
            audit._MAX_EXTRA_HTML_BYTES = 10
            try:
                book = load_book(p)
            finally:
                audit._MAX_EXTRA_HTML_BYTES = real
        self.assertEqual(book.extra_docs, {})


class ContractTests(unittest.TestCase):
    """The advisory contract and the shared dispatch wiring."""

    def test_verdict_never_reports_problem(self):
        opf = _opf([("s1", "s1.xhtml")], "A Real Book")
        files = {
            "s1.xhtml": _doc(
                "Full Metal Panic! Volume 5",
                body="<p>3 of 4 people found the following review helpful "
                "Jnovels.com was this review helpful</p>",
            )
        }
        with tempfile.TemporaryDirectory() as tmp:
            book = load_book(_epub(tmp, opf, files))
        problem, status, lines = audit._contamination_dir(analyze_contamination(book))
        self.assertFalse(problem)
        self.assertEqual(status, "ADVISORY")
        self.assertTrue(lines)

    def test_dispatch_covers_every_analyzer(self):
        # the pin: a key added to ALL without a wired analyzer is a loud
        # KeyError in directory/single mode, never a silent ocr misroute
        # (the v0.44.0 cover/tocdrift bug this map replaces)
        self.assertEqual(set(audit._dir_dispatch(1, 2, 3)), set(audit.ALL))

    def test_directory_mode_routes_cover_to_cover(self):
        # regression pin for the misroute: a book with no cover wiring must
        # answer ADVISORY under the cover key in directory mode (the old
        # chain ran the ocr analyzer there and answered OK)
        opf = _opf([("s1", "s1.xhtml")], "A Real Book")
        files = {"s1.xhtml": _doc("Chapter One")}
        with tempfile.TemporaryDirectory() as tmp:
            _epub(tmp, opf, files)
            buf = io.StringIO()
            with cl.redirect_stdout(buf):
                rc = audit.run_directory(pathlib.Path(tmp), ["cover"], 2000, 20000)
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("ADVISORY", out)

    def test_directory_mode_reports_contamination_without_failing(self):
        opf = _opf([("s1", "s1.xhtml")], "A Real Book")
        files = {
            "s1.xhtml": _doc(
                "Full Metal Panic! Volume 5",
                body="<p>Download all your Favorite Light Novels Jnovels.com</p>",
            )
        }
        with tempfile.TemporaryDirectory() as tmp:
            _epub(tmp, opf, files)
            buf = io.StringIO()
            with cl.redirect_stdout(buf):
                rc = audit.run_directory(
                    pathlib.Path(tmp), ["contamination"], 2000, 20000
                )
        out = buf.getvalue()
        self.assertEqual(rc, 0)  # advisory: the exit code never moves
        self.assertIn("ADVISORY", out)
        self.assertIn("Full Metal Panic! Volume 5", out)


if __name__ == "__main__":
    unittest.main()
