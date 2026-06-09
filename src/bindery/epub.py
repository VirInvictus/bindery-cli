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

from .transforms import HTML_TRANSFORMS, XML_TRANSFORMS, apply_transforms

CONTENT_SUFFIXES = (".xhtml", ".html", ".htm", ".xml")

_UID_ATTR_RE = re.compile(r'unique-identifier="([^"]+)"')
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


def repair_epub(src: Path, dst: Path) -> RepairReport:
    """Write a repaired copy of `src` to `dst`. Returns a RepairReport."""
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
            elif low.endswith(CONTENT_SUFFIXES):
                text = data.decode("utf-8", "replace")
                text, counts = apply_transforms(text, HTML_TRANSFORMS)
                if counts:
                    report.add(counts)
                    report.files_changed += 1
                data = text.encode("utf-8")

            zout.writestr(item, data, compress_type=item.compress_type)

    return report
