import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from bindery import audit

ocr = audit


class TestScriptOf(unittest.TestCase):
    def test_known_scripts(self):
        self.assertEqual(audit.script_of(0x0410), "Cyrillic")
        self.assertEqual(audit.script_of(0x4E2D), "CJK-Han")
        self.assertEqual(audit.script_of(0x0627), "Arabic")

    def test_latin_is_none(self):
        self.assertIsNone(audit.script_of(ord("a")))


class TestFindings(unittest.TestCase):
    def _result(self, **over):
        r = {
            "lang": "en",
            "scripts": {},
            "nonlatin": 0,
            "nonlatin_frac": 0.0,
            "ratios": {
                "en": 0.30,
                "pt": 0.05,
                "de": 0.04,
                "fr": 0.03,
                "es": 0.03,
                "it": 0.03,
                "nl": 0.02,
            },
            "best": "en",
            "nwords": 4000,
            "signature": False,
        }
        r.update(over)
        return r

    def test_clean_english(self):
        self.assertEqual(audit.findings(self._result()), [])

    def test_non_latin(self):
        cats = [
            c
            for c, _ in audit.findings(
                self._result(nonlatin=500, nonlatin_frac=0.5, scripts={"Cyrillic": 500})
            )
        ]
        self.assertIn("NON-LATIN SCRIPT", cats)

    def test_latin_foreign(self):
        cats = [
            c
            for c, _ in audit.findings(
                self._result(
                    best="pt",
                    ratios={
                        "pt": 0.30,
                        "en": 0.05,
                        "de": 0.02,
                        "fr": 0.02,
                        "es": 0.10,
                        "it": 0.08,
                        "nl": 0.02,
                    },
                )
            )
        ]
        self.assertIn("LATIN-SCRIPT FOREIGN", cats)

    def test_injection_signature(self):
        cats = [c for c, _ in audit.findings(self._result(signature=True))]
        self.assertIn("INJECTION SIGNATURE", cats)


class TestResolveLibraryRoot(unittest.TestCase):
    """audit_content finds the library next to the script or in the cwd."""

    @contextlib.contextmanager
    def _cwd(self, path):
        old = os.getcwd()
        os.chdir(path)
        try:
            yield
        finally:
            os.chdir(old)

    def test_cwd_with_db_resolves(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="cq_root_"))
        (tmp / "metadata.db").write_bytes(b"")
        with self._cwd(tmp):
            root = audit.resolve_library_root()
        self.assertIsNotNone(root)
        self.assertEqual(root.resolve(), tmp.resolve())

    def test_no_db_anywhere_is_none(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="cq_empty_"))
        with self._cwd(tmp):
            self.assertIsNone(audit.resolve_library_root())


class TestPageNumberValue(unittest.TestCase):
    def test_arabic_and_roman(self):
        self.assertEqual(audit.number_value("42"), 42)
        self.assertEqual(audit.number_value("xiv"), 14)
        self.assertEqual(audit.number_value("II"), 2)

    def test_rejects_non_numbers(self):
        self.assertIsNone(audit.number_value("Chapter"))
        self.assertIsNone(audit.number_value("12345"))  # >4 digits
        self.assertIsNone(audit.number_value("i"))  # lone roman i is too noisy
        self.assertIsNone(audit.number_value("42a"))

    def test_word_romans_are_not_numbers(self):
        # reported 2026-09-08: the character-set roman regex read ordinary words
        # (mid, dim, mix, lid, civil) as page numbers, inflating baked-hit counts
        for w in ("mid", "dim", "mix", "lid", "civil", "mild", "mil", "ill", "civ"):
            self.assertIsNone(audit.number_value(w), w)

    def test_strict_roman_grammar_still_parses_numerals(self):
        self.assertEqual(audit.number_value("xiv"), 14)
        self.assertEqual(audit.number_value("xciv"), 94)
        self.assertEqual(audit.number_value("xcix"), 99)
        self.assertIsNone(audit.number_value("mcmxcix"))  # over the page range


class TestIsDefective(unittest.TestCase):
    def _r(self, **over):
        r = {"n_hits": 10, "span": 0.9, "run": 5, "watermark": False, "examples": []}
        r.update(over)
        return r

    def test_clear_defect(self):
        self.assertTrue(audit.is_defective(self._r()))

    def test_too_few_hits(self):
        self.assertFalse(audit.is_defective(self._r(n_hits=4)))

    def test_localized_cluster_dropped_by_span(self):
        # a footnote-poem / scraped-comment cluster: many hits, tiny span
        self.assertFalse(audit.is_defective(self._r(n_hits=20, span=0.02)))


class TestPageNumberScan(unittest.TestCase):
    """End-to-end scan() over synthetic EPUBs: a baked-in conversion flags, a
    clean chapter-numbered book does not."""

    CONTAINER = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    OPF = (
        '<package xmlns="http://www.idpf.org/2007/opf">'
        "<manifest>"
        '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
        "</manifest>"
        '<spine><itemref idref="c1"/></spine></package>'
    )

    def _epub(self, tmp, body):
        import zipfile as zf

        p = pathlib.Path(tmp) / "t.epub"
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", self.OPF)
            z.writestr("text.xhtml", f"<html><body>{body}</body></html>")
        return p

    def test_baked_page_numbers_flag(self):
        # a page number wedged between an unfinished paragraph and its lowercase
        # continuation, repeated through the book: the real defect.
        para_a = "<p>" + ("the quick brown fox jumped over " * 6) + "and then</p>"
        para_b = "<p>" + ("continued in lowercase as the sentence ran on " * 6) + "</p>"
        body = "".join(f"{para_a}<p>{n}</p>{para_b}" for n in range(1, 8))
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_pagenumbers(self._epub(tmp, body))
        self.assertGreaterEqual(r["n_hits"], 5)
        self.assertTrue(audit.is_defective(r))

    def test_clean_chapter_numbers_do_not_flag(self):
        # a number that opens a chapter (next text is a fresh capitalized
        # sentence) is legitimate, not baked.
        chapter = "<p>" + ("A clean chapter of ordinary prose ends here. " * 6) + "</p>"
        body = "".join(f"<p>{n}</p>{chapter}" for n in range(1, 12))
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_pagenumbers(self._epub(tmp, body))
        self.assertFalse(audit.is_defective(r))


class TestVisibleChars(unittest.TestCase):
    def test_strips_tags_scripts_and_styles(self):
        html = (
            "<style>p{color:red}</style><p>Hello <b>world</b></p>"
            "<script>var x = 1</script>"
        )
        self.assertEqual(audit._visible_chars(html), len("Hello world"))

    def test_decodes_entities(self):
        self.assertEqual(audit._visible_chars("<p>a &amp; b</p>"), len("a & b"))


class TestEmptyTextScan(unittest.TestCase):
    """End-to-end scan() over synthetic EPUBs: a content-less stub is EMPTY,
    a real-text book is OK, a short work is THIN (advisory)."""

    CONTAINER = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    OPF = (
        '<package xmlns="http://www.idpf.org/2007/opf">'
        "<manifest>"
        '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
        "</manifest>"
        '<spine><itemref idref="c1"/></spine></package>'
    )

    def _epub(self, tmp, body):
        import zipfile as zf

        p = pathlib.Path(tmp) / "t.epub"
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", self.OPF)
            z.writestr("text.xhtml", f"<html><body>{body}</body></html>")
        return p

    def test_empty_stub_flagged(self):
        # a Bookmate-style stub: a single cover image, no body text
        body = '<p><img src="cover.png"/></p>'
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_emptytext(self._epub(tmp, body))
        self.assertEqual(r["chars"], 0)
        self.assertEqual(audit.classify(r, 2000, 20000), "EMPTY")

    def test_full_text_ok(self):
        body = "<p>" + ("Real prose that fills the book. " * 1000) + "</p>"
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_emptytext(self._epub(tmp, body))
        self.assertEqual(audit.classify(r, 2000, 20000), "OK")

    def test_thin_is_advisory(self):
        body = "<p>" + ("short story prose. " * 300) + "</p>"  # ~5700 chars
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_emptytext(self._epub(tmp, body))
        self.assertEqual(audit.classify(r, 2000, 20000), "THIN")


class TestPctDecode(unittest.TestCase):
    def test_reserved_char(self):
        self.assertEqual(
            audit._pct_decode("Text/CR%21X_split.html"), "Text/CR!X_split.html"
        )

    def test_multibyte_utf8(self):
        # 'ö' is the two-byte run %C3%B6, which must decode together, not per-byte
        self.assertEqual(audit._pct_decode("a%C3%B6b"), "aöb")

    def test_invalid_escape_left_literal(self):
        self.assertEqual(audit._pct_decode("50%-off"), "50%-off")


class TestPercentEncodedSpine(unittest.TestCase):
    """Regression: a spine doc whose archive name has a reserved char is
    referenced percent-encoded in the OPF (Sigil/calibre emit '%21' for '!').
    The href must be decoded before matching the namelist, or a text-full book
    resolves to nothing and reads as EMPTY (the Serpent Sea false positive)."""

    CONTAINER = TestEmptyTextScan.CONTAINER
    OPF = (
        '<package xmlns="http://www.idpf.org/2007/opf">'
        "<manifest>"
        '<item id="c1" href="Text/CR%21RT_split_001.html" media-type="application/xhtml+xml"/>'
        "</manifest>"
        '<spine><itemref idref="c1"/></spine></package>'
    )

    def _epub(self, tmp):
        import zipfile as zf

        p = pathlib.Path(tmp) / "t.epub"
        body = "<p>" + ("Real prose that fills the book. " * 1000) + "</p>"
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", self.OPF)
            z.writestr("Text/CR!RT_split_001.html", f"<html><body>{body}</body></html>")
        return p

    def test_encoded_href_resolves_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_emptytext(self._epub(tmp))
        self.assertGreater(r["chars"], 20000)
        self.assertEqual(audit.classify(r, 2000, 20000), "OK")


class TestPlaceholderExport(unittest.TestCase):
    """Partial / placeholder exports: most chapters are an identical stub (a DRM
    'content unavailable' notice) while one or two real chapters push the
    whole-book char count over the THIN floor, so the total-char check is fooled.
    classify() must call these PARTIAL; a full book with many DISTINCT small
    section dividers must stay OK (the false-positive guard)."""

    CONTAINER = TestEmptyTextScan.CONTAINER

    def _epub(self, tmp, docs):
        # docs: list of (name, body_html); spine follows the list order
        import zipfile as zf

        manifest = "".join(
            f'<item id="d{i}" href="{n}" media-type="application/xhtml+xml"/>'
            for i, (n, _) in enumerate(docs)
        )
        spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(docs)))
        opf = (
            '<package xmlns="http://www.idpf.org/2007/opf">'
            f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>"
        )
        p = pathlib.Path(tmp) / "t.epub"
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", opf)
            for n, body in docs:
                z.writestr(n, f"<html><body>{body}</body></html>")
        return p

    def test_drm_signature_is_partial(self):
        real = "<p>" + ("Real prose. " * 3000) + "</p>"
        stub = "<p>sorry something went wrong loading your content. Contact support@bookshout.com</p>"
        docs = [("c0.xhtml", real)] + [(f"c{i}.xhtml", stub) for i in range(1, 12)]
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_emptytext(self._epub(tmp, docs))
        self.assertGreater(r["chars"], 20000)  # would otherwise clear the THIN floor
        self.assertTrue(r["placeholder_sig"])
        self.assertEqual(audit.classify(r, 2000, 20000), "PARTIAL")

    def test_repeated_stub_without_signature_is_partial(self):
        real = "<p>" + ("Real prose. " * 3000) + "</p>"
        stub = "<p>This chapter is not included in this edition preview.</p>"
        docs = [("c0.xhtml", real)] + [(f"c{i}.xhtml", stub) for i in range(1, 12)]
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_emptytext(self._epub(tmp, docs))
        self.assertFalse(r["placeholder_sig"])
        self.assertGreaterEqual(r["stub_docs"], 3)
        self.assertEqual(audit.classify(r, 2000, 20000), "PARTIAL")

    def test_distinct_small_dividers_stay_ok(self):
        real = "<p>" + ("Real prose. " * 3000) + "</p>"
        docs = [("c0.xhtml", real)] + [
            (f"d{i}.xhtml", f"<p>Part {i}: a distinct section divider heading.</p>")
            for i in range(12)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_emptytext(self._epub(tmp, docs))
        self.assertLess(r["stub_docs"], 3)  # distinct text, so no repeated stub
        self.assertEqual(audit.classify(r, 2000, 20000), "OK")


class TestOcrSplitDetection(unittest.TestCase):
    """End-to-end scan_ocr() over synthetic EPUBs: a mid-sentence paragraph
    split counts; dialogue fragments and scene breaks do not."""

    CONTAINER = TestEmptyTextScan.CONTAINER
    OPF = TestEmptyTextScan.OPF

    def _epub(self, tmp, body):
        import zipfile as zf

        p = pathlib.Path(tmp) / "t.epub"
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", self.OPF)
            z.writestr("text.xhtml", f"<html><body>{body}</body></html>")
        return p

    def test_mid_sentence_split_counts(self):
        # the Jingo defect: a paragraph ends without terminal punctuation
        # (here on a function word, the line-wrap signature) and the next
        # starts lowercase, mid-sentence
        a = (
            "<p>"
            + ("He stared across the harbour and " * 5)
            + "could see the shape of</p>"
        )
        b = "<p>another boat, " + ("moving through the fog " * 5) + "slowly.</p>"
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_ocr(self._epub(tmp, (a + b) * 40))
        self.assertEqual(r["splits"], 40)
        self.assertGreaterEqual(r["split_rate"], audit.OCR_FLAG_RATE)
        self.assertEqual(r["func_frac"], 1.0)
        self.assertTrue(audit.is_ocr_damaged(r))

    def test_image_interrupted_pair_is_cleared(self):
        # a formula/figure between the fragments renders fine; not a split
        a = "<p>" + ("The channel capacity is given by the value " * 4) + "shown by</p>"
        img = '<div><img src="eq1.png"/></div>'
        b = "<p>where the terms " + ("are defined in the usual way " * 4) + "here.</p>"
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_ocr(self._epub(tmp, (a + img + b) * 40))
        self.assertEqual(r["splits"], 0)

    def test_clause_boundary_style_measures_low_func_frac(self):
        # deliberately unpunctuated literary prose (Fosse / Evaristo shape):
        # splits abound but end at clause boundaries, so func_frac stays low
        # and the book is not called damaged
        a = "<p>" + ("she thinks back to when she started " * 5) + "out in theatre</p>"
        b = (
            "<p>when she and her running mate "
            + ("developed a reputation " * 5)
            + "</p>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_ocr(self._epub(tmp, (a + b) * 40))
        self.assertGreater(r["splits"], 0)
        self.assertLess(r["func_frac"], audit.OCR_FUNC_MIN)
        self.assertFalse(audit.is_ocr_damaged(r))

    def test_dialogue_fragment_is_not_a_split(self):
        # "'Course not." starts with a quote, not a lowercase letter
        prose = "<p>" + ("Ordinary narrative prose carries on here. " * 5) + "</p>"
        body = (prose + "<p>'Course not,' said Nobby.</p>") * 20
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_ocr(self._epub(tmp, body))
        self.assertEqual(r["splits"], 0)

    def test_scene_break_is_not_a_split(self):
        # an unpunctuated paragraph end followed by a scene-break marker and a
        # fresh capitalized paragraph: a boundary, not a split
        a = "<p>" + ("The chapter wound down as the light faded " * 5) + "and so on</p>"
        marker = "<p>* * *</p>"
        b = "<p>Morning came bright and early. " + ("The day began anew. " * 5) + "</p>"
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_ocr(self._epub(tmp, (a + marker + b) * 20))
        self.assertEqual(r["splits"], 0)

    def test_clean_prose_measures_zero(self):
        body = "<p>" + ("A clean paragraph ends with a period. " * 5) + "</p>"
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_ocr(self._epub(tmp, body * 40))
        self.assertEqual(r["splits"], 0)
        self.assertFalse(audit.is_ocr_damaged(r))

    def test_side_signals(self):
        prose = "<p>" + ("Filler prose to give the book body text. " * 5) + "</p>"
        damaged = (
            "<p>That bottom–feedin' scum said ' 'Course, guv.' "
            "They walked through AnkhMorpork, the city of Ankh-Morpork.</p>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.scan_ocr(self._epub(tmp, prose * 10 + damaged))
        self.assertEqual(r["en_dash_words"], 1)
        self.assertEqual(r["doubled_quotes"], 1)
        self.assertEqual(r["glued"], ["AnkhMorpork~Ankh-Morpork"])


class TestIsOcrDamaged(unittest.TestCase):
    """Threshold boundaries on the FLAG gate."""

    def _r(self, **over):
        r = {
            "paras": 1000,
            "splits": audit.OCR_MIN_SPLITS,
            "split_rate": audit.OCR_FLAG_RATE,
            "func_frac": audit.OCR_FUNC_MIN,
        }
        r.update(over)
        return r

    def test_at_threshold_flags(self):
        self.assertTrue(audit.is_ocr_damaged(self._r()))

    def test_rate_just_below_threshold_passes(self):
        self.assertFalse(
            audit.is_ocr_damaged(self._r(split_rate=audit.OCR_FLAG_RATE * 0.99))
        )

    def test_too_few_splits_passes(self):
        self.assertFalse(
            audit.is_ocr_damaged(
                self._r(splits=audit.OCR_MIN_SPLITS - 1, split_rate=0.5)
            )
        )

    def test_too_few_paragraphs_passes(self):
        # fragmentary short works never have enough paragraphs for the rate
        # to mean anything
        self.assertFalse(
            audit.is_ocr_damaged(
                self._r(paras=audit.OCR_MIN_PARAS - 1, splits=40, split_rate=0.9)
            )
        )

    def test_clause_boundary_splits_pass(self):
        # a high split rate with a low function-word fraction is deliberate
        # style, not damage
        self.assertFalse(
            audit.is_ocr_damaged(
                self._r(splits=200, split_rate=0.4, func_frac=audit.OCR_FUNC_MIN * 0.5)
            )
        )


class TestAllIncludesOcr(unittest.TestCase):
    """`all` runs the ocr analyzer inside the same single decompression pass."""

    def test_directory_all_run_reports_ocr(self):
        import contextlib as cl
        import io

        a = (
            "<p>"
            + ("He stared across the harbour and " * 8)
            + "could see the shape of</p>"
        )
        b = "<p>another boat, " + ("moving through the fog " * 8) + "slowly.</p>"
        body = (a + b) * 40  # long enough that emptytext stays OK
        with tempfile.TemporaryDirectory() as tmp:
            import zipfile as zf

            p = pathlib.Path(tmp) / "t.epub"
            with zf.ZipFile(p, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", TestEmptyTextScan.CONTAINER)
                z.writestr("content.opf", TestEmptyTextScan.OPF)
                z.writestr("text.xhtml", f"<html><body>{body}</body></html>")
            buf = io.StringIO()
            with cl.redirect_stdout(buf):
                rc = audit.run_directory(
                    pathlib.Path(tmp), list(audit.ALL), 2000, 20000
                )
        out = buf.getvalue()
        self.assertEqual(rc, 1)
        self.assertIn("ocr", out)
        self.assertIn("REVIEW", out)


class TestVisibleTextCache(unittest.TestCase):
    """emptytext and ocr both need the book's rendered text. Under `all` they
    each used to strip tags over the whole book independently, which is the
    expensive half of a pass whose whole point is touching each EPUB once."""

    def _book(self, tmp):
        import zipfile

        p = pathlib.Path(tmp) / "t.epub"
        body = "<p>" + ("Some ordinary prose here. " * 40) + "</p>"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", TestEmptyTextScan.CONTAINER)
            z.writestr("content.opf", TestEmptyTextScan.OPF)
            z.writestr("text.xhtml", f"<html><body>{body}</body></html>")
        return audit.load_book(p)

    def test_text_is_extracted_once_and_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = self._book(tmp)
            calls = []
            real = audit._visible_text
            audit._visible_text = lambda html: (calls.append(1), real(html))[1]
            try:
                audit.analyze_emptytext(book)
                after_first = len(calls)
                audit.analyze_ocr(book)
                self.assertEqual(len(calls), after_first)  # ocr reused the cache
            finally:
                audit._visible_text = real

    def test_cached_text_matches_a_direct_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = self._book(tmp)
            direct = [audit._visible_text(book.docs.get(d, "")) for d in book.spine]
            self.assertEqual(book.visible_texts(), direct)


class TestContentSections(unittest.TestCase):
    """An injection signature is a defect regardless of the expected-foreign
    flag (regression: a signature hit on a declared-foreign book printed
    "(expected-foreign)" and "0 file(s) need review" while still failing the
    run with exit code 1)."""

    def test_signature_on_expected_foreign_book_counts(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = audit._content_sections(
                [],
                [],
                [(101, "Some Foreign Book", "fiction", True, "importknig signature")],
            )
        out = buf.getvalue()
        self.assertEqual(rc, 1)
        self.assertNotIn("expected-foreign", out)
        self.assertIn("1 file(s) need review", out)


class TestLoadBookCorruptEntry(unittest.TestCase):
    """A spine document with a corrupted archive entry (bad CRC) reads as
    empty text instead of crashing the whole book."""

    CONTAINER = TestEmptyTextScan.CONTAINER
    OPF = TestEmptyTextScan.OPF

    def test_bad_crc_entry_reads_empty(self):
        import struct
        import zipfile as zf

        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "t.epub"
            with zf.ZipFile(p, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", self.CONTAINER)
                z.writestr("content.opf", self.OPF)
                z.writestr("text.xhtml", "<html><body><p>real prose</p></body></html>")
            with zf.ZipFile(p) as z:
                offset = z.getinfo("text.xhtml").header_offset
            with open(p, "r+b") as f:
                f.seek(offset + 26)
                nlen, elen = struct.unpack("<HH", f.read(4))
                f.seek(offset + 30 + nlen + elen + 10)
                byte = f.read(1)
                f.seek(-1, os.SEEK_CUR)
                f.write(bytes([byte[0] ^ 0xFF]))
            book = audit.load_book(p)
        self.assertEqual(book.spine, ["text.xhtml"])
        self.assertEqual(book.docs["text.xhtml"], "")

    def test_bad_crc_is_reported_as_corrupt_not_empty(self):
        # Phase 9 flip: the old pin asserted only the empty-text reading. The
        # corruption is now named — its own verdict, never an EMPTY mislabel
        # that would send a damaged archive down the content-less-stub path.
        import struct
        import zipfile as zf

        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "t.epub"
            with zf.ZipFile(p, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", self.CONTAINER)
                z.writestr("content.opf", self.OPF)
                z.writestr("text.xhtml", "<html><body><p>real prose</p></body></html>")
            with zf.ZipFile(p) as z:
                offset = z.getinfo("text.xhtml").header_offset
            with open(p, "r+b") as f:
                f.seek(offset + 26)
                nlen, elen = struct.unpack("<HH", f.read(4))
                f.seek(offset + 30 + nlen + elen + 10)
                byte = f.read(1)
                f.seek(-1, os.SEEK_CUR)
                f.write(bytes([byte[0] ^ 0xFF]))
            book = audit.load_book(p)
            r = audit.analyze_corrupt(book)
        self.assertEqual(r["n"], 1)
        self.assertEqual(r["first"], "text.xhtml")
        problem, status, lines = audit._corrupt_verdict(r)
        self.assertTrue(problem)
        self.assertEqual(status, "CORRUPT")
        self.assertIn("corrupt:1", lines[0])

    def test_corrupt_book_skips_the_empty_verdict(self):
        # The CRC break lands in the ONLY spine doc: the book measures zero
        # chars, but emptytext must not call it EMPTY — the archive verdict
        # already told the truth.
        import struct
        import zipfile as zf

        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "t.epub"
            with zf.ZipFile(p, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", self.CONTAINER)
                z.writestr("content.opf", self.OPF)
                z.writestr("text.xhtml", "<html><body><p>real prose</p></body></html>")
            with zf.ZipFile(p) as z:
                offset = z.getinfo("text.xhtml").header_offset
            with open(p, "r+b") as f:
                f.seek(offset + 26)
                nlen, elen = struct.unpack("<HH", f.read(4))
                f.seek(offset + 30 + nlen + elen + 10)
                byte = f.read(1)
                f.seek(-1, os.SEEK_CUR)
                f.write(bytes([byte[0] ^ 0xFF]))
            book = audit.load_book(p)
            r = audit.analyze_emptytext(book)
            verdict = audit.classify(r, 2000, 20000)
        self.assertEqual(verdict, "CORRUPT")


class TestRunSingle(unittest.TestCase):
    """audit --id: one book, fetched via cquarry's single-entity get_book —
    no library-wide scan, and the EPUB resolved through get_format_path."""

    CONTAINER = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    OPF = (
        '<package xmlns="http://www.idpf.org/2007/opf">'
        "<manifest>"
        '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
        "</manifest>"
        '<spine><itemref idref="c1"/></spine></package>'
    )

    def _library(self, root, epub_formats=("EPUB",)):
        import sqlite3
        import zipfile

        root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(root / "metadata.db")
        conn.executescript(
            """
            CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT,
                author_sort TEXT, timestamp TEXT, pubdate TEXT, has_cover INT,
                last_modified TEXT, series_index REAL DEFAULT 1.0, path TEXT, uuid TEXT);
            CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
            CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INT, author INT);
            CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
            CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INT, series INT);
            CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
            CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INT, publisher INT);
            CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, link TEXT);
            CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INT, tag INT);
            CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT, link TEXT);
            CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code INT);
            CREATE TABLE ratings (id INTEGER PRIMARY KEY, rating INT, link TEXT DEFAULT '');
            CREATE TABLE books_ratings_link (id INTEGER PRIMARY KEY, book INT, rating INT);
            CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT,
                name TEXT, uncompressed_size INT);
            CREATE TABLE identifiers (book INT, type TEXT, val TEXT);
            """
        )
        conn.execute(
            "INSERT INTO books (id,title,sort,path) VALUES (1,'T','T','A/T (1)')"
        )
        conn.execute("INSERT INTO authors (id,name) VALUES (1,'Author')")
        conn.execute("INSERT INTO books_authors_link (book,author) VALUES (1,1)")
        for fmt in epub_formats:
            conn.execute(
                "INSERT INTO data (book,format,name) VALUES (1,?,'T - Author')", (fmt,)
            )
        conn.commit()
        conn.close()
        book_dir = root / "A" / "T (1)"
        book_dir.mkdir(parents=True, exist_ok=True)
        if "EPUB" in epub_formats:
            with zipfile.ZipFile(book_dir / "T - Author.epub", "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", self.CONTAINER)
                z.writestr("content.opf", self.OPF)
                z.writestr(
                    "text.xhtml",
                    "<html><body><p>"
                    + ("the quick brown fox jumped over the lazy dog. " * 60)
                    + "</p></body></html>",
                )
        return root

    @contextlib.contextmanager
    def _cwd(self, path):
        old = os.getcwd()
        os.chdir(path)
        try:
            yield
        finally:
            os.chdir(old)

    def test_clean_book_passes(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as td:
            root = self._library(pathlib.Path(td))
            with self._cwd(root):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = audit.run_single(1, ["content"], 2000, 20000)
        self.assertEqual(rc, 0)
        self.assertIn("CLEAN", out.getvalue())

    def test_unknown_id_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._library(pathlib.Path(td))
            with self._cwd(root):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = audit.run_single(999, ["content"], 2000, 20000)
        self.assertEqual(rc, 2)
        self.assertIn("no book #999", out.getvalue())

    def test_book_without_epub_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._library(pathlib.Path(td), epub_formats=("PDF",))
            with self._cwd(root):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = audit.run_single(1, ["content"], 2000, 20000)
        self.assertEqual(rc, 2)


class TestMonolithic(unittest.TestCase):
    """The monolithic analyzer: per-doc character volume, flagged at or above
    --max-doc-chars. Motivating case: a ~30M-char dictionary EPUB that
    epubcheck and every other analyzer passed but that would not render past
    a point on real readers."""

    CONTAINER = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )

    def _epub(self, tmp, docs):
        import zipfile as zf

        p = pathlib.Path(tmp) / "t.epub"
        manifest = "".join(
            f'<item id="d{i}" href="d{i}.xhtml" media-type="application/xhtml+xml"/>'
            for i in range(len(docs))
        )
        spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(docs)))
        opf = (
            '<package xmlns="http://www.idpf.org/2007/opf">'
            f"<manifest>{manifest}</manifest>"
            f"<spine>{spine}</spine></package>"
        )
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", opf)
            for i, body in enumerate(docs):
                z.writestr(f"d{i}.xhtml", f"<html><body>{body}</body></html>")
        return p

    def test_single_oversized_doc_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            big = (
                "<p>"
                + ("the quick brown fox jumped over the lazy dog. " * 10_000)
                + "</p>"
            )
            r = audit.scan_monolithic(self._epub(tmp, [big]))
        self.assertGreaterEqual(r["max_doc_chars"], 400_000)
        self.assertTrue(audit.is_monolithic(r, audit.DEFAULT_MAX_DOC_CHARS))
        problem, status, lines = audit._monolithic_dir(r, audit.DEFAULT_MAX_DOC_CHARS)
        self.assertTrue(problem)
        self.assertEqual(status, "FLAG")
        self.assertIn("chars", lines[0])
        self.assertIn(r["worst_doc"], lines[0])

    def test_many_small_docs_clean(self):
        # 20 docs x 20k chars = 400k total, but no single doc over the floor.
        with tempfile.TemporaryDirectory() as tmp:
            docs = [
                "<p>" + ("steady ordinary prose in every chapter. " * 500) + "</p>"
            ] * 20
            r = audit.scan_monolithic(self._epub(tmp, docs))
        self.assertLess(r["max_doc_chars"], 30_000)
        self.assertFalse(audit.is_monolithic(r, audit.DEFAULT_MAX_DOC_CHARS))

    def test_threshold_override_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = [
                "<p>" + ("steady ordinary prose in every chapter. " * 500) + "</p>"
            ] * 20
            r = audit.scan_monolithic(self._epub(tmp, docs))
        self.assertTrue(audit.is_monolithic(r, 10_000))
        problem, status, _ = audit._monolithic_dir(r, 10_000)
        self.assertTrue(problem)
        self.assertEqual(status, "FLAG")

    def test_advisory_silence_under_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = [
                "<p>" + ("steady ordinary prose in every chapter. " * 500) + "</p>"
            ] * 20
            r = audit.scan_monolithic(self._epub(tmp, docs))
        problem, status, _ = audit._monolithic_dir(r, audit.DEFAULT_MAX_DOC_CHARS)
        self.assertFalse(problem)
        self.assertEqual(status, "OK")


class TestRunSingleMonolithicTag(unittest.TestCase):
    """--id mode reports the monolithic verdict the same way directory mode
    does, and --tag applies only to flagged books (phase 7 acceptance)."""

    CONTAINER = TestRunSingle.CONTAINER
    OPF = TestRunSingle.OPF

    @contextlib.contextmanager
    def _cwd(self, path):
        old = os.getcwd()
        os.chdir(path)
        try:
            yield
        finally:
            os.chdir(old)

    def _tags(self, root):
        import sqlite3

        conn = sqlite3.connect(root / "metadata.db")
        try:
            return {
                t
                for (t,) in conn.execute(
                    "SELECT t.name FROM books_tags_link bt JOIN tags t ON t.id = bt.tag"
                )
            }
        finally:
            conn.close()

    def test_flagged_book_is_tagged_under_override(self):
        with tempfile.TemporaryDirectory() as td:
            root = TestRunSingle._library(self, pathlib.Path(td))
            with self._cwd(root):
                rc = audit.run_single(
                    1, ["monolithic"], 2000, 20000, tag="Flagged", max_doc_chars=1000
                )
                tags = self._tags(root)
        self.assertEqual(rc, 1)
        self.assertIn("Flagged", tags)

    def test_unflagged_book_is_untagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = TestRunSingle._library(self, pathlib.Path(td))
            with self._cwd(root):
                rc = audit.run_single(1, ["monolithic"], 2000, 20000, tag="Flagged")
                tags = self._tags(root)
        self.assertEqual(rc, 0)
        self.assertNotIn("Flagged", tags)


class TestSpineIntegrity(unittest.TestCase):
    """The WI official-build pattern (phase 8): series-wide ToC manifests
    (~750 references vs 14-19 content docs) reported as CONVENTION, never
    flagged; a book missing part of its own span is a FRAGMENT."""

    CONTAINER = TestRunSingle.CONTAINER
    OPF = TestRunSingle.OPF

    def _epub_bytes(self, chapters, nav_hrefs, bodies=None) -> bytes:
        import io
        import zipfile as zf

        buf = io.BytesIO()
        manifest = "".join(
            f'<item id="c{i}" href="{h}" media-type="application/xhtml+xml"/>'
            for i, h in enumerate(chapters)
        )
        nav_item = (
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
            'properties="nav"/>'
        )
        spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
        opf = (
            '<package xmlns="http://www.idpf.org/2007/opf">'
            f"<manifest>{manifest}{nav_item}</manifest>"
            f"<spine>{spine}</spine></package>"
        )
        anchors = "".join(f'<li><a href="{h}">Chapter</a></li>' for h in nav_hrefs)
        nav_html = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><body><nav epub:type="toc">'
            f"<ol>{anchors}</ol></nav></body></html>"
        )
        with zf.ZipFile(buf, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr(
                "META-INF/container.xml",
                '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles><rootfile full-path="content.opf" '
                'media-type="application/oebps-package+xml"/></rootfiles></container>',
            )
            z.writestr("content.opf", opf)
            for h in chapters:
                body = (bodies or {}).get(
                    h, f"<html><body><p>chapter {h}</p></body></html>"
                )
                z.writestr(h, body)
            z.writestr("nav.xhtml", nav_html)
        return buf.getvalue()

    def _epub(self, tmp, chapters, nav_hrefs, bodies=None):
        p = pathlib.Path(tmp) / "t.epub"
        p.write_bytes(self._epub_bytes(chapters, nav_hrefs, bodies))
        return audit.load_book(p)

    def test_series_wide_manifest_is_convention_not_fragment(self):
        # 19 real chapters (consecutive span); the nav references 750 targets
        # of which 731 are other volumes' files. Reported, never flagged.
        chapters = [f"ch{n:03d}.xhtml" for n in range(1, 20)]
        nav = [f"ch{n:03d}.xhtml" for n in range(1, 20)] + [
            f"v08/ch{n:03d}.xhtml" for n in range(20, 752)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            book = self._epub(tmp, chapters, nav)
            r = audit.spine_integrity(book)
        self.assertEqual(r["class"], "convention")
        self.assertEqual(r["refs"], 751)
        self.assertEqual(r["absent"], 732)
        problem, status, lines = audit._spine_verdict(r)
        self.assertFalse(problem)
        self.assertEqual(status, "ADVISORY")

    def test_broken_span_is_a_fragment(self):
        # Ten chapters present with holes in the numbering, and nav targets
        # missing beyond them: the book is a fragment of itself.
        chapters = [f"ch{n:03d}.xhtml" for n in (1, 2, 3, 4, 5, 7, 9, 11, 13, 15)]
        nav = [f"ch{n:03d}.xhtml" for n in range(1, 31)]
        with tempfile.TemporaryDirectory() as tmp:
            book = self._epub(tmp, chapters, nav)
            r = audit.spine_integrity(book)
        self.assertEqual(r["class"], "fragment")
        problem, status, _ = audit._spine_verdict(r)
        self.assertTrue(problem)
        self.assertEqual(status, "FRAGMENT")

    def test_all_targets_present_is_silent(self):
        chapters = [f"ch{n:03d}.xhtml" for n in range(1, 6)]
        nav = [f"ch{n:03d}.xhtml" for n in range(1, 6)]
        with tempfile.TemporaryDirectory() as tmp:
            book = self._epub(tmp, chapters, nav)
            r = audit.spine_integrity(book)
        self.assertEqual(r["class"], "ok")

    def test_declared_absent_ncx_does_not_poison_corrupt(self):
        # reported 2026-09-08: a healthy book with a leftover declared-but-
        # absent toc.ncx manifest entry was branded CORRUPT "re-source"
        # because the ToC accounting read through a closed zip. A manifest
        # leftover is not a damaged archive.
        import io
        import zipfile as zf

        buf = io.BytesIO()
        with zf.ZipFile(buf, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", TestRunSingle.CONTAINER)
            z.writestr(
                "content.opf",
                '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
                '<item id="toc" href="toc.ncx" '
                'media-type="application/x-dtbncx+xml"/>'
                "</manifest>"
                '<spine><itemref idref="c1"/></spine></package>',
            )
            z.writestr("c1.xhtml", "<html><body><p>chapter</p></body></html>")
            # deliberately NO toc.ncx entry in the archive
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "t.epub"
            p.write_bytes(buf.getvalue())
            book = audit.load_book(p)
        self.assertEqual(book.corrupt, [])
        self.assertEqual(book.toc_refs, 0)

    def test_nested_ncx_targets_resolve_against_the_ncx_directory(self):
        # reported 2026-09-08: NCX content srcs were resolved against the
        # OPF's directory, so a spec-compliant nested NCX counted every
        # target absent
        import io
        import zipfile as zf

        buf = io.BytesIO()
        with zf.ZipFile(buf, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", TestRunSingle.CONTAINER)
            z.writestr(
                "content.opf",
                '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                '<item id="c1" href="text/c1.xhtml" '
                'media-type="application/xhtml+xml"/>'
                '<item id="toc" href="text/toc.ncx" '
                'media-type="application/x-dtbncx+xml"/>'
                "</manifest>"
                '<spine><itemref idref="c1"/></spine></package>',
            )
            z.writestr("text/c1.xhtml", "<html><body><p>chapter</p></body></html>")
            z.writestr(
                "text/toc.ncx",
                '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
                '<navPoint id="n1"><content src="c1.xhtml"/></navPoint>'
                "</navMap></ncx>",
            )
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "t.epub"
            p.write_bytes(buf.getvalue())
            book = audit.load_book(p)
        self.assertEqual((book.toc_refs, book.toc_absent), (1, 0))

    def test_entity_encoded_nav_hrefs_resolve(self):
        # reported 2026-09-08: nav hrefs were compared raw against archive
        # names, so an &amp;-encoded href never matched its own file
        import io
        import zipfile as zf

        buf = io.BytesIO()
        with zf.ZipFile(buf, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", TestRunSingle.CONTAINER)
            z.writestr(
                "content.opf",
                '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                '<item id="c1" href="ch&amp;1.xhtml" '
                'media-type="application/xhtml+xml"/>'
                '<item id="nav" href="nav.xhtml" '
                'media-type="application/xhtml+xml" properties="nav"/>'
                "</manifest>"
                '<spine><itemref idref="c1"/></spine></package>',
            )
            z.writestr("ch&1.xhtml", "<html><body><p>chapter</p></body></html>")
            z.writestr(
                "nav.xhtml",
                '<html><body><nav><ol><li><a href="ch&amp;1.xhtml">c</a></li>'
                "</ol></nav></body></html>",
            )
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "t.epub"
            p.write_bytes(buf.getvalue())
            book = audit.load_book(p)
        self.assertEqual(book.toc_refs, 1)
        self.assertEqual(book.toc_absent, 0)

    def test_drm_entries_get_their_own_verdict(self):
        # reported 2026-09-08: zip-encrypted entries read as CORRUPT
        # "re-source" instead of their own DRM status
        import io
        import zipfile as zf

        buf = io.BytesIO()
        with zf.ZipFile(buf, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", TestRunSingle.CONTAINER)
            z.writestr(
                "content.opf",
                '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
                "</manifest>"
                '<spine><itemref idref="c1"/></spine></package>',
            )
            z.writestr("c1.xhtml", "<html><body><p>chapter</p></body></html>")
            z.writestr(
                "META-INF/encryption.xml",
                '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                "<EncryptedData><CipherData>"
                '<CipherReference URI="c1.xhtml"/>'
                "</CipherData></EncryptedData></encryption>",
            )
        raw = bytearray(buf.getvalue())

        def _mark_encrypted(raw: bytearray, filename: bytes) -> None:
            # set the general-purpose encryption bit (0x1) on the entry in
            # both the local header and the central directory: zipfile raises
            # RuntimeError on read exactly as it does for real DRM
            i = raw.find(b"PK\x03\x04")
            while i != -1:
                name_len = int.from_bytes(raw[i + 26 : i + 28], "little")
                if bytes(raw[i + 30 : i + 30 + name_len]) == filename:
                    raw[i + 6] |= 0x1
                    break
                i = raw.find(b"PK\x03\x04", i + 4)
            j = raw.find(b"PK\x01\x02")
            while j != -1:
                name_len = int.from_bytes(raw[j + 28 : j + 30], "little")
                if bytes(raw[j + 46 : j + 46 + name_len]) == filename:
                    raw[j + 8] |= 0x1
                    break
                j = raw.find(b"PK\x01\x02", j + 4)

        _mark_encrypted(raw, b"c1.xhtml")
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "t.epub"
            p.write_bytes(bytes(raw))
            book = audit.load_book(p)
        self.assertEqual(book.corrupt, [])
        self.assertEqual(book.encrypted, ["c1.xhtml"])
        r = audit.analyze_corrupt(book)
        problem, status, _ = audit._corrupt_verdict(r)
        self.assertTrue(problem)
        self.assertEqual(status, "ENCRYPTED")

    def test_spine_miss_is_diagnosed_not_silent(self):
        # reported 2026-09-08: an itemref whose decoded href missed the
        # stored name silently dropped the doc and read as EMPTY with no
        # diagnostic; the miss is now counted into the emptytext record
        import io
        import zipfile as zf

        buf = io.BytesIO()
        with zf.ZipFile(buf, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", TestRunSingle.CONTAINER)
            z.writestr(
                "content.opf",
                '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                '<item id="c1" href="stored-name.xhtml" '
                'media-type="application/xhtml+xml"/>'
                "</manifest>"
                '<spine><itemref idref="c1"/></spine></package>',
            )
            z.writestr("different-name.xhtml", "<html><body><p>x</p></body></html>")
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "t.epub"
            p.write_bytes(buf.getvalue())
            book = audit.load_book(p)
        self.assertEqual(book.spine, [])
        self.assertEqual(book.spine_missing, 1)
        r = audit.analyze_emptytext(book)
        self.assertEqual(r["spine_missing"], 1)
        self.assertIn("unresolved", audit._empty_detail(r))

    def test_duplicate_trailing_numbers_are_not_a_fragment(self):
        # reported 2026-09-08: part1a/part1b/part2 all carry trailing numbers
        # 1,1,2 and the raw sequence read as "broken", flagging a healthy
        # book as a fragment of itself
        import io
        import zipfile as zf

        buf = io.BytesIO()
        with zf.ZipFile(buf, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", TestRunSingle.CONTAINER)
            chapters = ["part1a.xhtml", "part1b.xhtml", "part2.xhtml"]
            manifest = "".join(
                f'<item id="c{i}" href="{h}" media-type="application/xhtml+xml"/>'
                for i, h in enumerate(chapters)
            )
            spine = "".join(f'<itemref idref="c{i}"/>' for i in range(3))
            z.writestr(
                "content.opf",
                '<package xmlns="http://www.idpf.org/2007/opf">'
                f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>",
            )
            for h in chapters:
                z.writestr(h, "<html><body><p>chapter</p></body></html>")
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "t.epub"
            p.write_bytes(buf.getvalue())
            book = audit.load_book(p)
            r = audit.spine_integrity(book)
        self.assertEqual(r["class"], "ok")

    def test_fragment_and_monolithic_compose_in_one_report(self):
        # The verdict surfaces are independent: a library book can be a
        # fragment AND monolithic, and one report counts both.
        import contextlib
        import io

        big = (
            "<p>" + ("the quick brown fox jumped over the lazy dog. " * 10_000) + "</p>"
        )
        chapters = [f"ch{n:03d}.xhtml" for n in (1, 2, 3, 4, 5, 7, 9, 11, 13, 15)]
        nav = [f"ch{n:03d}.xhtml" for n in range(1, 31)]
        with tempfile.TemporaryDirectory() as td:
            root = TestRunSingle._library(self, pathlib.Path(td))
            epub = root / "A" / "T (1)" / "T - Author.epub"
            epub.write_bytes(
                self._epub_bytes(
                    chapters,
                    nav,
                    bodies={"ch003.xhtml": f"<html><body>{big}</body></html>"},
                )
            )
            old = os.getcwd()
            os.chdir(root)
            try:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = audit.run_single(1, list(audit.ALL), 2000, 20000)
            finally:
                os.chdir(old)
        self.assertEqual(rc, 1)
        import re

        text = re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue())
        # The single-book renderer prints verdict labels: FLAG for the
        # monolithic hit, FRAGMENT for the spine, both in one report.
        self.assertIn("FLAG   monolithic", text)
        self.assertIn("max doc", text)
        self.assertIn("FRAGMENT spine", text)


class TestAuditJson(unittest.TestCase):
    """audit --json: per-file analyzer verdicts in the library --json shape.
    The phase-1 EPUB slice reads this file, so every verdict the console
    renders must arrive in the payload: clean analyzers as OK, flagged ones
    as problem verdicts, scan errors as their own record shape."""

    CONTAINER = TestEmptyTextScan.CONTAINER
    OPF = TestEmptyTextScan.OPF

    def _epub(self, tmp, name, body):
        import zipfile as zf

        p = pathlib.Path(tmp) / name
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", self.OPF)
            z.writestr("text.xhtml", f"<html><body>{body}</body></html>")
        return p

    def test_directory_mode_clean_and_flagged(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp:
            self._epub(tmp, "good.epub", "<p>" + "Real prose here. " * 1500 + "</p>")
            self._epub(tmp, "stub.epub", "<p><img src='cover.png'/></p>")
            out = pathlib.Path(tmp) / "report.json"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = audit.run_directory(
                    pathlib.Path(tmp), ["emptytext"], 2000, 20000, json_path=out
                )
            data = json.loads(out.read_text())
        self.assertEqual(rc, 1)
        self.assertEqual(data["mode"], "audit")
        # the always-on archive/spine verdicts are part of the analyzer set
        # (reported 2026-09-08: the list used to omit them while every
        # record still carried their verdicts)
        self.assertEqual(data["analyzers"], ["archive", "emptytext", "spine"])
        self.assertEqual(data["summary"]["scanned"], 2)
        self.assertEqual(data["summary"]["problems"], 1)
        self.assertEqual(data["summary"]["errors"], 0)
        books = {pathlib.Path(b["path"]).name: b for b in data["books"]}
        good = books["good.epub"]
        stub = books["stub.epub"]
        # the clean book carries the OK verdict, so a consumer can tell
        # "scanned and clean" from "not selected"
        self.assertEqual(good["status"], "clean")
        self.assertEqual(good["verdicts"]["emptytext"]["status"], "OK")
        self.assertFalse(good["verdicts"]["emptytext"]["problem"])
        # the always-on archive/spine verdicts are silent-but-present
        self.assertEqual(good["verdicts"]["archive"]["status"], "OK")
        self.assertEqual(good["verdicts"]["spine"]["status"], "OK")
        self.assertEqual(stub["status"], "problem")
        self.assertEqual(stub["verdicts"]["emptytext"]["status"], "EMPTY")
        self.assertTrue(stub["verdicts"]["emptytext"]["problem"])
        self.assertTrue(stub["verdicts"]["emptytext"]["details"])

    def test_directory_mode_corrupt_owns_the_body_text_story(self):
        import contextlib
        import io
        import struct
        import zipfile as zf

        with tempfile.TemporaryDirectory() as tmp:
            p = self._epub(tmp, "t.epub", "<html><body><p>real prose</p></body></html>")
            with zf.ZipFile(p) as z:
                offset = z.getinfo("text.xhtml").header_offset
            with open(p, "r+b") as f:
                f.seek(offset + 26)
                nlen, elen = struct.unpack("<HH", f.read(4))
                f.seek(offset + 30 + nlen + elen + 10)
                byte = f.read(1)
                f.seek(-1, os.SEEK_CUR)
                f.write(bytes([byte[0] ^ 0xFF]))
            out = pathlib.Path(tmp) / "report.json"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = audit.run_directory(
                    pathlib.Path(tmp), ["emptytext"], 2000, 20000, json_path=out
                )
            data = json.loads(out.read_text())
        self.assertEqual(rc, 1)
        (book,) = data["books"]
        self.assertEqual(book["verdicts"]["archive"]["status"], "CORRUPT")
        self.assertTrue(book["verdicts"]["archive"]["problem"])
        # emptytext has no verdict when the archive owns the story
        self.assertNotIn("emptytext", book["verdicts"])

    def test_directory_mode_scan_error_is_its_own_record(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp:
            self._epub(tmp, "good.epub", "<p>prose</p>")
            (pathlib.Path(tmp) / "garbage.epub").write_bytes(b"not a zip")
            out = pathlib.Path(tmp) / "report.json"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = audit.run_directory(
                    pathlib.Path(tmp), ["emptytext"], 2000, 20000, json_path=out
                )
            data = json.loads(out.read_text())
        self.assertEqual(rc, 1)
        self.assertEqual(data["summary"]["errors"], 1)
        books = {b["status"]: b for b in data["books"]}
        err = books["error"]
        self.assertIn("error", err)
        self.assertEqual(err["verdicts"], {})

    def test_single_mode_writes_one_record(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as td:
            root = TestRunSingle._library(self, pathlib.Path(td))
            out = pathlib.Path(td) / "report.json"
            old = os.getcwd()
            os.chdir(root)
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = audit.run_single(1, ["content"], 2000, 20000, json_path=out)
            finally:
                os.chdir(old)
            data = json.loads(out.read_text())
        self.assertEqual(rc, 0)
        (book,) = data["books"]
        self.assertEqual(book["id"], 1)
        self.assertEqual(book["title"], "T")
        self.assertEqual(book["status"], "clean")
        self.assertEqual(book["verdicts"]["content"]["status"], "OK")

    def test_library_mode_writes_per_book_records(self):
        import contextlib
        import io
        import sqlite3
        import zipfile

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            root.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(root / "metadata.db")
            conn.executescript(
                """
                CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT,
                    author_sort TEXT, timestamp TEXT, pubdate TEXT, has_cover INT,
                    last_modified TEXT, series_index REAL DEFAULT 1.0, path TEXT, uuid TEXT);
                CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
                CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INT, author INT);
                CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, link TEXT);
                CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INT, tag INT);
                CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT, link TEXT);
                CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code INT);
                CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT,
                    name TEXT, uncompressed_size INT);
                """
            )
            conn.execute(
                "INSERT INTO books (id,title,sort,path) VALUES (1,'T','T','A/T (1)')"
            )
            conn.execute("INSERT INTO authors (id,name) VALUES (1,'Author')")
            conn.execute("INSERT INTO books_authors_link (book,author) VALUES (1,1)")
            conn.execute(
                "INSERT INTO data (book,format,name) VALUES (1,'EPUB','T - Author')"
            )
            conn.commit()
            conn.close()
            book_dir = root / "A" / "T (1)"
            book_dir.mkdir(parents=True)
            with zipfile.ZipFile(book_dir / "T - Author.epub", "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", self.CONTAINER)
                z.writestr("content.opf", self.OPF)
                z.writestr(
                    "text.xhtml",
                    "<html><body><p>"
                    + ("ordinary prose sentence here. " * 60)
                    + "</p></body></html>",
                )
            out = root / "report.json"
            old = os.getcwd()
            os.chdir(root)
            try:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = audit.run_library(["content"], 2000, 20000, json_path=out)
            finally:
                os.chdir(old)
            data = json.loads(out.read_text())
        self.assertEqual(rc, 0)
        self.assertEqual(data["summary"]["scanned"], 1)
        (book,) = data["books"]
        self.assertEqual(book["id"], 1)
        self.assertEqual(book["path"], str(book_dir / "T - Author.epub"))
        self.assertEqual(book["status"], "clean")
        self.assertEqual(book["verdicts"]["content"]["status"], "OK")
        self.assertEqual(book["verdicts"]["archive"]["status"], "OK")
        self.assertEqual(book["verdicts"]["spine"]["status"], "OK")

    def test_corrupt_book_fails_library_mode_like_directory_mode(self):
        # reported 2026-09-08: the library report loop iterated only the
        # content-analyzer tuple, so a CRC-corrupt book printed
        # "emptytext CLEAN" and exited 0 in library mode while directory mode
        # said CORRUPT and exited 1
        import contextlib
        import io
        import sqlite3
        import zipfile

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            root.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(root / "metadata.db")
            conn.executescript(
                """
                CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT,
                    author_sort TEXT, timestamp TEXT, pubdate TEXT, has_cover INT,
                    last_modified TEXT, series_index REAL DEFAULT 1.0, path TEXT, uuid TEXT);
                CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
                CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INT, author INT);
                CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, link TEXT);
                CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INT, tag INT);
                CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT, link TEXT);
                CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code INT);
                CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT,
                    name TEXT, uncompressed_size INT);
                """
            )
            conn.execute(
                "INSERT INTO books (id,title,sort,path) VALUES (1,'T','T','A/T (1)')"
            )
            conn.execute("INSERT INTO authors (id,name) VALUES (1,'Author')")
            conn.execute("INSERT INTO books_authors_link (book,author) VALUES (1,1)")
            conn.execute(
                "INSERT INTO data (book,format,name) VALUES (1,'EPUB','T - Author')"
            )
            conn.commit()
            conn.close()
            book_dir = root / "A" / "T (1)"
            book_dir.mkdir(parents=True)
            # store (not deflate) so a single flipped data byte is surgical
            # CRC damage: the archive still opens and scans, one entry fails
            # its CRC
            with zipfile.ZipFile(book_dir / "T - Author.epub", "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", self.CONTAINER)
                z.writestr("content.opf", self.OPF)
                info = zipfile.ZipInfo("text.xhtml")
                z.writestr(
                    info,
                    "<html><body><p>prose</p></body></html>",
                    compress_type=zipfile.ZIP_STORED,
                )
            raw = bytearray((book_dir / "T - Author.epub").read_bytes())
            marker = b"prose"
            i = raw.find(marker) + 1
            raw[i] ^= 0xFF
            (book_dir / "T - Author.epub").write_bytes(bytes(raw))
            old = os.getcwd()
            os.chdir(root)
            try:
                buf, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                    rc = audit.run_library(["emptytext"], 2000, 20000)
            finally:
                os.chdir(old)
        self.assertEqual(rc, 1)
        self.assertIn("CORRUPT", buf.getvalue())


class TestNavPropertyAndDuplicates(unittest.TestCase):
    """Papercuts from the 2026-09-08 sweep: nav selection matched the token
    'nav' by substring, and duplicate zip entries resolved last-wins with no
    note anywhere."""

    def _book(self, body_extra: str):
        import io
        import zipfile as zf

        buf = io.BytesIO()
        with zf.ZipFile(buf, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", TestRunSingle.CONTAINER)
            z.writestr(
                "content.opf",
                '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
                '<item id="weird" href="other.xhtml" '
                'media-type="application/xhtml+xml" properties="data-nav"/>'
                "</manifest>"
                '<spine><itemref idref="c1"/></spine></package>',
            )
            z.writestr("c1.xhtml", "<html><body><p>chapter</p></body></html>")
            z.writestr("other.xhtml", "<html><body><p>other</p></body></html>")
            if body_extra:
                z.writestr("c1.xhtml", body_extra)
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "t.epub"
            p.write_bytes(buf.getvalue())
            return audit.load_book(p)

    def test_data_nav_property_is_not_the_nav(self):
        # 'nav' in 'data-nav' selected the wrong item by substring; only a
        # whole 'nav' token declares the navigation document
        book = self._book("")
        self.assertIsNone(book.nav)

    def test_duplicate_entries_are_counted(self):
        import io
        import zipfile as zf

        buf = io.BytesIO()
        with zf.ZipFile(buf, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", TestRunSingle.CONTAINER)
            z.writestr(
                "content.opf",
                '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
                "</manifest>"
                '<spine><itemref idref="c1"/></spine></package>',
            )
            z.writestr("c1.xhtml", "<html><body><p>first</p></body></html>")
            z.writestr("c1.xhtml", "<html><body><p>second</p></body></html>")
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "t.epub"
            p.write_bytes(buf.getvalue())
            book = audit.load_book(p)
        self.assertEqual(book.dup_entries, 1)
        r = audit.analyze_corrupt(book)
        self.assertEqual(r["dup_entries"], 1)


class TestAuditThresholdValidation(unittest.TestCase):
    def test_min_chars_above_thin_chars_is_refused(self):
        # reported 2026-09-08: the EMPTY threshold above the THIN threshold
        # silently shadowed every THIN advisory
        import contextlib
        import io

        from bindery.cli import build_parser, run_audit_cmd

        args = build_parser().parse_args(
            ["audit", "emptytext", "--min-chars", "30000", "--thin-chars", "20000"]
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = run_audit_cmd(args)
        self.assertEqual(rc, 2)
        self.assertIn("must not exceed", err.getvalue())


class TestLibraryTagEndToEnd(unittest.TestCase):
    """library-mode --tag end to end: an EMPTY book gets tagged through
    cquarry's write path, against a real (temporary) metadata.db."""

    def test_flagged_book_is_tagged(self):
        import contextlib
        import io
        import sqlite3
        import zipfile

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            root.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(root / "metadata.db")
            conn.executescript(
                """
                CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT,
                    author_sort TEXT, timestamp TEXT, pubdate TEXT, has_cover INT,
                    last_modified TEXT, series_index REAL DEFAULT 1.0, path TEXT, uuid TEXT);
                CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
                CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INT, author INT);
                CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, link TEXT);
                CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INT, tag INT);
                CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT, link TEXT);
                CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code INT);
                CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT,
                    name TEXT, uncompressed_size INT);
                CREATE TABLE identifiers (book INT, type TEXT, val TEXT);
                """
            )
            conn.execute(
                "INSERT INTO books (id,title,sort,path) VALUES (1,'Empty','Empty','A/E (1)')"
            )
            conn.execute("INSERT INTO authors (id,name) VALUES (1,'Author')")
            conn.execute("INSERT INTO books_authors_link (book,author) VALUES (1,1)")
            conn.execute(
                "INSERT INTO data (book,format,name) VALUES (1,'EPUB','E - Author')"
            )
            conn.commit()
            conn.close()
            book_dir = root / "A" / "E (1)"
            book_dir.mkdir(parents=True)
            with zipfile.ZipFile(book_dir / "E - Author.epub", "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", TestRunSingle.CONTAINER)
                z.writestr("content.opf", TestRunSingle.OPF)
                z.writestr("text.xhtml", "<html><body></body></html>")
            old = os.getcwd()
            os.chdir(root)
            try:
                buf, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                    rc = audit.run_library(["emptytext"], 2000, 20000, tag="Flagged")
                conn = sqlite3.connect(root / "metadata.db")
                try:
                    tagged = conn.execute(
                        "SELECT b.id FROM books b JOIN books_tags_link l ON b.id = l.book "
                        "JOIN tags t ON l.tag = t.id WHERE t.name = 'Flagged'"
                    ).fetchall()
                finally:
                    conn.close()
            finally:
                os.chdir(old)
        self.assertEqual(rc, 1)  # an EMPTY book is a finding
        self.assertEqual(tagged, [(1,)])


class TestCompletenessAnalyzer(unittest.TestCase):
    """Phase 15: the completeness spot-check analyzer. The fixture shapes come
    from the 2026-09-10 Redwall phase-1 run: Lord Brocktree (a real Epilogue
    doc sitting before a trailing 520-char ToC), Mattimeo (a 174k-char split
    doc whose final chapter heading has real prose after it), and The Long
    Patrol (percent-encoded hrefs the hand-rolled sampler could not read at
    all; 55/57 prose docs). Synthetic fillers replicate each SHAPE; no real
    text lives in this suite."""

    CONTAINER = TestEmptyTextScan.CONTAINER

    PARA = "The hares marched along the ridge in the fine morning light. "

    def _prose(self, paras=7, heading=None, closer=""):
        h = f"<h2>{heading}</h2>" if heading else ""
        return h + "<p>" + (self.PARA * paras) + "</p>" + closer

    def _epub(self, tmp, docs, hrefs=None):
        # docs: list of (name, body_html); hrefs optionally remaps the
        # manifest href (percent-encoded forms) while the archive name stays
        import zipfile as zf

        manifest = ""
        for i, (n, _) in enumerate(docs):
            href = (hrefs or {}).get(n, n)
            manifest += (
                f'<item id="d{i}" href="{href}" media-type="application/xhtml+xml"/>'
            )
        spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(docs)))
        opf = (
            '<package xmlns="http://www.idpf.org/2007/opf">'
            f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>"
        )
        p = pathlib.Path(tmp) / "t.epub"
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", opf)
            for n, body in docs:
                z.writestr(n, f"<html><body>{body}</body></html>")
        return p

    def test_real_back_matter_before_trailing_toc(self):
        # Lord Brocktree shape: six real chapters and a real Epilogue doc,
        # then a link-dense ToC (over the prose threshold at 456 visible
        # chars, like the real 520-char one) as the FINAL spine doc. The ToC
        # must classify as trailing furniture: never a prose sample, so the
        # closing excerpt comes from the Epilogue's back matter.
        chapters = [
            (f"ch{i}.xhtml", self._prose(7, f"Chapter {i}")) for i in range(1, 7)
        ]
        epilogue = (
            "epilogue.xhtml",
            self._prose(
                7,
                "Epilogue",
                closer="<p>And so the tale closes with the hares home at last.</p>",
            ),
        )
        toc = (
            "toc.xhtml",
            "<h1>Contents</h1>"
            + "".join(
                f'<p><a href="ch{i}.xhtml">Chapter {i}: The March of the '
                "Hares and the Siege of the Mountain</a></p>"
                for i in range(1, 7)
            )
            + '<p><a href="epilogue.xhtml">Epilogue: Home at Salamandastron'
            "</a></p>",
        )
        docs = [("title.xhtml", "<p>A Tale of the Long Patrol</p>")]
        docs += chapters + [epilogue, toc]
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.analyze_completeness(audit.load_book(self._epub(tmp, docs)))
        self.assertEqual(r["spine_docs"], 9)
        self.assertEqual(r["trailing_toc"], "toc.xhtml")
        # six chapters + the epilogue; the 456-char ToC is excluded
        self.assertEqual(r["prose_docs"], 7)
        self.assertEqual(r["spots"][-1]["doc"], "epilogue.xhtml")
        self.assertIn("home at last", r["spots"][-1]["ends"])

    def test_final_split_doc_with_prose_after_heading_is_not_a_toc(self):
        # Mattimeo shape: one huge split doc carrying fifty chapter headings,
        # each followed by real prose (Chapter 50 was verified to have prose
        # after it). The link-line heuristics must never read it as a ToC,
        # and the closing excerpt must be the prose after the last heading.
        chapters = [
            (f"ch{i}.xhtml", self._prose(7, f"Chapter {i}")) for i in range(1, 6)
        ]
        split_body = "".join(
            f"<h2>Chapter {i}</h2><p>{self.PARA * 4}</p>" for i in range(1, 51)
        )
        split = ("split.xhtml", split_body)
        docs = [("title.xhtml", "<p>A Tale of the Long Patrol</p>")]
        docs += chapters + [split]
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.analyze_completeness(audit.load_book(self._epub(tmp, docs)))
        self.assertEqual(r["trailing_toc"], "")
        self.assertEqual(r["spine_docs"], 7)
        self.assertEqual(r["prose_docs"], 6)  # five chapters + the split doc
        self.assertEqual(r["spots"][-1]["doc"], "split.xhtml")
        self.assertIn("fine morning light", r["spots"][-1]["ends"])

    def test_percent_encoded_hrefs_resolve(self):
        # The Long Patrol shape: manifest hrefs carry %20 runs for the spaces
        # in the stored archive names (the hand-rolled sampler read none of
        # these docs). The shipped audit's IRI decoding resolves them for
        # free: every spine doc lands, short front matter stays out of the
        # prose set, and the final doc is sampled whole.
        docs = [
            ("title.xhtml", "<p>A Tale of the Long Patrol</p>"),
            ("note.xhtml", "<p>A brief historical note.</p>"),
        ]
        docs += [
            (f"Chapter {i:02d}.xhtml", self._prose(7, f"Chapter {i}"))
            for i in range(1, 6)
        ]
        docs.append(("The End.xhtml", self._prose(7, "Chapter Six")))
        hrefs = {n: n.replace(" ", "%20") for n, _ in docs}
        with tempfile.TemporaryDirectory() as tmp:
            r = audit.analyze_completeness(
                audit.load_book(self._epub(tmp, docs, hrefs))
            )
        self.assertEqual(r["spine_docs"], 8)
        self.assertEqual(r["prose_docs"], 6)
        self.assertEqual(r["trailing_toc"], "")
        self.assertEqual(r["spots"][-1]["doc"], "The End.xhtml")
        self.assertEqual(len(r["spots"]), 3)
        self.assertTrue(r["spots"][0]["opens"])

    def test_unreadable_docs_are_counted(self):
        # A corrupt (bad-CRC) spine doc reads as empty text; the completeness
        # record must surface it as an unreadable fraction, never as prose.
        import struct
        import zipfile as zf

        docs = [(f"ch{i}.xhtml", self._prose(7, f"Chapter {i}")) for i in range(1, 4)]
        docs.append(("broken.xhtml", self._prose(7, "Chapter 4")))
        with tempfile.TemporaryDirectory() as tmp:
            p = self._epub(tmp, docs)
            with zf.ZipFile(p) as z:
                offset = z.getinfo("broken.xhtml").header_offset
            with open(p, "r+b") as f:
                f.seek(offset + 26)
                nlen, elen = struct.unpack("<HH", f.read(4))
                f.seek(offset + 30 + nlen + elen + 10)
                byte = f.read(1)
                f.seek(-1, os.SEEK_CUR)
                f.write(bytes([byte[0] ^ 0xFF]))
            r = audit.analyze_completeness(audit.load_book(p))
        self.assertEqual(r["spine_docs"], 4)
        self.assertEqual(r["unreadable"], 1)
        self.assertGreater(r["unreadable_frac"], 0.0)
        self.assertEqual(r["prose_docs"], 3)


class TestTrailingTocClassifier(unittest.TestCase):
    """The trailing-ToC shape test directly: a chapter list is dense with
    short link lines; any doc with real paragraphs is not a ToC."""

    def test_link_list_is_toc(self):
        body = "<h1>Contents</h1>" + "".join(
            f'<p><a href="ch{i}.xhtml">Chapter {i}: An Entry With Some Length</a></p>'
            for i in range(1, 9)
        )
        self.assertTrue(audit._trailing_toc(body))

    def test_prose_doc_is_not_a_toc(self):
        body = "<h2>Chapter One</h2><p>" + ("Ordinary prose runs here. " * 40) + "</p>"
        self.assertFalse(audit._trailing_toc(body))

    def test_heading_followed_by_prose_is_not_a_toc(self):
        # the Mattimeo guard: a heading line followed by real paragraphs is
        # content, even when other headings crowd the document
        body = "".join(
            f"<h2>Chapter {i}</h2><p>" + ("Ordinary prose runs here. " * 30) + "</p>"
            for i in range(1, 6)
        )
        self.assertFalse(audit._trailing_toc(body))

    def test_short_link_run_is_not_a_toc(self):
        # a colophon or a prev/next pager: too few lines to call a ToC
        body = "".join(
            f'<p><a href="ch{i}.xhtml">Chapter {i}</a></p>' for i in range(1, 4)
        )
        self.assertFalse(audit._trailing_toc(body))

    def test_prose_without_links_is_not_a_toc(self):
        # a dialogue-heavy doc of many short paragraphs carries no links
        body = "".join(f"<p>Said the hare, line {i}.</p>" for i in range(1, 21))
        self.assertFalse(audit._trailing_toc(body))


class TestCompletenessVerdict(unittest.TestCase):
    """The completeness verdict is advisory by contract: the archive, spine,
    and emptytext verdicts own the flags; completeness reports the shape."""

    def _r(self, **over):
        r = {
            "spine_docs": 31,
            "spine_missing": 0,
            "prose_docs": 30,
            "unreadable": 0,
            "unreadable_frac": 0.0,
            "blank_docs": 0,
            "trailing_toc": "",
            "spots": [
                {
                    "doc": "ch01.xhtml",
                    "chars": 5000,
                    "opens": "It began",
                    "ends": "it ended",
                }
            ],
        }
        r.update(over)
        return r

    def test_clean_book_is_ok_and_never_a_problem(self):
        problem, status, lines = audit._completeness_dir(self._r())
        self.assertFalse(problem)
        self.assertEqual(status, "OK")
        self.assertTrue(any("prose 30/31" in ln for ln in lines))
        self.assertTrue(any("ch01.xhtml" in ln for ln in lines))

    def test_trailing_toc_is_advisory(self):
        problem, status, lines = audit._completeness_dir(
            self._r(trailing_toc="toc.xhtml", prose_docs=30)
        )
        self.assertFalse(problem)
        self.assertEqual(status, "ADVISORY")
        self.assertTrue(any("toc.xhtml" in ln for ln in lines))

    def test_high_unreadable_fraction_is_advisory(self):
        problem, status, _ = audit._completeness_dir(
            self._r(unreadable=6, unreadable_frac=0.12)
        )
        self.assertFalse(problem)
        self.assertEqual(status, "ADVISORY")

    def test_blank_docs_are_reported(self):
        _problem, _status, lines = audit._completeness_dir(self._r(blank_docs=2))
        self.assertTrue(any("blank" in ln for ln in lines))


class TestCompletenessInAll(unittest.TestCase):
    """`all` runs the completeness analyzer in the same single decompression
    pass, and the --json payload carries its verdict per book."""

    def test_all_includes_completeness(self):
        self.assertIn("completeness", audit.ALL)

    def test_directory_json_record_carries_completeness(self):
        import zipfile as zf

        para = "The hares marched along the ridge in the fine morning light. "
        body = "<h2>Chapter One</h2><p>" + (para * 8) + "</p>"
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "t.epub"
            with zf.ZipFile(p, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", TestEmptyTextScan.CONTAINER)
                z.writestr("content.opf", TestEmptyTextScan.OPF)
                z.writestr("text.xhtml", f"<html><body>{body}</body></html>")
            out = pathlib.Path(tmp) / "audit.json"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = audit.run_directory(
                    pathlib.Path(tmp),
                    ["completeness"],
                    2000,
                    20000,
                    json_path=out,
                )
            payload = json.loads(out.read_text())
        self.assertEqual(rc, 0)  # advisory: never fails the run
        self.assertIn("completeness", payload["analyzers"])
        rec = payload["books"][0]
        self.assertIn("completeness", rec["verdicts"])
        self.assertEqual(rec["verdicts"]["completeness"]["status"], "OK")
        self.assertIn("prose 1/1", rec["verdicts"]["completeness"]["details"][0])


class TestObfuscatedVsDrm(unittest.TestCase):
    """Phase 16 B4: encryption.xml's font-obfuscation entries (publisher
    embedding; readable; benign) surface as their own advisory instead of
    the DRM-flavoured ENCRYPTED verdict. Prevalence: 86 of 5,228 library
    books carry obfuscation-only encryption.xml and no real DRM at all."""

    CONTAINER = TestEmptyTextScan.CONTAINER

    OPF = (
        '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="bookid">urn:uuid:X</dc:identifier></metadata>'
        "<manifest>"
        '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="font" href="fonts/f.otf" media-type="font/otf"/>'
        "</manifest>"
        '<spine><itemref idref="c1"/></spine></package>'
    )

    ENC = (
        '<?xml version="1.0"?>'
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<enc:EncryptedData xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
        '<enc:EncryptionMethod Algorithm="{algo}"/>'
        '<enc:CipherData><enc:CipherReference URI="fonts/f.otf"/></enc:CipherData>'
        "</enc:EncryptedData></encryption>"
    )

    def _epub(self, tmp, algo, corrupt_font=False):
        import zipfile as zf

        p = pathlib.Path(tmp) / "t.epub"
        body = "<p>" + ("Ordinary prose for the audit battery. " * 60) + "</p>"
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", self.OPF)
            z.writestr("text.xhtml", f"<html><body>{body}</body></html>")
            z.writestr("fonts/f.otf", b"OTTO" + b"\x00" * 64)
            z.writestr("META-INF/encryption.xml", self.ENC.format(algo=algo))
        if corrupt_font:
            import struct

            with zf.ZipFile(p) as z:
                offset = z.getinfo("fonts/f.otf").header_offset
            with open(p, "r+b") as f:
                f.seek(offset + 26)
                nlen, elen = struct.unpack("<HH", f.read(4))
                f.seek(offset + 30 + nlen + elen + 10)
                byte = f.read(1)
                f.seek(-1, os.SEEK_CUR)
                f.write(bytes([byte[0] ^ 0xFF]))
        return p

    def test_readable_obfuscation_is_advisory_not_drm(self):
        # both the spec-text IDPF URI and the wild Adobe URI the prevalence
        # study found (ns.adobe.com, not the spec's adobe.com/2005)
        for algo in (
            "http://www.idpf.org/2008/embedding",
            "http://ns.adobe.com/pdf/enc#RC",
            "http://www.adobe.com/2005/pdf/enc#RC",
        ):
            with self.subTest(algo=algo):
                with tempfile.TemporaryDirectory() as tmp:
                    book = audit.load_book(self._epub(tmp, algo))
                    r = audit.analyze_corrupt(book)
                self.assertEqual(r["encrypted_n"], 0)
                self.assertEqual(r["obfuscated_n"], 1)
                self.assertEqual(r["obfuscated_first"], "fonts/f.otf")
                problem, status, lines = audit._corrupt_verdict(r)
                self.assertFalse(problem)
                self.assertEqual(status, "OBFUSCATED")
                self.assertTrue(any("fonts/f.otf" in ln for ln in lines))

    def test_unreadable_declared_non_obfuscation_is_drm(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = audit.load_book(
                self._epub(
                    tmp,
                    "http://www.w3.org/2001/04/xmlenc#aes128-cbc",
                    corrupt_font=True,
                )
            )
            r = audit.analyze_corrupt(book)
        self.assertEqual(r["encrypted_n"], 1)
        self.assertEqual(r["encrypted_drm_n"], 1)
        problem, status, lines = audit._corrupt_verdict(r)
        self.assertTrue(problem)
        self.assertEqual(status, "ENCRYPTED")
        self.assertIn("DRM-protected", lines[0])

    def test_unreadable_obfuscation_entry_is_corrupt_not_drm(self):
        # an obfuscated font that cannot decompress is archive damage, not
        # a business model: the re-source advice is the right one
        with tempfile.TemporaryDirectory() as tmp:
            book = audit.load_book(
                self._epub(tmp, "http://www.idpf.org/2008/embedding", corrupt_font=True)
            )
            r = audit.analyze_corrupt(book)
        self.assertEqual(r["encrypted_n"], 1)
        self.assertEqual(r["encrypted_drm_n"], 0)
        problem, status, lines = audit._corrupt_verdict(r)
        self.assertTrue(problem)
        self.assertEqual(status, "CORRUPT")

    def test_clean_book_archive_verdict_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "t.epub"
            import zipfile as zf

            body = "<p>" + ("Ordinary prose. " * 60) + "</p>"
            with zf.ZipFile(p, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", self.CONTAINER)
                z.writestr("content.opf", self.OPF)
                z.writestr("text.xhtml", f"<html><body>{body}</body></html>")
            r = audit.analyze_corrupt(audit.load_book(p))
        problem, status, lines = audit._corrupt_verdict(r)
        self.assertFalse(problem)
        self.assertEqual(status, "OK")
        self.assertEqual(lines, [])

    def test_directory_run_reports_obfuscation_without_failing(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp:
            self._epub(tmp, "http://ns.adobe.com/pdf/enc#RC")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = audit.run_directory(pathlib.Path(tmp), ["emptytext"], 2000, 20000)
        out = buf.getvalue()
        self.assertEqual(rc, 0)  # benign: never fails the run
        self.assertIn("OBFUSCATED", out)
        self.assertIn("reported, not flagged", out)
