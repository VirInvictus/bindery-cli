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
    apply_transforms,
    strip_invalid_attributes,
)

CONTENT_SUFFIXES = (".xhtml", ".html", ".htm", ".xml")

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


def fix_manifest_ids(opf_text: str) -> tuple[str, int]:
    """Rename manifest item ids that are not valid XML names (e.g. start with a digit)
    and update every reference to them: spine idref, spine toc, item fallback and
    media-overlay, and the EPUB 2 cover meta. Returns (text, count).

    Calibre-converted books often carry manifest ids copied from random filenames that
    start with a digit; epubcheck rejects them. The href/filenames are untouched.
    """
    existing = {m.group(2) for m in _ITEM_ID_RE.finditer(opf_text)}
    rename: dict[str, str] = {}
    for old in existing:
        if not _is_invalid_ncname(old):
            continue
        new = "id_" + old.replace(":", "_")
        while new in existing or new in rename.values():
            new = "_" + new
        rename[old] = new
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
) -> RepairReport:
    """Write a repaired copy of `src` to `dst`. Returns a RepairReport.

    With `fix_ids`, also rewrite invalid manifest ids in the OPF (off by default, since
    it touches the OPF; the dc: metadata is never altered, only item ids and their refs).
    With `strip_attrs`, drop attributes that are invalid XML (digit-led names, unbound
    namespace prefixes like Office VML `v:shapes`).
    With `reserialize`, rebuild any content document that is still not well-formed via
    html5lib (closes unclosed non-void elements); good documents are left untouched.
    """
    report = RepairReport()

    with zipfile.ZipFile(src) as z:
        opf = _locate_opf(z)
        uid = opf_unique_id(z.read(opf).decode("utf-8", "replace")) if opf else None
        # Running-header detection and the page-layer decision need the whole book, so
        # collect content text once up front. Only when the lossy strip is requested.
        runheads: set[str] = set()
        delete_layer = False
        if strip_pagination:
            htmls = [
                z.read(i).decode("utf-8", "replace")
                for i in z.infolist()
                if i.filename.lower().endswith(CONTENT_SUFFIXES)
            ]
            runheads = collect_runheads(htmls)
            delete_layer = detect_page_layer(htmls, runheads)

    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        if "mimetype" in zin.namelist():
            zout.writestr(
                "mimetype", zin.read("mimetype"), compress_type=zipfile.ZIP_STORED
            )

        # An entry is re-encoded only when a fix actually fired; an untouched entry is
        # copied byte-for-byte. Re-encoding the decode("utf-8", "replace") round-trip
        # of an unchanged file would silently swap any non-UTF-8 bytes for U+FFFD.
        for item in zin.infolist():
            name = item.filename
            if name == "mimetype":
                continue
            # read(item), not read(name): with duplicate entry names (seen in broken
            # EPUBs), read(name) returns the first entry's bytes for every duplicate.
            data = zin.read(item)
            low = name.lower()

            if low.endswith(".ncx"):
                text = data.decode("utf-8", "replace")
                text, counts = apply_transforms(text, XML_TRANSFORMS)
                synced = False
                if uid:
                    text, synced = sync_ncx_uid(text, uid)
                    if synced:
                        report.ncx_uid_synced = True
                if counts or synced:
                    report.add(counts)
                    report.files_changed += 1
                    data = text.encode("utf-8")
            elif fix_ids and low.endswith(".opf"):
                text = data.decode("utf-8", "replace")
                text, n = fix_manifest_ids(text)
                if n:
                    report.add({"fix_manifest_ids": n})
                    report.files_changed += 1
                    data = text.encode("utf-8")
            elif low.endswith(CONTENT_SUFFIXES):
                text = data.decode("utf-8", "replace")
                text, counts = apply_transforms(text, HTML_TRANSFORMS)
                if strip_attrs:
                    text, n = strip_invalid_attributes(text)
                    if n:
                        counts["stripped_invalid_attrs"] = n
                if reserialize:
                    text, n = reserialize_if_broken(text)
                    if n:
                        counts["reserialized"] = n
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
