"""Opt-in lossy repair: strip print page numbers (and running headers) baked into
the body text by a PDF/OCR conversion.

This is deliberately NOT one of the core transforms. Bindery's other fixes are
semantics-preserving (they render identically to the author's intent); this one
removes visible content the author never wrote (a converter's page numbers and
running headers) and, where such a number split a sentence, rejoins the two
paragraphs. It is therefore gated differently (epubcheck must be no worse, since
the gain is invisible to epubcheck) and is off unless --strip-pagination is given.

The detection is the same shape as this package's audit pagenumbers analyzer
(audit.py: the two evolved from one script), but the code is deliberately
separate: a standalone <p> whose whole text is a bare number is only treated as
baked when it genuinely interrupts prose. Merging happens only on the two
confident interrupt signals (a lowercase continuation after the number, or a
word split across it); otherwise the number is deleted and the paragraph break
is left as-is. Running headers/footers are detected as short blocks repeated
across the whole book. (Note the two number grammars are NOT identical:
number_value here excludes year ranges 1500-2099 outright, while the audit
compensates for years at its call site. A third caller must copy one, not
invent a third.)

Four independent safety nets, any failure aborts the edit and returns the
document unchanged:
  1. character conservation: every visible character of real prose is preserved
     (only the removed numbers/headers disappear, and word-split hyphens close up);
  2. tag balance: <p> and <a> stay balanced after splicing, and no element's
     open/close balance changes at all (the all-tag delta net, v1.2.5);
  3. prose flow: every destructive decision operates inside a verified prose
     corridor -- gaps between consecutive owned blocks are pure whitespace. A
     tag in a gap (a self-closing divider, table cell markup) is layout the
     block model does not own, and no merge, sweep, or runhead cascade crosses
     it. This is what makes the "<p> elements only" scope below real rather
     than aspirational: the All About Cake cookbook (2026-11-21) merged across
     a <p class="rec_line"/><table><td> boundary, discarded the table openers,
     and orphaned the rest of the table behind the merged paragraph;
  4. the caller's epubcheck gate (no net-new fatals or errors) is the final oracle.

Scope is intentionally `<p>` elements only, which is where the defect is carried
in every observed case; this keeps the splicing well understood. Numbers belonging
to a document's own numbering system (verse stanzas, sections) are out of scope
entirely: their values repeat across documents, unlike page numbers, which are
unique by construction (v1.2.6).
"""

from __future__ import annotations

import html as _html
import re
from collections import Counter

PROSE_MIN = 120  # a neighbour this long counts as a real prose paragraph
RUNHEAD_MIN_REPEAT = 8  # a short block repeated this often is a running header
RUNHEAD_MAX_LEN = 60  # running headers are short

# <p> cannot nest <p> in valid HTML, so a non-greedy match to the next </p> is exact.
# The (?<!/) keeps a SELF-CLOSING <p .../> start tag out of the match (XML-legal,
# common on divider paragraphs): it has no </p>, so a match starting there ran
# forward to the NEXT paragraph's close tag, welding both into one "paragraph"
# whose text could read as a page number and strip real markup with it (issue #31).
_P_RE = re.compile(r"<p\b[^>]*?(?<!/)>.*?</p>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_INT_RE = re.compile(r"\d{1,4}\Z")
# A strict roman-numeral grammar, not a character set. The old [ivxlcdm]{2,7} also
# matched ordinary English words built from those letters (mid, dim, mix, lid,
# civil), and a standalone word between two prose paragraphs is exactly where a
# confident interrupt fires, so real words were deleted as page numbers
# (reported 2026-09-08). Two gates replace it: the numeral must be well-formed
# (subtractive pairs explicit), and its value must stay under 100, because page
# numbers in roman form do not run that high while words do (mix = 1009, civ = 104).
# Public since v0.44.0: this is the CANONICAL page-number grammar. audit.py
# imports it rather than carrying a second copy; if a future grammar ever
# needs to differ between the two callers, that is a decision to make here,
# not a third copy to fork.
ROMAN_RE = re.compile(
    r"m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})", re.IGNORECASE
)
ROMAN_MAX = 100
# An element carrying an id is a navigation target (page-list, internal link); it must
# survive even when its visible number is removed, or the nav breaks. That covers both
# <a id=...> anchors inside a removed block and an id on the removed <p> itself.
# The anchor is the #30 lookbehind class generalized to xml:id (v1.2.1, issue #34):
# `\bid=` fires right after a hyphen, so `data-id="page7"` -- arbitrary data no
# reader can resolve -- over-protected a number block from the strip, while
# `xml:id` IS a real XML id the oracle resolves fragments against and stays.
_ID_ANCHOR_RE = re.compile(
    r"<a\b[^>]*(?:\s)(?:xml:)?id=(?:\"[^\"]*\"|'[^']*')[^>]*>.*?</a>"
    r"|<a\b[^>]*(?:\s)(?:xml:)?id=(?:\"[^\"]*\"|'[^']*')[^>]*/>",
    re.IGNORECASE | re.DOTALL,
)
_OPEN_ID_RE = re.compile(r"(?:^|\s)(?:xml:)?id=(\"[^\"]*\"|'[^']*')", re.IGNORECASE)
# strip a trailing hyphen that ends the visible text, even if closing tags follow it
_TRAIL_HYPHEN_RE = re.compile(r"-(\s*(?:</[a-zA-Z][^>]*>\s*)*)\Z")
# The all-tag delta net (see _safe): non-self-closing open tags and close tags,
# name captured for per-element balance comparison.
_OPEN_TAG_RE = re.compile(r"<([a-zA-Z][\w:.-]*)\b[^>]*?(?<!/)>")
_CLOSE_TAG_RE = re.compile(r"</([a-zA-Z][\w:.-]*)")


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
    Year-range values (1500-2099) are excluded: those are chronologies, not pages.
    Romans keep the old 2-7 character window (a lone i/v/x stayed too noisy)."""
    if _INT_RE.fullmatch(text):
        v = int(text)
        return None if 1500 <= v <= 2099 else v
    if 2 <= len(text) <= 7 and ROMAN_RE.fullmatch(text):
        v = roman_value(text)
        return v if v is not None and v < ROMAN_MAX else None
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


# Internal-numbering detection (v1.2.6, issue #38): stanza/section numbers restart
# per unit (canto, chapter, section), so their VALUES reappear across documents;
# page numbers are unique by construction (pages never repeat). A document whose
# standalone-number values are substantially shared with other documents carries
# internal numbering, and the strip takes no numbers from it. Cross-doc sequence
# resets are unusable directly (content docs are visited in archive order, which
# scrambles cross-file sequence), but value-sharing is order-independent. Don Juan
# lost 62 stanza numbers to the baked signals before this existed: enjambed
# stanzas end in commas, which is the exact verse analogue of a page-number
# sentence interrupt.
INTERNAL_SHARE_MIN = 0.25


def collect_internal_numbering(htmls: list[str]) -> list[bool]:
    """Per-document internal-numbering verdicts, aligned with htmls order.

    A document is internal-numbering when at least INTERNAL_SHARE_MIN of its
    standalone number values also appear in other documents. A single-document
    book can never be flagged (there is nothing to share against); front matter
    that restarts arabic numbering at 1 gets protected at the cost of missing
    those page numbers, never at the cost of stripping verse numbers."""
    doc_values: list[set[str]] = []
    for html_text in htmls:
        doc_values.append(
            {
                b.text
                for b in (_Block(m) for m in _P_RE.finditer(html_text))
                if b.kind == "number"
            }
        )
    seen_docs: Counter[str] = Counter()
    for values in doc_values:
        for v in values:
            seen_docs[v] += 1
    flags = []
    for values in doc_values:
        if not values:
            flags.append(False)
            continue
        shared = sum(1 for v in values if seen_docs[v] > 1)
        flags.append(shared / len(values) >= INTERNAL_SHARE_MIN)
    return flags


def detect_page_layer(
    htmls: list[str], runheads: set[str], internal_flags: list[bool] | None = None
) -> bool:
    """Decide whether a book has a genuine print-page-number layer, which licenses the
    aggressive between-paragraph deletion. Two signals must BOTH hold, so a merely
    chapter-numbered book is never mistaken for a paginated one:

    1. a substantial body of standalone arabic numbers (>= 20), more than any chapter
       list; and
    2. several confident sentence interrupts (>= 3), the fingerprint of page numbers
       bleeding into the text flow. Chapter numbers open chapters with a capital, so a
       chapter-numbered book scores zero here regardless of how many chapters it has.
       A confident interrupt also requires a flat prose corridor (every gap between
       the prose, the number, and the next prose is whitespace), so a cookbook's
       quantity cells beside lowercase ingredient cells -- the All About Cake shape
       (v1.2.5: 110 cell digits, 4 cell-boundary "interrupts") -- never license
       the sweep.

    Documents whose numbers are internal numbering (stanza/section values shared
    across documents, v1.2.6) contribute neither signal: Don Juan's 1,948+ stanza
    numbers must never license a sweep over verse numbering.

    The interrupt count, not an ascending run, is the discriminator, because content
    documents are visited in archive order (not reading order), which scrambles any
    cross-file sequence."""
    if internal_flags is None:
        internal_flags = collect_internal_numbering(htmls)
    vals = 0
    confident = 0
    # strict: the flags are aligned with htmls by construction (epub.py builds
    # both from the same infolist filter); a mismatch is a wiring bug
    for html_text, internal in zip(htmls, internal_flags, strict=True):
        if internal:
            continue
        blocks = [_Block(m) for m in _P_RE.finditer(html_text)]
        for b in blocks:
            if b.kind == "prose" and b.text in runheads:
                b.kind = "runhead"
        for i, b in enumerate(blocks):
            if b.kind == "number":
                if b.text.isdigit():
                    vals += 1
                if _is_baked(blocks, i, html_text)[1]:
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


def _nearest(blocks: list[_Block], i: int, step: int):
    """The nearest non-empty block in direction `step`, or None. An empty <p> is
    layout padding, so it never counts as the neighbour of a page number."""
    j = i + step
    while 0 <= j < len(blocks):
        if blocks[j].kind != "empty":
            return j
        j += step
    return None


def _is_baked(blocks: list[_Block], i: int, html_text: str) -> tuple[bool, bool]:
    """Return (baked, confident_interrupt) for the number block at index i. Both
    verdicts require the number to sit in prose flow: a digit enclosed by foreign
    markup on both sides (a lone table cell) is recipe/layout content, never a
    page-number interrupt (v1.2.5, the All About Cake cookbook)."""
    flow = _in_prose_flow(html_text, blocks, i)
    pp = _nearest_prose(blocks, i, -1)
    np = _nearest_prose(blocks, i, +1)
    prose_prev = pp is not None and len(blocks[pp].text) > PROSE_MIN
    prose_next = np is not None and len(blocks[np].text) > PROSE_MIN
    if not (prose_prev or prose_next):
        return False, False
    prev_ne = _nearest(blocks, i, -1)
    next_ne = _nearest(blocks, i, +1)
    word_split = bool(pp is not None and _visible(blocks[pp].inner).endswith("-"))
    lower_cont = bool(
        next_ne is not None
        and blocks[next_ne].is_prose
        and blocks[next_ne].text[:1].islower()
    )
    # A confident interrupt merges pp through np: the splice owns the whole
    # corridor, so every gap inside must be blank. Text shape alone merged
    # across <p class="rec_line"/><table><td> boundaries and discarded the
    # table openers the block model never saw (v1.2.5).
    confident = (
        (word_split or lower_cont)
        and pp is not None
        and np is not None
        and _flat_run(html_text, blocks, pp, np)
    )
    prev_runhead = prev_ne is not None and blocks[prev_ne].kind == "runhead"
    next_runhead = next_ne is not None and blocks[next_ne].kind == "runhead"
    prev_unfinished = bool(
        prose_prev
        and pp is not None
        and (blocks[pp].text[-1].islower() or blocks[pp].text[-1] == ",")
    )
    baked = (
        confident
        or (prev_unfinished and (prose_next or next_runhead))
        or (prev_runhead and next_runhead)
    ) and flow
    return baked, confident


def _nearest_prose(blocks: list[_Block], i: int, step: int):
    j = i + step
    while 0 <= j < len(blocks):
        if blocks[j].is_prose:
            return j
        j += step
    return None


def _flat_run(html_text: str, blocks: list[_Block], a: int, b: int) -> bool:
    """True when every gap between consecutive blocks in [min(a, b), max(a, b)] is
    pure whitespace: the corridor holds only the block model's own <p> spans.
    Any other tag in a gap (a self-closing divider, table cell markup) is layout
    the model does not own, so no splice that spans a..b may consume it."""
    lo, hi = (a, b) if a <= b else (b, a)
    pos = blocks[lo].end
    for j in range(lo + 1, hi + 1):
        if html_text[pos : blocks[j].start].strip():
            return False
        pos = blocks[j].end
    return True


def _in_prose_flow(html_text: str, blocks: list[_Block], i: int) -> bool:
    """True when at least one of the block's immediate gaps (to the previous or
    next block; the document edge counts as markup) is pure whitespace. A digit
    enclosed by foreign markup on BOTH sides -- a lone recipe-quantity cell --
    is layout content, never a page number interrupting text; one-sided furniture
    (a divider beside the number) stays in scope."""
    if i > 0 and not html_text[blocks[i - 1].end : blocks[i].start].strip():
        return True
    return (
        i + 1 < len(blocks)
        and not html_text[blocks[i].end : blocks[i + 1].start].strip()
    )


def strip_pagination_doc(
    html_text: str,
    runheads: set[str],
    delete_layer: bool = False,
    internal_numbering: bool = False,
) -> tuple[str, int]:
    """Remove baked page numbers/running headers from one content document.

    A number is removed when it is a "baked" interrupt (the conservative default) or,
    when `delete_layer` is set (the book has a confirmed dense page-number layer), when
    it is an arabic page number sitting in the body text. Paragraphs are rejoined ONLY
    on a confident interrupt (lowercase continuation or word split); every other removal
    is delete-only, leaving the paragraph break as-is. Roman numerals are removed only on
    a confident interrupt, so roman chapter/front-matter numbering is preserved. Both
    paths require prose flow (v1.2.5): a digit enclosed by foreign markup on both sides
    -- a lone recipe-quantity table cell -- is never page furniture, and a confident
    merge additionally requires a flat corridor between the merged paragraphs, so the
    splice can never consume markup the block model does not own.

    `internal_numbering` (v1.2.6, issue #38) marks a document whose standalone numbers
    are verse/stanza/section numbering -- values shared across documents -- and skips
    every number in it: Don Juan's enjambed stanzas (comma-ended or lowercase-starting)
    are homologous with real page-number sentence interrupts, and no text-shape signal
    can tell them apart, so the discriminator is the book-level numbering detector.

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
        if b.kind != "number" or internal_numbering:
            continue
        baked, confident = _is_baked(blocks, i, html_text)
        is_arabic = b.text.isdigit()
        pp = _nearest_prose(blocks, i, -1)
        np = _nearest_prose(blocks, i, +1)
        # In a confirmed page-layer book every standalone arabic <p>N</p> is a page
        # number (char conservation guarantees only digits are lost), so the layer path
        # needs no prose-proximity gate; that gate would miss numbers among short
        # dialogue lines. It still requires prose flow: a digit enclosed by cell
        # markup on both sides is a recipe quantity, not page furniture (v1.2.5,
        # the All About Cake cookbook swept 110 ingredient cells under this path).
        # Roman numerals still fall through (chapter numbers preserved).
        removable = baked or (
            delete_layer and is_arabic and _in_prose_flow(html_text, blocks, i)
        )
        if not removable:
            continue  # roman chapter numbers, sparse-book numbers, year-range values
        drop.add(i)
        # a running header/footer hugging a removed number is the same page furniture,
        # but only when it sits in the number's own prose run: the cascade must not
        # reach across cell boundaries into a neighbouring table cell
        for side in (_nearest(blocks, i, -1), _nearest(blocks, i, +1)):
            if (
                side is not None
                and blocks[side].kind == "runhead"
                and _flat_run(html_text, blocks, i, side)
            ):
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
            m_id = _OPEN_ID_RE.search(blocks[j].open_tag)
            if m_id:
                # A removed <p id=...> cannot keep its shell inside the merged <p>
                # (p cannot nest p), so its id survives as an empty anchor.
                anchors.append(f"<a id={m_id.group(1)}/>")
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
        if anchors or _OPEN_ID_RE.search(b.open_tag):
            # An id anywhere in the block is a navigation target (page-list, internal
            # link): keep an emptied shell so those references still resolve.
            repl = b.open_tag + "".join(anchors) + "</p>"
        else:
            repl = ""
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


def _tag_deltas(s: str) -> Counter:
    """Per-element open/close balance of a document, negative values kept (a
    pre-existing stray close must survive the splice, not read as a splice error)."""
    opens = Counter(t.lower() for t in _OPEN_TAG_RE.findall(s))
    closes = Counter(t.lower() for t in _CLOSE_TAG_RE.findall(s))
    return Counter(
        {name: opens[name] - closes[name] for name in set(opens) | set(closes)}
    )


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
    # All-tag delta net (v1.2.5): the splice must not change ANY element's open/close
    # balance. The p/a net above reads only two names; the All About Cake merge
    # discarded a <table>/<tr>/<td> opener run the p/a net cannot see, and the
    # orphaned closers then rode through as an epubcheck fatal on the candidate.
    before_deltas = _tag_deltas(before)
    after_deltas = _tag_deltas(after)
    for name in set(before_deltas) | set(after_deltas):
        if before_deltas[name] != after_deltas[name]:
            return False
    return True
