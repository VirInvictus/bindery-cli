"""Opt-in lossy repair: strip print page numbers (and running headers) baked into
the body text by a PDF/OCR conversion.

This is deliberately NOT one of the core transforms. Bindery's other fixes are
semantics-preserving (they render identically to the author's intent); this one
removes visible content the author never wrote (a converter's page numbers and
running headers) and, where such a number split a sentence, rejoins the two
paragraphs. It is therefore gated differently (epubcheck must be no worse, since
the gain is invisible to epubcheck) and is off unless --strip-pagination is given.

The detection mirrors CalibreQuarry's audit_epub_pagenumbers.py: a standalone
<p> whose whole text is a bare number is only treated as baked when it genuinely
interrupts prose. Merging happens only on the two confident interrupt signals (a
lowercase continuation after the number, or a word split across it); otherwise the
number is deleted and the paragraph break is left as-is. Running headers/footers
are detected as short blocks repeated across the whole book.

Three independent safety nets, any failure aborts the edit and returns the
document unchanged:
  1. character conservation: every visible character of real prose is preserved
     (only the removed numbers/headers disappear, and word-split hyphens close up);
  2. tag balance: <p> and <a> stay balanced after splicing;
  3. the caller's epubcheck gate (no net-new fatals or errors) is the final oracle.

Scope is intentionally `<p>` elements only, which is where the defect is carried
in every observed case; this keeps the splicing well understood.
"""

from __future__ import annotations

import html as _html
import re
from collections import Counter

PROSE_MIN = 120  # a neighbour this long counts as a real prose paragraph
RUNHEAD_MIN_REPEAT = 8  # a short block repeated this often is a running header
RUNHEAD_MAX_LEN = 60  # running headers are short

# <p> cannot nest <p> in valid HTML, so a non-greedy match to the next </p> is exact.
_P_RE = re.compile(r"<p\b[^>]*>.*?</p>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_INT_RE = re.compile(r"\d{1,4}\Z")
_ROMAN_RE = re.compile(r"[ivxlcdm]{2,7}\Z", re.IGNORECASE)
# An element carrying an id is a navigation target (page-list, internal link); it must
# survive even when its visible number is removed, or the nav breaks.
_ID_ANCHOR_RE = re.compile(
    r"<a\b[^>]*\bid=\"[^\"]*\"[^>]*>.*?</a>|<a\b[^>]*\bid=\"[^\"]*\"[^>]*/>",
    re.IGNORECASE | re.DOTALL,
)
# strip a trailing hyphen that ends the visible text, even if closing tags follow it
_TRAIL_HYPHEN_RE = re.compile(r"-(\s*(?:</[a-zA-Z][^>]*>\s*)*)\Z")


def roman_value(s: str) -> int | None:
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    s = s.lower()
    for i, c in enumerate(s):
        if c not in vals:
            return None
        v = vals[c]
        total += -v if (i + 1 < len(s) and vals[s[i + 1]] > v) else v
    return total or None


def number_value(text: str) -> int | None:
    """A bare page-number-ish value (1-9999 arabic, or a roman numeral), else None.
    Year-range values (1500-2099) are excluded: those are chronologies, not pages."""
    if _INT_RE.fullmatch(text):
        v = int(text)
        return None if 1500 <= v <= 2099 else v
    if _ROMAN_RE.fullmatch(text):
        return roman_value(text)
    return None


def _visible(inner_html: str) -> str:
    """The collapsed, entity-decoded visible text of a fragment."""
    return _WS_RE.sub(" ", _html.unescape(_TAG_RE.sub(" ", inner_html))).strip()


def _char_norm(s: str) -> str:
    """Lowercase, dropping whitespace and hyphens: the canonical form for the
    character-conservation check (so a word split closing up reads as no change)."""
    return re.sub(r"[\s\-‐‑]", "", _html.unescape(s).lower())


def collect_runheads(htmls: list[str]) -> set[str]:
    """Running headers/footers/watermarks: short non-numeric blocks repeated across
    the whole book. Whole-book scope is why this takes every content document."""
    counter: Counter[str] = Counter()
    for html_text in htmls:
        for m in _P_RE.finditer(html_text):
            text = _visible(m.group(0)[m.group(0).index(">") + 1 : -4])
            if text and len(text) <= RUNHEAD_MAX_LEN and number_value(text) is None:
                counter[text] += 1
    return {t for t, n in counter.items() if n >= RUNHEAD_MIN_REPEAT}


def detect_page_layer(htmls: list[str], runheads: set[str]) -> bool:
    """Decide whether a book has a genuine print-page-number layer, which licenses the
    aggressive between-paragraph deletion. Two signals must BOTH hold, so a merely
    chapter-numbered book is never mistaken for a paginated one:

    1. a substantial body of standalone arabic numbers (>= 20), more than any chapter
       list; and
    2. several confident sentence interrupts (>= 3), the fingerprint of page numbers
       bleeding into the text flow. Chapter numbers open chapters with a capital, so a
       chapter-numbered book scores zero here regardless of how many chapters it has.

    The interrupt count, not an ascending run, is the discriminator, because content
    documents are visited in archive order (not reading order), which scrambles any
    cross-file sequence."""
    vals = 0
    confident = 0
    for html_text in htmls:
        blocks = [_Block(m) for m in _P_RE.finditer(html_text)]
        for b in blocks:
            if b.kind == "prose" and b.text in runheads:
                b.kind = "runhead"
        for i, b in enumerate(blocks):
            if b.kind == "number":
                if b.text.isdigit():
                    vals += 1
                if _is_baked(blocks, i)[1]:
                    confident += 1
    return vals >= 20 and confident >= 3


class _Block:
    __slots__ = ("start", "end", "open_tag", "inner", "text", "kind", "num")

    def __init__(self, m: re.Match):
        full = m.group(0)
        gt = full.index(">") + 1
        self.start = m.start()
        self.end = m.end()
        self.open_tag = full[:gt]
        self.inner = full[gt:-4]  # strip the trailing </p>
        self.text = _visible(self.inner)
        self.num = number_value(self.text)
        if self.num is not None:
            self.kind = "number"
        elif self.text == "":
            self.kind = "empty"
        else:
            self.kind = "prose"  # 'runhead' is assigned later, needs the global set

    @property
    def is_prose(self) -> bool:
        return self.kind == "prose"


def _nearest(blocks: list[_Block], i: int, step: int, *, skip_empty: bool):
    j = i + step
    while 0 <= j < len(blocks):
        if not (skip_empty and blocks[j].kind == "empty"):
            return j
        j += step
    return None


def _is_baked(blocks: list[_Block], i: int) -> tuple[bool, bool]:
    """Return (baked, confident_interrupt) for the number block at index i."""
    pp = _nearest_prose(blocks, i, -1)
    np = _nearest_prose(blocks, i, +1)
    prose_prev = pp is not None and len(blocks[pp].text) > PROSE_MIN
    prose_next = np is not None and len(blocks[np].text) > PROSE_MIN
    if not (prose_prev or prose_next):
        return False, False
    prev_ne = _nearest(blocks, i, -1, skip_empty=True)
    next_ne = _nearest(blocks, i, +1, skip_empty=True)
    word_split = bool(pp is not None and _visible(blocks[pp].inner).endswith("-"))
    lower_cont = bool(
        next_ne is not None
        and blocks[next_ne].is_prose
        and blocks[next_ne].text[:1].islower()
    )
    prev_runhead = prev_ne is not None and blocks[prev_ne].kind == "runhead"
    next_runhead = next_ne is not None and blocks[next_ne].kind == "runhead"
    prev_unfinished = bool(
        prose_prev
        and pp is not None
        and (blocks[pp].text[-1].islower() or blocks[pp].text[-1] == ",")
    )
    confident = word_split or lower_cont
    baked = (
        confident
        or (prev_unfinished and (prose_next or next_runhead))
        or (prev_runhead and next_runhead)
    )
    return baked, confident


def _nearest_prose(blocks: list[_Block], i: int, step: int):
    j = i + step
    while 0 <= j < len(blocks):
        if blocks[j].is_prose:
            return j
        j += step
    return None


def strip_pagination_doc(
    html_text: str, runheads: set[str], delete_layer: bool = False
) -> tuple[str, int]:
    """Remove baked page numbers/running headers from one content document.

    A number is removed when it is a "baked" interrupt (the conservative default) or,
    when `delete_layer` is set (the book has a confirmed dense page-number layer), when
    it is an arabic page number sitting in the body text. Paragraphs are rejoined ONLY
    on a confident interrupt (lowercase continuation or word split); every other removal
    is delete-only, leaving the paragraph break as-is. Roman numerals are removed only on
    a confident interrupt, so roman chapter/front-matter numbering is preserved.

    Returns (new_html, blocks_removed); the input unchanged (count 0) if a safety net
    fails."""
    blocks = [_Block(m) for m in _P_RE.finditer(html_text)]
    if not blocks:
        return html_text, 0
    for b in blocks:
        if b.kind == "prose" and b.text in runheads:
            b.kind = "runhead"

    prose_idx = [i for i, b in enumerate(blocks) if b.is_prose]
    drop: set[int] = set()
    unions: list[tuple[int, int]] = []  # (left prose idx, right prose idx) to merge

    # Block-centric: decide each number independently, so page numbers are caught
    # wherever they sit (between prose, beside a heading, next to a chapter number).
    for i, b in enumerate(blocks):
        if b.kind != "number":
            continue
        baked, confident = _is_baked(blocks, i)
        is_arabic = b.text.isdigit()
        pp = _nearest_prose(blocks, i, -1)
        np = _nearest_prose(blocks, i, +1)
        # In a confirmed page-layer book every standalone arabic <p>N</p> is a page
        # number (char conservation guarantees only digits are lost), so the layer path
        # needs no prose-proximity gate; that gate would miss numbers among short
        # dialogue lines. Roman numerals still fall through (chapter numbers preserved).
        removable = baked or (delete_layer and is_arabic)
        if not removable:
            continue  # roman chapter numbers, sparse-book numbers, year-range values
        drop.add(i)
        # a running header/footer hugging a removed number is the same page furniture
        for side in (
            _nearest(blocks, i, -1, skip_empty=True),
            _nearest(blocks, i, +1, skip_empty=True),
        ):
            if side is not None and blocks[side].kind == "runhead":
                drop.add(side)
        if confident and pp is not None and np is not None:
            unions.append((pp, np))
            for j in range(pp + 1, np):  # cruft between the merged halves
                if blocks[j].kind in ("number", "runhead", "empty"):
                    drop.add(j)

    if not drop and not unions:
        return html_text, 0

    # Union consecutive prose blocks into merge groups.
    parent = {i: i for i in prose_idx}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, c in unions:
        parent[find(c)] = find(a)
    groups: dict[int, list[int]] = {}
    for i in prose_idx:
        groups.setdefault(find(i), []).append(i)

    # Build the splice edits: (start, end, replacement).
    edits: list[tuple[int, int, str]] = []
    handled: set[int] = set()
    for members in groups.values():
        members.sort()
        if len(members) == 1:
            continue  # untouched prose paragraph
        first, last = members[0], members[-1]
        # Everything in [first.start, last.end] is replaced by one merged <p>.
        anchors: list[str] = []
        for j in range(first, last + 1):
            if j in members:
                continue
            anchors += _ID_ANCHOR_RE.findall(blocks[j].inner)
        parts = [blocks[members[0]].inner]
        for k in range(1, len(members)):
            left = blocks[members[k - 1]]
            if _visible(left.inner).endswith("-"):
                parts[-1] = _TRAIL_HYPHEN_RE.sub(r"\1", parts[-1])
                sep = ""
            else:
                sep = " "
            parts.append(sep + blocks[members[k]].inner)
        merged_inner = parts[0] + "".join(anchors) + "".join(parts[1:])
        merged = blocks[first].open_tag + merged_inner + "</p>"
        edits.append((blocks[first].start, blocks[last].end, merged))
        for j in range(first, last + 1):
            handled.add(j)

    # Delete-only drops that are not inside a merge span.
    for j in sorted(drop):
        if j in handled:
            continue
        b = blocks[j]
        anchors = _ID_ANCHOR_RE.findall(b.inner)
        repl = (b.open_tag + "".join(anchors) + "</p>") if anchors else ""
        edits.append((b.start, b.end, repl))

    edits.sort(key=lambda e: e[0], reverse=True)
    out = html_text
    for start, end, repl in edits:
        out = out[:start] + repl + out[end:]

    removed = sum(1 for j in drop if blocks[j].kind == "number")
    if removed == 0:
        return html_text, 0
    if not _safe(html_text, out, blocks, drop):
        return html_text, 0
    return out, removed


def _safe(before: str, after: str, blocks: list[_Block], drop: set[int]) -> bool:
    """Character conservation + tag balance. Either failing means the splice went
    wrong, so the caller keeps the original document."""
    removed_chars = Counter()
    for j in drop:
        removed_chars += Counter(_char_norm(blocks[j].text))
    if Counter(_char_norm(_visible(after))) + removed_chars != Counter(
        _char_norm(_visible(before))
    ):
        return False
    for tag in ("p", "a"):
        # Count opening tags that are NOT self-closing (`<p/>` needs no `</p>`), so
        # the pre-existing self-closing tags these messy EPUBs carry do not read as an
        # imbalance. A correct splice keeps real opens == closes.
        opens = len(re.findall(rf"<{tag}\b[^>]*?(?<!/)>", after, re.IGNORECASE))
        closes = len(re.findall(rf"</{tag}\b", after, re.IGNORECASE))
        if opens != closes:
            return False
    return True
