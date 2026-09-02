"""EPUB-level repair: apply the text transforms across an archive and rewrite it.

Like oceanstrip's rewrite, this copies entries one at a time and forces the mimetype
entry first and stored, so the output is never less conformant than the input. Content
documents get the full HTML transform pipeline; the NCX sidecar gets the lighter XML
pipeline plus a dtb:uid sync to the OPF unique identifier (the NCX-001 fix). The OPF
itself is left untouched to keep Calibre's embedded metadata pristine.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .pagination import collect_runheads, detect_page_layer, strip_pagination_doc
from .reserialize import reserialize_if_broken
from .transforms import (
    HTML_TRANSFORMS,
    XML_TRANSFORMS,
    add_img_alt,
    apply_transforms,
    css_protected_tags,
    escape_unknown_entities,
    fix_empty_body,
    fix_id_colons,
    fix_missing_title,
    strip_broken_tags,
    strip_invalid_attributes,
    strip_invalid_value,
    style_block_tags,
    unwrap_block_in_inline,
    unwrap_illegal_tags,
)
from .watermark import strip_watermark_html

CONTENT_SUFFIXES = (".xhtml", ".html", ".htm", ".xml")

# The OCF-mandated content of the mimetype entry: exact bytes, no trailing newline.
MIMETYPE = b"application/epub+zip"
# The timestamp for a mimetype entry we are adding, where there is nothing to inherit.
# A constant, not the wall clock, so repairing the same book twice is byte-identical.
# 1980-01-01 is the earliest a zip can represent.
MIMETYPE_EPOCH = (1980, 1, 1, 0, 0, 0)

# Stray marker entries injected by producers (case-insensitive base name)
MARKER_NAMES = {"oceanofpdf.com"}

# Attribute regexes accept either quote style: a single-quoting toolchain would
# otherwise make the NCX-001 sync and OPF location silently no-op. The ([\"']) group
# plus the tempered (?:(?!\1).)* body match a value up to its own quote character.
_UID_ATTR_RE = re.compile(r"unique-identifier=([\"'])((?:(?!\1).)+)\1")
_ITEM_ID_RE = re.compile(r'(<item\b[^>]*?\bid=")([^"]*)(")', re.IGNORECASE)
_ROOTFILE_RE = re.compile(r"full-path=([\"'])((?:(?!\1).)+)\1")


def _locate_opf(z: zipfile.ZipFile) -> str | None:
    """The package document path, from META-INF/container.xml when possible.

    Falling back to the first .opf in archive order is a last resort: broken EPUBs
    sometimes carry stray duplicate .opf entries, and picking the wrong one would
    sync the wrong uid into the NCX.
    """
    try:
        container = z.read("META-INF/container.xml").decode("utf-8", "replace")
    except KeyError:
        container = ""
    m = _ROOTFILE_RE.search(container)
    if m and m.group(2) in z.namelist():
        return m.group(2)
    return next((n for n in z.namelist() if n.lower().endswith(".opf")), None)


def _is_invalid_ncname(s: str) -> bool:
    """True if `s` cannot be an XML id (NCName): empty, leading non-letter/underscore,
    or containing a colon. This is what epubcheck flags as RSC-005 'must be an XML name'."""
    if not s:
        return True
    if ":" in s:
        return True
    return not (s[0].isalpha() or s[0] == "_")


def _plan_renames(existing: set[str]) -> dict[str, str]:
    """Map every invalid id in `existing` to a valid replacement no other id claims.

    Iteration is sorted rather than in set order because two invalid ids can want the
    same replacement (`1:2` and `1_2` both yield `id_1_2`); set order made which one
    got the extra `_` prefix depend on the hash seed, so the same book repaired to
    different bytes from run to run.
    """
    rename: dict[str, str] = {}
    taken = set(existing)
    for old in sorted(existing):
        if not _is_invalid_ncname(old):
            continue
        new = "id_" + old.replace(":", "_")
        while new in taken:
            new = "_" + new
        taken.add(new)
        rename[old] = new
    return rename


def fix_manifest_ids(opf_text: str) -> tuple[str, int]:
    """Rename manifest item ids that are not valid XML names (e.g. start with a digit)
    and update every reference to them: spine idref, spine toc, item fallback and
    media-overlay, and the EPUB 2 cover meta. Returns (text, count).

    Calibre-converted books often carry manifest ids copied from random filenames that
    start with a digit; epubcheck rejects them. The href/filenames are untouched.
    """
    rename = _plan_renames({m.group(2) for m in _ITEM_ID_RE.finditer(opf_text)})
    if not rename:
        return opf_text, 0

    def repl_attr(m: re.Match) -> str:
        return m.group(1) + rename.get(m.group(2), m.group(2)) + m.group(3)

    out = _ITEM_ID_RE.sub(repl_attr, opf_text)
    out = re.sub(r'(\bidref=")([^"]*)(")', repl_attr, out)
    out = re.sub(r'(\bfallback=")([^"]*)(")', repl_attr, out)
    out = re.sub(r'(\bmedia-overlay=")([^"]*)(")', repl_attr, out)
    out = re.sub(
        r'(<spine\b[^>]*?\btoc=")([^"]*)(")', repl_attr, out, flags=re.IGNORECASE
    )
    # The EPUB 2 cover convention points at a manifest id; Calibre and most readers
    # find the cover through it, so a renamed cover item must be re-pointed.
    out = re.sub(
        r'(<meta\b[^>]*\bname="cover"[^>]*\bcontent=")([^"]*)(")',
        repl_attr,
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r'(<meta\b[^>]*\bcontent=")([^"]*)("[^>]*\bname="cover")',
        repl_attr,
        out,
        flags=re.IGNORECASE,
    )
    return out, len(rename)


# Any id attribute, either quote style (the OPF _ITEM_ID_RE is item-specific and
# double-quote-only; NCX toolchains emit both styles).
_XML_ID_RE = re.compile(r"""(\bid=)(["'])((?:(?!\2).)*)(\2)""", re.IGNORECASE)


def fix_ncx_ids(ncx_text: str) -> tuple[str, int]:
    """Rename NCX ids that are not valid XML names (RSC-005), same scheme as
    fix_manifest_ids. Returns (text, count of ids renamed).

    Old conversions stamp navPoint ids from UUIDs (digit-led) or colon-bearing
    strings; epubcheck rejects every one. Unlike manifest ids, NCX ids are internal
    to the NCX (nothing in the OPF or content documents references a navPoint id),
    so the rename needs no cross-file bookkeeping.
    """
    rename = _plan_renames({m.group(3) for m in _XML_ID_RE.finditer(ncx_text)})
    if not rename:
        return ncx_text, 0

    def repl(m: re.Match) -> str:
        return m.group(1) + m.group(2) + rename.get(m.group(3), m.group(3)) + m.group(4)

    return _XML_ID_RE.sub(repl, ncx_text), len(rename)


# Both accept single or double quotes; group 3 is the uid value and group(1)+group(4)
# reconstruct everything around it, so the replacement logic is quote-agnostic too.
_DTB_UID_RE = re.compile(
    r"(<meta\b[^>]*\bname=[\"']dtb:uid[\"'][^>]*\bcontent=([\"']))((?:(?!\2).)*)(\2)",
    re.IGNORECASE,
)
_DTB_UID_RE_REV = re.compile(
    r"(<meta\b[^>]*\bcontent=([\"']))((?:(?!\2).)*)"
    r"(\2[^>]*\bname=[\"']dtb:uid[\"'][^>]*>)",
    re.IGNORECASE,
)


@dataclass
class RepairReport:
    """What a repair did, aggregated across the archive."""

    fixes: dict[str, int] = field(default_factory=dict)
    files_changed: int = 0
    ncx_uid_synced: bool = False

    def add(self, counts: dict[str, int]) -> None:
        for k, v in counts.items():
            self.fixes[k] = self.fixes.get(k, 0) + v

    @property
    def total(self) -> int:
        return sum(self.fixes.values()) + (1 if self.ncx_uid_synced else 0)

    def __bool__(self) -> bool:
        return self.total > 0


_SPINE_PAGE_MAP_RE = re.compile(r"(<spine\b[^>]*?)\s+page-map=(?:\"[^\"]*\"|'[^']*')")


def strip_page_map(opf_text: str) -> tuple[str, int]:
    """Remove the non-standard ``page-map`` attribute from ``<spine>``.

    Older conversion pipelines (HarperCollins / Anna's Archive output) stamp
    ``<spine ... page-map="page-map">``; the attribute is not part of the OPF
    schema and epubcheck rejects the package over it. The page-map file itself
    (if any) is left alone — this only drops the dangling reference.
    """
    return _SPINE_PAGE_MAP_RE.subn(r"\1", opf_text)


_PAGELIST_OPEN_RE = re.compile(r"<pageList(?=[\s/>])([^>]*)")


def fix_pagelist_class(ncx_text: str) -> tuple[str, int]:
    """Add ``class="pages"`` to ``<pageList>`` elements missing the attribute.

    NCX 2.x requires ``class`` on ``<pageList>``; older conversions leave it
    out and epubcheck answers RSC-005 'missing required attribute "class"'.
    ``class="pages"`` is the value upstream's own tooling emits, and a
    pageList that already carries a class is left untouched.
    """
    count = 0

    def _add(m: re.Match) -> str:
        nonlocal count
        attrs = m.group(1)
        if re.search(r"\bclass\s*=", attrs, re.IGNORECASE):
            return m.group(0)
        count += 1
        return f'<pageList class="pages"{attrs}'

    return _PAGELIST_OPEN_RE.sub(_add, ncx_text), count


def opf_unique_id(opf_text: str) -> str | None:
    """The dc:identifier value referenced by the OPF unique-identifier attribute."""
    attr = _UID_ATTR_RE.search(opf_text)
    if not attr:
        return None
    idpat = rf"id=[\"']{re.escape(attr.group(2))}[\"']"
    m = re.search(rf"{idpat}[^>]*>([^<]+)<", opf_text) or re.search(
        rf"<dc:identifier[^>]*{idpat}[^>]*>([^<]+)", opf_text
    )
    return m.group(1).strip() if m else None


def sync_ncx_uid(ncx_text: str, uid: str) -> tuple[str, bool]:
    """Set the NCX dtb:uid meta to `uid`. Returns (text, changed)."""
    cur = _DTB_UID_RE.search(ncx_text) or _DTB_UID_RE_REV.search(ncx_text)
    if not cur:
        return ncx_text, False
    if cur.group(3) == uid:
        return ncx_text, False

    # Replace via a function so a uid containing backslashes is inserted literally
    # instead of being parsed as a regex replacement template.
    def repl(m: re.Match) -> str:
        return m.group(1) + uid + m.group(4)

    new = _DTB_UID_RE.sub(repl, ncx_text)
    if new == ncx_text:
        new = _DTB_UID_RE_REV.sub(repl, ncx_text)
    return new, True


def ncx_uid_mismatch(src: Path) -> bool:
    """Cheaply detect NCX-001 (toc.ncx dtb:uid != OPF unique-identifier) without epubcheck."""
    try:
        with zipfile.ZipFile(src) as z:
            opf = _locate_opf(z)
            ncx = next((n for n in z.namelist() if n.lower().endswith(".ncx")), None)
            if not opf or not ncx:
                return False
            uid = opf_unique_id(z.read(opf).decode("utf-8", "replace"))
            if not uid:
                return False
            text = z.read(ncx).decode("utf-8", "replace")
            m = _DTB_UID_RE.search(text) or _DTB_UID_RE_REV.search(text)
            return bool(m and m.group(3) != uid)
    except zipfile.BadZipFile, OSError:
        return False


def repair_epub(
    src: Path,
    dst: Path,
    *,
    fix_ids: bool = False,
    reserialize: bool = False,
    strip_attrs: bool = False,
    strip_pagination: bool = False,
    strip_brokentags: bool = False,
    strip_watermarks: bool = False,
    escape_entities: bool = False,
    img_alt: bool = False,
    empty_body: bool = False,
    missing_title: bool = False,
    id_colons: bool = False,
    block_in_inline: bool = False,
    invalid_value: bool = False,
    illegal_tags: bool = False,
    page_map: bool = False,
) -> RepairReport:
    """Write a repaired copy of `src` to `dst`. Returns a RepairReport.

    With `fix_ids`, also rewrite invalid manifest ids in the OPF (off by default, since
    it touches the OPF; the dc: metadata is never altered, only item ids and their refs)
    and invalid ids in the NCX.
    With `img_alt`, add `alt=""` to <img> elements missing the required attribute.
    With `strip_attrs`, drop attributes that are invalid XML (digit-led names, unbound
    namespace prefixes like Office VML `v:shapes`).
    With `reserialize`, rebuild any content document that is still not well-formed via
    html5lib (closes unclosed non-void elements); good documents are left untouched.
    With `escape_entities`, escape entity names outside the HTML5 table
    (`&foo;` -> `&amp;foo;`); documents with a DOCTYPE internal subset are skipped.
    With `strip_brokentags`, strip leaked HTML closing tags (e.g. </P>) that render as text.
    With `strip_watermarks`, remove known producer watermarks (e.g. OceanofPDF).

    The structural repairs are all opt-in, never part of the core pass:
    with `empty_body`, append &nbsp; to a strictly empty <body>; with `missing_title`,
    inject a <title>Unknown</title> fallback; with `id_colons`, translate illegal
    colons in id attributes and their #fragment references; with `block_in_inline`,
    unwrap a <span> that illegally wraps a block element; with `invalid_value`, strip
    misplaced value="..." attributes; with `illegal_tags`, delete illegal/deprecated
    tags (<st>, <sentence>, <o>, <w>, <pagebreak>) keeping their inner text — any tag
    name an EPUB stylesheet styles as an element selector is protected for the whole
    book, so styled formatting can never be silently destroyed.
    With `page_map`, normalize legacy page-map markup: drop the non-standard page-map
    attribute from the OPF spine and add the required class="pages" to classless
    <pageList> elements in the NCX (older HarperCollins / Anna's Archive conversions
    fail epubcheck on both).
    """
    report = RepairReport()

    # `src` is opened before `dst`, so an unreadable archive still raises before the
    # output file is created.
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        opf = _locate_opf(zin)
        uid = opf_unique_id(zin.read(opf).decode("utf-8", "replace")) if opf else None
        # Running-header detection and the page-layer decision need the whole book, so
        # collect content text once up front. Only when the lossy strip is requested.
        runheads: set[str] = set()
        delete_layer = False
        if strip_pagination:
            htmls = [
                zin.read(i).decode("utf-8", "replace")
                for i in zin.infolist()
                if i.filename.lower().endswith(CONTENT_SUFFIXES)
            ]
            runheads = collect_runheads(htmls)
            delete_layer = detect_page_layer(htmls, runheads)

        # The CSS precondition for --unwrap-illegal-tags is a whole-book property:
        # a stylesheet anywhere can style a content document, so scan every stylesheet
        # once up front and protect those tag names everywhere (inline <style> blocks
        # are added per document below).
        book_css_tags: frozenset[str] = frozenset()
        if illegal_tags:
            css_texts = [
                zin.read(i).decode("utf-8", "replace")
                for i in zin.infolist()
                if i.filename.lower().endswith(".css")
            ]
            book_css_tags = css_protected_tags(*css_texts)

        # The mimetype content is an OCF constant, so adding a missing entry and
        # normalizing wrong or whitespace-padded content is deterministic and
        # semantics-preserving; the gate checks it like any other fix.
        src_mime = zin.getinfo("mimetype") if "mimetype" in zin.namelist() else None
        if src_mime is None:
            report.add({"mimetype_added": 1})
        elif zin.read(src_mime) != MIMETYPE:
            report.add({"mimetype_normalized": 1})
        # A bare string arcname would make zipfile stamp this entry with the current
        # clock, and it was the only such entry in the archive (every other one is
        # written from its source ZipInfo), so two repairs of one book differed in
        # exactly those bytes. Carry the source timestamp over instead.
        #
        # A fresh ZipInfo rather than the source one: OCF requires the mimetype entry
        # to carry no extra field, so reusing the source entry wholesale would
        # propagate a violation from an already-broken book. external_attr matches
        # what writestr sets for a string arcname, leaving the timestamp as the only
        # change to the output.
        mime_info = zipfile.ZipInfo(
            "mimetype", date_time=src_mime.date_time if src_mime else MIMETYPE_EPOCH
        )
        mime_info.external_attr = 0o600 << 16
        zout.writestr(mime_info, MIMETYPE, compress_type=zipfile.ZIP_STORED)

        # An entry is re-encoded only when a fix actually fired; an untouched entry is
        # copied byte-for-byte. Re-encoding the decode("utf-8", "replace") round-trip
        # of an unchanged file would silently swap any non-UTF-8 bytes for U+FFFD.
        for item in zin.infolist():
            name = item.filename
            if name == "mimetype":
                continue
            if strip_watermarks and name.rsplit("/", 1)[-1].lower() in MARKER_NAMES:
                report.files_changed += 1
                report.add({"dropped_marker": 1})
                continue
            # read(item), not read(name): with duplicate entry names (seen in broken
            # EPUBs), read(name) returns the first entry's bytes for every duplicate.
            data = zin.read(item)
            low = name.lower()

            if low.endswith(".ncx"):
                text = data.decode("utf-8", "replace")
                text, counts = apply_transforms(text, XML_TRANSFORMS)
                if fix_ids:
                    text, n = fix_ncx_ids(text)
                    if n:
                        counts["fix_ncx_ids"] = n
                if page_map:
                    text, n = fix_pagelist_class(text)
                    if n:
                        counts["pagelist_class_added"] = n
                synced = False
                if uid:
                    text, synced = sync_ncx_uid(text, uid)
                    if synced:
                        report.ncx_uid_synced = True
                if counts or synced:
                    report.add(counts)
                    report.files_changed += 1
                    data = text.encode("utf-8")
            elif low.endswith(".opf") and (fix_ids or page_map):
                text = data.decode("utf-8", "replace")
                opf_changed = False
                if fix_ids:
                    text, n = fix_manifest_ids(text)
                    if n:
                        report.add({"fix_manifest_ids": n})
                        opf_changed = True
                if page_map:
                    text, n = strip_page_map(text)
                    if n:
                        report.add({"page_map_stripped": n})
                        opf_changed = True
                if opf_changed:
                    report.files_changed += 1
                    data = text.encode("utf-8")
            elif low.endswith(CONTENT_SUFFIXES):
                text = data.decode("utf-8", "replace")
                text, counts = apply_transforms(text, HTML_TRANSFORMS)
                if escape_entities:
                    text, n = escape_unknown_entities(text)
                    if n:
                        counts["escape_unknown_entities"] = n
                if strip_attrs:
                    text, n = strip_invalid_attributes(text)
                    if n:
                        counts["stripped_invalid_attrs"] = n
                if img_alt:
                    text, n = add_img_alt(text)
                    if n:
                        counts["img_alt_added"] = n
                if reserialize:
                    text, n = reserialize_if_broken(text)
                    if n:
                        counts["reserialized"] = n
                if empty_body:
                    text, n = fix_empty_body(text)
                    if n:
                        counts["fix_empty_body"] = n
                if missing_title:
                    text, n = fix_missing_title(text)
                    if n:
                        counts["fix_missing_title"] = n
                if id_colons:
                    text, n = fix_id_colons(text)
                    if n:
                        counts["fix_id_colons"] = n
                if block_in_inline:
                    text, n = unwrap_block_in_inline(text)
                    if n:
                        counts["unwrap_block_in_inline"] = n
                if invalid_value:
                    text, n = strip_invalid_value(text)
                    if n:
                        counts["strip_invalid_value"] = n
                if illegal_tags:
                    protected = book_css_tags | style_block_tags(text)
                    text, n = unwrap_illegal_tags(text, protected_tags=protected)
                    if n:
                        counts["unwrap_illegal_tags"] = n
                if strip_watermarks:
                    text, n = strip_watermark_html(text)
                    if n:
                        counts["stripped_watermarks"] = n
                if strip_brokentags:
                    text, n = strip_broken_tags(text)
                    if n:
                        counts["stripped_broken_tags"] = n
                if strip_pagination:
                    text, n = strip_pagination_doc(text, runheads, delete_layer)
                    if n:
                        counts["stripped_pagination"] = n
                if counts:
                    report.add(counts)
                    report.files_changed += 1
                    data = text.encode("utf-8")

            zout.writestr(item, data, compress_type=item.compress_type)

    return report
