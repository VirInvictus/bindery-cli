"""EPUB-level repair: apply the text transforms across an archive and rewrite it.

Like oceanstrip's rewrite, this copies entries one at a time and forces the mimetype
entry first and stored, so the output is never less conformant than the input. Content
documents get the full HTML transform pipeline; the NCX sidecar gets the lighter XML
pipeline plus a dtb:uid sync to the OPF unique identifier (the NCX-001 fix). The OPF
itself is left untouched to keep Calibre's embedded metadata pristine.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from .pagination import collect_runheads, detect_page_layer, strip_pagination_doc
from .reserialize import reserialize_if_broken
from .transforms import (
    _PROTECTED_RE,
    HTML_TRANSFORMS,
    XML_TRANSFORMS,
    add_img_alt,
    apply_transforms,
    css_protected_tags,
    encode_url_spaces,
    escape_unknown_entities,
    fix_empty_body,
    fix_id_colons,
    fix_missing_title,
    outside_protected_map,
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


_EPUB3_ATTR_RE = re.compile(
    r"\s+(?:page-progression-direction|epub:type|aria-label)"
    r'=(?:"[^"]*"|\'[^\']*\')'
)


def strip_epub3_attributes(text: str) -> tuple[str, int]:
    """Scrub the EPUB3-only attributes older conversions sprinkle onto EPUB2
    documents: ``page-progression-direction``, ``epub:type`` and
    ``aria-label`` — epubcheck answers RSC-005 for each on an EPUB2 package
    (the 2026-09-02 verdict: scrub, not tolerate). Rendering is unchanged;
    none of them carries visible content. The set is fixed and documented:
    extend it only with a named epubcheck finding, never speculatively, so a
    reader-legitimate attribute can never be swept up by accident.
    """
    return _EPUB3_ATTR_RE.subn("", text)


# The EPUB3/HTML5 semantic elements an EPUB2 (XHTML 1.1) document cannot
# carry, and the element each downgrades to. figcaption becomes a paragraph
# (its content is phrasing text); figure and section become divs.
EPUB3_DOWNGRADE_TAGS = {"figure": "div", "figcaption": "p", "section": "div"}


def _append_class(attrs: str, name: str) -> str:
    """Add `name` to the class attribute in `attrs` (existing classes kept)."""
    class_re = re.compile(r"class=(?:\"([^\"]*)\"|'([^']*)')")
    m = class_re.search(attrs)
    if m:
        existing = m.group(1) if m.group(1) is not None else m.group(2)
        quote = '"' if m.group(1) is not None else "'"
        merged = f"{existing} {name}".strip()
        return class_re.sub(
            lambda _: f"class={quote}{merged}{quote}",
            attrs,
            count=1,
        )
    return f'{attrs} class="{name}"'


def downgrade_epub3_tags(
    text: str, protected_tags: frozenset[str] = frozenset()
) -> tuple[str, int]:
    """Downgrade EPUB3/HTML5 semantic elements to their EPUB2 equivalents.

    `<figure>` becomes a `<div class="figure ...">`, `<figcaption>` a
    `<p class="figcaption ...">`, `<section>` a `<div class="section ...">`;
    existing classes are kept so class-selector stylesheets keep working, and
    the semantic name is appended as the styling hook. Names in
    `protected_tags` (element-selector references in the book's stylesheets,
    see transforms.css_protected_tags with tags=EPUB3_DOWNGRADE_TAGS) are left
    untouched: if a book styles `figure { ... }`, downgrading it would change
    how the text renders, so preservation wins — and the book keeps its
    RSC-005 findings, which is the honest outcome.
    """
    count = 0
    for tag, new in EPUB3_DOWNGRADE_TAGS.items():
        if tag in protected_tags:
            continue

        def repl_open(m: re.Match, tag: str = tag, new: str = new) -> str:
            nonlocal count
            attrs = m.group(1)
            self_close = attrs.rstrip().endswith("/")
            if self_close:
                attrs = attrs.rstrip()[:-1]
            count += 1
            return f"<{new}{_append_class(attrs, tag)}{'/' if self_close else ''}>"

        def repl_end(m: re.Match, new: str = new) -> str:
            nonlocal count
            count += 1
            return f"</{new}>"

        text = re.sub(rf"<{tag}\b([^>]*)>", repl_open, text, flags=re.IGNORECASE)
        text = re.sub(rf"</{tag}\s*>", repl_end, text, flags=re.IGNORECASE)
    return text, count


# ---------------------------------------------------------------------------
# Broken-reference repairs (Phase 12, all opt-in). The well-formedness fixes
# rewrite what is already there; these remove references the archive cannot
# satisfy: manifest items, stylesheets, images and anchors pointing at absent
# files, fragments no target document defines, and URL references a strict
# reader cannot resolve. Every removal is epubcheck-visible, so all three ride
# the normal gate.

# A URI scheme per RFC 3986: letter, then letter/digit/+/-/. up to a colon.
# Relative paths and fragments never match (no colon before the first /?#).
_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")
# Schemes a reader can actually resolve; everything else in an anchor
# (kindle:embed:, file:, scrivcmt:) is a dead reference. Fixed, documented
# set: extend only with a named epubcheck finding, never speculatively.
_RESOLVABLE_SCHEMES = {"http", "https", "mailto"}


def _norm_path(p: str) -> str:
    """Archive-path shape for comparisons: percent-decoded, posix-normalized."""
    return posixpath.normpath(unquote(p))


def _resolve_href(base_dir: str, href: str) -> str | None:
    """Resolve a package-relative href to an archive path, or None when it
    cannot denote an archive file (external scheme, empty target). The
    fragment is dropped first: it selects a position in the file, not the file."""
    if _URL_SCHEME_RE.match(href):
        return None
    target = href.partition("#")[0]
    if not target:
        return None
    return _norm_path(posixpath.join(base_dir, target))


# Attribute accessors for the reference repairs. Whitespace-anchored (not \b)
# so `data-href`-style names stay out; the optional namespaced prefix must end
# in a colon, so `xlink:href` is covered and `data-src` is not. Group 2 is the
# value in either quote style.
_HREF_ATTR_RE = re.compile(
    r"""(?:^|\s)href\s*=\s*(["'])((?:(?!\1).)*)\1""", re.IGNORECASE
)
_SRC_ATTR_RE = re.compile(
    r"""(?:^|\s)src\s*=\s*(["'])((?:(?!\1).)*)\1""", re.IGNORECASE
)
_ALT_ATTR_RE = re.compile(
    r"""(?:^|\s)alt\s*=\s*(["'])((?:(?!\1).)*)\1""", re.IGNORECASE
)
_IDREF_ATTR_RE = re.compile(
    r"""(?:^|\s)idref\s*=\s*(["'])((?:(?!\1).)*)\1""", re.IGNORECASE
)
# Removes the href attribute (with its leading whitespace) from a tag body.
_DROP_HREF_ATTR_RE = re.compile(r"""\s+href\s*=\s*(?:"[^"]*"|'[^']*')""", re.IGNORECASE)

# Quote-aware start-tag matchers for the elements the prune touches — same
# shape as transforms._VOID_RE, so a '>' inside an attribute value cannot end
# the tag early.
_LINK_TAG_RE = re.compile(
    r"""<link(?=[\s/>])((?:"[^"]*"|'[^']*'|[^>])*?)\s*(/?)>""",
    re.IGNORECASE | re.DOTALL,
)
_ANCHOR_TAG_RE = re.compile(
    r"""<a(?=[\s/>])((?:"[^"]*"|'[^']*'|[^>])*?)\s*(/?)>""", re.IGNORECASE | re.DOTALL
)
_IMG_TAG_RE = re.compile(
    r"""<img(?=[\s/>])((?:"[^"]*"|'[^']*'|[^>])*?)\s*(/?)>""",
    re.IGNORECASE | re.DOTALL,
)
_ITEM_TAG_RE = re.compile(r"""<item\b(?:(?:"[^"]*"|'[^']*'|[^>])*)>""", re.IGNORECASE)
# `\b` is what keeps `<item\b` from matching `<itemref`: "itemref" has a word
# character right after "item", so there is no boundary there.
_NCX_CONTENT_SRC_RE = re.compile(
    r"""(<content\b[^>]*?(?:^|\s)(?:[\w.-]+:)?src\s*=\s*)(["'])((?:(?!\2).)*)(\2)""",
    re.IGNORECASE,
)


def prune_missing_manifest_items(
    opf_text: str, opf_dir: str, present: frozenset[str], spine_ids: set[str]
) -> tuple[str, int]:
    """Drop `<item>` manifest declarations whose file is absent from the
    archive — unless the item is a spine document.

    A missing spine document is a damaged fragment (the audit's
    spine-integrity check reports it); it is never silently pruned. Fonts,
    stylesheets and media the converter never copied in answer PKG-010 with a
    dead-declaration removal instead of a stub file.
    """
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        tag = m.group(0)
        hm = _HREF_ATTR_RE.search(tag)
        if not hm:
            return tag
        resolved = _resolve_href(opf_dir, hm.group(2))
        if resolved is None or resolved in present:
            return tag
        idm = _XML_ID_RE.search(tag)
        if idm and idm.group(3) in spine_ids:
            return tag
        count += 1
        return ""

    return _ITEM_TAG_RE.sub(repl, opf_text), count


def prune_dead_links(
    text: str, doc_dir: str, present: frozenset[str]
) -> tuple[str, int]:
    """Drop `<link ...>` elements whose href target is absent from the archive.

    The Swann's Way shape: 29 documents link `../page-template.xpgt` (an
    Adobe InDesign page template) that the converter never copied in — 29
    identical RSC-007s. A link element carries no visible content, so removing
    the dead reference changes nothing a reader can see. External schemes are
    never touched.
    """

    def fix(part: str) -> tuple[str, int]:
        count = 0

        def repl(m: re.Match) -> str:
            nonlocal count
            hm = _HREF_ATTR_RE.search(m.group(1))
            if not hm:
                return m.group(0)
            resolved = _resolve_href(doc_dir, hm.group(2))
            if resolved is None or resolved in present:
                return m.group(0)
            count += 1
            return ""

        return _LINK_TAG_RE.sub(repl, part), count

    return outside_protected_map(text, fix)


def strip_missing_file_hrefs(
    text: str, doc_dir: str, present: frozenset[str]
) -> tuple[str, int]:
    """Remove the href attribute from anchors pointing at files the archive
    does not contain; the anchor element and its inner text stay."""

    def fix(part: str) -> tuple[str, int]:
        count = 0

        def repl(m: re.Match) -> str:
            nonlocal count
            attrs, slash = m.group(1), m.group(2)
            hm = _HREF_ATTR_RE.search(attrs)
            if not hm:
                return m.group(0)
            resolved = _resolve_href(doc_dir, hm.group(2))
            if resolved is None or resolved in present:
                return m.group(0)
            count += 1
            return "<a" + _DROP_HREF_ATTR_RE.sub("", attrs, count=1) + slash + ">"

        return _ANCHOR_TAG_RE.sub(repl, part), count

    return outside_protected_map(text, fix)


def prune_missing_images(
    text: str, doc_dir: str, present: frozenset[str]
) -> tuple[str, dict[str, int]]:
    """Remove or unwrap `<img>` elements whose src is absent from the archive.

    An image that is not in the archive never renders; readers show either
    nothing or the alt text. With alt text, the element is replaced by that
    text (it arrives escaped from the attribute, so the words survive);
    without, the dead element goes entirely. External schemes are untouched.
    Returns per-shape counters, so this one does its protected-span split
    manually (outside_protected_map sums one int; here two counters merge).
    """

    def fix_part(part: str) -> tuple[str, dict[str, int]]:
        counts: dict[str, int] = {}

        def repl(m: re.Match) -> str:
            attrs = m.group(1)
            sm = _SRC_ATTR_RE.search(attrs)
            if not sm:
                return m.group(0)
            resolved = _resolve_href(doc_dir, sm.group(2))
            if resolved is None or resolved in present:
                return m.group(0)
            am = _ALT_ATTR_RE.search(attrs)
            alt = am.group(2) if am else ""
            if alt.strip():
                key = "missing_imgs_unwrapped"
            else:
                key = "missing_imgs_pruned"
            counts[key] = counts.get(key, 0) + 1
            return alt if key == "missing_imgs_unwrapped" else ""

        return _IMG_TAG_RE.sub(repl, part), counts

    total: dict[str, int] = {}
    if "<!" in text:
        parts = _PROTECTED_RE.split(text)
        for i in range(0, len(parts), 2):
            parts[i], c = fix_part(parts[i])
            for k, v in c.items():
                total[k] = total.get(k, 0) + v
        return "".join(parts), total
    return fix_part(text)


def prune_missing_resources_doc(
    text: str, doc_dir: str, present: frozenset[str]
) -> tuple[str, dict[str, int]]:
    """All content-document shapes of --prune-missing-resources, in sequence."""
    text, n = prune_dead_links(text, doc_dir, present)
    counts: dict[str, int] = {"dead_links_pruned": n} if n else {}
    text, n = strip_missing_file_hrefs(text, doc_dir, present)
    if n:
        counts["missing_file_hrefs_stripped"] = n
    text, img_counts = prune_missing_images(text, doc_dir, present)
    for k, v in img_counts.items():
        counts[k] = counts.get(k, 0) + v
    return text, counts


def strip_broken_anchors_doc(
    text: str,
    doc_dir: str,
    self_ids: frozenset[str],
    ids_by_doc: dict[str, frozenset[str]],
) -> tuple[str, dict[str, int]]:
    """Strip href attributes that cannot resolve, keeping the anchor text.

    Two shapes, both epubcheck-visible:
    - href="#frag" or href="doc#frag" where the target document exists but
      does not define the fragment (RSC-020 'fragment identifier not defined'
      / RSC-012 'points to the wrong element'). The href goes; the anchor and
      its inner text stay byte-for-byte.
    - href carrying a scheme no reader resolves (kindle:embed:, file:, ...).
    A target document absent from the archive is left for
    --prune-missing-resources (file shape) or the spine-integrity report
    (whole-document shape): this fix never guesses at a repair destination.
    """

    def fix_part(part: str) -> tuple[str, dict[str, int]]:
        counts: dict[str, int] = {}

        def repl(m: re.Match) -> str:
            attrs, slash = m.group(1), m.group(2)
            hm = _HREF_ATTR_RE.search(attrs)
            if not hm:
                return m.group(0)
            href = hm.group(2)
            dead = False
            key = "nonfile_scheme_hrefs_stripped"
            sm = _URL_SCHEME_RE.match(href)
            if sm:
                dead = sm.group(0)[:-1].lower() not in _RESOLVABLE_SCHEMES
            elif "#" in href:
                target, frag = href.partition("#")[::2]
                if target:
                    ids = ids_by_doc.get(_resolve_href(doc_dir, target) or "")
                else:
                    ids = self_ids
                if ids is not None and frag not in ids:
                    dead = True
                    key = "broken_fragment_hrefs_stripped"
            if not dead:
                return m.group(0)
            counts[key] = counts.get(key, 0) + 1
            return "<a" + _DROP_HREF_ATTR_RE.sub("", attrs, count=1) + slash + ">"

        return _ANCHOR_TAG_RE.sub(repl, part), counts

    total: dict[str, int] = {}
    if "<!" in text:
        parts = _PROTECTED_RE.split(text)
        for i in range(0, len(parts), 2):
            parts[i], c = fix_part(parts[i])
            for k, v in c.items():
                total[k] = total.get(k, 0) + v
        return "".join(parts), total
    return fix_part(text)


def strip_ncx_broken_fragments(
    ncx_text: str, ncx_dir: str, ids_by_doc: dict[str, frozenset[str]]
) -> tuple[str, int]:
    """Point `<content src="doc#frag"/>` at `doc` when `doc` exists but does
    not define `frag` (RSC-012).

    Chapter navigation survives at document precision; the fragment is
    dropped, never re-pointed at a guessed sibling document — Mobipocket
    filepos anchors drift when a converter re-splits the flow, and the id may
    exist nowhere at all. A target absent from the archive entirely is left
    alone: that is the spine-integrity report's finding, not a repair.
    """

    def fix(part: str) -> tuple[str, int]:
        count = 0

        def repl(m: re.Match) -> str:
            nonlocal count
            prefix, quote, src = m.group(1), m.group(2), m.group(3)
            if _URL_SCHEME_RE.match(src) or "#" not in src:
                return m.group(0)
            target, frag = src.partition("#")[::2]
            resolved = _resolve_href(ncx_dir, target) if target else None
            ids = ids_by_doc.get(resolved or "") if resolved else None
            if ids is None or frag in ids:
                return m.group(0)
            count += 1
            return f"{prefix}{quote}{target}{quote}"

        return _NCX_CONTENT_SRC_RE.sub(repl, part), count

    return outside_protected_map(ncx_text, fix)


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
    strip_epub3_attrs: bool = False,
    downgrade_epub3: bool = False,
    prune_missing: bool = False,
    strip_anchors: bool = False,
    url_spaces: bool = False,
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
    With `strip_epub3_attrs`, scrub the EPUB3-only attributes epubcheck rejects on an
    EPUB2 package (page-progression-direction, epub:type, aria-label; fixed set).
    With `downgrade_epub3`, downgrade EPUB3/HTML5 semantic elements (figure, figcaption,
    section) to their EPUB2 equivalents with the semantic name kept as a class; names a
    stylesheet styles as an element selector are protected for the whole book.
    With `prune_missing`, remove references to files the archive does not contain
    (RSC-007/PKG-010): dead <link> elements, anchors' href to absent files, absent
    <img> sources (replaced by their alt text when they carry one), and orphaned
    non-spine OPF manifest items. Spine documents are never pruned.
    With `strip_anchors`, strip href attributes that cannot resolve: a #fragment the
    target document does not define (RSC-020/RSC-012; NCX navTargets keep the document
    target) and non-resolvable URI schemes (kindle:, file:). Anchor text is always
    preserved byte-for-byte.
    With `url_spaces`, percent-encode raw spaces in src/href attribute values across
    the package (OPF href, NCX src, content src/href): a literal space is not a valid
    URL (RSC-020).
    """
    report = RepairReport()

    # `src` is opened before `dst`, so an unreadable archive still raises before the
    # output file is created.
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        opf = _locate_opf(zin)
        opf_text = zin.read(opf).decode("utf-8", "replace") if opf else None
        uid = opf_unique_id(opf_text) if opf_text is not None else None
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
        downgrade_css_tags: frozenset[str] = frozenset()
        if illegal_tags or downgrade_epub3:
            css_texts = [
                zin.read(i).decode("utf-8", "replace")
                for i in zin.infolist()
                if i.filename.lower().endswith(".css")
            ]
            if illegal_tags:
                book_css_tags = css_protected_tags(*css_texts)
            if downgrade_epub3:
                downgrade_css_tags = css_protected_tags(
                    *css_texts, tags=tuple(EPUB3_DOWNGRADE_TAGS)
                )

        # The broken-reference context (Phase 12 flags) is a whole-book property,
        # computed once: the archive's file set for existence checks, and every
        # content document's id set for fragment checks.
        present: frozenset[str] = frozenset()
        ids_by_doc: dict[str, frozenset[str]] = {}
        spine_ids: set[str] = set()
        opf_dir = posixpath.dirname(opf) if opf else ""
        if prune_missing or strip_anchors:
            present = frozenset(_norm_path(n) for n in zin.namelist())
        if strip_anchors:
            for i in zin.infolist():
                if i.filename.lower().endswith(CONTENT_SUFFIXES):
                    # The id sets must describe the documents as they will look when
                    # the anchor pass runs, so replicate the transforms that can move
                    # an id or a fragment (the core pass, plus the two opt-ins that
                    # rename ids): a --fix-id-colons rename rewrites ids and their
                    # #fragment refs together, and checking post-rename fragments
                    # against pre-rename id sets would strip valid references.
                    t, _ = apply_transforms(
                        zin.read(i).decode("utf-8", "replace"), HTML_TRANSFORMS
                    )
                    if reserialize:
                        t, _ = reserialize_if_broken(t)
                    if id_colons:
                        t, _ = fix_id_colons(t)
                    ids_by_doc[_norm_path(i.filename)] = frozenset(
                        m.group(3) for m in _XML_ID_RE.finditer(t)
                    )
        if prune_missing and opf_text is not None:
            spine_ids = {m.group(2) for m in _IDREF_ATTR_RE.finditer(opf_text)}

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
                if url_spaces:
                    text, n = encode_url_spaces(text)
                    if n:
                        counts["url_spaces_encoded"] = n
                if strip_anchors:
                    text, n = strip_ncx_broken_fragments(
                        text, posixpath.dirname(name), ids_by_doc
                    )
                    if n:
                        counts["ncx_fragments_stripped"] = n
                synced = False
                if uid:
                    text, synced = sync_ncx_uid(text, uid)
                    if synced:
                        report.ncx_uid_synced = True
                if counts or synced:
                    report.add(counts)
                    report.files_changed += 1
                    data = text.encode("utf-8")
            elif low.endswith(".opf") and (
                fix_ids or page_map or strip_epub3_attrs or prune_missing or url_spaces
            ):
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
                if strip_epub3_attrs:
                    text, n = strip_epub3_attributes(text)
                    if n:
                        report.add({"epub3_attrs_stripped": n})
                        opf_changed = True
                if prune_missing:
                    text, n = prune_missing_manifest_items(
                        text, opf_dir, present, spine_ids
                    )
                    if n:
                        report.add({"manifest_items_pruned": n})
                        opf_changed = True
                if url_spaces:
                    text, n = encode_url_spaces(text)
                    if n:
                        report.add({"url_spaces_encoded": n})
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
                if strip_epub3_attrs:
                    text, n = strip_epub3_attributes(text)
                    if n:
                        counts["epub3_attrs_stripped"] = n
                if downgrade_epub3:
                    protected = downgrade_css_tags | style_block_tags(
                        text, tags=tuple(EPUB3_DOWNGRADE_TAGS)
                    )
                    text, n = downgrade_epub3_tags(text, protected_tags=protected)
                    if n:
                        counts["epub3_tags_downgraded"] = n
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
                if strip_anchors:
                    text, acounts = strip_broken_anchors_doc(
                        text,
                        posixpath.dirname(name),
                        ids_by_doc.get(_norm_path(name), frozenset()),
                        ids_by_doc,
                    )
                    if acounts:
                        counts.update(acounts)
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
                if prune_missing:
                    text, pcounts = prune_missing_resources_doc(
                        text, posixpath.dirname(name), present
                    )
                    if pcounts:
                        counts.update(pcounts)
                if url_spaces:
                    text, n = encode_url_spaces(text)
                    if n:
                        counts["url_spaces_encoded"] = n
                if counts:
                    report.add(counts)
                    report.files_changed += 1
                    data = text.encode("utf-8")

            zout.writestr(item, data, compress_type=item.compress_type)

    return report
