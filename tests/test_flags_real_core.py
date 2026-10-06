"""Every repair flag, driven through the real command line and the real
repair core (no mocks). The 2026-10-02 test-gap sweep named this gap:
almost every CLI-level test mocks bindery.cli.repair_epub, so only a
couple of flags were ever proven to work from argv. Each row here runs
`bindery repair <fixture> <out> --<flag> --no-validate --json` through
main() against a minimal book carrying exactly that flag's defect, and
asserts the flag's own fix key fired in the JSON record.

--no-validate is deliberate: the gate itself is pinned by the
mocked-oracle tests and runs for real wherever epubcheck exists; CI has
no epubcheck, and this file pins the argv-to-fix path, which is
flag-independent of the oracle."""

import io
import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

from bindery.cli import main

try:
    import html5lib  # noqa: F401

    HAVE_HTML5LIB = True
except ImportError:
    HAVE_HTML5LIB = False

CONTAINER = (
    '<?xml version="1.0"?><container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles></container>'
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n"

LONG = (
    "the quick brown fox jumped over the lazy dog and then kept on running for many "
    "miles across the wide green fields under a bright and cloudless summer sky"
)

STUB_DOC = (
    '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
    "<body>This content is not available.</body></html>"
)


def _doc(body: str, *, title: str = "t") -> str:
    return (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
        f"<head><title>{title}</title></head><body>{body}</body></html>"
    )


def _opf(items, refs, *, version: str = "2.0", meta: str = "", guide: str = "") -> str:
    return (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" '
        f'version="{version}" unique-identifier="id">'
        f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">{meta}</metadata>'
        f"<manifest>{''.join(items)}</manifest>"
        f"<spine>{''.join(refs)}</spine>{guide}</package>"
    )


def _item(id_: str, href: str, mt: str = "application/xhtml+xml") -> str:
    return f'<item id="{id_}" href="{href}" media-type="{mt}"/>'


def _ref(id_: str) -> str:
    return f'<itemref idref="{id_}"/>'


def _fixtures() -> dict:
    """flag option -> (entries, expected fix key). Each book is minimal and
    carries exactly one flag's defect; the assertion is that the flag's own
    fix key fires through the real core."""
    c1 = _item("c1", "c1.xhtml")
    r1 = _ref("c1")
    plain = _doc("<p>word word word word</p>")
    return {
        "--fix-ids": (
            {
                "OEBPS/content.opf": _opf([_item("1bad", "c1.xhtml")], [_ref("1bad")]),
                "OEBPS/c1.xhtml": plain,
            },
            "fix_manifest_ids",
        ),
        "--add-img-alt": (
            {
                "OEBPS/content.opf": _opf([c1, _item("i", "i.png", "image/png")], [r1]),
                "OEBPS/c1.xhtml": _doc('<p>x</p><img src="i.png"/>'),
                "OEBPS/i.png": PNG_BYTES,
            },
            "img_alt_added",
        ),
        "--reserialize": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": (
                    '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                    "<p>one<p>two</body></html>"
                ),
            },
            "reserialized",
        ),
        "--strip-bad-attrs": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": _doc('<img v:shapes="x" src="i.png"/>'),
            },
            "stripped_invalid_attrs",
        ),
        "--escape-unknown-entities": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": _doc("<p>a &foo; b</p>"),
            },
            "escape_unknown_entities",
        ),
        "--fix-empty-body": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": (
                    '<html xmlns="http://www.w3.org/1999/xhtml"><body></body></html>'
                ),
            },
            "fix_empty_body",
        ),
        "--fix-missing-title": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": (
                    '<html xmlns="http://www.w3.org/1999/xhtml">'
                    "<head></head><body><p>x</p></body></html>"
                ),
            },
            "fix_missing_title",
        ),
        "--fix-id-colons": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": _doc('<h2 id="s:1">t</h2><a href="#s:1">go</a>'),
            },
            "fix_id_colons",
        ),
        "--unwrap-block-in-inline": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": _doc('<span><div class="c">block</div></span>'),
            },
            "unwrap_block_in_inline",
        ),
        "--strip-invalid-value": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": _doc('<div value="1">x</div>'),
            },
            "strip_invalid_value",
        ),
        "--unwrap-illegal-tags": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": _doc("<w>hidden</w><p>x</p>"),
            },
            "unwrap_illegal_tags",
        ),
        "--fix-page-map": (
            {
                "OEBPS/content.opf": (
                    '<?xml version="1.0"?>'
                    '<package xmlns="http://www.idpf.org/2007/opf" '
                    'version="2.0" unique-identifier="id"><metadata/>'
                    f"<manifest>{c1}{_item('n', 'toc.ncx', 'application/x-dtbncx+xml')}"
                    f'</manifest><spine page-map="pmap" toc="n">{r1}</spine></package>'
                ),
                "OEBPS/c1.xhtml": plain,
                "OEBPS/toc.ncx": (
                    '<?xml version="1.0"?>'
                    '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                    "<head/><docTitle><text>t</text></docTitle><navMap>"
                    '<pageList><navTarget id="pg1" playOrder="1">'
                    "<navLabel><text>1</text></navLabel>"
                    '<content src="c1.xhtml"/></navTarget></pageList>'
                    "</navMap></ncx>"
                ),
            },
            "page_map_stripped",
        ),
        "--strip-epub3-attrs": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": _doc('<p epub:type="chapter">x</p>'),
            },
            "epub3_attrs_stripped",
        ),
        "--downgrade-epub3-tags": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": _doc("<figure><figcaption>cap</figcaption></figure>"),
            },
            "epub3_tags_downgraded",
        ),
        "--prune-missing-resources": (
            {
                "OEBPS/content.opf": _opf([c1, _item("orphan", "absent2.xhtml")], [r1]),
                "OEBPS/c1.xhtml": _doc(
                    '<link href="gone.css" rel="stylesheet" type="text/css"/>'
                    '<img src="absent.png"/>'
                ),
            },
            "dead_links_pruned",
        ),
        "--strip-broken-anchors": (
            {
                "OEBPS/content.opf": _opf(
                    [c1, _item("c2", "c2.xhtml")], [r1, _ref("c2")]
                ),
                "OEBPS/c1.xhtml": _doc('<a href="c2.xhtml#nope">text</a>'),
                "OEBPS/c2.xhtml": _doc("<p>no ids here</p>"),
            },
            "broken_fragment_hrefs_stripped",
        ),
        "--encode-url-spaces": (
            {
                "OEBPS/content.opf": _opf(
                    [c1, _item("i", "raw name.png", "image/png")], [r1]
                ),
                "OEBPS/c1.xhtml": _doc('<img src="raw name.png"/>'),
                "OEBPS/raw name.png": PNG_BYTES,
            },
            "entries_renamed",
        ),
        "--fix-container": (
            {
                "META-INF/container.xml": (
                    '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="OEBPS/missing.opf" '
                    'media-type="application/oebps-package+xml"/>'
                    "</rootfiles></container>"
                ),
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": plain,
            },
            "container_generated",
        ),
        "--fix-media-types": (
            {
                "OEBPS/content.opf": _opf(
                    [c1, _item("i", "i.png", "image/jpeg")], [r1]
                ),
                "OEBPS/c1.xhtml": _doc("<p>x</p>"),
                "OEBPS/i.png": PNG_BYTES,
            },
            "media_types_normalized",
        ),
        "--fix-cover": (
            {
                "OEBPS/content.opf": _opf(
                    [c1, _item("cov", "cover.png", "image/jpeg")],
                    [r1],
                    meta='<meta name="cover" content="nope"/>',
                    guide='<guide><reference type="cover" href="cover.png"/></guide>',
                ),
                "OEBPS/c1.xhtml": plain,
                "OEBPS/cover.png": PNG_BYTES,
            },
            "cover_meta_repointed",
        ),
        "--fix-comment-double-hyphen": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": _doc("<!-- a -- b --><p>x</p>"),
            },
            "fix_comment_double_hyphen",
        ),
        "--strip-pagination": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": _doc(
                    "".join(
                        f"<p>{LONG} and</p><p>{n}</p><p>kept {LONG}.</p>"
                        for n in range(1, 25)
                    )
                ),
            },
            "stripped_pagination",
        ),
        "--strip-broken-tags": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": _doc("<p>text /div&gt; more</p>"),
            },
            "stripped_broken_tags",
        ),
        "--strip-watermarks": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": plain,
                "oceanofpdf.com": "",
            },
            "dropped_marker",
        ),
        "--strip-stub-docs": (
            {
                "OEBPS/content.opf": _opf(
                    [_item(f"s{i}", f"s{i}.xhtml") for i in range(3)]
                    + [_item(f"c{i}", f"c{i}.xhtml") for i in range(2)],
                    [_ref(f"s{i}") for i in range(3)]
                    + [_ref(f"c{i}") for i in range(2)],
                ),
                "OEBPS/s0.xhtml": STUB_DOC,
                "OEBPS/s1.xhtml": STUB_DOC,
                "OEBPS/s2.xhtml": STUB_DOC,
                "OEBPS/c0.xhtml": _doc(
                    "<p>Chapter one begins here with plenty of real prose so the "
                    "book has honest chapters beside its placeholder pages.</p>"
                ),
                "OEBPS/c1.xhtml": _doc(
                    "<p>Chapter two continues the real text with its own words, "
                    "long enough that no analyzer would call it a placeholder.</p>"
                ),
            },
            "stub_docs_dropped",
        ),
        "--fix-svg-dup-ids": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": plain,
                "OEBPS/page0001.svg": (
                    '<svg xmlns="http://www.w3.org/2000/svg" '
                    'xmlns:xlink="http://www.w3.org/1999/xlink">'
                    '<defs><path id="gl2305" d="M0 0"/>'
                    '<path id="gl2305" d="M1 1"/></defs>'
                    '<use xlink:href="#gl2305"/></svg>'
                ),
            },
            "fix_svg_dup_ids",
        ),
        "--fix-cdata-terminator": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": _doc(
                    '<style type="text/css">/*<![CDATA[*/ p { color: red; } '
                    "/*]]></style><p>x</p>"
                ),
            },
            "fix_cdata_terminator",
        ),
        "--fix-misnested-inline": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": _doc("<p><i><b>S</i></b> <i><b>ome</i></b></p>"),
            },
            "fix_misnested_inline",
        ),
        "--fix-stray-close": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": _doc(
                    '<div class="wedge" /><div class="container"><p>x</p></div></div>'
                ),
            },
            "fix_stray_close",
        ),
        "--fix-unterminated-attr": (
            {
                "OEBPS/content.opf": _opf([c1], [r1]),
                "OEBPS/c1.xhtml": (
                    '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
                    '<head><title>t</title></head><body><p class="footnote>\n'
                    "footnote text here</p></body></html>"
                ),
            },
            "fix_unterminated_attr",
        ),
    }


class FlagEndToEndTests(unittest.TestCase):
    def _fixes_for(self, flag: str) -> dict:
        entries, _ = _fixtures()[flag]
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.epub"
            dst = Path(td) / "out.epub"
            out = Path(td) / "out.json"
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
                if "META-INF/container.xml" not in entries:
                    z.writestr("META-INF/container.xml", CONTAINER)
                for entry, data in entries.items():
                    z.writestr(entry, data)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(
                    [
                        "repair",
                        str(src),
                        str(dst),
                        flag,
                        "--no-validate",
                        "--json",
                        str(out),
                    ]
                )
            self.assertEqual(rc, 0, f"{flag}: main() returned {rc}: {buf.getvalue()}")
            self.assertTrue(dst.exists(), f"{flag}: nothing written")
            record = json.loads(out.read_text())
        self.assertEqual(record["status"], "unvalidated", flag)
        return record["fixes"]

    def test_fix_ids(self):
        self.assertGreaterEqual(self._fixes_for("--fix-ids")["fix_manifest_ids"], 1)

    def test_add_img_alt(self):
        self.assertGreaterEqual(self._fixes_for("--add-img-alt")["img_alt_added"], 1)

    @unittest.skipUnless(HAVE_HTML5LIB, "html5lib not installed")
    def test_reserialize(self):
        self.assertGreaterEqual(self._fixes_for("--reserialize")["reserialized"], 1)

    def test_strip_bad_attrs(self):
        self.assertGreaterEqual(
            self._fixes_for("--strip-bad-attrs")["stripped_invalid_attrs"], 1
        )

    def test_escape_unknown_entities(self):
        self.assertGreaterEqual(
            self._fixes_for("--escape-unknown-entities")["escape_unknown_entities"], 1
        )

    def test_fix_empty_body(self):
        self.assertGreaterEqual(
            self._fixes_for("--fix-empty-body")["fix_empty_body"], 1
        )

    def test_fix_missing_title(self):
        self.assertGreaterEqual(
            self._fixes_for("--fix-missing-title")["fix_missing_title"], 1
        )

    def test_fix_id_colons(self):
        self.assertGreaterEqual(self._fixes_for("--fix-id-colons")["fix_id_colons"], 1)

    def test_unwrap_block_in_inline(self):
        self.assertGreaterEqual(
            self._fixes_for("--unwrap-block-in-inline")["unwrap_block_in_inline"], 1
        )

    def test_strip_invalid_value(self):
        self.assertGreaterEqual(
            self._fixes_for("--strip-invalid-value")["strip_invalid_value"], 1
        )

    def test_unwrap_illegal_tags(self):
        self.assertGreaterEqual(
            self._fixes_for("--unwrap-illegal-tags")["unwrap_illegal_tags"], 1
        )

    def test_fix_page_map(self):
        self.assertGreaterEqual(
            self._fixes_for("--fix-page-map")["page_map_stripped"], 1
        )

    def test_strip_epub3_attrs(self):
        self.assertGreaterEqual(
            self._fixes_for("--strip-epub3-attrs")["epub3_attrs_stripped"], 1
        )

    def test_downgrade_epub3_tags(self):
        self.assertGreaterEqual(
            self._fixes_for("--downgrade-epub3-tags")["epub3_tags_downgraded"], 1
        )

    def test_prune_missing_resources(self):
        self.assertGreaterEqual(
            self._fixes_for("--prune-missing-resources")["dead_links_pruned"], 1
        )

    def test_strip_broken_anchors(self):
        self.assertGreaterEqual(
            self._fixes_for("--strip-broken-anchors")["broken_fragment_hrefs_stripped"],
            1,
        )

    def test_encode_url_spaces(self):
        self.assertGreaterEqual(
            self._fixes_for("--encode-url-spaces")["entries_renamed"], 1
        )

    def test_fix_container(self):
        self.assertGreaterEqual(
            self._fixes_for("--fix-container")["container_generated"], 1
        )

    def test_fix_media_types(self):
        self.assertGreaterEqual(
            self._fixes_for("--fix-media-types")["media_types_normalized"], 1
        )

    def test_fix_cover(self):
        self.assertGreaterEqual(
            self._fixes_for("--fix-cover")["cover_meta_repointed"], 1
        )

    def test_fix_comment_double_hyphen(self):
        self.assertGreaterEqual(
            self._fixes_for("--fix-comment-double-hyphen")["fix_comment_double_hyphen"],
            1,
        )

    def test_strip_pagination(self):
        self.assertGreaterEqual(
            self._fixes_for("--strip-pagination")["stripped_pagination"], 20
        )

    def test_strip_broken_tags(self):
        self.assertGreaterEqual(
            self._fixes_for("--strip-broken-tags")["stripped_broken_tags"], 1
        )

    def test_strip_watermarks(self):
        self.assertGreaterEqual(
            self._fixes_for("--strip-watermarks")["dropped_marker"], 1
        )

    def test_strip_stub_docs(self):
        self.assertGreaterEqual(
            self._fixes_for("--strip-stub-docs")["stub_docs_dropped"], 3
        )

    def test_fix_svg_dup_ids(self):
        self.assertGreaterEqual(
            self._fixes_for("--fix-svg-dup-ids")["fix_svg_dup_ids"], 1
        )

    def test_fix_cdata_terminator(self):
        self.assertGreaterEqual(
            self._fixes_for("--fix-cdata-terminator")["fix_cdata_terminator"], 1
        )

    def test_fix_misnested_inline(self):
        self.assertGreaterEqual(
            self._fixes_for("--fix-misnested-inline")["fix_misnested_inline"], 2
        )

    def test_fix_stray_close(self):
        self.assertGreaterEqual(
            self._fixes_for("--fix-stray-close")["fix_stray_close"], 1
        )

    def test_fix_unterminated_attr(self):
        self.assertGreaterEqual(
            self._fixes_for("--fix-unterminated-attr")["fix_unterminated_attr"], 1
        )


if __name__ == "__main__":
    unittest.main()
