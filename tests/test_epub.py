"""End-to-end repair on an in-memory EPUB: NCX-001 sync, entity/void fixes in content,
mimetype repair, and untouched binaries."""

import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from bindery.epub import (
    RepairFlags,
    _locate_opf,
    collect_undeclared_resources,
    declare_missing_manifest_items,
    downgrade_epub3_tags,
    fix_manifest_ids,
    fix_ncx_ids,
    fix_pagelist_class,
    fix_unique_identifier_wiring,
    generate_container,
    ncx_uid_mismatch,
    opf_unique_id,
    package_version,
    repair_epub,
    strip_epub3_attributes,
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
# The Hackers spelling (issue #22's residual NCX-001): every element
# carries the ncx: prefix, including the dtb:uid meta.
NCX_BAD_PREFIXED = (
    '<?xml version="1.0"?>'
    '<ncx:ncx xmlns:ncx="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
    "<ncx:head>"
    '<ncx:meta name="dtb:uid" content="urn:uuid:THE-WRONG-ID"/>'
    "</ncx:head></ncx:ncx>"
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

    def test_sync_ncx_uid_prefixed_meta(self):
        # v0.57.1 (issue #22 follow-up): the 2010-era NCX spellings every
        # element as <ncx:meta>; the sync reads and rewrites the dtb:uid
        # there exactly as on the unprefixed shape.
        out, changed = sync_ncx_uid(NCX_BAD_PREFIXED, "urn:uuid:THE-RIGHT-ID")
        self.assertTrue(changed)
        self.assertIn('content="urn:uuid:THE-RIGHT-ID"', out)
        _, changed2 = sync_ncx_uid(out, "urn:uuid:THE-RIGHT-ID")
        self.assertFalse(changed2)

    def test_prefixed_meta_rev_order(self):
        # content before name, under the prefix: the reversed-order matcher
        # carries the same prefix extension.
        ncx = (
            '<ncx:ncx xmlns:ncx="http://www.daisy.org/z3986/2005/ncx/"><ncx:head>'
            '<ncx:meta content="urn:uuid:THE-WRONG-ID" name="dtb:uid"/>'
            "</ncx:head></ncx:ncx>"
        )
        out, changed = sync_ncx_uid(ncx, "urn:uuid:THE-RIGHT-ID")
        self.assertTrue(changed)
        self.assertIn('content="urn:uuid:THE-RIGHT-ID"', out)
        ET.fromstring(out)

    def test_prefixed_meta_no_false_positives(self):
        # A prefixed element whose local name merely starts with "meta"
        # (metadata) is not a dtb:uid carrier, prefix or not.
        ncx = (
            '<p:package xmlns:p="urn:x"><p:head>'
            '<p:metadata name="dtb:uid" content="urn:uuid:THE-WRONG-ID"/>'
            "</p:head></p:package>"
        )
        self.assertEqual(sync_ncx_uid(ncx, "urn:uuid:OTHER"), (ncx, False))

    def test_ncx_uid_mismatch_prefixed(self):
        # The --only ncx selector reads the same matchers: a prefixed-NCX
        # book with a drifted uid is a candidate, an in-sync one is not.
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("OEBPS/c1.xhtml", CONTENT)
                z.writestr("OEBPS/toc.ncx", NCX_BAD_PREFIXED)
                z.writestr("OEBPS/content.opf", OPF)
            self.assertTrue(ncx_uid_mismatch(src))
            out, changed = sync_ncx_uid(NCX_BAD_PREFIXED, "urn:uuid:THE-RIGHT-ID")
            self.assertTrue(changed)
            src2 = Path(td) / "synced.epub"
            with zipfile.ZipFile(src2, "w") as z:
                z.writestr("OEBPS/c1.xhtml", CONTENT)
                z.writestr("OEBPS/toc.ncx", out)
                z.writestr("OEBPS/content.opf", OPF)
            self.assertFalse(ncx_uid_mismatch(src2))


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

    def test_prefixed_item_id_renamed_with_refs(self):
        # issue #20's prefix blindness extended to fix_manifest_ids' own lane:
        # a <opf:item id="7cgqkgid"> was invisible to the rename while the
        # spine's <itemref idref> was not, so the idref was renamed against a
        # standing id
        opf = (
            "<manifest>"
            '<opf:item id="7cgqkgid" href="a.xhtml" '
            'media-type="application/xhtml+xml"/>'
            "</manifest>"
            '<spine><itemref idref="7cgqkgid"/></spine>'
        )
        out, n = fix_manifest_ids(opf)
        self.assertEqual(n, 1)
        self.assertIn('id="id_7cgqkgid"', out)
        self.assertIn('idref="id_7cgqkgid"', out)

    def test_single_quoted_opf_renames_too(self):
        # reported 2026-09-08: the double-quote-only regex made a
        # single-quoting toolchain a silent count-0 no-op
        opf = (
            "<manifest>"
            "<item id='7cgqkgid' href='a.xhtml' media-type='application/xhtml+xml'/>"
            "</manifest>"
            "<spine><itemref idref='7cgqkgid'/></spine>"
        )
        out, n = fix_manifest_ids(opf)
        self.assertEqual(n, 1)
        self.assertIn("id='id_7cgqkgid'", out)
        self.assertIn("idref='id_7cgqkgid'", out)

    def test_colon_id_renamed(self):
        out, n = fix_manifest_ids('<item id="a:b" href="x"/><itemref idref="a:b"/>')
        self.assertEqual(n, 1)
        self.assertIn('id="id_a_b"', out)
        self.assertIn('idref="id_a_b"', out)

    def test_apostrophe_id_renamed_with_refs(self):
        # The Lisey's Story shape (issue #3, 2026-10-06): calibre ids copied
        # from filenames carry an apostrophe, which is not an XML NameChar.
        # epubcheck's message says "without colons", but the original
        # empty/colon/leading-char check passed these ids wholesale, so a
        # full --all sweep left all 163 RSC-005s standing.
        opf = (
            "<manifest>"
            '<item id="Stephen_King_-_Lisey\'s_story22" href="s22.htm" '
            'media-type="application/xhtml+xml"/>'
            "</manifest>"
            '<spine><itemref idref="Stephen_King_-_Lisey\'s_story22"/></spine>'
        )
        out, n = fix_manifest_ids(opf)
        self.assertEqual(n, 1)
        self.assertIn('id="id_Stephen_King_-_Lisey_s_story22"', out)
        self.assertIn('idref="id_Stephen_King_-_Lisey_s_story22"', out)

    def test_space_id_renamed(self):
        out, n = fix_manifest_ids('<item id="a b" href="x"/><itemref idref="a b"/>')
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
            report = repair_epub(src, dst, RepairFlags(fix_ids=True))
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
            report = repair_epub(src, dst, RepairFlags(page_map=True))
            self.assertEqual(report.fixes.get("page_map_stripped"), 1)
            self.assertEqual(report.fixes.get("pagelist_class_added"), 1)
            with zipfile.ZipFile(dst) as z:
                opf_out = z.read("OEBPS/content.opf").decode()
                ncx_out = z.read("OEBPS/toc.ncx").decode()
        self.assertNotIn("page-map", opf_out)
        self.assertIn('<pageList class="pages">', ncx_out)


class TestStripEpub3Attrs(unittest.TestCase):
    """The attribute half of the 2026-09-02 RSC-005 verdict: scrub the
    EPUB3-only attributes epubcheck rejects on an EPUB2 package."""

    def test_the_three_named_attributes_go(self):
        text = (
            '<package page-progression-direction="ltr"/>'
            '<body epub:type="chapter" aria-label="chapter">'
            "<p>text</p></body>"
        )
        out, n = strip_epub3_attributes(text)
        self.assertEqual(n, 3)
        self.assertEqual(out, "<package/><body><p>text</p></body>")

    def test_single_quoted_values(self):
        out, n = strip_epub3_attributes("<body epub:type='chapter'>x</body>")
        self.assertEqual((out, n), ("<body>x</body>", 1))

    def test_reader_legitimate_attributes_survive(self):
        # The fixed set must never sweep up lookalikes: bare `type`, the
        # wider aria family (aria-labelledby is not in the set; aria-hidden
        # JOINED it in issue #11 after the census found it rejected on EPUB2
        # packages), and a spine's own (EPUB3-valid) direction attr name
        # only matched in full.
        text = '<input type="text"><section aria-labelledby="t">x</section>'
        out, n = strip_epub3_attributes(text)
        self.assertEqual((out, n), (text, 0))

    def test_aria_hidden_and_role_now_stripped(self):
        # issue #11: the dominant EPUB3-semantics-on-legacy-package family
        text = '<span role="doc-pagebreak" aria-hidden="true">x</span>'
        out, n = strip_epub3_attributes(text)
        self.assertEqual((out, n), ("<span>x</span>", 2))

    def test_prose_mentioning_the_attributes_survives(self):
        # reported 2026-09-08: the unanchored regex deleted visible prose like
        # `Use epub:type="chapter" here` down to `Use here`, contradicting the
        # fix's own docstring. Only real start tags are edited.
        text = '<p>Use epub:type="chapter" here, readers.</p>'
        out, n = strip_epub3_attributes(text)
        self.assertEqual((out, n), (text, 0))

    def test_cdata_and_comments_are_protected(self):
        text = '<p><![CDATA[if (x) epub:type="chapter"]]></p><!-- aria-label="old" -->'
        out, n = strip_epub3_attributes(text)
        self.assertEqual((out, n), (text, 0))

    def test_multi_line_start_tag_is_still_scrubbed(self):
        text = '<section\n  epub:type="chapter"\n  aria-label="c">\n<p>x</p></section>'
        out, n = strip_epub3_attributes(text)
        self.assertEqual(n, 2)
        self.assertNotIn("epub:type", out)
        self.assertIn("<p>x</p>", out)


class TestPackageVersion(unittest.TestCase):
    """The EPUB2-targeted fixes are licensed by the package version."""

    def test_parses_major(self):
        self.assertEqual(package_version('<package version="2.0"/>'), 2)
        self.assertEqual(package_version("<package version='3.0.1'/>"), 3)
        self.assertEqual(package_version('<package version="1.2"/>'), 1)

    def test_unknown_is_none(self):
        self.assertIsNone(package_version(None))
        self.assertIsNone(package_version("<package/>"))
        self.assertIsNone(package_version('<package version="x.y"/>'))

    def test_opt_in_via_repair_epub(self):
        # the package declares EPUB 2: that declaration is what licenses the
        # scrub (an EPUB3 package keeps its legal attributes; see the
        # version-gate test below)
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="bookid" '
            'page-progression-direction="ltr">'
            "<metadata/><spine/></package>"
        )
        content = (
            '<?xml version="1.0"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml">'
            '<body epub:type="chapter" aria-label="c"><p>x</p></body></html>'
        )
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("OEBPS/content.opf", opf)
                z.writestr("OEBPS/c1.xhtml", content)
            report = repair_epub(src, dst)  # off by default
            self.assertNotIn("epub3_attrs_stripped", report.fixes)
            report = repair_epub(src, dst, RepairFlags(strip_epub3_attrs=True))
            self.assertEqual(report.fixes.get("epub3_attrs_stripped"), 3)
            with zipfile.ZipFile(dst) as z:
                opf_out = z.read("OEBPS/content.opf").decode()
                c_out = z.read("OEBPS/c1.xhtml").decode()
        self.assertNotIn("page-progression-direction", opf_out)
        self.assertNotIn("epub:type", c_out)
        self.assertIn("<p>x</p>", c_out)

    def test_epub3_package_is_not_scrubbed(self):
        # reported 2026-09-08: the EPUB2-targeted fixes fired on every book
        # under --all, stripping a legal epub:type="toc" off an EPUB3 nav and
        # silently removing a legal epub:type="chapter". The package version
        # gates them: EPUB 3 keeps its attributes.
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" '
            'version="3.0" unique-identifier="bookid">'
            "<metadata/><spine/></package>"
        )
        content = (
            '<?xml version="1.0"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml">'
            '<body epub:type="chapter" aria-label="c"><p>x</p></body></html>'
        )
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("OEBPS/content.opf", opf)
                z.writestr("OEBPS/c1.xhtml", content)
            report = repair_epub(src, dst, RepairFlags(strip_epub3_attrs=True))
            self.assertNotIn("epub3_attrs_stripped", report.fixes)
            with zipfile.ZipFile(dst) as z:
                c_out = z.read("OEBPS/c1.xhtml").decode()
        self.assertIn('epub:type="chapter"', c_out)
        self.assertIn('aria-label="c"', c_out)

    def test_unknown_package_version_licenses_nothing(self):
        # a missing or unparseable package version leaves the EPUB2-targeted
        # fixes unlicensed: EPUB2-ness must be proven, not assumed
        for opf in (
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf">'
            "<metadata/><spine/></package>",
            '<?xml version="1.0"?><package version="x.y"><metadata/></package>',
        ):
            with tempfile.TemporaryDirectory() as td:
                src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
                with zipfile.ZipFile(src, "w") as z:
                    z.writestr("mimetype", "application/epub+zip")
                    z.writestr("OEBPS/content.opf", opf)
                    z.writestr(
                        "OEBPS/c1.xhtml",
                        '<html xmlns="http://www.w3.org/1999/xhtml">'
                        '<body epub:type="chapter"><p>x</p></body></html>',
                    )
                report = repair_epub(src, dst, RepairFlags(strip_epub3_attrs=True))
                self.assertNotIn("epub3_attrs_stripped", report.fixes)


class TestVersionGateKeepsFlagsIntact(unittest.TestCase):
    """The package-version gate copies the flags (dataclasses.replace)
    instead of mutating them: one EPUB3 book in a sweep must not switch
    the EPUB2-targeted fixes off for every later book. The in-place
    mutation regression class of 2026-09-08, pinned on the caller's
    object because no report key can see it. Also pins both gated fields
    decoupling: a regression that neutralizes only one of the pair must
    still fail here (the downgrade half on an EPUB3 package)."""

    EPUB3_OPF = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" '
        'version="3.0" unique-identifier="bookid">'
        "<metadata/><spine/></package>"
    )
    EPUB3_CONTENT = (
        '<?xml version="1.0"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        '<body epub:type="chapter" aria-label="c">'
        "<figure><figcaption>cap</figcaption></figure><p>x</p></body></html>"
    )

    def _repair(self, dst):
        src = dst.with_name("in.epub")
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("OEBPS/content.opf", self.EPUB3_OPF)
            z.writestr("OEBPS/c1.xhtml", self.EPUB3_CONTENT)
        return src

    def test_gate_does_not_mutate_the_callers_flags(self):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "out.epub"
            src = self._repair(dst)
            flags = RepairFlags(strip_epub3_attrs=True, downgrade_epub3=True)
            repair_epub(src, dst, flags)
        self.assertTrue(flags.strip_epub3_attrs, "gate mutated the caller's flags")
        self.assertTrue(flags.downgrade_epub3, "gate mutated the caller's flags")

    def test_both_epub2_fixes_stay_inert_on_an_epub3_package(self):
        # the replace() line neutralizes the pair together; a regression
        # that decouples them (one field replaced, one mutated to respect
        # the caller's True) re-enables the mutated half on EPUB3 books
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "out.epub"
            src = self._repair(dst)
            report = repair_epub(
                src, dst, RepairFlags(strip_epub3_attrs=True, downgrade_epub3=True)
            )
            self.assertNotIn("epub3_attrs_stripped", report.fixes)
            self.assertNotIn("epub3_tags_downgraded", report.fixes)
            with zipfile.ZipFile(dst) as z:
                c_out = z.read("OEBPS/c1.xhtml").decode()
        self.assertIn('epub:type="chapter"', c_out)
        self.assertIn("<figure>", c_out)


class TestDowngradeEpub3Tags(unittest.TestCase):
    """The element half of the RSC-005 verdict: EPUB3 semantic elements
    downgrade to their EPUB2 equivalents, classes kept, CSS-protected names
    untouched."""

    def test_figure_and_section_become_divs(self):
        text = "<figure>x</figure><section>y</section>"
        out, n = downgrade_epub3_tags(text)
        self.assertEqual(n, 4)
        self.assertEqual(out, '<div class="figure">x</div><div class="section">y</div>')

    def test_figcaption_becomes_a_paragraph(self):
        out, n = downgrade_epub3_tags("<figcaption>cap</figcaption>")
        self.assertEqual((out, n), ('<p class="figcaption">cap</p>', 2))

    def test_existing_classes_are_kept_and_extended(self):
        text = '<figure class="photo wide" id="f1">x</figure>'
        out, _ = downgrade_epub3_tags(text)
        self.assertEqual(out, '<div class="photo wide figure" id="f1">x</div>')
        out, _ = downgrade_epub3_tags("<figure class='one'>x</figure>")
        self.assertEqual(out, "<div class='one figure'>x</div>")

    def test_self_closing_form(self):
        out, n = downgrade_epub3_tags("<figure/>")
        self.assertEqual((out, n), ('<div class="figure"/>', 1))

    def test_css_protected_names_untouched(self):
        text = "<figure>x</figure><section>y</section>"
        out, n = downgrade_epub3_tags(text, protected_tags=frozenset({"figure"}))
        self.assertEqual(n, 2)
        self.assertIn("<figure>x</figure>", out)
        self.assertIn('<div class="section">y</div>', out)

    def test_integration_with_and_without_css_protection(self):
        content = (
            '<?xml version="1.0"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
            "<body><figure>cap</figure></body></html>"
        )
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" '
            'version="2.0" unique-identifier="b"><metadata/><spine/></package>'
        )
        for sheet, expect_downgraded in ((None, True), ("figure { margin: 0 }", False)):
            with tempfile.TemporaryDirectory() as td:
                src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
                with zipfile.ZipFile(src, "w") as z:
                    z.writestr("mimetype", "application/epub+zip")
                    z.writestr("OEBPS/content.opf", opf)
                    z.writestr("OEBPS/c1.xhtml", content)
                    if sheet:
                        z.writestr("OEBPS/s.css", sheet)
                report = repair_epub(src, dst, RepairFlags(downgrade_epub3=True))
                with zipfile.ZipFile(dst) as z:
                    out = z.read("OEBPS/c1.xhtml").decode()
            if expect_downgraded:
                self.assertEqual(report.fixes.get("epub3_tags_downgraded"), 2)
                self.assertIn('<div class="figure">', out)
            else:
                # the book styles `figure` as an element selector: preservation
                # wins and the RSC-005 findings stay, which is the honest outcome
                self.assertNotIn("epub3_tags_downgraded", report.fixes)
                self.assertIn("<figure>cap</figure>", out)


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
        # a mimetype fix IS a change: files_changed must say so (reported
        # 2026-09-08: it landed in report.fixes without incrementing). The
        # CONTENT fixture's self-close fix accounts for the other file.
        self.assertEqual(report.files_changed, 2)

    def test_padded_mimetype_normalized(self):
        report, _, data = self._repair("application/epub+zip\n")
        self.assertEqual(report.fixes.get("mimetype_normalized"), 1)
        self.assertEqual(data, b"application/epub+zip")

    def test_archive_comment_is_carried_over(self):
        # reported 2026-09-08: the rewrite dropped the archive comment field
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
                z.comment = b"converted by a chatty tool"
                z.writestr("mimetype", b"application/epub+zip")
                z.writestr("OEBPS/c1.xhtml", CONTENT)
            repair_epub(src, dst)
            with zipfile.ZipFile(dst) as z:
                self.assertEqual(z.comment, b"converted by a chatty tool")

    def test_encrypted_ncx_is_not_a_crash(self):
        # reported 2026-09-08: --only ncx probed outside the exception net,
        # and z.read on a zip-encrypted entry raises RuntimeError, which
        # crashed the whole selection pass
        encrypted = zipfile.ZipInfo("OEBPS/toc.ncx")
        encrypted.flag_bits |= 0x1  # the "encrypted" bit; no data follows
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "drm.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mimetype", b"application/epub+zip")
                z.writestr(encrypted, b"\x00\x01")  # unreadable without a password
            self.assertFalse(ncx_uid_mismatch(src))

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
                repair_epub(src, d, RepairFlags(fix_ids=True))
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
            report = repair_epub(src, dst, RepairFlags(escape_entities=True))
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
            RepairFlags(
                missing_title=True,
                id_colons=True,
                block_in_inline=True,
                invalid_value=True,
                illegal_tags=True,
            ),
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
        repair_epub(self.src, self.dst, RepairFlags(illegal_tags=True))
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
        repair_epub(self.src, self.dst, RepairFlags(illegal_tags=True))
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
        report = repair_epub(self.src, self.dst, RepairFlags(empty_body=True))
        self.assertIn("fix_empty_body", report.fixes)


class TestPruneMissingResources(unittest.TestCase):
    """--prune-missing-resources: references to files the archive does not
    contain are removed (RSC-007/PKG-010); everything present survives, and
    spine documents are never pruned."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / "in.epub"
        self.dst = Path(self.tmp.name) / "out.epub"

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self):
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" '
            'unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="bookid">urn:uuid:U</dc:identifier>'
            "</metadata>"
            "<manifest>"
            '<item href="c1.xhtml" id="c1" media-type="application/xhtml+xml"/>'
            '<item href="style.css" id="css" media-type="text/css"/>'
            '<item href="fonts/gone.ttf" id="f1" media-type="font/ttf"/>'
            '<item href="gone_spine.xhtml" id="sp" '
            'media-type="application/xhtml+xml"/>'
            "</manifest>"
            '<spine><itemref idref="c1"/><itemref idref="sp"/></spine>'
            "</package>"
        )
        content = (
            '<?xml version="1.0"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
            '<link rel="stylesheet" href="style.css"/>'
            '<link rel="stylesheet" href="../page-template.xpgt"/>'
            "</head><body>"
            '<a href="missing.doc">gone</a>'
            '<a href="c1.xhtml">kept</a>'
            '<img src="present.jpg" alt="p"/>'
            '<img src="gone1.jpg" alt="Figure 1"/>'
            '<img src="gone2.jpg"/>'
            '<img src="gone3.jpg" alt="   "/>'
            "</body></html>"
        )
        with zipfile.ZipFile(self.src, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("OEBPS/c1.xhtml", content)
            z.writestr("OEBPS/style.css", "p { margin: 0 }")
            z.writestr("OEBPS/present.jpg", b"\xff\xd8\xff")
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/toc.ncx", NCX_BAD.replace("THE-WRONG-ID", "U"))

    def test_prunes_only_missing_references(self):
        self._build()
        report = repair_epub(self.src, self.dst, RepairFlags(prune_missing=True))
        self.assertEqual(report.fixes.get("dead_links_pruned"), 1)
        self.assertEqual(report.fixes.get("missing_file_hrefs_stripped"), 1)
        self.assertEqual(report.fixes.get("missing_imgs_unwrapped"), 1)
        self.assertEqual(report.fixes.get("missing_imgs_pruned"), 2)
        self.assertEqual(report.fixes.get("manifest_items_pruned"), 1)
        with zipfile.ZipFile(self.dst) as z:
            c = z.read("OEBPS/c1.xhtml").decode()
            opf = z.read("OEBPS/content.opf").decode()
            self.assertIn("OEBPS/style.css", z.namelist())
        # the dead link element is gone entirely; the live one stays
        self.assertNotIn("page-template.xpgt", c)
        self.assertIn('<link rel="stylesheet" href="style.css"/>', c)
        # the anchor to an absent file loses its href, never its text
        self.assertIn("<a>gone</a>", c)
        self.assertIn('<a href="c1.xhtml">kept</a>', c)
        # an alt-carrying img becomes its text; bare and blank-alt imgs go
        self.assertIn("Figure 1", c)
        self.assertNotIn("gone1.jpg", c)
        self.assertNotIn("gone2.jpg", c)
        self.assertNotIn("gone3.jpg", c)
        self.assertIn('<img src="present.jpg" alt="p"/>', c)
        # orphaned non-spine manifest item pruned; the spine-declared absent
        # item survives (damaged fragments are reported, never pruned)
        self.assertNotIn("fonts/gone.ttf", opf)
        self.assertIn("gone_spine.xhtml", opf)

    def test_opt_in(self):
        self._build()
        report = repair_epub(self.src, self.dst)
        for key in (
            "dead_links_pruned",
            "missing_file_hrefs_stripped",
            "missing_imgs_unwrapped",
            "missing_imgs_pruned",
            "manifest_items_pruned",
        ):
            self.assertNotIn(key, report.fixes)

    def test_idempotent(self):
        self._build()
        repair_epub(self.src, self.dst, RepairFlags(prune_missing=True))
        dst2 = Path(self.tmp.name) / "out2.epub"
        report = repair_epub(self.dst, dst2, RepairFlags(prune_missing=True))
        for key in (
            "dead_links_pruned",
            "missing_file_hrefs_stripped",
            "missing_imgs_unwrapped",
            "missing_imgs_pruned",
            "manifest_items_pruned",
        ):
            self.assertNotIn(key, report.fixes)

    def test_escaped_ampersand_declaration_over_a_raw_entry_survives(self):
        # issue #20 (Designers & Dragons): href="K&amp;C.png" over the entry
        # "K&C.png" resolved to the escaped spelling, read as absent, and the
        # prune deleted a live declaration -- epubcheck answered RSC-008 for
        # every content reference that survived. The resolver decodes the
        # attribute-value entities before the existence check.
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            opf = (
                '<?xml version="1.0"?>'
                '<package xmlns="http://www.idpf.org/2007/opf" '
                'unique-identifier="bookid">'
                '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                '<dc:identifier id="bookid">urn:uuid:U</dc:identifier>'
                "</metadata>"
                "<manifest>"
                '<item href="c1.xhtml" id="c1" '
                'media-type="application/xhtml+xml"/>'
                '<item href="K&amp;C.png" id="pic" media-type="image/png"/>'
                "</manifest>"
                "<spine><itemref idref='c1'/></spine></package>"
            )
            with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("OEBPS/content.opf", opf)
                z.writestr(
                    "OEBPS/c1.xhtml",
                    '<?xml version="1.0"?><html '
                    'xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title>'
                    "</head><body><p>x</p></body></html>",
                )
                z.writestr("OEBPS/K&C.png", b"\x89PNG\r\n\x1a\n")
            report = repair_epub(src, dst, RepairFlags(prune_missing=True))
            self.assertNotIn("manifest_items_pruned", report.fixes)
            with zipfile.ZipFile(dst) as z:
                self.assertIn(
                    "K&amp;C.png", z.read("OEBPS/content.opf").decode("utf-8")
                )


class TestStripBrokenAnchors(unittest.TestCase):
    """--strip-broken-anchors: hrefs to fragments the target does not define and
    to unresolvable URI schemes are stripped; anchor text is byte-identical."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / "in.epub"
        self.dst = Path(self.tmp.name) / "out.epub"

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self):
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" '
            'unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="bookid">urn:uuid:U</dc:identifier>'
            "</metadata>"
            "<manifest>"
            '<item href="a.xhtml" id="a" media-type="application/xhtml+xml"/>'
            '<item href="b.xhtml" id="b" media-type="application/xhtml+xml"/>'
            "</manifest>"
            '<spine><itemref idref="a"/><itemref idref="b"/></spine>'
            "</package>"
        )
        doc_a = (
            '<?xml version="1.0"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title>'
            '</head><body><p id="real">target</p></body></html>'
        )
        doc_b = (
            '<?xml version="1.0"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title>'
            "</head><body>"
            '<a href="a.xhtml#real">ok</a>'
            '<a href="a.xhtml#ghost">cross</a>'
            '<a href="#ghost">local</a>'
            '<a href="kindle:embed:0003?mime=image/jpg">Cover</a>'
            '<a href="https://example.com/x?a=1&amp;b=2">web</a>'
            '<a href="absent.doc#x">dangling</a>'
            "</body></html>"
        )
        ncx = (
            '<?xml version="1.0"?>'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><head>'
            '<meta name="dtb:uid" content="urn:uuid:U"/></head>'
            "<navMap>"
            '<navPoint id="n1" playOrder="1"><navLabel><text>A</text></navLabel>'
            '<content src="a.xhtml#real"/></navPoint>'
            '<navPoint id="n2" playOrder="2"><navLabel><text>B</text></navLabel>'
            '<content src="a.xhtml#ghost"/></navPoint>'
            '<navPoint id="n3" playOrder="3"><navLabel><text>C</text></navLabel>'
            '<content src="absent.xhtml#x"/></navPoint>'
            "</navMap></ncx>"
        )
        with zipfile.ZipFile(self.src, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("OEBPS/a.xhtml", doc_a)
            z.writestr("OEBPS/b.xhtml", doc_b)
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/toc.ncx", ncx)

    def test_strips_only_unresolvable_hrefs(self):
        self._build()
        report = repair_epub(self.src, self.dst, RepairFlags(strip_anchors=True))
        self.assertEqual(report.fixes.get("broken_fragment_hrefs_stripped"), 2)
        self.assertEqual(report.fixes.get("nonfile_scheme_hrefs_stripped"), 1)
        self.assertEqual(report.fixes.get("ncx_fragments_stripped"), 1)
        with zipfile.ZipFile(self.dst) as z:
            b = z.read("OEBPS/b.xhtml").decode()
            ncx = z.read("OEBPS/toc.ncx").decode()
        # dead fragment refs lose the href, keep the anchor text byte-for-byte
        self.assertIn("<a>cross</a>", b)
        self.assertIn("<a>local</a>", b)
        self.assertIn("<a>Cover</a>", b)
        # every resolvable reference survives untouched
        self.assertIn('<a href="a.xhtml#real">ok</a>', b)
        self.assertIn('href="https://example.com/x?a=1&amp;b=2"', b)
        # a wholly absent target document is not ours to touch
        self.assertIn('<a href="absent.doc#x">dangling</a>', b)
        # NCX falls back to the document target; absent targets untouched
        self.assertIn('<content src="a.xhtml"/>', ncx)
        self.assertIn('<content src="absent.xhtml#x"/>', ncx)

    def test_guide_reference_broken_fragment_is_stripped(self):
        # issue #21 (Fools Crow): the guide's undefined fragment hid while the
        # target doc carried its own broken links; once the anchor pass fixed
        # those, epubcheck flagged the package edge. The guide gets the same
        # document-precision fallback as the NCX.
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="bookid">urn:uuid:U</dc:identifier>'
            "</metadata>"
            "<manifest>"
            '<item href="c1.xhtml" id="c1" media-type="application/xhtml+xml"/>'
            "</manifest>"
            '<spine><itemref idref="c1"/></spine>'
            "<guide>"
            '<reference href="c1.xhtml#real" type="toc" title="ToC"/>'
            '<reference href="c1.xhtml#ghost" type="text" title="G"/>'
            "</guide></package>"
        )
        doc = (
            '<?xml version="1.0"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title>'
            '</head><body><p id="real">target</p></body></html>'
        )
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("OEBPS/content.opf", opf)
                z.writestr("OEBPS/c1.xhtml", doc)
            report = repair_epub(src, dst, RepairFlags(strip_anchors=True))
            self.assertEqual(report.fixes.get("guide_fragments_stripped"), 1)
            with zipfile.ZipFile(dst) as z:
                out = z.read("OEBPS/content.opf").decode()
        self.assertIn('<reference href="c1.xhtml#real" type="toc"', out)
        self.assertIn('href="c1.xhtml" type="text"', out)

    def test_guide_fragment_stripped_on_a_renamed_target(self):
        # the review finding on this feature: ids_by_doc is keyed by the
        # RENAMED output spellings, so the guide strip must run after the
        # reference rewrite (pre-order it silently no-op'd on exactly the
        # books that rename)
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="bookid">urn:uuid:U</dc:identifier>'
            "</metadata>"
            "<manifest>"
            '<item href="a b.xhtml" id="a" media-type="application/xhtml+xml"/>'
            "</manifest>"
            '<spine><itemref idref="a"/></spine>'
            '<guide><reference href="a b.xhtml#ghost" type="toc" title="T"/></guide>'
            "</package>"
        )
        doc = (
            '<?xml version="1.0"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title>'
            "</head><body><p>plain</p></body></html>"
        )
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("OEBPS/content.opf", opf)
                z.writestr("OEBPS/a b.xhtml", doc)
            report = repair_epub(
                src, dst, RepairFlags(strip_anchors=True, url_spaces=True)
            )
            self.assertEqual(report.fixes.get("entries_renamed"), 1)
            self.assertEqual(report.fixes.get("guide_fragments_stripped"), 1)
            with zipfile.ZipFile(dst) as z:
                out = z.read("OEBPS/content.opf").decode()
        self.assertIn('<reference href="a_b.xhtml" type="toc"', out)

    def test_playorder_contract_rehealed_after_fragment_collapse(self):
        # issue #21 (Troubled Man): the always-on playOrder pass ran before
        # the anchor pass, so two distinct-fragment navTargets that collapsed
        # onto one document left the NCX violating the same-target-shares-one-
        # value contract. The pass re-runs on the final navMap.
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="bookid">urn:uuid:U</dc:identifier>'
            "</metadata>"
            "<manifest>"
            '<item href="c1.xhtml" id="c1" media-type="application/xhtml+xml"/>'
            "</manifest>"
            '<spine><itemref idref="c1"/></spine></package>'
        )
        ncx = (
            '<?xml version="1.0"?>'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><head>'
            '<meta name="dtb:uid" content="urn:uuid:U"/></head>'
            "<navMap>"
            '<navPoint id="n1" playOrder="1"><navLabel><text>A</text></navLabel>'
            '<content src="c1.xhtml#ghost_a"/></navPoint>'
            '<navPoint id="n2" playOrder="2"><navLabel><text>B</text></navLabel>'
            '<content src="c1.xhtml#ghost_b"/></navPoint>'
            "</navMap></ncx>"
        )
        doc = (
            '<?xml version="1.0"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title>'
            "</head><body><p>plain</p></body></html>"
        )
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("OEBPS/content.opf", opf)
                z.writestr("OEBPS/toc.ncx", ncx)
                z.writestr("OEBPS/c1.xhtml", doc)
            report = repair_epub(src, dst, RepairFlags(strip_anchors=True))
            self.assertEqual(report.fixes.get("ncx_fragments_stripped"), 2)
            with zipfile.ZipFile(dst) as z:
                out = z.read("OEBPS/toc.ncx").decode()
        self.assertEqual(out.count('<content src="c1.xhtml"/>'), 2)
        # one target, one shared playOrder value (the collapsed navPoints
        # re-sequenced onto the same value instead of a contract violation)
        self.assertIn(
            'playOrder="1"><navLabel><text>A</text></navLabel>'
            '<content src="c1.xhtml"/>',
            out,
        )
        self.assertIn(
            'playOrder="1"><navLabel><text>B</text></navLabel>'
            '<content src="c1.xhtml"/>',
            out,
        )

    def test_anchor_text_conserved(self):
        self._build()
        import re as _re

        with zipfile.ZipFile(self.src) as z:
            before = _re.sub(r"<[^>]+>", "", z.read("OEBPS/b.xhtml").decode())
        repair_epub(self.src, self.dst, RepairFlags(strip_anchors=True))
        with zipfile.ZipFile(self.dst) as z:
            after = _re.sub(r"<[^>]+>", "", z.read("OEBPS/b.xhtml").decode())
        self.assertEqual(before, after)

    def test_opt_in(self):
        self._build()
        report = repair_epub(self.src, self.dst)
        for key in (
            "broken_fragment_hrefs_stripped",
            "nonfile_scheme_hrefs_stripped",
            "ncx_fragments_stripped",
        ):
            self.assertNotIn(key, report.fixes)

    def test_non_utf8_document_is_skipped_not_mojibake(self):
        # reported 2026-09-08: a fix firing on a windows-1252 document decoded
        # with U+FFFD substitutes and re-encoded UTF-8 under a declaration
        # that still named the old encoding. The entry must be copied
        # byte-for-byte and reported for manual repair instead.
        cp1252_doc = (
            '<?xml version="1.0" encoding="windows-1252"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head></head>'
            "<body><p>caf\xe9 r\xe9sum\xe9</p>"
            "<span><p>inner</p></span></body></html>"
        ).encode("windows-1252")
        utf8_doc = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head></head>'
            "<body><p>café</p>"
            "<span><p>inner</p></span></body></html>"
        ).encode()
        with zipfile.ZipFile(self.src, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("OEBPS/bad.xhtml", cp1252_doc)
            z.writestr("OEBPS/good.xhtml", utf8_doc)
        report = repair_epub(self.src, self.dst, RepairFlags(block_in_inline=True))
        self.assertEqual(report.fixes.get("non_utf8_docs_skipped"), 1)
        with zipfile.ZipFile(self.dst) as z:
            self.assertEqual(z.read("OEBPS/bad.xhtml"), cp1252_doc)
            good = z.read("OEBPS/good.xhtml").decode("utf-8")
        # the UTF-8 sibling is still repaired
        self.assertNotIn("<span><p>", good)
        self.assertEqual(report.fixes.get("unwrap_block_in_inline"), 1)

    def test_snapshot_follows_id_deleting_fixes(self):
        # reported 2026-09-08: the id snapshot predated the unwrap fixes, so an
        # id an unwrap deleted still counted as present and fragments pointing
        # at it survived as dangling references. The anchor pass now runs last,
        # and the snapshot replicates every id-moving fix ahead of it.
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" '
            'unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="bookid">urn:uuid:U</dc:identifier>'
            "</metadata>"
            "<manifest>"
            '<item href="a.xhtml" id="a" media-type="application/xhtml+xml"/>'
            "</manifest>"
            '<spine><itemref idref="a"/></spine>'
            "</package>"
        )
        doc = (
            '<?xml version="1.0"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title>'
            "</head><body>"
            '<p><pagebreak id="pb:1"/>text</p>'
            '<a href="#pb:1">jump</a>'
            "</body></html>"
        )
        with zipfile.ZipFile(self.src, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("OEBPS/a.xhtml", doc)
            z.writestr("OEBPS/content.opf", opf)
        report = repair_epub(
            self.src, self.dst, RepairFlags(strip_anchors=True, illegal_tags=True)
        )
        self.assertEqual(report.fixes.get("unwrap_illegal_tags"), 1)
        self.assertEqual(report.fixes.get("broken_fragment_hrefs_stripped"), 1)
        with zipfile.ZipFile(self.dst) as z:
            out = z.read("OEBPS/a.xhtml").decode()
        self.assertNotIn("pagebreak", out)
        self.assertIn("<a>jump</a>", out)


class TestEncodeUrlSpacesEpub(unittest.TestCase):
    """--encode-url-spaces: raw spaces in src/href attribute values are
    percent-encoded across the package, and entries whose names carry raw
    spaces are renamed to their underscore spellings with every reference
    rewritten (v0.44.0; refs to entries that refused the rename still take
    the percent-encode, and refs to absent files have nothing to rename
    so they encode as before)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / "in.epub"
        self.dst = Path(self.tmp.name) / "out.epub"

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self):
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" '
            'unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="bookid">urn:uuid:U</dc:identifier>'
            "</metadata>"
            "<manifest>"
            '<item href="a b.xhtml" id="a" media-type="application/xhtml+xml"/>'
            "</manifest>"
            '<spine><itemref idref="a"/></spine>'
            "</package>"
        )
        ncx = (
            '<?xml version="1.0"?>'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><head>'
            '<meta name="dtb:uid" content="urn:uuid:U"/></head>'
            '<navMap><navPoint id="n1" playOrder="1">'
            "<navLabel><text>A</text></navLabel>"
            '<content src="a b.xhtml"/></navPoint></navMap></ncx>'
        )
        content = (
            '<?xml version="1.0"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title>'
            '</head><body><a href="a b.xhtml">x</a>'
            '<img src="i 1.jpg" alt="i"/></body></html>'
        )
        with zipfile.ZipFile(self.src, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("OEBPS/a b.xhtml", content)
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/toc.ncx", ncx)

    def test_spaces_encoded_everywhere(self):
        self._build()
        report = repair_epub(self.src, self.dst, RepairFlags(url_spaces=True))
        # The existing entry renames and its three references rewrite;
        # the absent "i 1.jpg" has no entry to rename, so its reference
        # takes the percent-encode as always.
        self.assertEqual(report.fixes.get("entries_renamed"), 1)
        self.assertEqual(report.fixes.get("renamed_refs_rewritten"), 3)
        self.assertEqual(report.fixes.get("url_spaces_encoded"), 1)
        with zipfile.ZipFile(self.dst) as z:
            self.assertIn("OEBPS/a_b.xhtml", z.namelist())
            self.assertNotIn("OEBPS/a b.xhtml", z.namelist())
            opf = z.read("OEBPS/content.opf").decode()
            ncx = z.read("OEBPS/toc.ncx").decode()
            c = z.read("OEBPS/a_b.xhtml").decode()
        self.assertIn('href="a_b.xhtml"', opf)
        self.assertIn('src="a_b.xhtml"', ncx)
        self.assertIn('href="a_b.xhtml"', c)
        self.assertIn('src="i%201.jpg"', c)

    def test_idempotent(self):
        self._build()
        repair_epub(self.src, self.dst, RepairFlags(url_spaces=True))
        dst2 = Path(self.tmp.name) / "out2.epub"
        report = repair_epub(self.dst, dst2, RepairFlags(url_spaces=True))
        self.assertNotIn("url_spaces_encoded", report.fixes)

    def test_opt_in(self):
        self._build()
        report = repair_epub(self.src, self.dst)
        self.assertNotIn("url_spaces_encoded", report.fixes)

    def test_renamed_ref_brackets_are_percent_encoded(self):
        # issue #19: the renamed spelling can still carry '[' / ']'; epubcheck
        # refuses them in a URL path (RSC-020 'Illegal character in path
        # segment'), so the raw form re-flagged every rewritten reference and
        # five books' error counts multiplied. The encoded form decodes back
        # to the raw-spelled entry before lookup.
        with zipfile.ZipFile(self.src, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr(
                "OEBPS/content.opf",
                '<?xml version="1.0"?>'
                '<package xmlns="http://www.idpf.org/2007/opf" '
                'unique-identifier="bookid">'
                '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                '<dc:identifier id="bookid">urn:uuid:U</dc:identifier>'
                "</metadata>"
                "<manifest>"
                '<item href="pic [1].jpg" id="p" media-type="image/jpeg"/>'
                "</manifest>"
                '<spine><itemref idref="p"/></spine>'
                "</package>",
            )
            z.writestr("OEBPS/pic [1].jpg", b"\xff\xd8\xffBINARY")
        report = repair_epub(self.src, self.dst, RepairFlags(url_spaces=True))
        self.assertEqual(report.fixes.get("entries_renamed"), 1)
        self.assertEqual(report.fixes.get("renamed_refs_rewritten"), 1)
        with zipfile.ZipFile(self.dst) as z:
            self.assertIn("OEBPS/pic_[1].jpg", z.namelist())
            self.assertIn(
                'href="pic_%5B1%5D.jpg"',
                z.read("OEBPS/content.opf").decode("utf-8"),
            )


class TestFixUniqueIdentifier(unittest.TestCase):
    """The OPF-030 wiring repair (v0.56.0): add the id the package names to
    the first dc:identifier when no element carries it; every ambiguous
    shape refuses."""

    OPF_HEAD = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
        'unique-identifier="{uid}">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
    )

    def _opf(self, uid: str, idents: str) -> str:
        return self.OPF_HEAD.format(uid=uid) + idents + "</metadata></package>"

    def test_wires_the_first_identifier(self):
        opf = self._opf(
            "bookid",
            "<dc:identifier>urn:uuid:X</dc:identifier>"
            "<dc:identifier>urn:uuid:Y</dc:identifier>",
        )
        out, n = fix_unique_identifier_wiring(opf)
        self.assertEqual(n, 1)
        self.assertIn('<dc:identifier id="bookid">', out)
        self.assertIn("<dc:identifier>urn:uuid:Y</dc:identifier>", out)
        ET.fromstring(out)

    def test_existing_attributes_are_kept(self):
        opf = self._opf(
            "bookid", '<dc:identifier opf:scheme="ISBN">978</dc:identifier>'
        )
        out, n = fix_unique_identifier_wiring(opf)
        self.assertEqual(n, 1)
        self.assertIn('<dc:identifier id="bookid" opf:scheme="ISBN">', out)

    def test_empty_attribute_refuses(self):
        opf = self._opf("", "<dc:identifier>urn:uuid:X</dc:identifier>").replace(
            'unique-identifier=""', ""
        )
        out, n = fix_unique_identifier_wiring(opf)
        self.assertEqual((out, n), (opf, 0))

    def test_invalid_ncname_refuses(self):
        opf = self._opf("1bad", "<dc:identifier>urn:uuid:X</dc:identifier>")
        out, n = fix_unique_identifier_wiring(opf)
        self.assertEqual((out, n), (opf, 0))

    def test_already_wired_refuses(self):
        opf = self._opf(
            "bookid", '<dc:identifier id="other">urn:uuid:X</dc:identifier>'
        )
        # uid names "bookid", the first identifier carries a DIFFERENT id:
        # wiring onto a later element is a choice, refused
        out, n = fix_unique_identifier_wiring(opf)
        self.assertEqual((out, n), (opf, 0))

    def test_uid_carried_anywhere_refuses(self):
        opf = self._opf(
            "bookid",
            "<dc:identifier>urn:uuid:X</dc:identifier>"
            '<dc:identifier id="bookid">urn:uuid:Y</dc:identifier>',
        )
        out, n = fix_unique_identifier_wiring(opf)
        self.assertEqual((out, n), (opf, 0))

    def test_no_identifier_refuses(self):
        opf = self._opf("bookid", "<dc:title>t</dc:title>")
        out, n = fix_unique_identifier_wiring(opf)
        self.assertEqual((out, n), (opf, 0))

    def test_repair_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr(
                    "OEBPS/content.opf",
                    self._opf("bookid", "<dc:identifier>urn:uuid:X</dc:identifier>"),
                )
                z.writestr(
                    "OEBPS/c1.xhtml",
                    '<?xml version="1.0"?><html '
                    'xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title>'
                    "</head><body><p>x</p></body></html>",
                )
            report = repair_epub(src, dst, flags=RepairFlags(unique_identifier=True))
            self.assertEqual(report.fixes.get("unique_identifier_wired"), 1)
            with zipfile.ZipFile(dst) as z:
                self.assertIn(
                    '<dc:identifier id="bookid">',
                    z.read("OEBPS/content.opf").decode("utf-8"),
                )

    def test_repair_also_syncs_the_masked_ncx(self):
        # The eagle shape: the dangling uid wiring MASKS the NCX-001 check
        # (epubcheck cannot compare dtb:uid to an unresolvable identifier).
        # One run must wire the OPF AND align the NCX to the now-resolvable
        # uid, or the fix trades OPF-030 for a surfaced NCX-001.
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr(
                    "OEBPS/content.opf",
                    self._opf("bookid", "<dc:identifier>urn:uuid:X</dc:identifier>"),
                )
                z.writestr("OEBPS/toc.ncx", NCX_BAD)
                z.writestr(
                    "OEBPS/c1.xhtml",
                    '<?xml version="1.0"?><html '
                    'xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title>'
                    "</head><body><p>x</p></body></html>",
                )
            report = repair_epub(src, dst, flags=RepairFlags(unique_identifier=True))
            self.assertEqual(report.fixes.get("unique_identifier_wired"), 1)
            self.assertTrue(report.ncx_uid_synced)
            with zipfile.ZipFile(dst) as z:
                ncx = z.read("OEBPS/toc.ncx").decode("utf-8")
            self.assertIn('content="urn:uuid:X"', ncx)


class TestDeclareMissingItems(unittest.TestCase):
    """The RSC-008 repair (issue #16): declare present-but-undeclared
    resources the book references; never touch the spine."""

    OPF = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
        'unique-identifier="id"><metadata/>'
        '<manifest><item id="c1" href="c1.xhtml" '
        'media-type="application/xhtml+xml"/></manifest>'
        "<spine><itemref idref='c1'/></spine></package>"
    )

    def test_declare_adds_sorted_deterministic_items(self):
        targets = frozenset({"OEBPS/images/b.png", "OEBPS/images/a.png"})
        out, n = declare_missing_manifest_items(self.OPF, "OEBPS", targets)
        self.assertEqual(n, 2)
        a = out.index('id="bindery-d1" href="images/a.png"')
        b = out.index('id="bindery-d2" href="images/b.png"')
        self.assertLess(a, b)
        self.assertIn('media-type="image/png"', out)
        ET.fromstring(out)

    def test_declare_skips_unknowable_media_types(self):
        out, n = declare_missing_manifest_items(
            self.OPF, "OEBPS", frozenset({"OEBPS/data.blob"})
        )
        self.assertEqual((out, n), (self.OPF, 0))

    def test_declare_id_collision_gets_prefixed(self):
        opf = self.OPF.replace(
            "</manifest>",
            '<item id="bindery-d1" href="x.png" media-type="image/png"/></manifest>',
        )
        out, n = declare_missing_manifest_items(
            opf, "OEBPS", frozenset({"OEBPS/y.png"})
        )
        self.assertEqual(n, 1)
        self.assertIn('id="_bindery-d1" href="y.png"', out)

    def test_declare_without_manifest_is_a_noop(self):
        out, n = declare_missing_manifest_items(
            "<package/>", "OEBPS", frozenset({"OEBPS/y.png"})
        )
        self.assertEqual((out, n), ("<package/>", 0))

    def test_collect_finds_img_link_css_and_guide_targets(self):
        entries = {
            "OEBPS/c1.xhtml": (
                '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
                "<head><title>t</title>"
                '<link rel="stylesheet" href="style.css"/>'
                "<style>p { background: url(bg.png); }</style></head>"
                "<body><p>x</p><img src='images/i.png'/></body></html>"
            ),
            "OEBPS/content.opf": self.OPF,
            "OEBPS/style.css": "p { list-style-image: url(../OEBPS/dot.gif); }",
            "OEBPS/images/i.png": b"\x89PNG\r\n\x1a\n",
            "OEBPS/bg.png": b"\x89PNG\r\n\x1a\n",
            "OEBPS/dot.gif": b"GIF89a",
        }
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            with zipfile.ZipFile(src, "w") as z:
                for name, data in entries.items():
                    z.writestr(name, data)
            with zipfile.ZipFile(src) as z:
                opf_text = z.read("OEBPS/content.opf").decode()
                got = collect_undeclared_resources(
                    z, opf_text, "OEBPS", frozenset(z.namelist())
                )
        self.assertEqual(
            got,
            frozenset(
                {
                    "OEBPS/images/i.png",
                    "OEBPS/bg.png",
                    "OEBPS/style.css",
                    "OEBPS/dot.gif",
                }
            ),
        )

    def test_collect_skips_schemes_fragments_spaces_and_declared(self):
        entries = {
            "OEBPS/c1.xhtml": (
                '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
                "<head><title>t</title></head><body>"
                '<a href="https://x/y.png">e</a>'
                '<a href="c1.xhtml#frag">f</a>'
                '<img src="has space.png"/>'
                '<img src="c1_x.png"/>'
                "</body></html>"
            ),
            "OEBPS/content.opf": self.OPF.replace(
                'media-type="application/xhtml+xml"/>',
                'media-type="application/xhtml+xml"/>'
                '<item id="c1_x" href="c1_x.png" media-type="image/png"/>',
            ),
            "OEBPS/c1_x.png": b"\x89PNG\r\n\x1a\n",
            "OEBPS/has space.png": b"\x89PNG\r\n\x1a\n",
        }
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            with zipfile.ZipFile(src, "w") as z:
                for name, data in entries.items():
                    z.writestr(name, data)
            with zipfile.ZipFile(src) as z:
                opf_text = z.read("OEBPS/content.opf").decode()
                got = collect_undeclared_resources(
                    z, opf_text, "OEBPS", frozenset(z.namelist())
                )
        self.assertEqual(got, frozenset())

    def test_repair_end_to_end_declares_the_image(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            dst = Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("OEBPS/content.opf", self.OPF)
                z.writestr(
                    "OEBPS/c1.xhtml",
                    '<?xml version="1.0"?><html '
                    'xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title>'
                    "</head><body><p>x</p><img src='images/i.png'/></body></html>",
                )
                z.writestr("OEBPS/images/i.png", b"\x89PNG\r\n\x1a\n")
            report = repair_epub(src, dst, flags=RepairFlags(declare_missing=True))
            self.assertEqual(report.fixes.get("manifest_items_declared"), 1)
            with zipfile.ZipFile(dst) as z:
                opf_out = z.read("OEBPS/content.opf").decode()
            self.assertIn('href="images/i.png"', opf_out)
            self.assertIn('media-type="image/png"', opf_out)

    def test_prefixed_manifest_items_count_as_declared(self):
        # issue #20 (Roadside Picnic): a Sony-style OPF writes <opf:item>;
        # the census matched only bare <item>, saw nothing declared, and the
        # declarer duplicated every declaration (one OPF-074 per added item).
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
            'unique-identifier="id"><metadata/>'
            '<manifest xmlns:opf="http://www.idpf.org/2007/opf">'
            '<opf:item id="c1" href="c1.xhtml" '
            'media-type="application/xhtml+xml"/>'
            '<opf:item id="pic" href="images/i.png" media-type="image/png"/>'
            "</manifest>"
            "<spine><itemref idref='c1'/></spine></package>"
        )
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr(
                    "OEBPS/c1.xhtml",
                    '<?xml version="1.0"?><html '
                    'xmlns="http://www.w3.org/1999/xhtml">'
                    "<body><img src='images/i.png'/></body></html>",
                )
                z.writestr("OEBPS/content.opf", opf)
                z.writestr("OEBPS/images/i.png", b"\x89PNG\r\n\x1a\n")
            with zipfile.ZipFile(src) as z:
                got = collect_undeclared_resources(
                    z, opf, "OEBPS", frozenset(z.namelist())
                )
        self.assertEqual(got, frozenset())

    def test_escaped_ampersand_href_counts_as_declared(self):
        # issue #20 (Designers & Dragons): entry names carry raw '&' while
        # the manifest spells it &amp;. The prune read the escaped spelling
        # as an absent file (deleting live declarations; the declarer then
        # re-added the same files from the raw-& content references, and the
        # two flags swapped the items inside one run). The census resolves
        # the raw-& reference against the &amp;-spelled declaration.
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
            'unique-identifier="id"><metadata/>'
            "<manifest>"
            '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="pic" href="K&amp;C.png" media-type="image/png"/>'
            "</manifest>"
            "<spine><itemref idref='c1'/></spine></package>"
        )
        # the content reference carries the RAW ampersand (what a recovering
        # producer writes and what epubcheck's lenient reader accepts)
        content = (
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
            "<body><img src='K&C.png'/></body></html>"
        )
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("OEBPS/c1.xhtml", content)
                z.writestr("OEBPS/content.opf", opf)
                z.writestr("OEBPS/K&C.png", b"\x89PNG\r\n\x1a\n")
            with zipfile.ZipFile(src) as z:
                got = collect_undeclared_resources(
                    z, opf, "OEBPS", frozenset(z.namelist())
                )
        self.assertEqual(got, frozenset())

    def test_numeric_character_reference_href_counts_as_declared(self):
        # issue #20's resolver decode covers numeric refs too (every XML
        # parser decodes them in attribute values): href="K&#38;C.png" names
        # the entry K&C.png
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
            'unique-identifier="id"><metadata/>'
            "<manifest>"
            '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="pic" href="K&#38;C.png" media-type="image/png"/>'
            "</manifest>"
            "<spine><itemref idref='c1'/></spine></package>"
        )
        content = (
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
            "<body><img src='K&C.png'/></body></html>"
        )
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("OEBPS/c1.xhtml", content)
                z.writestr("OEBPS/content.opf", opf)
                z.writestr("OEBPS/K&C.png", b"\x89PNG\r\n\x1a\n")
            with zipfile.ZipFile(src) as z:
                got = collect_undeclared_resources(
                    z, opf, "OEBPS", frozenset(z.namelist())
                )
        self.assertEqual(got, frozenset())


if __name__ == "__main__":
    unittest.main()


class TestFixContainer(unittest.TestCase):
    """Phase 16 B1: generate META-INF/container.xml at the located OPF. The
    gateway repair: a missing or stale container is an epubcheck fatal that
    locks the whole book out of every shipped repair, and the locator's
    first-.opf fallback never wrote the file it found."""

    OPF = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="bookid">urn:uuid:X</dc:identifier>'
        "</metadata></package>"
    )
    CONTAINER = (
        '<?xml version="1.0"?>'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )

    @staticmethod
    def _build(path: Path, entries: dict) -> None:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in entries.items():
                z.writestr(name, data)

    def test_missing_container_is_generated_at_the_located_opf(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(
                src,
                {
                    "mimetype": "application/epub+zip",
                    "OEBPS/content.opf": self.OPF,
                    "OEBPS/c1.xhtml": CONTENT,
                },
            )
            report = repair_epub(src, dst)  # off by default
            self.assertNotIn("container_generated", report.fixes)
            report = repair_epub(src, dst, RepairFlags(fix_container=True))
            self.assertEqual(report.fixes.get("container_generated"), 1)
            with zipfile.ZipFile(dst) as z:
                self.assertIsNone(z.testzip())
                container = z.read("META-INF/container.xml").decode("utf-8")
            self.assertIn('full-path="OEBPS/content.opf"', container)
            self.assertIn("urn:oasis:names:tc:opendocument:xmlns:container", container)
            # the generated entry is timestamped from the constant epoch, so
            # two repairs of one book are byte-identical
            with zipfile.ZipFile(dst) as z:
                self.assertEqual(
                    z.getinfo("META-INF/container.xml").date_time,
                    (1980, 1, 1, 0, 0, 0),
                )

    def test_stale_container_pointing_at_an_absent_opf_is_replaced(self):
        stale = self.CONTAINER.replace("OEBPS/content.opf", "OLD/gone.opf")
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(
                src,
                {
                    "mimetype": "application/epub+zip",
                    "META-INF/container.xml": stale,
                    "OEBPS/content.opf": self.OPF,
                    "OEBPS/c1.xhtml": CONTENT,
                },
            )
            report = repair_epub(src, dst, RepairFlags(fix_container=True))
            self.assertEqual(report.fixes.get("container_generated"), 1)
            with zipfile.ZipFile(dst) as z:
                self.assertEqual(
                    len([n for n in z.namelist() if n == "META-INF/container.xml"]), 1
                )
                container = z.read("META-INF/container.xml").decode("utf-8")
            self.assertIn('full-path="OEBPS/content.opf"', container)
            self.assertNotIn("gone.opf", container)

    def test_healthy_container_is_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            original = {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": self.CONTAINER,
                "OEBPS/content.opf": self.OPF,
                "OEBPS/c1.xhtml": CONTENT,
            }
            self._build(src, original)
            report = repair_epub(src, dst, RepairFlags(fix_container=True))
            self.assertNotIn("container_generated", report.fixes)
            with zipfile.ZipFile(dst) as z:
                self.assertEqual(
                    z.read("META-INF/container.xml"),
                    original["META-INF/container.xml"].encode("utf-8"),
                )

    def test_no_locatable_opf_means_no_fix(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(
                src,
                {"mimetype": "application/epub+zip", "c1.xhtml": CONTENT},
            )
            report = repair_epub(src, dst, RepairFlags(fix_container=True))
            self.assertNotIn("container_generated", report.fixes)
            with zipfile.ZipFile(dst) as z:
                self.assertNotIn("META-INF/container.xml", z.namelist())

    def test_generated_container_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            once, twice = Path(td) / "a.epub", Path(td) / "b.epub"
            self._build(
                src,
                {
                    "mimetype": "application/epub+zip",
                    "OEBPS/content.opf": self.OPF,
                    "OEBPS/c1.xhtml": CONTENT,
                },
            )
            repair_epub(src, once, RepairFlags(fix_container=True))
            report = repair_epub(once, twice, RepairFlags(fix_container=True))
            self.assertNotIn("container_generated", report.fixes)

    def test_generate_container_escapes_xml_specials(self):
        """An OPF entry name is book-controlled and lands in a double-quoted
        XML attribute; an unescaped '&' or quote would install a fresh fatal
        into a book the repair just fixed (six-lens audit, 2026-09-12)."""
        for name in ("Tom & Jerry.opf", 'weird"name.opf', "a<b>c.opf", "plain.opf"):
            container = generate_container(name)
            root = ET.fromstring(container)
            rootfile = root.find(
                ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
            )
            self.assertEqual(rootfile.get("full-path"), name)

    def test_ampersand_opf_name_round_trips_through_the_generated_container(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            once, twice = Path(td) / "a.epub", Path(td) / "b.epub"
            opf_name = "OEBPS/Tom & Jerry.opf"
            self._build(
                src,
                {
                    "mimetype": "application/epub+zip",
                    opf_name: self.OPF,
                    "OEBPS/c1.xhtml": CONTENT,
                },
            )
            report = repair_epub(src, once, RepairFlags(fix_container=True))
            self.assertEqual(report.fixes.get("container_generated"), 1)
            with zipfile.ZipFile(once) as z:
                self.assertIsNone(z.testzip())
                container = z.read("META-INF/container.xml").decode("utf-8")
                root = ET.fromstring(container)
                rootfile = root.find(
                    ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
                )
                self.assertEqual(rootfile.get("full-path"), opf_name)
                # the escaping reader: the locator resolves the escaped
                # attribute back to the raw zip entry, not the first-.opf
                # fallback (which a stray duplicate .opf could poison)
                self.assertEqual(_locate_opf(z), opf_name)
            # and the escaped container reads back as healthy, not stale
            report = repair_epub(once, twice, RepairFlags(fix_container=True))
            self.assertNotIn("container_generated", report.fixes)


class TestPruneDanglingEdges(unittest.TestCase):
    """Phase 16 B2: pruning an item must rewrite the edges that point at it
    (spine@toc, item@media-overlay, item@fallback, the EPUB2 cover meta),
    not leave dangling idrefs the gate then rejects."""

    def test_unit_rewrites_each_edge_class(self):
        from bindery.epub import prune_dangling_edges

        opf = (
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
            '<item id="keep" href="k.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="snd" href="s.mp3" media-overlay="dead"/>'
            '<item id="img" href="i.png" fallback="dead"/>'
            "</manifest>"
            '<spine toc="dead"><itemref idref="keep"/></spine>'
            '<meta name="cover" content="dead"/>'
            "</package>"
        )
        out, n = prune_dangling_edges(opf, {"dead"})
        self.assertEqual(n, 4)
        self.assertNotIn('toc="dead"', out)
        self.assertNotIn('media-overlay="dead"', out)
        self.assertNotIn('fallback="dead"', out)
        self.assertNotIn('content="dead"', out)
        self.assertIn("<spine>", out)  # the optional attribute, removed whole
        self.assertIn('<itemref idref="keep"/>', out)

    def test_edges_to_live_items_are_kept(self):
        from bindery.epub import prune_dangling_edges

        opf = (
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
            '<item id="alive" href="a.png" media-type="image/png"/>'
            "</manifest>"
            '<spine toc="ncx"><itemref idref="alive"/></spine>'
            '<meta name="cover" content="alive"/>'
            "</package>"
        )
        out, n = prune_dangling_edges(opf, {"dead"})
        self.assertEqual((out, n), (opf, 0))

    def test_end_to_end_prune_rewrites_what_it_drops(self):
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="bookid">urn:uuid:X</dc:identifier>'
            "</metadata>"
            "<manifest>"
            '<item id="keep" href="k.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="dead" href="dead.css" media-type="text/css"/>'
            '<item id="snd" href="s.mp3" media-type="audio/mpeg" media-overlay="dead"/>'
            "</manifest>"
            '<spine toc="dead"><itemref idref="keep"/></spine>'
            '<meta name="cover" content="dead"/>'
            "</package>"
        )
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", TestFixContainer.CONTAINER)
                z.writestr("OEBPS/content.opf", opf)
                z.writestr("OEBPS/k.xhtml", CONTENT)
                # the audio file survives the prune; only its edge dies
                z.writestr("OEBPS/s.mp3", b"ID3snd")
            report = repair_epub(src, dst, RepairFlags(prune_missing=True))
            self.assertEqual(report.fixes.get("manifest_items_pruned"), 1)
            self.assertEqual(report.fixes.get("prune_edges_rewritten"), 3)
            with zipfile.ZipFile(dst) as z:
                out = z.read("OEBPS/content.opf").decode("utf-8")
            self.assertNotIn('"dead"', out)
            self.assertIn('<spine><itemref idref="keep"/></spine>', out)
            self.assertNotIn("dead.css", out)  # the item itself is gone too


class TestFixMediaTypes(unittest.TestCase):
    """Phase 16 B3: manifest media-type normalization. The prevalence class
    is images declared with the wrong type (a jpg stamped image/png by an
    aggregator); the fix is attribute-only, magic-byte-verified."""

    @staticmethod
    def _build(path: Path, media_type: str, data: bytes, quote='"') -> None:
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="bookid">urn:uuid:X</dc:identifier>'
            "</metadata>"
            "<manifest>"
            f'<item id="pic" href={quote}pic.jpg{quote} '
            f"media-type={quote}{media_type}{quote}/>"
            "</manifest>"
            '<spine><itemref idref="pic"/></spine></package>'
        )
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", TestFixContainer.CONTAINER)
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/pic.jpg", data)

    def test_wrong_media_type_is_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(src, "image/png", b"\xff\xd8\xff\xe0JFIF")
            report = repair_epub(src, dst)  # off by default
            self.assertNotIn("media_types_normalized", report.fixes)
            report = repair_epub(src, dst, RepairFlags(fix_media_types=True))
            self.assertEqual(report.fixes.get("media_types_normalized"), 1)
            with zipfile.ZipFile(dst) as z:
                self.assertIn(
                    'media-type="image/jpeg"',
                    z.read("OEBPS/content.opf").decode("utf-8"),
                )

    def test_single_quoted_declaration_keeps_its_quote_style(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(src, "image/png", b"\xff\xd8\xff\xe0JFIF", quote="'")
            report = repair_epub(src, dst, RepairFlags(fix_media_types=True))
            self.assertEqual(report.fixes.get("media_types_normalized"), 1)
            with zipfile.ZipFile(dst) as z:
                self.assertIn(
                    "media-type='image/jpeg'",
                    z.read("OEBPS/content.opf").decode("utf-8"),
                )

    def test_percent_encoded_entry_name_still_resolves(self):
        # the audit's silent no-op: the peek opened the percent-DECODED
        # resolved path against the zip's RAW entry names, so a wild-style
        # book ("im%20g.jpg" stored, href="im%20g.jpg") never normalized its
        # declaration. The normalized-to-raw map fixed the resolution.
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            opf = (
                '<?xml version="1.0"?>'
                '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
                '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                '<dc:identifier id="bookid">urn:uuid:X</dc:identifier>'
                "</metadata>"
                "<manifest>"
                '<item id="pic" href="im%20g.jpg" media-type="image/png"/>'
                "</manifest>"
                '<spine><itemref idref="pic"/></spine></package>'
            )
            with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", TestFixContainer.CONTAINER)
                z.writestr("OEBPS/content.opf", opf)
                z.writestr("OEBPS/im%20g.jpg", b"\xff\xd8\xff\xe0JFIF")
            report = repair_epub(src, dst, RepairFlags(fix_media_types=True))
            self.assertEqual(report.fixes.get("media_types_normalized"), 1)

    def test_correct_declaration_is_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(src, "image/jpeg", b"\xff\xd8\xff\xe0JFIF")
            report = repair_epub(src, dst, RepairFlags(fix_media_types=True))
            self.assertNotIn("media_types_normalized", report.fixes)

    def test_extension_lie_is_kept_when_the_magic_disagrees(self):
        # a PNG renamed .jpg declared image/png: the extension map alone
        # would "normalize" it to image/jpeg and make the declaration worse
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(src, "image/png", b"\x89PNG\r\n\x1a\ndata")
            report = repair_epub(src, dst, RepairFlags(fix_media_types=True))
            self.assertNotIn("media_types_normalized", report.fixes)

    def test_absent_file_is_left_to_the_prune(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            opf = (
                '<?xml version="1.0"?>'
                '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
                '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                '<dc:identifier id="bookid">urn:uuid:X</dc:identifier>'
                "</metadata>"
                '<manifest><item id="pic" href="pic.jpg" media-type="image/png"/></manifest>'
                '<spine><itemref idref="pic"/></spine></package>'
            )
            with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", TestFixContainer.CONTAINER)
                z.writestr("OEBPS/content.opf", opf)
            report = repair_epub(src, dst, RepairFlags(fix_media_types=True))
            self.assertNotIn("media_types_normalized", report.fixes)


class TestFontMediaTypes(unittest.TestCase):
    """v0.56.0: the CSS-007 font half of --fix-media-types. The font/*
    spellings are blessed on both package versions; the magic must confirm
    the extension; .eot is never touched (epubcheck rejects EOT as a font
    src however it is declared)."""

    @staticmethod
    def _build(path: Path, href: str, media_type: str, data: bytes) -> None:
        opf = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="bookid">urn:uuid:X</dc:identifier>'
            "</metadata>"
            "<manifest>"
            f'<item id="f" href="{href}" media-type="{media_type}"/>'
            "</manifest>"
            "<spine/></package>"
        )
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", TestFixContainer.CONTAINER)
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/" + href, data)

    def test_font_declared_text_plain_is_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(src, "f.ttf", "text/plain", b"\x00\x01\x00\x00\x00")
            report = repair_epub(src, dst, RepairFlags(fix_media_types=True))
            self.assertEqual(report.fixes.get("media_types_normalized"), 1)
            with zipfile.ZipFile(dst) as z:
                self.assertIn(
                    'media-type="font/ttf"',
                    z.read("OEBPS/content.opf").decode("utf-8"),
                )

    def test_font_with_wrong_magic_is_left(self):
        # text/plain declared over a file whose bytes are no font magic:
        # the declaration is wrong but the extension cannot be trusted either
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(src, "f.ttf", "text/plain", b"not a font at all")
            report = repair_epub(src, dst, RepairFlags(fix_media_types=True))
            self.assertNotIn("media_types_normalized", report.fixes)

    def test_woff2_octet_stream_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(src, "f.woff2", "application/octet-stream", b"wOF2\x00\x01")
            report = repair_epub(src, dst, RepairFlags(fix_media_types=True))
            self.assertEqual(report.fixes.get("media_types_normalized"), 1)
            with zipfile.ZipFile(dst) as z:
                self.assertIn(
                    'media-type="font/woff2"',
                    z.read("OEBPS/content.opf").decode("utf-8"),
                )

    def test_eot_is_never_touched(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(
                src, "f.eot", "application/vnd.ms-fontobject", b"\x00\x00\x00lol"
            )
            report = repair_epub(src, dst, RepairFlags(fix_media_types=True))
            self.assertNotIn("media_types_normalized", report.fixes)

    def test_probe_survives_source_output_offset_drift(self):
        # issue #18: writestr stamps the ZipInfo it is handed with the
        # DESTINATION offsets, and the copy loop used to hand it the
        # source's own info. On a book whose output entries land at
        # different offsets than the source's (different deflate level),
        # the peek's by-name open then read the wrong entry's local
        # header; 56 books came back unreadable in the 2026-10-25
        # structural run. The image sits BEFORE the OPF so its info is
        # already stamped by the time the OPF's peek probes it; the
        # source is written at compresslevel 9 so the output (default
        # level) drifts from the first deflate entry onward.
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            filler = b"filler text line\n" * 400
            opf = (
                '<?xml version="1.0"?>'
                '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
                '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                '<dc:identifier id="bookid">urn:uuid:X</dc:identifier>'
                "</metadata>"
                "<manifest>"
                '<item id="pic" href="pic.jpg" media-type="image/png"/>'
                "</manifest>"
                "<spine/></package>"
            )
            with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", TestFixContainer.CONTAINER)
                for i in range(6):
                    z.writestr(f"OEBPS/f{i}.xhtml", filler)
                z.writestr("OEBPS/pic.jpg", b"\xff\xd8\xff\xe0JFIF")
                z.writestr("OEBPS/content.opf", opf)
            report = repair_epub(src, dst, RepairFlags(fix_media_types=True))
            self.assertEqual(report.fixes.get("media_types_normalized"), 1)

    def test_badzipfile_from_the_probe_is_a_skipped_probe(self):
        # issue #18's hardening half: zipfile.BadZipFile subclasses
        # Exception directly (not OSError), so the peek's except tuple
        # missed it and one entry that cannot be opened killed the whole
        # book as unreadable. The probe is best-effort by contract: the
        # item keeps its (wrong but honest) declaration. The failure is
        # injected for the by-name open only — the copy loop's
        # read(item) goes through a ZipInfo and must still succeed.
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(src, "pic.jpg", "image/png", b"\xff\xd8\xff\xe0JFIF")
            real_open = zipfile.ZipFile.open

            def failing_open(self, name, mode="r", pwd=None, **kw):
                if isinstance(name, str) and name.endswith("pic.jpg"):
                    raise zipfile.BadZipFile("Bad magic number for file header")
                return real_open(self, name, mode, pwd, **kw)

            with mock.patch.object(zipfile.ZipFile, "open", failing_open):
                report = repair_epub(src, dst, RepairFlags(fix_media_types=True))
            self.assertNotIn("media_types_normalized", report.fixes)


class TestFixCover(unittest.TestCase):
    """Phase 16 C, the cover-wiring ruling (2026-09-12: hybrid). The
    deterministic half is the EPUB2 cover meta: a dangling content id is
    re-pointed from the guide when the guide names an existing manifest
    item, and removed when nothing identifies the item. The EPUB3
    properties="cover-image" slice stays audit-only (choosing which image
    is the cover by name is the guesswork the charter forbids)."""

    OPF_HEAD = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="bookid">urn:uuid:X</dc:identifier>'
    )
    OPF_TAIL = "</package>"

    @staticmethod
    def _build(path: Path, opf: str) -> None:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", TestFixContainer.CONTAINER)
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/c1.xhtml", CONTENT)
            z.writestr("OEBPS/cover.jpg", b"\xff\xd8\xff\xe0JFIF")

    def test_dangling_meta_is_repointed_from_the_guide(self):
        opf = (
            self.OPF_HEAD
            + '<meta name="cover" content="gone"/>'
            + "</metadata><manifest>"
            '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="real" href="cover.jpg" media-type="image/jpeg"/>'
            '</manifest><spine><itemref idref="c1"/></spine>'
            '<guide><reference type="cover" title="Cover" href="cover.jpg"/></guide>'
            + self.OPF_TAIL
        )
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(src, opf)
            report = repair_epub(src, dst)  # off by default
            self.assertNotIn("cover_meta_repointed", report.fixes)
            report = repair_epub(src, dst, RepairFlags(fix_cover=True))
            self.assertEqual(report.fixes.get("cover_meta_repointed"), 1)
            with zipfile.ZipFile(dst) as z:
                out = z.read("OEBPS/content.opf").decode("utf-8")
            self.assertIn('content="real"', out)
            self.assertNotIn('content="gone"', out)

    def test_dangling_meta_without_a_guide_is_removed(self):
        opf = (
            self.OPF_HEAD
            + '<meta name="cover" content="gone"/>'
            + "</metadata><manifest>"
            '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
            '</manifest><spine><itemref idref="c1"/></spine>' + self.OPF_TAIL
        )
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(src, opf)
            report = repair_epub(src, dst, RepairFlags(fix_cover=True))
            self.assertEqual(report.fixes.get("cover_meta_removed"), 1)
            with zipfile.ZipFile(dst) as z:
                out = z.read("OEBPS/content.opf").decode("utf-8")
            self.assertNotIn('name="cover"', out)

    def test_healthy_meta_is_untouched(self):
        opf = (
            self.OPF_HEAD
            + '<meta name="cover" content="real"/>'
            + "</metadata><manifest>"
            '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="real" href="cover.jpg" media-type="image/jpeg"/>'
            '</manifest><spine><itemref idref="c1"/></spine>' + self.OPF_TAIL
        )
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(src, opf)
            report = repair_epub(src, dst, RepairFlags(fix_cover=True))
            self.assertNotIn("cover_meta_repointed", report.fixes)
            self.assertNotIn("cover_meta_removed", report.fixes)
            with zipfile.ZipFile(dst) as z:
                self.assertIn(
                    'content="real"', z.read("OEBPS/content.opf").decode("utf-8")
                )

    def test_single_quoted_meta_keeps_its_quote_style(self):
        opf = (
            self.OPF_HEAD
            + "<meta name='cover' content='gone'/>"
            + "</metadata><manifest>"
            '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="real" href="cover.jpg" media-type="image/jpeg"/>'
            '</manifest><spine><itemref idref="c1"/></spine>'
            '<guide><reference type="cover" title="Cover" href="cover.jpg"/></guide>'
            + self.OPF_TAIL
        )
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            self._build(src, opf)
            report = repair_epub(src, dst, RepairFlags(fix_cover=True))
            self.assertEqual(report.fixes.get("cover_meta_repointed"), 1)
            with zipfile.ZipFile(dst) as z:
                self.assertIn(
                    "content='real'", z.read("OEBPS/content.opf").decode("utf-8")
                )

    def test_cover_file_absent_is_left_to_the_prune(self):
        # the meta's item EXISTS but its file is gone: --fix-cover alone
        # leaves it (the declaration is honest), the prune removes the item
        # and the edge completion removes the meta
        opf = (
            self.OPF_HEAD
            + '<meta name="cover" content="real"/>'
            + "</metadata><manifest>"
            '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="real" href="gone.jpg" media-type="image/jpeg"/>'
            '</manifest><spine><itemref idref="c1"/></spine>' + self.OPF_TAIL
        )
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
            with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", TestFixContainer.CONTAINER)
                z.writestr("OEBPS/content.opf", opf)
                z.writestr("OEBPS/c1.xhtml", CONTENT)
            report = repair_epub(src, dst, RepairFlags(fix_cover=True))
            self.assertNotIn("cover_meta_repointed", report.fixes)
            self.assertNotIn("cover_meta_removed", report.fixes)
            report = repair_epub(
                src, dst, RepairFlags(fix_cover=True, prune_missing=True)
            )
            self.assertEqual(report.fixes.get("manifest_items_pruned"), 1)
            self.assertEqual(report.fixes.get("prune_edges_rewritten"), 1)


class TestStripStubDocs(unittest.TestCase):
    """--strip-stub-docs (the lossy lane's live fixture): a Bookmate-style
    export whose chapters are all the same short "content unavailable"
    placeholder. The identity rule mirrors the emptytext analyzer's
    placeholder signals: short (not blank), the SAME text across >= 3 spine
    docs and >= 30% of the spine; the whole-spine-stubs book is refused (it
    is EMPTY: re-source, never repair)."""

    STUB_TEXT = "This content is not available."
    CONTAINER = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )

    def _build(self, td, *, stubs=3, chapters=5, stub_text=STUB_TEXT, interleave=True):
        """A Bookmate-like book: `chapters` real chapters plus `stubs` spine
        docs carrying one identical short placeholder."""
        src = Path(td) / "in.epub"
        manifest = [
            f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
            for i in range(chapters)
        ]
        spine = [f'<itemref idref="c{i}"/>' for i in range(chapters)]
        stub_ids = [f"s{i}" for i in range(stubs)]
        for sid in stub_ids:
            manifest.append(
                f'<item id="{sid}" href="{sid}.xhtml" '
                'media-type="application/xhtml+xml"/>'
            )
        if interleave:
            # stubs woven through the spine, the real export's shape
            order = []
            for i in range(chapters):
                order.append(f"c{i}")
                if i < stubs:
                    order.append(f"s{i}")
        else:
            order = [f"c{i}" for i in range(chapters)] + stub_ids[:stubs]
        spine = [f'<itemref idref="{i}"/>' for i in order]
        opf = (
            '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="bookid">urn:uuid:STUB-BOOK</dc:identifier>'
            "</metadata>"
            f"<manifest>{''.join(manifest)}</manifest>"
            f"<spine>{''.join(spine)}</spine></package>"
        )
        ncx_points = "".join(
            f'<navPoint id="np-{i}" playOrder="{n + 1}">'
            f"<navLabel><text>{i}</text></navLabel>"
            f'<content src="{i}.xhtml"/></navPoint>'
            for n, i in enumerate(order)
        )
        ncx = (
            '<?xml version="1.0"?>'
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><head>'
            '<meta name="dtb:uid" content="urn:uuid:STUB-BOOK"/>'
            "</head><docTitle><text>T</text></docTitle>"
            f"<navMap>{ncx_points}</navMap></ncx>"
        )
        nav_lis = "".join(f'<li><a href="{i}.xhtml">{i}</a></li>' for i in order)
        nav = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            f'<nav id="toc"><ol>{nav_lis}</ol></nav></body></html>'
        )
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/toc.ncx", ncx)
            z.writestr("OEBPS/nav.xhtml", nav)
            for i in range(chapters):
                z.writestr(
                    f"OEBPS/c{i}.xhtml",
                    f"<html><body><p>Chapter {i}: "
                    + ("Real chapter prose lives here. " * 40)
                    + "</p></body></html>",
                )
            for sid in stub_ids[:stubs]:
                z.writestr(
                    f"OEBPS/{sid}.xhtml",
                    f"<html><body><p>{stub_text}</p></body></html>",
                )
        return src

    def test_stub_class_dropped_with_full_cascade(self):
        with tempfile.TemporaryDirectory() as td:
            src = self._build(td)
            dst = Path(td) / "out.epub"
            report = repair_epub(src, dst, RepairFlags(strip_stub_docs=True))
            self.assertEqual(report.fixes.get("stub_docs_dropped"), 3)
            self.assertEqual(report.fixes.get("stub_manifest_items_dropped"), 3)
            self.assertEqual(report.fixes.get("stub_itemrefs_dropped"), 3)
            self.assertEqual(report.fixes.get("stub_navpoints_dropped"), 3)
            self.assertEqual(report.fixes.get("stub_nav_items_dropped"), 3)
            with zipfile.ZipFile(dst) as z:
                names = z.namelist()
                opf = z.read("OEBPS/content.opf").decode()
                ncx = z.read("OEBPS/toc.ncx").decode()
                nav = z.read("OEBPS/nav.xhtml").decode()
        self.assertNotIn("s0.xhtml", "\n".join(names))
        self.assertIn("c0.xhtml", "\n".join(names))
        self.assertNotIn('idref="s0"', opf)
        self.assertNotIn('id="s0"', opf)
        self.assertNotIn('src="s0.xhtml"', ncx)
        # the navPoint removal runs before the always-on playOrder
        # resequencing, so the surviving sequence is strictly 1..5
        self.assertNotIn('playOrder="6"', ncx)
        self.assertIn('playOrder="5"', ncx)
        self.assertNotIn('href="s1.xhtml"', nav)

    def test_off_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            src = self._build(td)
            dst = Path(td) / "out.epub"
            report = repair_epub(src, dst)
        self.assertNotIn("stub_docs_dropped", report.fixes)

    def test_image_carrier_divider_pages_refused(self):
        # the Great Change misfire (issue #1, 2026-10-05): four part-divider
        # pages whose only content is a full-bleed <img>, byte-near-identical
        # except the image number. Their identical head <title> used to weld
        # them (plus the title page) into a fake stub class and offer a strip
        # that deleted real illustrations; the gate caught the deletion at
        # apply time, but the offer itself was wrong. Stub identity pools
        # BODY text and image carriers are exempt outright.
        with tempfile.TemporaryDirectory() as td:
            src = self._build(td, stubs=4, chapters=5)
            rebuilt = Path(td) / "dividers.epub"
            with zipfile.ZipFile(src) as zin, zipfile.ZipFile(rebuilt, "w") as zout:
                for item in zin.infolist():
                    data = zin.read(item)
                    base = item.filename.rsplit("/", 1)[-1]
                    if base.startswith("s") and base.endswith(".xhtml"):
                        data = (
                            b"<html><head><title>T</title></head><body>"
                            b'<p class="centered">'
                            b'<img class="full" src="1.jpg"/></p></body></html>'
                        )
                    zout.writestr(item, data)
            dst = Path(td) / "out.epub"
            report = repair_epub(rebuilt, dst, RepairFlags(strip_stub_docs=True))
        self.assertEqual(report.fixes, {})

    def test_whole_spine_of_stubs_is_refused(self):
        # every doc is the same stub: the book is EMPTY, not repairable
        with tempfile.TemporaryDirectory() as td:
            src = self._build(td, stubs=4, chapters=0)
            dst = Path(td) / "out.epub"
            report = repair_epub(src, dst, RepairFlags(strip_stub_docs=True))
        self.assertNotIn("stub_docs_dropped", report.fixes)

    def test_no_repeat_is_refused(self):
        # short docs with DISTINCT text are furniture, not a placeholder class
        with tempfile.TemporaryDirectory() as td:
            src = self._build(td, stubs=3, chapters=5)
            # rewrite the three stub docs with distinct texts
            with zipfile.ZipFile(src) as zin:
                entries = {n: zin.read(n) for n in zin.namelist()}
            for i, t in enumerate(("one", "two", "three")):
                entries[f"OEBPS/s{i}.xhtml"] = (
                    f"<html><body><p>{t}</p></body></html>".encode()
                )
            with zipfile.ZipFile(src, "w") as zout:
                for n, b in entries.items():
                    zout.writestr(n, b)
            dst = Path(td) / "out.epub"
            report = repair_epub(src, dst, RepairFlags(strip_stub_docs=True))
        self.assertNotIn("stub_docs_dropped", report.fixes)

    def test_below_fraction_is_refused(self):
        # 3 identical stubs across a 14-doc spine (21%) is under the 30% bar
        with tempfile.TemporaryDirectory() as td:
            src = self._build(td, stubs=3, chapters=12)
            dst = Path(td) / "out.epub"
            report = repair_epub(src, dst, RepairFlags(strip_stub_docs=True))
        self.assertNotIn("stub_docs_dropped", report.fixes)


SCARRED_OPF = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="ID1" '
    'version="2.0">'
    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
    "<dc:title>Century Rain</dc:title>"
    '<dc:creator xmlns:ns0="http://www.idpf.org/2007/opf" ns0:role="aut">'
    "Alastair Reynolds</dc:creator>"
    '<dc:identifier id="ID1" ns0:scheme="calibre">'
    "fccddb12-6dd3-4896-ab23-c676558ca98b</dc:identifier>"
    "</metadata>"
    '<manifest><item id="c1" href="c1.xhtml" '
    'media-type="application/xhtml+xml"/><item id="ncx" href="toc.ncx" '
    'media-type="application/x-dtbncx+xml"/></manifest>'
    '<spine toc="ncx"><itemref idref="c1"/></spine></package>'
)

# The Hackers shape (issue #22): no xmlns:dc on <metadata>; every dc:
# element self-declares inline except <dc:date>, whose ELEMENT prefix is
# then unbound, and the identifier's ns0:scheme is out of scope too.
EMBED_SCARRED_OPF = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="ID1" '
    'version="2.0">'
    "<metadata>"
    '<dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">Hackers</dc:title>'
    '<dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:ns0="http://www.idpf.org/2007/opf" ns0:role="aut" '
    'ns0:file-as="Levy, Steven">Steven Levy</dc:creator>'
    '<dc:identifier xmlns:dc="http://purl.org/dc/elements/1.1/" id="ID1" '
    'ns0:scheme="calibre">fccddb12-6dd3-4896-ab23-c676558ca98b</dc:identifier>'
    "<dc:date>2001-01-02</dc:date>"
    "</metadata>"
    '<manifest><item id="c1" href="c1.xhtml" '
    'media-type="application/xhtml+xml"/><item id="ncx" href="toc.ncx" '
    'media-type="application/x-dtbncx+xml"/></manifest>'
    '<spine toc="ncx"><itemref idref="c1"/></spine></package>'
)


class TestUnboundPrefix(unittest.TestCase):
    """Issue #9: the Calibre embed scar. One dc: element carries an
    ns0:-prefixed attribute whose declaration sits on a sibling, out of XML
    scope: two RSC-016 fatals plus OPF-030 on the unique-identifier, and the
    NCX uid sync measures as no gain while the package is unparseable."""

    def _build(self, td: str) -> tuple[Path, Path]:
        src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("OEBPS/c1.xhtml", CONTENT)
            z.writestr("OEBPS/toc.ncx", NCX_BAD)
            z.writestr("OEBPS/content.opf", SCARRED_OPF)
        return src, dst

    def test_off_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = self._build(td)
            report = repair_epub(src, dst)
        self.assertNotIn("unbound_prefixes_bound", report.fixes)

    def test_scarred_opf_bound_and_ncx_synced(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = self._build(td)
            self.assertTrue(ncx_uid_mismatch(src))
            report = repair_epub(src, dst, RepairFlags(unbound_prefix=True))
            self.assertEqual(report.fixes.get("unbound_prefixes_bound"), 1)
            self.assertTrue(report.ncx_uid_synced)
            with zipfile.ZipFile(dst) as z:
                opf = z.read("OEBPS/content.opf").decode()
            self.assertIn(
                '<dc:identifier xmlns:ns0="http://www.idpf.org/2007/opf" '
                'id="ID1" ns0:scheme="calibre">',
                opf,
            )
            # the healthy sibling is untouched
            self.assertIn(
                '<dc:creator xmlns:ns0="http://www.idpf.org/2007/opf" ns0:role="aut">',
                opf,
            )
            # the bound package parses and its unique-identifier resolves
            ET.fromstring(opf)
            self.assertFalse(ncx_uid_mismatch(dst))

    def _build_embed_scar(self, td: str) -> tuple[Path, Path]:
        src, dst = Path(td) / "in.epub", Path(td) / "out.epub"
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("OEBPS/c1.xhtml", CONTENT)
            # the real specimen's NCX is prefixed throughout (v0.57.1)
            z.writestr("OEBPS/toc.ncx", NCX_BAD_PREFIXED)
            z.writestr("OEBPS/content.opf", EMBED_SCARRED_OPF)
        return src, dst

    def test_embed_scar_element_and_attribute_bound(self):
        # Issue #22: one run binds both halves of the 9.7.0 scar. On the
        # specimen each fix alone left the other masked behind a still-fatal
        # package (1f/1e -> 1f/0e); together they parse it clean. The NCX
        # sync fires on the specimen's prefixed meta spelling (v0.57.1).
        with tempfile.TemporaryDirectory() as td:
            src, dst = self._build_embed_scar(td)
            report = repair_epub(src, dst, RepairFlags(unbound_prefix=True))
            self.assertEqual(report.fixes.get("unbound_prefixes_bound"), 2)
            self.assertTrue(report.ncx_uid_synced)
            with zipfile.ZipFile(dst) as z:
                opf = z.read("OEBPS/content.opf").decode()
            self.assertIn(
                '<dc:date xmlns:dc="http://purl.org/dc/elements/1.1/">2001-01-02</dc:date>',
                opf,
            )
            self.assertIn(
                '<dc:identifier xmlns:ns0="http://www.idpf.org/2007/opf" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" id="ID1"',
                opf,
            )
            ET.fromstring(opf)

    def test_off_by_default_embed_scar(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = self._build_embed_scar(td)
            report = repair_epub(src, dst)
        self.assertNotIn("unbound_prefixes_bound", report.fixes)
