"""End-to-end repair on an in-memory EPUB: NCX-001 sync, entity/void fixes in content,
mimetype repair, and untouched binaries."""

import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from bindery.epub import (
    fix_manifest_ids,
    ncx_uid_mismatch,
    opf_unique_id,
    repair_epub,
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


if __name__ == "__main__":
    unittest.main()
