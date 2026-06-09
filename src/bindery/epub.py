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

from .reserialize import reserialize_if_broken
from .transforms import (
    HTML_TRANSFORMS,
    XML_TRANSFORMS,
    apply_transforms,
    strip_invalid_attributes,
)

CONTENT_SUFFIXES = (".xhtml", ".html", ".htm", ".xml")

_UID_ATTR_RE = re.compile(r'unique-identifier="([^"]+)"')
_ITEM_ID_RE = re.compile(r'(<item\b[^>]*?\bid=")([^"]*)(")', re.IGNORECASE)


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
    and update every reference to them (spine idref, spine toc). Returns (text, count).

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
    # update the references: spine <itemref idref="..."> and <spine toc="...">
    out = re.sub(r'(\bidref=")([^"]*)(")', repl_attr, out)
    out = re.sub(
        r'(<spine\b[^>]*?\btoc=")([^"]*)(")', repl_attr, out, flags=re.IGNORECASE
    )
    return out, len(rename)


_DTB_UID_RE = re.compile(
    r'(<meta\b[^>]*\bname="dtb:uid"[^>]*\bcontent=")([^"]*)(")', re.IGNORECASE
)
_DTB_UID_RE_REV = re.compile(
    r'(<meta\b[^>]*\bcontent=")([^"]*)("[^>]*\bname="dtb:uid"[^>]*>)', re.IGNORECASE
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
    ident = attr.group(1)
    m = re.search(rf'id="{re.escape(ident)}"[^>]*>([^<]+)<', opf_text) or re.search(
        rf'<dc:identifier[^>]*id="{re.escape(ident)}"[^>]*>([^<]+)', opf_text
    )
    return m.group(1).strip() if m else None


def sync_ncx_uid(ncx_text: str, uid: str) -> tuple[str, bool]:
    """Set the NCX dtb:uid meta to `uid`. Returns (text, changed)."""
    cur = _DTB_UID_RE.search(ncx_text) or _DTB_UID_RE_REV.search(ncx_text)
    if not cur:
        return ncx_text, False
    if cur.group(2) == uid:
        return ncx_text, False
    new = _DTB_UID_RE.sub(rf"\g<1>{uid}\g<3>", ncx_text)
    if new == ncx_text:
        new = _DTB_UID_RE_REV.sub(rf"\g<1>{uid}\g<3>", ncx_text)
    return new, True


def ncx_uid_mismatch(src: Path) -> bool:
    """Cheaply detect NCX-001 (toc.ncx dtb:uid != OPF unique-identifier) without epubcheck."""
    try:
        with zipfile.ZipFile(src) as z:
            names = z.namelist()
            opf = next((n for n in names if n.lower().endswith(".opf")), None)
            ncx = next((n for n in names if n.lower().endswith(".ncx")), None)
            if not opf or not ncx:
                return False
            uid = opf_unique_id(z.read(opf).decode("utf-8", "replace"))
            if not uid:
                return False
            text = z.read(ncx).decode("utf-8", "replace")
            m = _DTB_UID_RE.search(text) or _DTB_UID_RE_REV.search(text)
            return bool(m and m.group(2) != uid)
    except zipfile.BadZipFile, OSError:
        return False


def repair_epub(
    src: Path,
    dst: Path,
    *,
    fix_ids: bool = False,
    reserialize: bool = False,
    strip_attrs: bool = False,
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
        names = z.namelist()
        opf = next((n for n in names if n.lower().endswith(".opf")), None)
        uid = opf_unique_id(z.read(opf).decode("utf-8", "replace")) if opf else None

    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        if "mimetype" in zin.namelist():
            zout.writestr(
                "mimetype", zin.read("mimetype"), compress_type=zipfile.ZIP_STORED
            )

        for item in zin.infolist():
            name = item.filename
            if name == "mimetype":
                continue
            data = zin.read(name)
            low = name.lower()

            if low.endswith(".ncx"):
                text = data.decode("utf-8", "replace")
                text, counts = apply_transforms(text, XML_TRANSFORMS)
                if uid:
                    text, synced = sync_ncx_uid(text, uid)
                    if synced:
                        report.ncx_uid_synced = True
                if counts or report.ncx_uid_synced:
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
                    text, an = strip_invalid_attributes(text)
                    if an:
                        counts["stripped_invalid_attrs"] = (
                            counts.get("stripped_invalid_attrs", 0) + an
                        )
                if reserialize:
                    text, rn = reserialize_if_broken(text)
                    if rn:
                        counts["reserialized"] = counts.get("reserialized", 0) + rn
                if counts:
                    report.add(counts)
                    report.files_changed += 1
                data = text.encode("utf-8")

            zout.writestr(item, data, compress_type=item.compress_type)

    return report
