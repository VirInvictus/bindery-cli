import contextlib
import io
import os
import pathlib
import tempfile
import unittest

from bindery import audit as audit

pagenum = audit
emptytext = audit
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
        self.assertEqual(pagenum.number_value("42"), 42)
        self.assertEqual(pagenum.number_value("xiv"), 14)
        self.assertEqual(pagenum.number_value("II"), 2)

    def test_rejects_non_numbers(self):
        self.assertIsNone(pagenum.number_value("Chapter"))
        self.assertIsNone(pagenum.number_value("12345"))  # >4 digits
        self.assertIsNone(pagenum.number_value("i"))  # lone roman i is too noisy
        self.assertIsNone(pagenum.number_value("42a"))


class TestIsDefective(unittest.TestCase):
    def _r(self, **over):
        r = {"n_hits": 10, "span": 0.9, "run": 5, "watermark": False, "examples": []}
        r.update(over)
        return r

    def test_clear_defect(self):
        self.assertTrue(pagenum.is_defective(self._r()))

    def test_too_few_hits(self):
        self.assertFalse(pagenum.is_defective(self._r(n_hits=4)))

    def test_localized_cluster_dropped_by_span(self):
        # a footnote-poem / scraped-comment cluster: many hits, tiny span
        self.assertFalse(pagenum.is_defective(self._r(n_hits=20, span=0.02)))


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
            r = pagenum.scan_pagenumbers(self._epub(tmp, body))
        self.assertGreaterEqual(r["n_hits"], 5)
        self.assertTrue(pagenum.is_defective(r))

    def test_clean_chapter_numbers_do_not_flag(self):
        # a number that opens a chapter (next text is a fresh capitalized
        # sentence) is legitimate, not baked.
        chapter = "<p>" + ("A clean chapter of ordinary prose ends here. " * 6) + "</p>"
        body = "".join(f"<p>{n}</p>{chapter}" for n in range(1, 12))
        with tempfile.TemporaryDirectory() as tmp:
            r = pagenum.scan_pagenumbers(self._epub(tmp, body))
        self.assertFalse(pagenum.is_defective(r))


class TestVisibleChars(unittest.TestCase):
    def test_strips_tags_scripts_and_styles(self):
        html = (
            "<style>p{color:red}</style><p>Hello <b>world</b></p>"
            "<script>var x = 1</script>"
        )
        self.assertEqual(emptytext._visible_chars(html), len("Hello world"))

    def test_decodes_entities(self):
        self.assertEqual(emptytext._visible_chars("<p>a &amp; b</p>"), len("a & b"))


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
            r = emptytext.scan_emptytext(self._epub(tmp, body))
        self.assertEqual(r["chars"], 0)
        self.assertEqual(emptytext.classify(r, 2000, 20000), "EMPTY")

    def test_full_text_ok(self):
        body = "<p>" + ("Real prose that fills the book. " * 1000) + "</p>"
        with tempfile.TemporaryDirectory() as tmp:
            r = emptytext.scan_emptytext(self._epub(tmp, body))
        self.assertEqual(emptytext.classify(r, 2000, 20000), "OK")

    def test_thin_is_advisory(self):
        body = "<p>" + ("short story prose. " * 300) + "</p>"  # ~5700 chars
        with tempfile.TemporaryDirectory() as tmp:
            r = emptytext.scan_emptytext(self._epub(tmp, body))
        self.assertEqual(emptytext.classify(r, 2000, 20000), "THIN")


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
            r = emptytext.scan_emptytext(self._epub(tmp))
        self.assertGreater(r["chars"], 20000)
        self.assertEqual(emptytext.classify(r, 2000, 20000), "OK")


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
            r = emptytext.scan_emptytext(self._epub(tmp, docs))
        self.assertGreater(r["chars"], 20000)  # would otherwise clear the THIN floor
        self.assertTrue(r["placeholder_sig"])
        self.assertEqual(emptytext.classify(r, 2000, 20000), "PARTIAL")

    def test_repeated_stub_without_signature_is_partial(self):
        real = "<p>" + ("Real prose. " * 3000) + "</p>"
        stub = "<p>This chapter is not included in this edition preview.</p>"
        docs = [("c0.xhtml", real)] + [(f"c{i}.xhtml", stub) for i in range(1, 12)]
        with tempfile.TemporaryDirectory() as tmp:
            r = emptytext.scan_emptytext(self._epub(tmp, docs))
        self.assertFalse(r["placeholder_sig"])
        self.assertGreaterEqual(r["stub_docs"], 3)
        self.assertEqual(emptytext.classify(r, 2000, 20000), "PARTIAL")

    def test_distinct_small_dividers_stay_ok(self):
        real = "<p>" + ("Real prose. " * 3000) + "</p>"
        docs = [("c0.xhtml", real)] + [
            (f"d{i}.xhtml", f"<p>Part {i}: a distinct section divider heading.</p>")
            for i in range(12)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            r = emptytext.scan_emptytext(self._epub(tmp, docs))
        self.assertLess(r["stub_docs"], 3)  # distinct text, so no repeated stub
        self.assertEqual(emptytext.classify(r, 2000, 20000), "OK")


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
            r = ocr.scan_ocr(self._epub(tmp, (a + b) * 40))
        self.assertEqual(r["splits"], 40)
        self.assertGreaterEqual(r["split_rate"], ocr.OCR_FLAG_RATE)
        self.assertEqual(r["func_frac"], 1.0)
        self.assertTrue(ocr.is_ocr_damaged(r))

    def test_image_interrupted_pair_is_cleared(self):
        # a formula/figure between the fragments renders fine; not a split
        a = "<p>" + ("The channel capacity is given by the value " * 4) + "shown by</p>"
        img = '<div><img src="eq1.png"/></div>'
        b = "<p>where the terms " + ("are defined in the usual way " * 4) + "here.</p>"
        with tempfile.TemporaryDirectory() as tmp:
            r = ocr.scan_ocr(self._epub(tmp, (a + img + b) * 40))
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
            r = ocr.scan_ocr(self._epub(tmp, (a + b) * 40))
        self.assertGreater(r["splits"], 0)
        self.assertLess(r["func_frac"], ocr.OCR_FUNC_MIN)
        self.assertFalse(ocr.is_ocr_damaged(r))

    def test_dialogue_fragment_is_not_a_split(self):
        # "'Course not." starts with a quote, not a lowercase letter
        prose = "<p>" + ("Ordinary narrative prose carries on here. " * 5) + "</p>"
        body = (prose + "<p>'Course not,' said Nobby.</p>") * 20
        with tempfile.TemporaryDirectory() as tmp:
            r = ocr.scan_ocr(self._epub(tmp, body))
        self.assertEqual(r["splits"], 0)

    def test_scene_break_is_not_a_split(self):
        # an unpunctuated paragraph end followed by a scene-break marker and a
        # fresh capitalized paragraph: a boundary, not a split
        a = "<p>" + ("The chapter wound down as the light faded " * 5) + "and so on</p>"
        marker = "<p>* * *</p>"
        b = "<p>Morning came bright and early. " + ("The day began anew. " * 5) + "</p>"
        with tempfile.TemporaryDirectory() as tmp:
            r = ocr.scan_ocr(self._epub(tmp, (a + marker + b) * 20))
        self.assertEqual(r["splits"], 0)

    def test_clean_prose_measures_zero(self):
        body = "<p>" + ("A clean paragraph ends with a period. " * 5) + "</p>"
        with tempfile.TemporaryDirectory() as tmp:
            r = ocr.scan_ocr(self._epub(tmp, body * 40))
        self.assertEqual(r["splits"], 0)
        self.assertFalse(ocr.is_ocr_damaged(r))

    def test_side_signals(self):
        prose = "<p>" + ("Filler prose to give the book body text. " * 5) + "</p>"
        damaged = (
            "<p>That bottom–feedin' scum said ' 'Course, guv.' "
            "They walked through AnkhMorpork, the city of Ankh-Morpork.</p>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            r = ocr.scan_ocr(self._epub(tmp, prose * 10 + damaged))
        self.assertEqual(r["en_dash_words"], 1)
        self.assertEqual(r["doubled_quotes"], 1)
        self.assertEqual(r["glued"], ["AnkhMorpork~Ankh-Morpork"])


class TestIsOcrDamaged(unittest.TestCase):
    """Threshold boundaries on the FLAG gate."""

    def _r(self, **over):
        r = {
            "paras": 1000,
            "splits": ocr.OCR_MIN_SPLITS,
            "split_rate": ocr.OCR_FLAG_RATE,
            "func_frac": ocr.OCR_FUNC_MIN,
        }
        r.update(over)
        return r

    def test_at_threshold_flags(self):
        self.assertTrue(ocr.is_ocr_damaged(self._r()))

    def test_rate_just_below_threshold_passes(self):
        self.assertFalse(
            ocr.is_ocr_damaged(self._r(split_rate=ocr.OCR_FLAG_RATE * 0.99))
        )

    def test_too_few_splits_passes(self):
        self.assertFalse(
            ocr.is_ocr_damaged(self._r(splits=ocr.OCR_MIN_SPLITS - 1, split_rate=0.5))
        )

    def test_too_few_paragraphs_passes(self):
        # fragmentary short works never have enough paragraphs for the rate
        # to mean anything
        self.assertFalse(
            ocr.is_ocr_damaged(
                self._r(paras=ocr.OCR_MIN_PARAS - 1, splits=40, split_rate=0.9)
            )
        )

    def test_clause_boundary_splits_pass(self):
        # a high split rate with a low function-word fraction is deliberate
        # style, not damage
        self.assertFalse(
            ocr.is_ocr_damaged(
                self._r(splits=200, split_rate=0.4, func_frac=ocr.OCR_FUNC_MIN * 0.5)
            )
        )


class TestAllIncludesOcr(unittest.TestCase):
    """`all` runs the ocr analyzer inside the same single decompression pass."""

    def test_all_tuple_has_ocr(self):
        self.assertIn("ocr", audit.ALL)

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


if __name__ == "__main__":
    unittest.main()


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


if __name__ == "__main__":
    unittest.main()
