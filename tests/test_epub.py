"""End-to-end repair on an in-memory EPUB: NCX-001 sync, entity/void fixes in content,
mimetype repair, and untouched binaries."""

import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from bindery.epub import (
    fix_manifest_ids,
    fix_ncx_ids,
    fix_pagelist_class,
    ncx_uid_mismatch,
    opf_unique_id,
    repair_epub,
    strip_page_map,
    sync_ncx_uid,
)

OPF = (
    '<?xml version="1.0"?>'
    '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
    '<dc:identifier id="bookid">urn:uuid:THE-RIGHT-ID</dc:identifier>'
    "</metadata></package>"
)
NCX_BAD = (
    '<?xml version="1.0"?>'
    '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><head>'
    '<meta name="dtb:uid" content="urn:uuid:THE-WRONG-ID"/>'
    "</head></ncx>"
)
CONTENT = (
    '<?xml version="1.0"?>'
    '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
    '<link rel="stylesheet" href="s.css"></head>'
    "<body><p>caf&eacute;&nbsp;<br>x</p></body></html>"
)


def build(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("OEBPS/c1.xhtml", CONTENT)
        z.writestr("OEBPS/toc.ncx", NCX_BAD)
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/img.jpg", b"\xff\xd8\xffBINARY")
        z.writestr("mimetype", "application/epub+zip")  # wrong place + deflated


class TestParts(unittest.TestCase):
    def test_opf_unique_id(self):
        self.assertEqual(opf_unique_id(OPF), "urn:uuid:THE-RIGHT-ID")

    def test_sync_ncx_uid(self):
        out, changed = sync_ncx_uid(NCX_BAD, "urn:uuid:THE-RIGHT-ID")
        self.assertTrue(changed)
        self.assertIn('content="urn:uuid:THE-RIGHT-ID"', out)
        # syncing an already-correct uid is a no-op
        _, changed2 = sync_ncx_uid(out, "urn:uuid:THE-RIGHT-ID")
        self.assertFalse(changed2)

    def test_sync_ncx_uid_with_backslash(self):
        # A uid containing backslashes must be inserted literally, not parsed as a
        # regex replacement template (where \1 would be a group reference).
        uid = r"urn:weird\1id"
        out, changed = sync_ncx_uid(NCX_BAD, uid)
        self.assertTrue(changed)
        self.assertIn(f'content="{uid}"', out)


class TestManifestIds(unittest.TestCase):
    def test_digit_led_ids_renamed_with_refs(self):
        opf = (
            "<manifest>"
            '<item id="7cgqkgid" href="a.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="24l5xjpf" href="b.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            "</manifest>"
            '<spine toc="ncx">'
            '<itemref idref="7cgqkgid"/><itemref idref="24l5xjpf"/>'
            "</spine>"
        )
        out, n = fix_manifest_ids(opf)
        self.assertEqual(n, 2)
        self.assertIn('id="id_7cgqkgid"', out)
        self.assertIn('idref="id_7cgqkgid"', out)
        self.assertIn('idref="id_24l5xjpf"', out)
        # valid ids and hrefs untouched
        self.assertIn('id="ncx"', out)
        self.assertIn('toc="ncx"', out)
        self.assertIn('href="a.xhtml"', out)

    def test_valid_ids_untouched(self):
        opf = '<item id="chapter1" href="c.xhtml" media-type="x"/>'
        out, n = fix_manifest_ids(opf)
        self.assertEqual((out, n), (opf, 0))

    def test_colon_id_renamed(self):
        out, n = fix_manifest_ids('<item id="a:b" href="x"/><itemref idref="a:b"/>')
        self.assertEqual(n, 1)
        self.assertIn('id="id_a_b"', out)
        self.assertIn('idref="id_a_b"', out)

    def test_collision_rename_is_deterministic(self):
        # `1:2` and `1_2` are both invalid and both want `id_1_2`. Iterating the id
        # *set* made which one got the `_` prefix depend on the hash seed, so the same
        # book repaired to different bytes from run to run.
        out, n = fix_manifest_ids('<item id="1:2" href="a"/><item id="1_2" href="b"/>')
        self.assertEqual(n, 2)
        self.assertEqual(
            out, '<item id="id_1_2" href="a"/><item id="_id_1_2" href="b"/>'
        )

    def test_fallback_and_cover_meta_references_updated(self):
        # fallback= and the EPUB 2 cover meta point at manifest ids too; leaving them
        # stale would orphan the fallback chain and break Calibre's cover detection.
        opf = (
            '<metadata><meta name="cover" content="31img"/></metadata>'
            "<manifest>"
            '<item id="31img" href="c.jpg" media-type="image/jpeg"/>'
            '<item id="2x" href="a.xhtml" fallback="31img" media-type="x"/>'
            "</manifest>"
        )
        out, n = fix_manifest_ids(opf)
        self.assertEqual(n, 2)
        self.assertIn('id="id_31img"', out)
        self.assertIn('fallback="id_31img"', out)
        self.assertIn('<meta name="cover" content="id_31img"/>', out)


class TestNcxIds(unittest.TestCase):
    def test_uuid_and_colon_ids_renamed(self):
        # The wild form: navPoint ids stamped from UUIDs, which are digit-led.
        ncx = (
            '<navMap><navPoint id="620a6fe8-04fc-4496" playOrder="1"/>'
            "<navPoint id='a:b' playOrder='2'/>"
            '<navPoint id="ok_id" playOrder="3"/></navMap>'
        )
        out, n = fix_ncx_ids(ncx)
        self.assertEqual(n, 2)
        self.assertIn('id="id_620a6fe8-04fc-4496"', out)
        self.assertIn("id='id_a_b'", out)
        self.assertIn('id="ok_id"', out)

    def test_valid_ids_untouched(self):
        ncx = '<navPoint id="navPoint-1" playOrder="1"/>'
        self.assertEqual(fix_ncx_ids(ncx), (ncx, 0))

    def test_collision_rename_is_deterministic(self):
        out, n = fix_ncx_ids('<navPoint id="1:2"/><navPoint id="1_2"/>')
        self.assertEqual(n, 2)
        self.assertEqual(out, '<navPoint id="id_1_2"/><navPoint id="_id_1_2"/>')

    def test_rename_collision_avoided(self):
        # "1x" renames to id_1x, which already exists; the new name must not collide.
        out, n = fix_ncx_ids('<navPoint id="1x"/><navPoint id="id_1x"/>')
        self.assertEqual(n, 1)
        self.assertIn('id="_id_1x"', out)
        self.assertIn('id="id_1x"', out)

    def test_opt_in_via_repair_epub(self):
        ncx = (
            '<?xml version="1.0"?>'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">'
            '<navMap><navPoint id="620a6fe8" playOrder="1"/></navMap></ncx>'
        )
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("OEBPS/toc.ncx", ncx)
            report = repair_epub(src, dst)  # off by default
            self.assertNotIn("fix_ncx_ids", report.fixes)
            report = repair_epub(src, dst, fix_ids=True)
            self.assertEqual(report.fixes.get("fix_ncx_ids"), 1)
            with zipfile.ZipFile(dst) as z:
                self.assertIn('id="id_620a6fe8"', z.read("OEBPS/toc.ncx").decode())


class TestPageMap(unittest.TestCase):
    """Phase 10's OPF/NCX edge cases: the non-standard spine page-map
    attribute and the classless NCX <pageList> (both epubcheck rejects on
    older HarperCollins / Anna's Archive conversions)."""

    def test_spine_page_map_stripped_both_quote_styles(self):
        opf = '<spine toc="ncx" page-map="page-map"><itemref idref="a"/></spine>'
        out, n = strip_page_map(opf)
        self.assertEqual(n, 1)
        self.assertEqual(out, '<spine toc="ncx"><itemref idref="a"/></spine>')
        out, n = strip_page_map("<spine page-map='page-map'/>")
        self.assertEqual((out, n), ("<spine/>", 1))

    def test_spine_without_page_map_is_untouched(self):
        opf = '<spine toc="ncx"><itemref idref="a"/></spine>'
        out, n = strip_page_map(opf)
        self.assertEqual((out, n), (opf, 0))

    def test_pagelist_class_injected(self):
        ncx = "<pageList><pageTarget/></pageList>"
        out, n = fix_pagelist_class(ncx)
        self.assertEqual(n, 1)
        self.assertEqual(out, '<pageList class="pages"><pageTarget/></pageList>')

    def test_pagelist_with_existing_class_untouched(self):
        ncx = '<pageList class="pages" id="pl"><pageTarget/></pageList>'
        out, n = fix_pagelist_class(ncx)
        self.assertEqual((out, n), (ncx, 0))

    def test_self_closing_pagelist(self):
        out, n = fix_pagelist_class("<pageList/>")
        self.assertEqual((out, n), ('<pageList class="pages"/>', 1))

    def test_opt_in_via_repair_epub(self):
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="bookid">urn:uuid:THE-RIGHT-ID</dc:identifier>'
            "</metadata>"
            '<spine page-map="page-map"><itemref idref="a"/></spine></package>'
        )
        ncx = (
            '<?xml version="1.0"?>'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><head>'
            '<meta name="dtb:uid" content="urn:uuid:THE-RIGHT-ID"/>'
            "</head><pageList><pageTarget/></pageList></ncx>"
        )
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("OEBPS/content.opf", opf)
                z.writestr("OEBPS/toc.ncx", ncx)
            report = repair_epub(src, dst)  # off by default
            self.assertNotIn("page_map_stripped", report.fixes)
            self.assertNotIn("pagelist_class_added", report.fixes)
            report = repair_epub(src, dst, page_map=True)
            self.assertEqual(report.fixes.get("page_map_stripped"), 1)
            self.assertEqual(report.fixes.get("pagelist_class_added"), 1)
            with zipfile.ZipFile(dst) as z:
                opf_out = z.read("OEBPS/content.opf").decode()
                ncx_out = z.read("OEBPS/toc.ncx").decode()
        self.assertNotIn("page-map", opf_out)
        self.assertIn('<pageList class="pages">', ncx_out)


class TestMimetypeRepair(unittest.TestCase):
    def _repair(self, mimetype_payload):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
                if mimetype_payload is not None:
                    z.writestr("mimetype", mimetype_payload)
                z.writestr("OEBPS/c1.xhtml", CONTENT)
            report = repair_epub(src, dst)
            with zipfile.ZipFile(dst) as z:
                first = z.infolist()[0]
                data = z.read("mimetype")
            return report, first, data

    def test_missing_mimetype_added(self):
        report, first, data = self._repair(None)
        self.assertEqual(report.fixes.get("mimetype_added"), 1)
        self.assertEqual(first.filename, "mimetype")
        self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
        self.assertEqual(data, b"application/epub+zip")

    def test_padded_mimetype_normalized(self):
        report, _, data = self._repair("application/epub+zip\n")
        self.assertEqual(report.fixes.get("mimetype_normalized"), 1)
        self.assertEqual(data, b"application/epub+zip")

    def test_correct_mimetype_untouched(self):
        report, first, data = self._repair("application/epub+zip")
        self.assertNotIn("mimetype_added", report.fixes)
        self.assertNotIn("mimetype_normalized", report.fixes)
        self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
        self.assertEqual(data, b"application/epub+zip")

    def test_mimetype_keeps_the_source_timestamp(self):
        # Written with a bare string arcname, zipfile stamped this entry with the
        # current clock, so two repairs of one book differed in exactly those bytes.
        # Carrying the source entry's timestamp over is what makes output
        # reproducible; every other entry already keeps its own.
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            stamp = (2001, 2, 3, 4, 5, 6)
            with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr(
                    zipfile.ZipInfo("mimetype", date_time=stamp), "application/epub+zip"
                )
                z.writestr("OEBPS/c1.xhtml", CONTENT)
            repair_epub(src, dst)
            with zipfile.ZipFile(dst) as z:
                self.assertEqual(z.getinfo("mimetype").date_time, stamp)

    def test_added_mimetype_gets_a_fixed_timestamp(self):
        # Nothing to inherit when the entry is being added, so it takes the zip
        # epoch rather than the wall clock, which would break reproducibility.
        _, first, _ = self._repair(None)
        self.assertEqual(first.date_time, (1980, 1, 1, 0, 0, 0))

    def test_repair_is_byte_reproducible(self):
        # The end the two tests above serve: repairing the same book twice must
        # produce identical archives. This passed by luck before the fix, whenever
        # both runs happened to land inside the same clock second.
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            build(src)
            outs = []
            for i in range(2):
                d = Path(td) / f"out{i}.epub"
                repair_epub(src, d, fix_ids=True)
                outs.append(d.read_bytes())
            self.assertEqual(outs[0], outs[1])


class TestEscapeEntitiesFlag(unittest.TestCase):
    CONTENT_UNKNOWN = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
        "<body><p>&foo;</p></body></html>"
    )

    def test_opt_in_only(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("c1.xhtml", self.CONTENT_UNKNOWN)
            # off by default: the unknown entity stays (fatal, but not ours to touch)
            report = repair_epub(src, dst)
            self.assertNotIn("escape_unknown_entities", report.fixes)
            with zipfile.ZipFile(dst) as z:
                self.assertIn("<p>&foo;</p>", z.read("c1.xhtml").decode())
            report = repair_epub(src, dst, escape_entities=True)
            self.assertEqual(report.fixes.get("escape_unknown_entities"), 1)
            with zipfile.ZipFile(dst) as z:
                self.assertIn("<p>&amp;foo;</p>", z.read("c1.xhtml").decode())


OPF_SQ = OPF.replace('"', "'")
NCX_BAD_SQ = NCX_BAD.replace('"', "'")


class TestSingleQuotedAttributes(unittest.TestCase):
    # A single-quoting toolchain used to make the NCX-001 sync and the OPF location
    # silently no-op: every attribute regex required double quotes.

    def test_opf_unique_id(self):
        self.assertEqual(opf_unique_id(OPF_SQ), "urn:uuid:THE-RIGHT-ID")

    def test_sync_ncx_uid(self):
        out, changed = sync_ncx_uid(NCX_BAD_SQ, "urn:uuid:THE-RIGHT-ID")
        self.assertTrue(changed)
        self.assertIn("content='urn:uuid:THE-RIGHT-ID'", out)

    def test_mismatch_detected_and_repaired(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr(
                    "META-INF/container.xml",
                    "<container><rootfiles>"
                    "<rootfile full-path='OEBPS/content.opf' "
                    "media-type='application/oebps-package+xml'/>"
                    "</rootfiles></container>",
                )
                z.writestr("OEBPS/content.opf", OPF_SQ)
                z.writestr("OEBPS/toc.ncx", NCX_BAD_SQ)
            self.assertTrue(ncx_uid_mismatch(src))
            repair_epub(src, dst)
            self.assertFalse(ncx_uid_mismatch(dst))
            with zipfile.ZipFile(dst) as z:
                self.assertIn(
                    "content='urn:uuid:THE-RIGHT-ID'",
                    z.read("OEBPS/toc.ncx").decode(),
                )


class TestRepairEpub(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / "in.epub"
        self.dst = Path(self.tmp.name) / "out.epub"
        build(self.src)

    def tearDown(self):
        self.tmp.cleanup()

    def test_detects_ncx_mismatch(self):
        self.assertTrue(ncx_uid_mismatch(self.src))

    def test_repair_report_and_output(self):
        report = repair_epub(self.src, self.dst)
        self.assertTrue(report)
        self.assertTrue(report.ncx_uid_synced)
        self.assertIn("fix_named_entities", report.fixes)
        self.assertIn("self_close_void", report.fixes)

        with zipfile.ZipFile(self.dst) as z:
            first = z.infolist()[0]
            self.assertEqual(first.filename, "mimetype")
            self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
            self.assertIn("urn:uuid:THE-RIGHT-ID", z.read("OEBPS/toc.ncx").decode())
            c = z.read("OEBPS/c1.xhtml").decode()
            self.assertNotIn("&nbsp;", c)
            self.assertNotIn("&eacute;", c)
            self.assertEqual(z.read("OEBPS/img.jpg"), b"\xff\xd8\xffBINARY")
        # the repaired book no longer has the mismatch
        self.assertFalse(ncx_uid_mismatch(self.dst))

    def test_opf_located_via_container_xml(self):
        # A stray decoy .opf earlier in archive order must not win over the rootfile
        # declared in META-INF/container.xml: the wrong uid would be synced.
        src = Path(self.tmp.name) / "decoy.epub"
        decoy_opf = OPF.replace("urn:uuid:THE-RIGHT-ID", "urn:uuid:DECOY-ID")
        with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("AAA/decoy.opf", decoy_opf)
            z.writestr(
                "META-INF/container.xml",
                '<container><rootfiles><rootfile full-path="OEBPS/content.opf" '
                'media-type="application/oebps-package+xml"/></rootfiles></container>',
            )
            z.writestr("OEBPS/content.opf", OPF)
            z.writestr("OEBPS/toc.ncx", NCX_BAD)
        self.assertTrue(ncx_uid_mismatch(src))
        repair_epub(src, self.dst)
        with zipfile.ZipFile(self.dst) as z:
            self.assertIn("urn:uuid:THE-RIGHT-ID", z.read("OEBPS/toc.ncx").decode())

    def test_duplicate_entry_names_preserved(self):
        # zin.read(name) returns the first entry's bytes for every duplicate; each
        # entry must be read individually so no data is silently swapped.
        src = Path(self.tmp.name) / "dupes.epub"
        with zipfile.ZipFile(src, "w") as z, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("OEBPS/a.txt", b"first")
            z.writestr("OEBPS/a.txt", b"second")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            repair_epub(src, self.dst)
        with zipfile.ZipFile(self.dst) as z:
            datas = [z.read(i) for i in z.infolist() if i.filename == "OEBPS/a.txt"]
        self.assertEqual(datas, [b"first", b"second"])

    def test_unchanged_entry_bytes_preserved(self):
        # A clean document that is not valid UTF-8 must be copied verbatim: the old
        # unconditional decode("utf-8", "replace") + re-encode swapped its non-UTF-8
        # bytes for U+FFFD even when no transform fired.
        raw = (
            '<?xml version="1.0" encoding="iso-8859-1"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml">'
            "<body><p>caf\xe9</p></body></html>"
        ).encode("latin-1")
        with zipfile.ZipFile(self.src, "a", zipfile.ZIP_DEFLATED) as z:
            z.writestr("OEBPS/clean.xhtml", raw)
        repair_epub(self.src, self.dst)
        with zipfile.ZipFile(self.dst) as z:
            self.assertEqual(z.read("OEBPS/clean.xhtml"), raw)


class TestOptInStructuralRepairs(unittest.TestCase):
    """End-to-end: the six structural repairs fire only behind their flags, and the
    CSS precondition protects styled tags book-wide during --unwrap-illegal-tags."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / "in.epub"
        self.dst = Path(self.tmp.name) / "out.epub"

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self, css: str, content: str | None = None):
        doc = content or (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head></head>'
            '<body><p id="sec:1">one</p>'
            '<div class="c" value="7">x</div>'
            "<w>bold</w><sentence>s</sentence>"
            "<span><p>inner</p></span></body></html>"
        )
        with zipfile.ZipFile(self.src, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("OEBPS/c1.xhtml", doc)
            z.writestr("OEBPS/style.css", css)
            z.writestr("OEBPS/content.opf", OPF)
            z.writestr("OEBPS/toc.ncx", NCX_BAD)

    STRUCTURAL_KEYS = (
        "fix_empty_body",
        "fix_missing_title",
        "fix_id_colons",
        "unwrap_block_in_inline",
        "strip_invalid_value",
        "unwrap_illegal_tags",
    )

    def test_default_repair_touches_nothing_structural(self):
        self._build(css="p { margin: 0 }")
        report = repair_epub(self.src, self.dst)
        for key in self.STRUCTURAL_KEYS:
            self.assertNotIn(key, report.fixes)
        with zipfile.ZipFile(self.dst) as z:
            c = z.read("OEBPS/c1.xhtml").decode()
        self.assertIn('id="sec:1"', c)
        self.assertIn("<w>bold</w>", c)
        self.assertIn('value="7"', c)
        self.assertIn("<span><p>inner</p></span>", c)
        self.assertNotIn("<title>Unknown", c)

    def test_flags_fire_but_css_styled_tag_is_protected(self):
        self._build(css="/* book styling */\nw { color: red }\np { margin: 0 }")
        report = repair_epub(
            self.src,
            self.dst,
            missing_title=True,
            id_colons=True,
            block_in_inline=True,
            invalid_value=True,
            illegal_tags=True,
        )
        self.assertIn("fix_missing_title", report.fixes)
        self.assertIn("fix_id_colons", report.fixes)
        self.assertIn("unwrap_block_in_inline", report.fixes)
        self.assertIn("strip_invalid_value", report.fixes)
        # <sentence> had no styling -> unwrapped; <w> is styled -> protected.
        self.assertIn("unwrap_illegal_tags", report.fixes)
        with zipfile.ZipFile(self.dst) as z:
            c = z.read("OEBPS/c1.xhtml").decode()
        self.assertIn("<w>bold</w>", c)
        self.assertNotIn("<sentence>", c)
        self.assertIn('id="sec_1"', c)
        self.assertIn("<title>Unknown</title>", c)
        self.assertNotIn('value="7"', c)
        self.assertNotIn("<span><p>", c)
        self.assertIn("<p>inner</p>", c)

    def test_unstyled_book_loses_all_illegal_tags(self):
        self._build(css="p { margin: 0 }")
        repair_epub(self.src, self.dst, illegal_tags=True)
        with zipfile.ZipFile(self.dst) as z:
            c = z.read("OEBPS/c1.xhtml").decode()
        self.assertNotIn("<w>", c)
        self.assertIn("bold", c)  # inner text survives

    def test_inline_style_block_protects_without_a_stylesheet(self):
        self._build(
            css="/* nothing relevant */",
            content=(
                '<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
                "<style>sentence { font-style: italic }</style></head>"
                "<body><w>x</w><sentence>y</sentence></body></html>"
            ),
        )
        repair_epub(self.src, self.dst, illegal_tags=True)
        with zipfile.ZipFile(self.dst) as z:
            c = z.read("OEBPS/c1.xhtml").decode()
        self.assertIn("<sentence>y</sentence>", c)  # protected via inline <style>
        self.assertNotIn("<w>", c)  # unstyled -> removed

    def test_empty_body_fix_is_opt_in(self):
        # A book with nothing else wrong: no OPF/NCX sidecars, so the default run
        # must be a true noop (an empty RepairReport).
        content = (
            '<?xml version="1.0"?><html '
            'xmlns="http://www.w3.org/1999/xhtml">'
            "<head><title>t</title></head><body> </body></html>"
        )
        with zipfile.ZipFile(self.src, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", b"application/epub+zip")
            z.writestr("OEBPS/c1.xhtml", content)
        self.assertFalse(repair_epub(self.src, self.dst))
        report = repair_epub(self.src, self.dst, empty_body=True)
        self.assertIn("fix_empty_body", report.fixes)


if __name__ == "__main__":
    unittest.main()
