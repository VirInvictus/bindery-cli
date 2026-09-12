#!/usr/bin/env python3
"""
bindery audit: read the actual text of every EPUB and flag content problems that
metadata and structural validators cannot see. Six analyzers, one tool:

  content      non-English bodies (wrong-language editions) and injected
               foreign-language ad-notices (declared lang=eng, body Portuguese
               / Russian / etc.)
  pagenumbers  print page numbers (and running headers) captured as body *text*
               by a bad PDF/OCR conversion, so they reflow into the middle of a
               sentence ("where the hay cart 16 was taking him")
  emptytext    content-less stubs: a "Bookmate" export is cover/promo images
               plus a tiny HTML placeholder, the spine pointing only at the
               placeholder, the book itself absent (passes epubcheck and a
               structural repairer because the one referenced doc is valid)
  ocr          OCR/conversion-damaged prose: paragraphs split mid-sentence
               ("could just make out the shape" / "of another boat"), plus
               dictionary-free side signals (en-dashes inside words, doubled
               opening quotes, space-stripped proper nouns recurring alongside
               their hyphenated form). Character-substitution errors ("sonic"
               for "some") are OUT OF SCOPE: catching those needs a wordlist,
               and this tool is stdlib-only by contract.
  monolithic   one spine doc so large that readers refuse to render it, though
               the book totals normally
  completeness the phase-1 spot-check: prose-doc count against the spine, the
               opening/closing text of the first/middle/last prose doc, a
               trailing-ToC classification of the final doc, and the fraction
               of unreadable docs (advisory: it reports the shape a human
               judges, it never flags the book)
  all          run all six in a SINGLE decompression pass per book

This merges the former audit_epub_content.py / audit_epub_pagenumbers.py /
audit_epub_emptytext.py: they shared the same spine resolution, library/
directory dual-mode, read-only contract, and exit codes, and differed only in
the per-book verdict. `all` opens each EPUB once and feeds the decoded spine to
all four analyzers (the expensive part is decompression, so this is a real
win at library scale).

Companion to validate_metadata.py (which audits the catalogue) and to Bindery
(which repairs EPUB structure). This one reads body text and changes nothing;
it opens metadata.db strictly mode=ro.

Run from the library directory:
    python3 bindery audit all                 # all four audits, whole library
    python3 bindery audit content             # one audit, whole library
    python3 bindery audit all ~/Downloads     # vet loose .epub files before import
    python3 bindery audit emptytext ~/Downloads --min-chars 1000

Library mode pulls the EPUB list (and tags / declared language) from
metadata.db; directory mode scans every .epub it finds recursively, the
workflow for checking downloads before they enter the library.

Exit codes:
    0 = clean (THIN empty-text hits are advisory and do not fail the run)
    1 = a real problem found (foreign content, baked page numbers, empty book,
        OCR-damaged prose) or a scan error
    2 = setup error (missing DB / library, or no .epub files in directory)
    3 = a flagged run whose --tag apply ALSO failed (1 from findings, 2 from
        the tagging write; documented here because it surfaced unexplained
        on a tagging failure)
"""

import html
import json
import os
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).parent))
import vir_tui as ui

# ----------------------------------------------------------------------------
# Shared scaffolding
# ----------------------------------------------------------------------------


def resolve_library_root() -> Path | None:
    """The library root is wherever metadata.db sits: next to this script (the
    copy living inside the library) or the current working directory (running
    the repo copy from inside a library), in that order."""
    for d in (Path(__file__).resolve().parent, Path.cwd()):
        if (d / "metadata.db").is_file():
            return d
    return None


# ANSI colours; suppress when stdout isn't a TTY.
USE_COLOR = sys.stdout.isatty()
RED = "\033[31m" if USE_COLOR else ""
YELLOW = "\033[33m" if USE_COLOR else ""
GREEN = "\033[32m" if USE_COLOR else ""
BOLD = "\033[1m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""

CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
OPF_NS = "{http://www.idpf.org/2007/opf}"

_PCT = re.compile(r"(?:%[0-9A-Fa-f]{2})+")


def _pct_decode(href: str) -> str:
    """Percent-decode an OPF href (UTF-8). OPF manifest hrefs are IRIs, so a
    reserved char like '!' is written '%21' and a multi-byte char as a run of
    %XX (e.g. 'ö' -> '%C3%B6'); each run must be decoded as bytes together.
    Stdlib-only stand-in for urllib.parse.unquote; invalid escapes stay literal."""
    return _PCT.sub(
        lambda m: bytes.fromhex(m.group(0).replace("%", "")).decode("utf-8", "replace"),
        href,
    )


class Book:
    """A decompressed EPUB: spine documents read once and shared by every
    analyzer. The whole point of the single-pass design lives here."""

    __slots__ = (
        "_visible",
        "corrupt",
        "docs",
        "dup_entries",
        "encrypted",
        "lang",
        "names",
        "nav",
        "spine",
        "spine_missing",
        "toc_refs",
        "toc_absent",
    )

    def __init__(
        self,
        spine,
        nav,
        lang,
        docs,
        names,
        corrupt,
        toc_refs,
        toc_absent,
        encrypted=None,
        spine_missing=0,
        dup_entries=0,
    ):
        self.spine = spine  # resolved, in-order, in-archive spine doc paths
        self.nav = nav  # the nav document path, or None
        self.lang = lang  # declared dc:language (lowercased), or ""
        self.docs = docs  # {path: decoded html} for every spine doc
        self.names = names  # full archive namelist (for image / marker counts)
        self.corrupt = corrupt  # entries whose full read failed (CRC/truncation)
        # DRM: entries declared in META-INF/encryption.xml. Their unreadability
        # is the book's business model, not damage, so they get their own
        # verdict instead of CORRUPT / re-source.
        self.encrypted = encrypted if encrypted is not None else []
        # itemrefs that could not be resolved to an archive file (dangling
        # idref, decoded-href vs stored-name mismatch): a large count on an
        # EMPTY-verdict book is the diagnostic, not a content-less stub.
        self.spine_missing = spine_missing
        # duplicate archive entry names (zip resolves last-wins silently);
        # surfaced in the archive verdict so nothing is silently shadowed
        self.dup_entries = dup_entries
        # Spine-integrity accounting (phase 8): nav/NCX ToC references vs what
        # the archive actually contains. The official Wandering Inn builds
        # ship series-wide ToC manifests (~750 references vs 14-19 real
        # content docs) — reported as `convention`, not flagged.
        self.toc_refs = toc_refs
        self.toc_absent = toc_absent
        self._visible: list[str] | None = None

    def visible_texts(self) -> list[str]:
        """Rendered text, one entry per spine doc, computed once per book.

        emptytext and ocr both need it, and under `all` they each used to strip
        tags over the whole book independently: the second pass was pure waste
        in a design whose entire point is touching each EPUB once.
        """
        if self._visible is None:
            self._visible = [_visible_text(self.docs.get(d, "")) for d in self.spine]
        return self._visible


def load_book(path: Path) -> Book:
    """Open an EPUB once: fully read EVERY archive entry (the CRC sweep the
    phase-1 skill used to do by hand), resolve spine + nav + declared
    language, and read every spine document's HTML. Decoded utf-8 with
    replacement (never raises); a corrupted archive entry (bad CRC, truncated
    stream) is recorded in `Book.corrupt` and reads as empty text — the
    corruption verdict, not emptytext, owns that story."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        nameset = set(names)
        corrupt: list[str] = []
        encrypted: list[str] = []
        spine_missing = 0
        dup_entries = len(names) - len(nameset)  # zip resolves last-wins
        raw: dict[str, bytes] = {}

        def _read(name: str) -> bytes | None:
            """Fully read one entry (CRC + real decompression, not just the
            central directory's word). A corrupt entry is recorded and its
            content treated as absent — never silently swallowed."""
            if name in raw:
                return raw[name]
            if name in corrupt or name in encrypted:
                return None
            try:
                blob = z.read(name)
            except Exception:
                if name in encrypted_names:
                    encrypted.append(name)  # DRM, not damage
                else:
                    corrupt.append(name)
                return None
            raw[name] = blob
            return blob

        container = ET.fromstring(_read("META-INF/container.xml"))
        rootfile = container.find(".//c:rootfile", CONTAINER_NS)
        opf_path = rootfile.get("full-path") if rootfile is not None else None
        if not opf_path:
            raise ValueError("container.xml has no rootfile")
        opf = ET.fromstring(_read(opf_path))
        base = os.path.dirname(opf_path)

        # Entries declared encrypted (DRM): an unreadable one is the book's
        # business model, not a damaged archive, so it gets its own verdict.
        encrypted_names: set[str] = set()
        # guard on the nameset: _read records a failed probe as corrupt, and
        # most books simply have no encryption.xml at all
        enc_blob = (
            _read("META-INF/encryption.xml")
            if ("META-INF/encryption.xml" in nameset)
            else None
        )
        if enc_blob:
            try:
                for el in ET.fromstring(enc_blob).iter():
                    if el.tag.split("}")[-1] == "CipherReference" and el.get("URI"):
                        encrypted_names.add(el.get("URI"))
            except ET.ParseError:
                pass

        manifest: dict[str, str] = {}
        nav_href: str | None = None
        for it in opf.iter(OPF_NS + "item"):
            item_id, href = it.get("id"), it.get("href")
            if not item_id or not href:
                continue
            manifest[item_id] = href
            if "nav" in (it.get("properties") or "").split():
                nav_href = href

        lang = ""
        for el in opf.iter():
            if el.tag.endswith("}language") and el.text:
                lang = el.text.strip().lower()
                break

        def full(href: str, rel_base: str | None = None) -> str:
            # Decode the percent-encoded IRI and drop any #fragment before
            # matching the archive namelist; otherwise a spine doc whose
            # filename has a reserved char (e.g. '!' written '%21') resolves to
            # nothing and the book reads as empty (false EMPTY verdict).
            # `rel_base` overrides the OPF directory: a nav or NCX document in
            # a subdirectory carries hrefs relative to ITS OWN directory, not
            # the OPF's (a nested NCX resolved against the OPF dir counted
            # every target absent).
            use_base = base if rel_base is None else rel_base
            href = _pct_decode(href.split("#", 1)[0])
            return os.path.normpath(f"{use_base}/{href}" if use_base else href).replace(
                "\\", "/"
            )

        spine: list[str] = []
        for itemref in opf.iter(OPF_NS + "itemref"):
            idref = itemref.get("idref")
            href = manifest.get(idref) if idref else None
            if not href:
                spine_missing += 1
                continue
            if full(href) in nameset:
                spine.append(full(href))
            else:
                # A decoded-href vs stored-name mismatch (NFC/NFD, %20 vs +,
                # backslash names) silently dropped the doc and read as EMPTY
                # on a book full of prose; count it so EMPTY carries the
                # diagnostic.
                spine_missing += 1
        nav = full(nav_href) if nav_href else None

        # The single full pass: every archive entry is fully read here (the
        # CRC sweep), so the text analysis below runs on what actually
        # decompressed. Corrupt entries are named, not averaged away.
        for name in names:
            if name.endswith("/"):
                continue
            _read(name)

        docs: dict[str, str] = {}
        for doc in spine:
            blob = raw.get(doc)
            docs[doc] = blob.decode("utf-8", "replace") if blob is not None else ""

        # ToC reference accounting: every nav/NCX anchor target checked against
        # the archive (manifest items are the spine's own source and already
        # accounted by the spine resolution). This runs INSIDE the open zip:
        # the accounting reads nav/NCX bodies through `_read`, and a read
        # after close raises and poisons Book.corrupt.
        toc_refs = 0
        toc_absent = 0

        def _account(href: str, href_base: str) -> None:
            nonlocal toc_refs, toc_absent
            href = html.unescape(href.strip())
            if href.startswith("#"):
                return  # in-page anchor, not an archive target
            if re.match(r"^[a-z]+:", href, re.IGNORECASE):
                return  # foreign URL scheme, not an archive target
            toc_refs += 1
            if full(href, href_base) not in nameset:
                toc_absent += 1

        nav_html = docs.get(nav) if nav else None
        if nav_html is None and nav and nav in nameset:
            # The nav document is usually in the spine, but nothing requires
            # it. A DECLARED-but-absent nav is a manifest leftover, not a
            # damaged archive: it is skipped here and must not poison
            # Book.corrupt (a healthy book with a leftover nav/toc.ncx entry
            # is not "re-source" material).
            blob = _read(nav)
            nav_html = blob.decode("utf-8", "replace") if blob is not None else ""
        if nav_html:
            nav_dir = os.path.dirname(nav) if nav else ""
            for m in re.finditer(r'href\s*=\s*["\']([^"\']+)', nav_html):
                _account(m.group(1), nav_dir)
        ncx_path = None
        for it in opf.iter(OPF_NS + "item"):
            if (it.get("media-type") or "").lower() == "application/x-dtbncx+xml":
                ncx_path = full(it.get("href") or "")
                break
        if ncx_path and ncx_path in nameset:
            blob = _read(ncx_path)
            if blob is not None:
                for m in re.finditer(
                    r'<content[^>]+src\s*=\s*["\']([^"\']+)',
                    blob.decode("utf-8", "replace"),
                ):
                    _account(m.group(1), os.path.dirname(ncx_path))

    return Book(
        spine,
        nav,
        lang,
        docs,
        names,
        corrupt,
        toc_refs,
        toc_absent,
        encrypted=encrypted,
        spine_missing=spine_missing,
        dup_entries=dup_entries,
    )


# ----------------------------------------------------------------------------
# Analyzer: content (non-English / injected notices)
# ----------------------------------------------------------------------------

CAP = 400_000  # clean chars read per book; ample for a language verdict

STYLE_RE = re.compile(r"<(style|script)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[a-zA-ZàâäéèêëïîôöùûüçñáíóúãõßÀ-ÿ']+")
SIGNATURE_RE = re.compile(r"importknig|книжный импорт|knizhny", re.IGNORECASE)

# Distinctive stopword sets. Book-length text makes the vote unambiguous;
# the small EN/IT overlap on "i" etc. is swamped by the rest.
STOPWORDS: dict[str, set[str]] = {
    "en": set(
        [
            "the",
            "of",
            "and",
            "to",
            "a",
            "in",
            "that",
            "is",
            "was",
            "for",
            "it",
            "with",
            "as",
            "his",
            "on",
            "be",
            "at",
            "by",
            "he",
            "this",
            "had",
            "not",
            "are",
            "but",
            "from",
            "or",
            "have",
            "an",
            "they",
            "which",
            "one",
            "you",
            "were",
            "her",
            "all",
            "she",
            "there",
            "would",
            "their",
        ]
    ),
    "de": set(
        [
            "der",
            "die",
            "und",
            "in",
            "den",
            "von",
            "zu",
            "das",
            "mit",
            "sich",
            "des",
            "auf",
            "für",
            "ist",
            "im",
            "dem",
            "nicht",
            "ein",
            "eine",
            "als",
            "auch",
            "es",
            "an",
            "werden",
            "aus",
            "er",
            "hat",
            "dass",
            "sie",
            "nach",
            "wird",
            "bei",
            "einer",
            "um",
        ]
    ),
    "fr": set(
        [
            "le",
            "la",
            "les",
            "de",
            "des",
            "un",
            "une",
            "et",
            "en",
            "dans",
            "que",
            "qui",
            "pour",
            "pas",
            "sur",
            "au",
            "avec",
            "ce",
            "il",
            "ne",
            "se",
            "plus",
            "par",
            "je",
            "nous",
            "vous",
            "est",
            "son",
            "ses",
            "aux",
        ]
    ),
    "es": set(
        [
            "el",
            "la",
            "los",
            "las",
            "de",
            "un",
            "una",
            "y",
            "en",
            "que",
            "no",
            "se",
            "con",
            "por",
            "para",
            "es",
            "su",
            "lo",
            "como",
            "más",
            "pero",
            "sus",
            "le",
            "ya",
            "o",
            "este",
            "sí",
            "porque",
            "esta",
            "entre",
        ]
    ),
    "it": set(
        [
            "il",
            "lo",
            "la",
            "i",
            "gli",
            "le",
            "di",
            "un",
            "uno",
            "una",
            "e",
            "che",
            "non",
            "per",
            "con",
            "su",
            "come",
            "più",
            "ma",
            "anche",
            "da",
            "sono",
            "mi",
            "si",
            "nel",
            "alla",
            "dei",
            "delle",
        ]
    ),
    "pt": set(
        [
            "o",
            "a",
            "os",
            "as",
            "de",
            "um",
            "uma",
            "e",
            "que",
            "do",
            "da",
            "em",
            "não",
            "se",
            "com",
            "por",
            "para",
            "mais",
            "mas",
            "como",
            "ao",
            "dos",
            "das",
            "na",
            "no",
            "à",
            "seu",
        ]
    ),
    "nl": set(
        [
            "de",
            "het",
            "een",
            "en",
            "van",
            "te",
            "dat",
            "die",
            "in",
            "is",
            "op",
            "ik",
            "niet",
            "met",
            "zijn",
            "er",
            "maar",
            "om",
            "ook",
            "als",
            "voor",
            "naar",
            "dan",
            "zou",
            "hij",
            "heeft",
        ]
    ),
}


def script_of(codepoint: int) -> str | None:
    if 0x0400 <= codepoint <= 0x04FF:
        return "Cyrillic"
    if 0x4E00 <= codepoint <= 0x9FFF:
        return "CJK-Han"
    if 0x3040 <= codepoint <= 0x30FF:
        return "Japanese-kana"
    if 0xAC00 <= codepoint <= 0xD7A3:
        return "Korean"
    if 0x0600 <= codepoint <= 0x06FF:
        return "Arabic"
    if 0x0370 <= codepoint <= 0x03FF:
        return "Greek"
    if 0x0590 <= codepoint <= 0x05FF:
        return "Hebrew"
    if 0x0900 <= codepoint <= 0x097F:
        return "Devanagari"
    return None


def _content_clean(html: str) -> str:
    html = STYLE_RE.sub(" ", html)
    return WS_RE.sub(" ", TAG_RE.sub(" ", html))


def analyze_content(book: Book) -> dict:
    """Language / script signal over the (pre-read) spine."""
    docs = book.spine
    parts: list[str] = []
    total = 0
    for doc in docs:
        if total >= CAP:
            break
        text = _content_clean(book.docs.get(doc, ""))
        parts.append(text)
        total += len(text)
    if docs and docs[-1] not in docs[: len(parts)]:
        parts.append(_content_clean(book.docs.get(docs[-1], "")))

    text = " ".join(parts)
    # Count scripts over the first 250k letters in a single pass, instead of
    # materializing a list of every letter's codepoint in a book-length string.
    scripts: Counter = Counter()
    total_letters = 0
    for c in text:
        if c.isalpha():
            total_letters += 1
            s = script_of(ord(c))
            if s:
                scripts[s] += 1
            if total_letters >= 250_000:
                break
    nonlatin = sum(scripts.values())
    total_letters = total_letters or 1
    words = WORD_RE.findall(text.lower())[:5000]
    ratios = {
        code: (sum(w in stops for w in words) / len(words) if words else 0.0)
        for code, stops in STOPWORDS.items()
    }
    best = max(ratios, key=lambda c: ratios[c])
    return {
        "lang": book.lang,
        "scripts": dict(scripts),
        "nonlatin": nonlatin,
        "nonlatin_frac": nonlatin / total_letters,
        "ratios": ratios,
        "best": best,
        "nwords": len(words),
        "signature": bool(SIGNATURE_RE.search(text)),
    }


def findings(r: dict) -> list[tuple[str, str]]:
    """Classify a content result into [(category, detail)]; empty = English and clean."""
    out: list[tuple[str, str]] = []
    if r["nonlatin"] >= 150 and r["nonlatin_frac"] > 0.02:
        top = max(r["scripts"], key=lambda s: r["scripts"][s])
        out.append(
            (
                "NON-LATIN SCRIPT",
                f"{r['nonlatin_frac'] * 100:.0f}% {top} ({r['nonlatin']} non-Latin letters)",
            )
        )
    if (
        r["best"] != "en"
        and r["nwords"] >= 400
        and r["ratios"][r["best"]] > 0.06
        and r["ratios"][r["best"]] > 1.3 * r["ratios"]["en"]
    ):
        top3 = ", ".join(
            f"{c}={v:.2f}"
            for c, v in sorted(r["ratios"].items(), key=lambda x: -x[1])[:3]
        )
        out.append(("LATIN-SCRIPT FOREIGN", f"looks {r['best'].upper()} [{top3}]"))
    if r["signature"]:
        out.append(
            ("INJECTION SIGNATURE", "importknig / Книжный импорт signature present")
        )
    return out


def scan_content(path: Path) -> dict:
    """Convenience: load + analyze a single file (used by tests / ad-hoc runs)."""
    return analyze_content(load_book(path))


# ----------------------------------------------------------------------------
# Analyzer: pagenumbers (print page numbers baked into body text)
# ----------------------------------------------------------------------------

INT_RE = re.compile(r"\d{1,4}$")
# A strict roman-numeral grammar, matching pagination.py: a character-set match
# ([ivxlcdm]{2,7}) read ordinary words (mid, dim, mix, lid, civil) as page
# numbers, inflating baked-hit counts (reported 2026-09-08). Two gates replace
# it: the numeral must be well-formed (subtractive pairs explicit), and its
# value must stay under 100, because page numbers in roman form do not run that
# high while words do (mix = 1009, civ = 104).
ROMAN_RE = re.compile(
    r"m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})", re.IGNORECASE
)
ROMAN_MAX = 100
# Block-level elements we track to reconstruct reading order.
BLOCK_TAGS = {
    "p",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "td",
    "th",
    "blockquote",
    "section",
    "caption",
    "figcaption",
}

# Tuning, validated by hand against a 3,897-EPUB library (every flagged book and
# the full n>=2 tail inspected). At these values the detector flagged 21 books,
# all true positives; the false-positive tail (experimental footnote-poems,
# scraped web-serial vote counts, placeholder section labels) all fell under
# MIN_BAKED_HITS or MIN_SPAN. Lowering MIN_BAKED_HITS to catch the few remaining
# 3-4 hit books (a localized stray-number patch) starts admitting those.
PROSE_MIN = 120  # a neighbour this long counts as a prose paragraph
RUNHEAD_MIN_REPEAT = 8  # a short block repeated this often is a running header
RUNHEAD_MAX_LEN = 60  # running headers are short
MIN_BAKED_HITS = 5  # below this, a handful of hits is too often coincidence
MIN_SPAN = 0.10  # flagged numbers must cover this fraction of the book (drops
# localized clusters: footnote-poems, scraped comment sections)
MIN_RUN = 1  # ascending run is informative but not gated; the baked test already
# requires genuine sentence interruption, so a short run is not disqualifying


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
    """A bare page-number-ish value (1-9999 arabic, or a roman numeral), else None."""
    if INT_RE.fullmatch(text):
        return int(text)
    if 2 <= len(text) <= 7 and ROMAN_RE.fullmatch(text):
        v = roman_value(text)
        return v if v is not None and v < ROMAN_MAX else None
    return None


class _Blocks:
    """Minimal block extractor over html.parser; tracks the innermost block tag
    so each emitted (tag, text) pair is one rendered block in reading order.

    img_before[i] is True when an image (img/svg) appeared between the last
    text before blocks[i] and blocks[i]'s own first text. Used by the ocr
    analyzer to clear paragraphs interrupted by a rendered figure (an inline
    formula or card diagram reads as a mid-sentence split otherwise).
    in_quote[i] is True when blocks[i] was emitted inside a <blockquote>
    (a display quotation legitimately starts and ends mid-sentence). Both are
    ocr-only; pagenumbers ignores them."""

    def __init__(self):
        from html.parser import HTMLParser

        outer = self

        class _P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.stack: list[str] = []
                self.buf: list[str] = []
                self.img_gap = False  # image seen since the last text content
                self.block_img = False  # img_gap captured at this block's first text
                self.has_text = False

            def handle_starttag(self, tag, attrs):
                del attrs
                if tag in ("img", "image", "svg"):
                    self.img_gap = True
                if tag in BLOCK_TAGS:
                    outer._flush(self)
                    self.stack.append(tag)

            def handle_endtag(self, tag):
                if tag in BLOCK_TAGS:
                    outer._flush(self)
                    # pop only the matching open tag: a stray mis-nested
                    # closer used to pop an unrelated ancestor off the
                    # stack and drop later blocks' in_quote guard
                    if self.stack and self.stack[-1] == tag:
                        self.stack.pop()
                    elif tag in self.stack:
                        idx = len(self.stack) - 1 - self.stack[::-1].index(tag)
                        del self.stack[idx]

            def handle_data(self, data):
                if data.strip():
                    if not self.has_text:
                        self.block_img = self.img_gap
                        self.has_text = True
                    self.img_gap = False
                self.buf.append(data)

        self.blocks: list[tuple[str, str]] = []
        self.img_before: list[bool] = []
        self.in_quote: list[bool] = []
        self._parser = _P()

    def _flush(self, parser):
        text = "".join(parser.buf).strip()
        parser.buf.clear()
        if text:
            tag = parser.stack[-1] if parser.stack else "?"
            self.blocks.append((tag, text))
            self.img_before.append(parser.block_img)
            self.in_quote.append("blockquote" in parser.stack)
        parser.has_text = False
        parser.block_img = False

    def feed(self, html: str):
        self._parser.feed(html)
        self._parser.close()
        self._flush(self._parser)


def analyze_pagenumbers(book: Book) -> dict:
    """Score baked-in page numbers over the (pre-read) spine, skipping nav."""
    blocks: list[tuple[str, str]] = []
    for doc in book.spine:
        if book.nav and doc == book.nav:
            continue
        parser = _Blocks()
        try:
            parser.feed(book.docs.get(doc, ""))
        except Exception:
            continue
        li = sum(1 for t, _ in parser.blocks if t == "li")
        # a doc that is mostly <li> is a TOC / page-list, not body text
        if parser.blocks and li / len(parser.blocks) > 0.5:
            continue
        blocks.extend(parser.blocks)

    text_len = sum(len(t) for _, t in blocks) or 1

    # Running headers / footers / watermarks: short blocks repeated many times.
    freq = Counter(
        t
        for t in (b[1] for b in blocks)
        if len(t) <= RUNHEAD_MAX_LEN and number_value(t) is None
    )
    runheads = {t for t, c in freq.items() if c >= RUNHEAD_MIN_REPEAT}

    hits: list[tuple[int, int]] = []  # (char offset, value)
    examples: list[dict] = []
    offset = 0
    for i, (tag, text) in enumerate(blocks):
        if tag in ("p", "div"):
            v = number_value(text)
            # 1500-2099 are almost always years (chronologies, dated chapters),
            # not page numbers; a real page count rarely reaches them.
            if v is not None and not (1500 <= v <= 2099):
                ptag, ptxt = blocks[i - 1] if i > 0 else ("", "")
                ntag, ntxt = blocks[i + 1] if i + 1 < len(blocks) else ("", "")
                prose_prev = ptag in ("p", "div") and len(ptxt) > PROSE_MIN
                prose_next = ntag in ("p", "div") and len(ntxt) > PROSE_MIN
                if prose_prev or prose_next:
                    prev_runhead = ptxt in runheads
                    next_runhead = ntxt in runheads
                    word_split = (
                        bool(ptxt) and ptxt[-1] == "-" and ptxt[-2:-1].isalpha()
                    )
                    lower_cont = bool(ntxt) and ntxt[0].islower()
                    # the previous body paragraph is unfinished (ends mid-word /
                    # mid-clause), so the number is wedged into a live sentence.
                    prev_unfinished = prose_prev and (
                        ptxt[-1].islower() or ptxt[-1] == ","
                    )
                    baked = (
                        word_split
                        or lower_cont
                        or (prev_unfinished and (prose_next or next_runhead))
                        or (prev_runhead and next_runhead)
                    )
                    if baked:
                        hits.append((offset, v))
                        if len(examples) < 8:
                            examples.append(
                                {"v": text, "prev": ptxt[-60:], "next": ntxt[:60]}
                            )
        offset += len(text)

    vals = [v for _, v in hits]
    if hits:
        span = (hits[-1][0] - hits[0][0]) / text_len
    else:
        span = 0.0
    run = best = 1 if vals else 0
    for i in range(1, len(vals)):
        if vals[i] - vals[i - 1] in (1, 2):
            best += 1
            run = max(run, best)
        else:
            best = 1
    return {
        "n_hits": len(hits),
        "span": span,
        "run": run,
        "watermark": any(
            re.search(r"download|boykma|\.com\b", h, re.IGNORECASE) for h in runheads
        ),
        "examples": examples,
    }


def is_defective(r: dict) -> bool:
    return (
        r["n_hits"] >= MIN_BAKED_HITS and r["span"] >= MIN_SPAN and r["run"] >= MIN_RUN
    )


def scan_pagenumbers(path: Path) -> dict:
    return analyze_pagenumbers(load_book(path))


# ----------------------------------------------------------------------------
# Analyzer: emptytext (empty / no-body-text stubs)
# ----------------------------------------------------------------------------

DEFAULT_MIN_CHARS = 2000  # at or below this: EMPTY (real defect)
DEFAULT_THIN_CHARS = 20000  # below this: THIN (advisory review)
# Monolithic-document floor (phase-1 skill: "roughly 300-500k" chars is where
# readers start refusing). A single content doc at or above this flags.
DEFAULT_MAX_DOC_CHARS = 300_000

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
BOOKMATE_MARKERS = ("bookmate.css", "calibre_bookmarks.txt")

# Partial / placeholder exports: a DRM-locked or sample export leaves most
# chapters as the same tiny "content unavailable" placeholder, so the book
# validates and clears the total-char floor on the strength of one or two real
# chapters (the whole-book char count is fooled). Detect by a known signature,
# or by the same short stub repeated across a large fraction of the spine.
# Seen cases: BookShout ("something went wrong loading... bookshout.com").
_PLACEHOLDER_SIG = re.compile(
    r"bookshout|something went wrong loading|failed to load|"
    r"(content|page|book)\s+(is\s+)?(not available|unavailable|could not be loaded)",
    re.IGNORECASE,
)
PLACEHOLDER_STUB_MIN = 12  # ignore blank / trivial spine docs
PLACEHOLDER_STUB_MAX = 600  # a placeholder stub is short
PLACEHOLDER_MIN_REPEAT = 3  # the same stub across at least this many spine docs
PLACEHOLDER_MIN_FRAC = 0.30  # ...and at least this fraction of the spine

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _visible_text(html: str) -> str:
    """Rendered text: drop script/style, strip tags, decode entities, collapse
    whitespace."""
    from html import unescape

    html = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", html)
    text = unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _visible_chars(html: str) -> int:
    return len(_visible_text(html))


def analyze_emptytext(book: Book) -> dict:
    """Count visible text across the (pre-read) spine and gather triage signals."""
    texts = book.visible_texts()
    chars = sum(len(t) for t in texts)
    images = sum(1 for n in book.names if n.lower().endswith(IMAGE_EXTS))
    bookmate = any(any(m in n.lower() for m in BOOKMATE_MARKERS) for n in book.names)

    # Partial / placeholder export: a known DRM signature anywhere, or the same
    # short stub repeated across a large fraction of the spine (most chapters
    # replaced by an identical "content unavailable" notice). Blank docs are
    # excluded by PLACEHOLDER_STUB_MIN so well-made books full of small section
    # dividers (each with distinct text) do not trip it.
    sig = any(_PLACEHOLDER_SIG.search(t) for t in texts)
    stubs = Counter(
        t for t in texts if PLACEHOLDER_STUB_MIN <= len(t) <= PLACEHOLDER_STUB_MAX
    )
    stub_n = stubs.most_common(1)[0][1] if stubs else 0
    spine_n = max(1, len(book.spine))
    repeated = (
        stub_n >= PLACEHOLDER_MIN_REPEAT and stub_n / spine_n >= PLACEHOLDER_MIN_FRAC
    )
    return {
        "chars": chars,
        "spine_len": len(book.spine),
        "spine_missing": book.spine_missing,
        "images": images,
        "bookmate": bookmate,
        "placeholder": bool(sig or repeated),
        "placeholder_sig": sig,
        "stub_docs": stub_n,
        "corrupt_n": len(book.corrupt),
        "corrupt_first": book.corrupt[0] if book.corrupt else "",
    }


def classify(r: dict, min_chars: int, thin_chars: int) -> str:
    # A corrupt archive entry reads as empty text; reporting EMPTY here would
    # be the right alarm for the wrong disease. The corruption verdict owns it.
    if r.get("corrupt_n"):
        return "CORRUPT"
    if r["chars"] <= min_chars:
        return "EMPTY"
    if r.get("placeholder"):
        return "PARTIAL"
    if r["chars"] < thin_chars:
        return "THIN"
    return "OK"


def _empty_detail(r: dict) -> str:
    bits = [f"{r['chars']} chars", f"spine {r['spine_len']}", f"{r['images']} images"]
    if r.get("spine_missing"):
        bits.append(
            f"{r['spine_missing']} spine itemref(s) unresolved to archive files"
        )
    if r.get("corrupt_n"):
        bits.append(f"corrupt:{r['corrupt_n']} (first: {r['corrupt_first']})")
    if r["bookmate"]:
        bits.append("bookmate")
    if r.get("placeholder"):
        if r.get("placeholder_sig"):
            bits.append("partial: DRM/placeholder signature")
        else:
            bits.append(
                f"partial: {r['stub_docs']}/{r['spine_len']} identical stub docs"
            )
    return ", ".join(bits)


def scan_emptytext(path: Path) -> dict:
    return analyze_emptytext(load_book(path))


# ----------------------------------------------------------------------------
# Analyzer: monolithic (one spine doc that is far too large)
# ----------------------------------------------------------------------------


def analyze_monolithic(book: Book) -> dict:
    """Find the largest single content document, in characters.

    Monolithic documents are invisible to emptytext (whole-book volume, not
    per-doc shape) and to epubcheck (which never sees renderer memory limits):
    a "clean" 30M-char dictionary that will not render past a point on real
    readers. Reuses the shared visible_texts() cache, so this adds no second
    decompression pass.
    """
    texts = book.visible_texts()
    worst_i = max(range(len(texts)), key=lambda i: len(texts[i]), default=None)
    return {
        "max_doc_chars": len(texts[worst_i]) if worst_i is not None else 0,
        "worst_doc": book.spine[worst_i] if worst_i is not None else "",
        "spine_len": len(book.spine),
    }


def is_monolithic(r: dict, max_doc_chars: int) -> bool:
    """Flag only at or above the threshold; high-but-under stays silent
    (advisory silence, like THIN)."""
    return r["max_doc_chars"] >= max_doc_chars


def scan_monolithic(path: Path) -> dict:
    return analyze_monolithic(load_book(path))


# ----------------------------------------------------------------------------
# Analyzer: completeness (the phase-1 spot-check: does the book reach its end?)
# ----------------------------------------------------------------------------

# A spine doc at or above this many visible chars is real content; under it is
# front/back matter (title pages, colophons) or furniture.
PROSE_DOC_MIN_CHARS = 400
SPOT_CHARS = 120  # opening/closing excerpt length per sampled doc
# A book this far unreadable (corrupt entries + unresolved itemrefs against
# all spine itemrefs) draws the advisory, though it never fails a run.
COMPLETENESS_UNREADABLE_FLAG = 0.10

# Trailing-ToC detection, tuned on the 2026-09-10 Redwall run: Lord
# Brocktree's trailing ToC is a 520-char link list sitting AFTER a real
# Epilogue doc, while Mattimeo's final doc carries chapter headings with real
# prose after every one of them and must never classify as a ToC. A ToC page
# is dense with short link lines and carries no real paragraphs.
TRAILING_TOC_MIN_BLOCKS = 4  # fewer lines is a colophon or pager, not a ToC
TRAILING_TOC_SHORT_MAX = 100  # a chapter-heading line is short
TRAILING_TOC_SHORT_FRAC = 0.75  # most lines are short
TRAILING_TOC_LONG_BLOCK = 400  # a real paragraph runs longer than any ToC line
TRAILING_TOC_LINK_FRAC = 0.5  # at least half the lines carry a link

_TOC_BLOCK_SPLIT_RE = re.compile(r"</(?:p|div|li|h[1-6]|td)>", re.IGNORECASE)
_A_TAG_RE = re.compile(r"<a[\s>]", re.IGNORECASE)


def _trailing_toc(html_src: str) -> bool:
    """Is this document a chapter list? Short link lines dominate and no
    block runs to paragraph length; a heading followed by real prose (the
    Mattimeo split-doc case) keeps a document out."""
    body = _SCRIPT_STYLE_RE.sub(" ", html_src)
    blocks: list[str] = []
    for chunk in _TOC_BLOCK_SPLIT_RE.split(body):
        text = _visible_text(chunk)
        if text:
            blocks.append(text)
    if len(blocks) < TRAILING_TOC_MIN_BLOCKS:
        return False
    short = sum(1 for b in blocks if len(b) <= TRAILING_TOC_SHORT_MAX)
    if short / len(blocks) < TRAILING_TOC_SHORT_FRAC:
        return False
    if any(len(b) > TRAILING_TOC_LONG_BLOCK for b in blocks):
        return False
    links = len(_A_TAG_RE.findall(body))
    return links >= len(blocks) * TRAILING_TOC_LINK_FRAC


def analyze_completeness(book: Book) -> dict:
    """The phase-1 completeness spot-check, over the (pre-read) spine.

    Reports the shape a human needs in order to judge "does this book reach
    its end": the prose-doc count against the spine, opening/closing excerpts
    of the first/middle/last prose doc, a trailing-ToC classification of the
    final doc, and the fraction of spine docs that could not be read at all
    (corrupt entries plus unresolved itemrefs). A trailing ToC is book
    furniture: it is excluded from the prose sampling so the closing excerpt
    comes from the real back matter (Lord Brocktree's Epilogue, not its
    520-char ToC). Reuses the shared visible_texts() cache: no second pass.
    """
    texts = book.visible_texts()
    spine_n = len(book.spine)
    trailing = ""
    if spine_n >= 2 and _trailing_toc(book.docs.get(book.spine[-1], "")):
        trailing = book.spine[-1]
    prose = [
        (book.spine[i], texts[i])
        for i in range(spine_n)
        if len(texts[i]) >= PROSE_DOC_MIN_CHARS and book.spine[i] != trailing
    ]
    corrupt_set = set(book.corrupt)
    unreadable = (
        sum(1 for i in range(spine_n) if not texts[i] and book.spine[i] in corrupt_set)
        + book.spine_missing
    )
    blank_docs = sum(
        1 for i in range(spine_n) if not texts[i] and book.spine[i] not in corrupt_set
    )
    spots: list[dict] = []
    if prose:
        picks = sorted({0, len(prose) // 2, len(prose) - 1})
        spots = [
            {
                "doc": prose[i][0],
                "chars": len(prose[i][1]),
                "opens": prose[i][1][:SPOT_CHARS],
                "ends": prose[i][1][-SPOT_CHARS:],
            }
            for i in picks
        ]
    total = spine_n + book.spine_missing
    return {
        "spine_docs": spine_n,
        "spine_missing": book.spine_missing,
        "prose_docs": len(prose),
        "unreadable": unreadable,
        "unreadable_frac": (unreadable / total) if total else 0.0,
        "blank_docs": blank_docs,
        "trailing_toc": trailing,
        "spots": spots,
    }


def scan_completeness(path: Path) -> dict:
    return analyze_completeness(load_book(path))


def _completeness_dir(r: dict) -> tuple[bool, str, list[str]]:
    """Advisory by contract: the archive, spine, and emptytext verdicts own
    the flags; completeness reports the shape a human judges."""
    lines = [
        f"prose {r['prose_docs']}/{r['spine_docs']} spine docs, "
        f"unreadable {r['unreadable']} ({r['unreadable_frac'] * 100:.0f}%)"
    ]
    status = "OK"
    if r["trailing_toc"]:
        lines.append(f"trailing ToC: {r['trailing_toc']}")
        status = "ADVISORY"
    elif r["unreadable_frac"] >= COMPLETENESS_UNREADABLE_FLAG:
        status = "ADVISORY"
    if r["blank_docs"]:
        lines.append(f"{r['blank_docs']} spine doc(s) present but blank")
    lines += [f"{s['doc']}: [{s['opens']}] ... [{s['ends']}]" for s in r["spots"]]
    return (False, status, lines)


# ----------------------------------------------------------------------------
# Analyzer: archive corruption (always on; owns the "empty body" story)
# ----------------------------------------------------------------------------


def analyze_corrupt(book: Book) -> dict:
    """Archive entries whose full read failed (bad CRC, truncated stream), plus
    entries declared encrypted (DRM) under their own status.

    Reported as its own verdict in every mode — a corrupt entry decompresses
    to nothing, and letting emptytext call that EMPTY mislabels a damaged
    archive as a content-less stub (the phase-1 re-source advice that follows
    from EMPTY would then aim at the wrong disease).
    """
    return {
        "n": len(book.corrupt),
        "first": book.corrupt[0] if book.corrupt else "",
        "encrypted_n": len(book.encrypted),
        "encrypted_first": book.encrypted[0] if book.encrypted else "",
        "dup_entries": book.dup_entries,
    }


def _corrupt_verdict(r: dict) -> tuple[bool, str, list[str]]:
    if r.get("encrypted_n") and not r.get("n"):
        # Pure DRM: nothing is damaged, the content is merely locked. The
        # re-source advice is wrong for it (re-acquiring the same book
        # yields the same lock).
        return (
            True,
            "ENCRYPTED",
            [
                f"encrypted:{r['encrypted_n']} (first: {r['encrypted_first']})"
                " — DRM-protected; not repairable, skip"
            ],
        )
    if r.get("encrypted_n"):
        return (
            True,
            "CORRUPT",
            [
                f"corrupt:{r['n']} (first: {r['first']}) — damaged archive; re-source",
                f"encrypted:{r['encrypted_n']} (first: {r['encrypted_first']})"
                " — DRM-protected entries",
            ],
        )
    lines = [f"corrupt:{r['n']} (first: {r['first']}) — damaged archive; re-source"]
    if r.get("dup_entries"):
        lines.append(
            f"duplicate entries: {r['dup_entries']} (zip resolves last-wins; "
            "which copy wins is undefined)"
        )
    return (
        True,
        "CORRUPT",
        lines,
    )


# ----------------------------------------------------------------------------
# Analyzer: spine integrity (ToC bloat vs a true fragment)
# ----------------------------------------------------------------------------


def spine_integrity(book: Book) -> dict:
    """Classify absent ToC/NCX targets.

    The official Wandering Inn builds ship series-wide ToC manifests (~750
    references vs 14-19 real content docs): `convention` when the absent
    count is ~= the reference count and the present docs' chapter span is
    consecutive — reported, never flagged. A broken span means the book is
    missing part of itself: `fragment`, flagged. Unnumbered or absent==0
    cases are `ok`/`unknown` (silent or advisory).
    """
    absent, refs = book.toc_absent, book.toc_refs
    if absent == 0:
        return {"class": "ok", "refs": refs, "absent": 0}
    nums = []
    for doc in book.spine:
        found = re.findall(r"\d+", doc.rsplit("/", 1)[-1])
        if not found:
            return {"class": "unknown", "refs": refs, "absent": absent}
        nums.append(int(found[-1]))
    nums = sorted(set(nums))  # part1a/part1b carries ONE chapter number, not two
    span = (
        "consecutive" if nums == list(range(nums[0], nums[0] + len(nums))) else "broken"
    )
    if refs >= 20 and absent >= refs * 0.9 and span == "consecutive":
        return {"class": "convention", "refs": refs, "absent": absent}
    if span == "broken":
        return {"class": "fragment", "refs": refs, "absent": absent}
    return {"class": "unknown", "refs": refs, "absent": absent}


def _spine_verdict(r: dict) -> tuple[bool, str, list[str]]:
    if r["class"] == "fragment":
        return (
            True,
            "FRAGMENT",
            [f"toc {r['refs']} refs, {r['absent']} absent — span broken"],
        )
    if r["class"] == "convention":
        return (
            False,
            "ADVISORY",
            [
                f"toc {r['refs']} refs, {r['absent']} absent (series-wide manifest convention)"
            ],
        )
    return (
        False,
        "ADVISORY",
        [f"toc {r['refs']} refs, {r['absent']} absent (unjudgeable span)"],
    )


# ----------------------------------------------------------------------------
# Analyzer: ocr (OCR/conversion-damaged prose)
# ----------------------------------------------------------------------------

# Tuning, validated by hand against a 4,605-EPUB library: every book passing
# the joint gate was inspected via its split examples. At these values the
# detector flagged 105 books; 104 were confirmed damage (line-wrap and
# page-break splits, double-spaced OCR text, running headers wedged into
# sentences), with one borderline residue (a book whose display quotes are
# publisher-styled plain <p>s, indistinguishable from damage without CSS).
# The motivating case: a damaged Jingo EPUB measured 80 mid-sentence splits
# where a clean edition of the same text measured 0.
#
# Known misses, by design: damage whose signature is word TRUNCATION ("which
# bel[ong] to western Spain") or whitespace corruption rather than paragraph
# splitting, and low-grade split damage that lands in the same func_frac band
# as literary stream-of-consciousness (Gulag Archipelago at 0.243 vs. The
# Sound and the Fury at 0.242: no threshold separates them).
OCR_MIN_PARAS = 50  # below this the rate is noise, not a signal
OCR_MIN_SPLITS = 10  # absolute floor; a handful of splits is coincidence
OCR_FLAG_RATE = 0.010  # splits per prose paragraph; FLAG at or above
OCR_FUNC_MIN = 0.25  # see func_frac below

# A split only counts when one side is a substantial prose block (reuses the
# pagenumbers notion of prose), so verse and dialogue beats -- short lines that
# legitimately end unpunctuated and start lowercase -- do not accumulate.
_OCR_PROSE_MIN = PROSE_MIN

# Residual false-positive guards, each from a class found in the validation
# sweep. A back-of-book index rendered as <p> blocks reads as a wall of
# unpunctuated lowercase runs ("See also" is its near-universal signature).
# An epistolary sign-off is a dangling sub-40-char fragment with no terminal
# punctuation ("in which hope I rest," / "respected sir,"). A display equation
# set as text is mostly non-alphabetic ("the equation" / "y2 + y = x3 - x ?")
# and renders fine on its own line.
_OCR_INDEX_RE = re.compile(r"\bSee also\b")
_OCR_INDEX_MIN_BLOCKS = 3
_OCR_TAIL_MIN = 40
_OCR_TERMINALS = ".!?\"'’”…)"
_OCR_ALPHA_MIN = 0.5


def _alpha_density(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    return sum(c.isalpha() for c in chars) / len(chars)


# The style-vs-damage discriminator. Deliberately unpunctuated literary prose
# (Fosse's Septology, Evaristo's "Girl, Woman, Other", Kingsnorth's "The Wake")
# racks up enormous split rates, but its paragraphs end at CLAUSE boundaries;
# conversion damage splits at line-wrap/page-break positions, so the fragment
# ends on a function word ("sat the disembodied" / "heads who were..."). On the
# reference library, style books measured func_frac <= 0.11 and every hand-
# confirmed damage case >= 0.26, so 0.25 separates cleanly. A small closed
# function-word set, same spirit as the content analyzer's stopword votes --
# not a dictionary.
OCR_FUNC_WORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "with",
        "for",
        "from",
        "as",
        "that",
        "this",
        "these",
        "those",
        "his",
        "her",
        "their",
        "its",
        "my",
        "your",
        "our",
        "was",
        "were",
        "is",
        "are",
        "be",
        "been",
        "being",
        "had",
        "has",
        "have",
        "he",
        "she",
        "they",
        "it",
        "we",
        "you",
        "i",
        "not",
        "no",
        "so",
        "if",
        "when",
        "than",
        "then",
        "who",
        "whom",
        "which",
        "into",
        "onto",
        "over",
        "under",
        "between",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "up",
        "down",
        "out",
        "off",
        "very",
        "more",
        "most",
        "some",
        "any",
        "each",
        "every",
        "either",
        "neither",
    ]
)

# En-dash embedded inside a word: OCR reads a hyphen as U+2013 ("bottom–feedin'").
EN_DASH_WORD_RE = re.compile(r"[A-Za-z]–[A-Za-z]")
# Doubled opening quote: an opening single quote re-recognized before a
# dialogue contraction ("' 'Course"). Straight or curly. The first quote must
# follow whitespace: a closing quote hugs the word before it, so this stays
# blind to legitimate close-then-open sequences ("...said.' 'No...") that
# single-quote-dialogue books produce constantly.
DOUBLED_QUOTE_RE = re.compile(r"(?<=\s)['‘]\s+['‘’]\w")
# A space-stripped proper-noun compound: CamelCase with 2+ humps
# ("AnkhMorpork"); only damage when the hyphenated form also appears.
_CAMEL_RE = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b")
_HUMP_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")


def analyze_ocr(book: Book) -> dict:
    """Score OCR/conversion damage over the (pre-read) spine, skipping nav.

    Primary signal: mid-sentence paragraph splits -- a prose block ends without
    terminal punctuation (last char a lowercase letter or comma) and the next
    prose block starts lowercase. Splits are only paired within one spine doc;
    chapter-file boundaries never produce a pair, and a pair interrupted by a
    rendered image (inline formula, card diagram) is cleared."""
    paras = 0
    splits = 0
    func_splits = 0
    examples: list[dict] = []
    for doc in book.spine:
        if book.nav and doc == book.nav:
            continue
        parser = _Blocks()
        try:
            parser.feed(book.docs.get(doc, ""))
        except Exception:
            continue
        li = sum(1 for t, _ in parser.blocks if t == "li")
        # a doc that is mostly <li> is a TOC / page-list, not body text
        if parser.blocks and li / len(parser.blocks) > 0.5:
            continue
        # a back-of-book index rendered as <p> blocks is not body text either
        if (
            sum(1 for _, t in parser.blocks if _OCR_INDEX_RE.search(t))
            >= _OCR_INDEX_MIN_BLOCKS
        ):
            continue
        paras += sum(1 for t, _ in parser.blocks if t in ("p", "div"))
        # pairs must be adjacent in reading order: an intervening heading /
        # scene-break block means a boundary, not a mid-sentence split
        for i in range(len(parser.blocks) - 1):
            ptag, prev = parser.blocks[i]
            ntag, nxt = parser.blocks[i + 1]
            if ptag not in ("p", "div") or ntag not in ("p", "div"):
                continue
            if parser.img_before[i + 1]:
                continue
            # a display quotation starts (and its resumption ends) mid-sentence
            # by design; never pair into or out of a <blockquote>
            if parser.in_quote[i] or parser.in_quote[i + 1]:
                continue
            # a dangling short unterminated fragment is a sign-off, not a
            # sentence remnant; a mostly-non-alphabetic fragment is display math
            if len(nxt) < _OCR_TAIL_MIN and nxt[-1] not in _OCR_TERMINALS:
                continue
            if min(_alpha_density(prev), _alpha_density(nxt)) < _OCR_ALPHA_MIN:
                continue
            unfinished = prev[-1].islower() or prev[-1] == ","
            lower_start = nxt[0].islower()
            substantial = len(prev) > _OCR_PROSE_MIN or len(nxt) > _OCR_PROSE_MIN
            if unfinished and lower_start and substantial:
                words = prev.rstrip(",").split()
                last = words[-1].lower() if words else ""
                # "Lordat noticed that" / lowercase block is the block-
                # quotation idiom of academic prose, not damage: an attribution
                # ending on "that" introduces a quote set as its own block,
                # which rightly starts lowercase
                if last == "that":
                    continue
                splits += 1
                if last in OCR_FUNC_WORDS:
                    func_splits += 1
                if len(examples) < 8:
                    examples.append({"prev": prev[-60:], "next": nxt[:60]})

    # Side signals over the visible text (dictionary-free by design).
    text = " ".join(book.visible_texts())
    glued: list[str] = []
    for w in set(_CAMEL_RE.findall(text)):
        hyphenated = _HUMP_RE.sub("-", w)
        if hyphenated in text:
            glued.append(f"{w}~{hyphenated}")
    return {
        "paras": paras,
        "splits": splits,
        "split_rate": splits / paras if paras else 0.0,
        "func_frac": func_splits / splits if splits else 0.0,
        "en_dash_words": len(EN_DASH_WORD_RE.findall(text)),
        "doubled_quotes": len(DOUBLED_QUOTE_RE.findall(text)),
        "glued": sorted(glued)[:8],
        "examples": examples,
    }


def is_ocr_damaged(r: dict) -> bool:
    return (
        r["paras"] >= OCR_MIN_PARAS
        and r["splits"] >= OCR_MIN_SPLITS
        and r["split_rate"] >= OCR_FLAG_RATE
        and r["func_frac"] >= OCR_FUNC_MIN
    )


def _ocr_detail(r: dict) -> str:
    bits = [
        f"{r['splits']} mid-sentence splits / {r['paras']} paragraphs "
        f"({r['split_rate'] * 100:.1f}%, {r['func_frac'] * 100:.0f}% on function words)"
    ]
    if r["en_dash_words"]:
        bits.append(f"en-dash-in-word x{r['en_dash_words']}")
    if r["doubled_quotes"]:
        bits.append(f"doubled quotes x{r['doubled_quotes']}")
    if r["glued"]:
        bits.append("glued nouns: " + ", ".join(r["glued"][:3]))
    return ", ".join(bits)


def scan_ocr(path: Path) -> dict:
    return analyze_ocr(load_book(path))


# ----------------------------------------------------------------------------
# Per-analyzer reporting (library mode)
# ----------------------------------------------------------------------------


def _content_sections(nonlatin_hits, latin_foreign, signature_hits) -> int:
    """Print the content sections; return 1 if any unexpected hit or signature."""

    def show(label: str, hits: list[tuple], color: str) -> int:
        unexpected = [h for h in hits if not h[3]]
        expected = [h for h in hits if h[3]]
        if hits:
            print(f"{color}{BOLD}{label} ({len(hits)}){RESET}")
            for book_id, title, tag, _exp, detail in sorted(unexpected):
                print(f"  {RED}#{book_id}{RESET} [{tag}] {title}")
                print(f"    {detail}")
            for book_id, title, tag, _exp, detail in sorted(expected):
                print(f"  #{book_id} [{tag}] {title}  {GREEN}(expected-foreign){RESET}")
                print(f"    {detail}")

        return len(unexpected)

    unexpected = 0
    unexpected += show("NON-LATIN SCRIPT", nonlatin_hits, RED)
    unexpected += show("LATIN-SCRIPT FOREIGN (stopword vote)", latin_foreign, RED)
    # An injected piracy/ad notice is a defect no matter what language the book
    # is declared in, so the expected-foreign flag never applies here: without
    # this, a signature hit on a declared-foreign book printed "(expected-
    # foreign)" and "0 file(s) need review" while still failing the run.
    signature_hits = [(b, t, g, False, d) for b, t, g, _e, d in signature_hits]
    unexpected += show("INJECTION SIGNATURE", signature_hits, YELLOW)
    if unexpected == 0 and not signature_hits:
        print(f"{GREEN}{BOLD}content CLEAN{RESET}: no unexpected foreign content.")
        return 0
    print(
        f"{RED}{BOLD}content FOUND{RESET}: {unexpected} file(s) need review "
        f"(replace wrong-language editions with English copies)."
    )
    return 1


def _pagenum_sections(found) -> int:
    if found:
        print(f"{RED}{BOLD}BAKED-IN PAGE NUMBERS ({len(found)}){RESET}")
        for book_id, title, tag, r in sorted(found, key=lambda x: -x[3]["n_hits"]):
            mark = f"  {YELLOW}[watermark]{RESET}" if r["watermark"] else ""
            print(
                f"  {RED}#{book_id}{RESET} [{tag}] {title}{mark}\n"
                f"    {r['n_hits']} baked numbers, {r['span'] * 100:.0f}% of book, run {r['run']}"
            )
            for ex in r["examples"][:3]:
                print(f"      ...{ex['prev']}  {BOLD}{ex['v']}{RESET}  {ex['next']}...")
        print()
        print(
            f"{RED}{BOLD}pagenumbers FOUND{RESET}: {len(found)} file(s) need review "
            f"(re-source and replace bad conversions)."
        )
        return 1
    print(f"{GREEN}{BOLD}pagenumbers CLEAN{RESET}: no baked-in page numbers found.")
    return 0


def _empty_sections(empty, partial, thin) -> int:
    if empty:
        print(f"{RED}{BOLD}EMPTY / NO TEXT ({len(empty)}){RESET}")
        for book_id, title, tag, r in sorted(empty, key=lambda x: x[3]["chars"]):
            print(f"  {RED}#{book_id}{RESET} [{tag}] {title}\n    {_empty_detail(r)}")
        print()
    if partial:
        print(f"{RED}{BOLD}PARTIAL / PLACEHOLDER EXPORT ({len(partial)}){RESET}")
        for book_id, title, tag, r in sorted(partial, key=lambda x: x[3]["chars"]):
            print(f"  {RED}#{book_id}{RESET} [{tag}] {title}\n    {_empty_detail(r)}")
        print()
    if thin:
        print(
            f"{YELLOW}{BOLD}THIN (review; may be legitimately short) ({len(thin)}){RESET}"
        )
        for book_id, title, tag, r in sorted(thin, key=lambda x: x[3]["chars"]):
            print(
                f"  {YELLOW}#{book_id}{RESET} [{tag}] {title}\n    {_empty_detail(r)}"
            )
        print()
    found = len(empty) + len(partial)
    if found:
        print(f"{RED}{BOLD}emptytext FOUND{RESET}: {found} file(s) need re-sourcing.")
        return 1
    suffix = f" ({len(thin)} thin, advisory)" if thin else ""
    print(f"{GREEN}{BOLD}emptytext CLEAN{RESET}: every EPUB has body text{suffix}.")
    return 0


def _ocr_sections(found) -> int:
    if found:
        print(f"{RED}{BOLD}OCR-DAMAGED PROSE ({len(found)}){RESET}")
        for book_id, title, tag, r in sorted(found, key=lambda x: -x[3]["split_rate"]):
            print(f"  {RED}#{book_id}{RESET} [{tag}] {title}\n    {_ocr_detail(r)}")
            for ex in r["examples"][:3]:
                print(f"      ...{ex['prev']}  {BOLD}/{RESET}  {ex['next']}...")
        print()
        print(
            f"{RED}{BOLD}ocr FOUND{RESET}: {len(found)} file(s) need review "
            f"(re-source and replace damaged conversions)."
        )
        return 1
    print(f"{GREEN}{BOLD}ocr CLEAN{RESET}: no OCR-damaged prose found.")
    return 0


def _monolithic_sections(hits) -> int:
    if hits:
        print(f"{RED}{BOLD}MONOLITHIC DOCUMENTS ({len(hits)}){RESET}")
        for book_id, title, tag, r in sorted(
            hits, key=lambda x: -x[3]["max_doc_chars"]
        ):
            print(f"  {RED}#{book_id}{RESET} [{tag}] {title}")
            print(
                f"    max doc {r['max_doc_chars']:,} chars ({r['worst_doc']});"
                f" spine {r['spine_len']}"
            )
        print()
        print(
            f"{RED}{BOLD}monolithic FOUND{RESET}: {len(hits)} file(s) need review "
            f"(readers may refuse to render; re-source or split by hand)."
        )
        return 1
    print(f"{GREEN}{BOLD}monolithic CLEAN{RESET}: no oversized single document.")
    return 0


def _corrupt_sections(hits) -> int:
    if hits:
        print(f"{RED}{BOLD}CORRUPT ARCHIVES ({len(hits)}){RESET}")
        for book_id, title, tag, n, first in sorted(hits):
            print(f"  {RED}#{book_id}{RESET} [{tag}] {title}")
            print(f"    corrupt:{n} (first: {first}) — damaged archive; re-source")
        print()
        print(
            f"{RED}{BOLD}archive FOUND{RESET}: {len(hits)} file(s) need re-sourcing "
            f"(a damaged archive is not a content decision)."
        )
        return 1
    print(f"{GREEN}{BOLD}archive CLEAN{RESET}: every entry fully readable.")
    return 0


def _spine_sections(hits, advisory) -> int:
    if advisory:
        print(
            f"{YELLOW}{BOLD}TOC MANIFEST CONVENTION ({len(advisory)}; reported, not flagged){RESET}"
        )
        for book_id, title, tag, r in sorted(advisory):
            print(f"  {YELLOW}#{book_id}{RESET} [{tag}] {title}")
            print(f"    toc {r['refs']} refs, {r['absent']} absent ({r['class']})")
        print()
    if hits:
        print(f"{RED}{BOLD}SPINE FRAGMENTS ({len(hits)}){RESET}")
        for book_id, title, tag, r in sorted(hits):
            print(f"  {RED}#{book_id}{RESET} [{tag}] {title}")
            print(f"    toc {r['refs']} refs, {r['absent']} absent — span broken")
        print()
        print(
            f"{RED}{BOLD}spine FOUND{RESET}: {len(hits)} file(s) are fragments "
            f"of themselves; quarantine."
        )
        return 1
    if not advisory:
        print(f"{GREEN}{BOLD}spine CLEAN{RESET}: every ToC target present.")
    return 0


def _completeness_sections(advisory) -> int:
    """Print the completeness advisories; the analyzer never fails a run."""
    if advisory:
        print(
            f"{YELLOW}{BOLD}COMPLETENESS SPOT-CHECK ({len(advisory)} to eyeball;"
            f" reported, not flagged){RESET}"
        )
        for book_id, title, tag, r in sorted(advisory):
            print(f"  {YELLOW}#{book_id}{RESET} [{tag}] {title}")
            _p, _s, lines = _completeness_dir(r)
            for ln in lines:
                print(f"    {ln}")
        print()
        print(
            f"{YELLOW}{BOLD}completeness DONE{RESET}: read the excerpts above;"
            f" a trailing ToC or unreadable docs never fail the run."
        )
        return 0
    print(f"{GREEN}{BOLD}completeness CLEAN{RESET}: every book reads whole.")
    return 0


# ----------------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------------

ALL: tuple[str, ...] = (
    "content",
    "pagenumbers",
    "emptytext",
    "ocr",
    "monolithic",
    "completeness",
)


def _record_verdict(
    record: dict, key: str, verdict: tuple[bool, str, list[str]]
) -> None:
    """Fold one analyzer verdict into a book's audit --json record."""
    problem, status, lines = verdict
    record["verdicts"][key] = {
        "problem": problem,
        "status": status,
        "details": list(lines),
    }
    if problem:
        record["status"] = "problem"


def write_audit_json(
    json_path: str | Path,
    records: list[dict],
    *,
    root: Path,
    selected: list[str],
    min_chars: int,
    thin_chars: int,
    max_doc_chars: int,
    scanned: int,
    error_count: int,
) -> bool:
    """Emit the machine-readable audit report (audit --json FILE).

    Same shape as library --json: mode/root/summary plus one record per file
    carrying per-analyzer verdicts (problem/status/details). This is the
    contract every downstream consumer of the phase-1 EPUB slice reads.
    Returns False (and reports) when the write fails: the caller turns that
    into a trouble exit instead of losing the run's summary to a traceback.
    """
    payload = {
        "mode": "audit",
        "root": str(root),
        "analyzers": sorted(set(selected) | {"archive", "spine"}),
        "thresholds": {
            "min_chars": min_chars,
            "thin_chars": thin_chars,
            "max_doc_chars": max_doc_chars,
        },
        "summary": {
            "scanned": scanned,
            "problems": sum(1 for r in records if r["status"] == "problem"),
            "errors": error_count,
        },
        "books": records,
    }
    try:
        Path(json_path).expanduser().write_text(json.dumps(payload, indent=2) + "\n")
        return True
    except OSError as e:
        print(f"ERROR: could not write --json file {json_path}: {e}", file=sys.stderr)
        return False


def run_library(
    selected: list[str],
    min_chars: int,
    thin_chars: int,
    tag: str | None = None,
    max_doc_chars: int = DEFAULT_MAX_DOC_CHARS,
    json_path: str | Path | None = None,
) -> int:
    # The scan loop below reuses `tag` for its per-book display column; keep
    # the --tag argument under a distinct name so it survives that shadowing.
    audit_tag = tag
    library_root = resolve_library_root()
    if library_root is None:
        print(
            "ERROR: no metadata.db next to this script or in the current "
            "directory. Run from the library directory."
        )
        return 2
    db_path = library_root / "metadata.db"

    from cquarry.db import CalibreDB

    try:
        db = CalibreDB(str(db_path))
    except Exception as e:
        print(f"ERROR: cannot open {db_path}: {e}")
        return 2
    try:
        cur = db.conn.cursor()
        booktags: dict[int, list[str]] = {}
        for bid, tname in cur.execute(
            "SELECT bt.book, t.name FROM books_tags_link bt JOIN tags t ON t.id = bt.tag"
        ):
            booktags.setdefault(bid, []).append(tname)
        declared: dict[int, set[str]] = {}
        for bid, lang in cur.execute(
            "SELECT bl.book, l.lang_code FROM books_languages_link bl "
            "JOIN languages l ON l.id = bl.lang_code"
        ):
            declared.setdefault(bid, set()).add(lang)
        rows = cur.execute(
            "SELECT b.id, b.title FROM books b "
            "JOIN data d ON d.book = b.id WHERE d.format = 'EPUB' ORDER BY b.id"
        ).fetchall()
        # Canonical path resolution (library root / books.path / name.epub)
        # comes from cquarry so the layout logic lives in exactly one place.
        epubs = [
            (
                bid,
                title,
                Path(db.get_format_path(bid, "EPUB", verify=False)),
            )
            for bid, title in rows
        ]
    finally:
        db.close()

    nonlatin_hits: list[tuple] = []
    latin_foreign: list[tuple] = []
    signature_hits: list[tuple] = []
    pagenum_found: list[tuple] = []
    empty_hits: list[tuple] = []
    partial_hits: list[tuple] = []
    thin_hits: list[tuple] = []
    ocr_found: list[tuple] = []
    mono_hits: list[tuple] = []
    completeness_advisory: list[tuple] = []
    corrupt_hits: list[tuple] = []
    spine_hits: list[tuple] = []
    spine_advisory: list[tuple] = []
    errors: list[tuple] = []
    json_records: list[dict] = []
    scanned = 0

    for book_id, title, full in ui.tqdm(epubs, desc=ui.info("Scanning library")):
        tags = booktags.get(book_id, [])
        tag = tags[0] if tags else "?"
        record = {
            "id": book_id,
            "title": title,
            "path": str(full),
            "status": "clean",
            "verdicts": {},
        }
        json_records.append(record)
        try:
            book = load_book(full)
        except Exception as e:
            errors.append((book_id, title, tag, f"{type(e).__name__}: {e}"))
            record["status"] = "error"
            record["error"] = f"{type(e).__name__}: {e}"
            continue
        scanned += 1
        corrupt_r = analyze_corrupt(book)
        if corrupt_r["n"]:
            corrupt_hits.append(
                (book_id, title, tag, corrupt_r["n"], corrupt_r["first"])
            )
            _record_verdict(record, "archive", _corrupt_verdict(corrupt_r))
        else:
            _record_verdict(record, "archive", (False, "OK", []))
        spine_r = spine_integrity(book)
        if spine_r["class"] == "fragment":
            spine_hits.append((book_id, title, tag, spine_r))
            _record_verdict(record, "spine", _spine_verdict(spine_r))
        elif spine_r["class"] != "ok":
            spine_advisory.append((book_id, title, tag, spine_r))
            _record_verdict(record, "spine", _spine_verdict(spine_r))
        else:
            _record_verdict(record, "spine", (False, "OK", []))

        if "content" in selected:
            r = analyze_content(book)
            langs = declared.get(book_id, set())
            decl = ",".join(sorted(langs)) if langs else "?"
            expected = any(lang != "eng" for lang in langs) or any(
                t.startswith("NonFic.Language.") for t in tags
            )
            for category, detail in findings(r):
                if category == "NON-LATIN SCRIPT":
                    nonlatin_hits.append(
                        (book_id, title, tag, expected, f"{detail}; declared={decl}")
                    )
                elif category == "LATIN-SCRIPT FOREIGN":
                    latin_foreign.append(
                        (book_id, title, tag, expected, f"{detail}; declared={decl}")
                    )
                else:
                    signature_hits.append((book_id, title, tag, expected, detail))
            _record_verdict(record, "content", _content_dir(r))

        if "pagenumbers" in selected:
            r = analyze_pagenumbers(book)
            if is_defective(r):
                pagenum_found.append((book_id, title, tag, r))
            _record_verdict(record, "pagenumbers", _pagenum_dir(r))

        if "emptytext" in selected and not corrupt_r["n"]:
            r = analyze_emptytext(book)
            verdict = classify(r, min_chars, thin_chars)
            if verdict == "EMPTY":
                empty_hits.append((book_id, title, tag, r))
            elif verdict == "PARTIAL":
                partial_hits.append((book_id, title, tag, r))
            elif verdict == "THIN":
                thin_hits.append((book_id, title, tag, r))
            # skipped wholesale when the archive verdict owns the book's
            # body-text story, so the record carries no emptytext key there
            _record_verdict(record, "emptytext", _empty_dir(r, min_chars, thin_chars))

        if "ocr" in selected:
            r = analyze_ocr(book)
            if is_ocr_damaged(r):
                ocr_found.append((book_id, title, tag, r))
            _record_verdict(record, "ocr", _ocr_dir(r))

        if "monolithic" in selected:
            r = analyze_monolithic(book)
            if is_monolithic(r, max_doc_chars):
                mono_hits.append((book_id, title, tag, r))
            _record_verdict(record, "monolithic", _monolithic_dir(r, max_doc_chars))

        if "completeness" in selected and not corrupt_r["n"]:
            r = analyze_completeness(book)
            _p, status, lines = _completeness_dir(r)
            if status != "OK":
                completeness_advisory.append((book_id, title, tag, r))
            # advisory by contract: problem is always False, so the record
            # status and the exit code are never moved by this analyzer
            _record_verdict(record, "completeness", (False, status, lines))

    print(f"Scanned {scanned} EPUBs in {library_root}\n")
    rc = 0
    multi = len(selected) > 1
    for key in ALL:
        if key not in selected:
            continue
        if multi:
            print(f"{BOLD}== {key} =={RESET}")
        if key == "content":
            rc |= _content_sections(nonlatin_hits, latin_foreign, signature_hits)
        elif key == "pagenumbers":
            rc |= _pagenum_sections(pagenum_found)
        elif key == "emptytext":
            rc |= _empty_sections(empty_hits, partial_hits, thin_hits)
        elif key == "monolithic":
            rc |= _monolithic_sections(mono_hits)
        elif key == "completeness":
            rc |= _completeness_sections(completeness_advisory)
        else:
            rc |= _ocr_sections(ocr_found)
        if multi:
            print()

    # The archive and spine verdicts are always-on — their scan runs on every
    # book whether or not any content analyzer was selected — so their
    # sections are not gated on --only. A CRC-corrupt book must print and
    # fail the run in library mode exactly as it does in directory mode;
    # leaving these branches inside the ALL loop made them dead code and let
    # a corrupt book exit 0 with "emptytext CLEAN".
    rc |= _corrupt_sections(corrupt_hits)
    rc |= _spine_sections(spine_hits, spine_advisory)

    if errors:
        print(f"{YELLOW}{BOLD}SCAN ERRORS ({len(errors)}){RESET}")
        for book_id, title, tag, msg in errors:
            print(f"  #{book_id} [{tag}] {title}\n    {msg}")
        print()
        rc |= 1

    if json_path is not None:
        if not write_audit_json(
            json_path,
            json_records,
            root=library_root,
            selected=selected,
            min_chars=min_chars,
            thin_chars=thin_chars,
            max_doc_chars=max_doc_chars,
            scanned=scanned,
            error_count=len(errors),
        ):
            rc |= 1

    if audit_tag:
        rc |= _apply_audit_tag(
            library_root,
            audit_tag,
            {
                # expected-foreign content findings are informational (the
                # book is DECLARED that way); only unexpected hits get tagged
                "content": [h[0] for h in nonlatin_hits if not h[3]]
                + [h[0] for h in latin_foreign if not h[3]]
                + [h[0] for h in signature_hits],  # signatures are always defects
                "pagenumbers": [h[0] for h in pagenum_found],
                "emptytext": [
                    h[0] for h in empty_hits + partial_hits
                ],  # THIN is advisory and stays untagged
                "ocr": [h[0] for h in ocr_found],
                "monolithic": [h[0] for h in mono_hits],
                "archive": [h[0] for h in corrupt_hits],
                "spine": [h[0] for h in spine_hits],
            },
        )
    return rc


def _apply_audit_tag(
    library_root: Path, tag: str, flagged: dict[str, list[int]]
) -> int:
    """Apply ``tag`` to every flagged book via cquarry's opt-in write path.

    Only reached with the explicit --tag flag; the audit itself stays strictly
    read-only. Calibre should be closed so the write does not fight its lock.
    Returns 0 on success, 2 on setup failure.
    """
    from cquarry.write import WritableCalibreDB

    ids = sorted({bid for bids in flagged.values() for bid in bids})
    if not ids:
        print("Nothing flagged; no tags applied.")
        return 0
    db_path = library_root / "metadata.db"
    try:
        with WritableCalibreDB(str(db_path)) as wdb:
            applied = 0
            for bid in ids:
                if wdb.add_tag(bid, tag):
                    applied += 1
    except Exception as e:
        print(f"ERROR: tagging failed ({type(e).__name__}: {e}).")
        print("Is Calibre closed? The write path refuses to fight its lock.")
        return 2
    print(
        f"Tagged {applied} of {len(ids)} flagged books with [{tag}]"
        " (already-tagged books skipped)."
    )
    return 0


def _content_dir(r: dict) -> tuple[bool, str, list[str]]:
    hits = findings(r)
    if not hits:
        return False, "OK", []
    return True, "REVIEW", [f"{cat}: {detail}" for cat, detail in hits]


def _pagenum_dir(r: dict) -> tuple[bool, str, list[str]]:
    if not is_defective(r):
        return False, "OK", []
    mark = "  [watermark]" if r["watermark"] else ""
    lines = [
        f"{r['n_hits']} baked numbers, {r['span'] * 100:.0f}% of book, run {r['run']}{mark}"
    ]
    lines += [
        f"...{ex['prev']}  {ex['v']}  {ex['next']}..." for ex in r["examples"][:2]
    ]
    return True, "REVIEW", lines


def _empty_dir(r: dict, min_chars: int, thin_chars: int) -> tuple[bool, str, list[str]]:
    verdict = classify(r, min_chars, thin_chars)
    if verdict == "OK":
        return False, "OK", []
    if verdict == "CORRUPT":
        return True, "CORRUPT", [_empty_detail(r)]
    return verdict in ("EMPTY", "PARTIAL"), verdict, [_empty_detail(r)]


def _monolithic_dir(r: dict, max_doc_chars: int) -> tuple[bool, str, list[str]]:
    if is_monolithic(r, max_doc_chars):
        return (
            True,
            "FLAG",
            [f"max doc {r['max_doc_chars']:,} chars ({r['worst_doc']})"],
        )
    return False, "OK", [f"max doc {r['max_doc_chars']:,} chars"]


def _ocr_dir(r: dict) -> tuple[bool, str, list[str]]:
    if not is_ocr_damaged(r):
        return False, "OK", []
    lines = [_ocr_detail(r)]
    lines += [f"...{ex['prev']}  /  {ex['next']}..." for ex in r["examples"][:2]]
    return True, "REVIEW", lines


def run_directory(
    directory: Path,
    selected: list[str],
    min_chars: int,
    thin_chars: int,
    max_doc_chars: int = DEFAULT_MAX_DOC_CHARS,
    json_path: str | Path | None = None,
) -> int:
    if not directory.is_dir():
        print(f"ERROR: {directory} is not a directory.")
        return 2
    epubs = sorted(directory.rglob("*.epub", case_sensitive=False))
    if not epubs:
        print(f"No .epub files found under {directory}")
        return 2

    print(f"Auditing {len(epubs)} EPUB(s) in {directory}\n")
    multi = len(selected) > 1
    problems = 0
    errors = 0
    json_records: list[dict] = []
    for path in ui.tqdm(epubs, desc=ui.info("Scanning directory")):
        record: dict = {"path": str(path), "status": "clean", "verdicts": {}}
        json_records.append(record)
        try:
            book = load_book(path)
        except Exception as e:
            ui.tqdm.write(
                f"  {YELLOW}ERROR {RESET} {path.name}\n      {type(e).__name__}: {e}"
            )
            errors += 1
            record["status"] = "error"
            record["error"] = f"{type(e).__name__}: {e}"
            continue

        verdicts = []
        book_problems = False
        corrupt_r = analyze_corrupt(book)
        spine_r = spine_integrity(book)
        for key in ALL:
            if key not in selected:
                continue
            if key in ("emptytext", "completeness") and corrupt_r["n"]:
                continue  # the archive verdict owns this book's body-text story
            if key == "content":
                problem, status, lines = _content_dir(analyze_content(book))
            elif key == "pagenumbers":
                problem, status, lines = _pagenum_dir(analyze_pagenumbers(book))
            elif key == "emptytext":
                problem, status, lines = _empty_dir(
                    analyze_emptytext(book), min_chars, thin_chars
                )
            elif key == "monolithic":
                problem, status, lines = _monolithic_dir(
                    analyze_monolithic(book), max_doc_chars
                )
            elif key == "completeness":
                problem, status, lines = _completeness_dir(analyze_completeness(book))
            else:
                problem, status, lines = _ocr_dir(analyze_ocr(book))
            if problem:
                book_problems = True
            verdicts.append((key, problem, status, lines))

        if corrupt_r["n"]:
            problem, status, lines = _corrupt_verdict(corrupt_r)
            book_problems = True
            verdicts.append(("archive", problem, status, lines))

        if spine_r["class"] != "ok":
            problem, status, lines = _spine_verdict(spine_r)
            if problem:
                book_problems = True
            verdicts.append(("spine", problem, status, lines))

        # JSON record: the display verdicts, then the always-on archive/spine
        # verdicts backfilled OK when they were silent. emptytext stays absent
        # when the archive verdict owns the book's body-text story.
        for key, problem, status, lines in verdicts:
            _record_verdict(record, key, (problem, status, lines))
        if "archive" not in record["verdicts"]:
            _record_verdict(record, "archive", (False, "OK", []))
        if "spine" not in record["verdicts"]:
            _record_verdict(record, "spine", (False, "OK", []))
        if book_problems:
            # the console counter counts books (one line each), matching
            # summary.problems, which counts records
            problems += 1

        if multi:
            ui.tqdm.write(f"  {path.name}")
            for key, problem, status, lines in verdicts:
                color = RED if problem else (YELLOW if status != "OK" else GREEN)
                ui.tqdm.write(f"      {color}{status:<6}{RESET} {key}")
                for ln in lines:
                    ui.tqdm.write(f"          {ln}")
        else:
            _key, problem, status, lines = verdicts[0]
            color = RED if problem else (YELLOW if status != "OK" else GREEN)
            ui.tqdm.write(f"  {color}{status:<6}{RESET} {path.name}")
            for ln in lines:
                ui.tqdm.write(f"      {ln}")
    print()

    json_ok = True
    if json_path is not None:
        json_ok = write_audit_json(
            json_path,
            json_records,
            root=directory,
            selected=selected,
            min_chars=min_chars,
            thin_chars=thin_chars,
            max_doc_chars=max_doc_chars,
            scanned=len(epubs) - errors,
            error_count=errors,
        )

    rc = 0 if json_ok else 1
    if problems == 0 and errors == 0:
        print(f"{GREEN}{BOLD}CLEAN{RESET}: no problems in {len(epubs)} file(s).")
        return rc
    print(
        f"{RED}{BOLD}FOUND{RESET}: {problems} problem(s) need review, "
        f"{errors} scan error(s)."
    )
    return 1 | rc


def run_single(
    book_id: int,
    selected: list[str],
    min_chars: int,
    thin_chars: int,
    tag: str | None = None,
    max_doc_chars: int = DEFAULT_MAX_DOC_CHARS,
    json_path: str | Path | None = None,
) -> int:
    """Audit one library book by id — cquarry's single-entity fetch.

    Uses :meth:`CalibreDB.get_book` so only that book's row is read instead
    of caching the entire library layout, then resolves the EPUB through
    cquarry's ``get_format_path``. The audit itself stays strictly read-only;
    ``--tag`` applies via cquarry's opt-in write path only when flagged.
    """
    library_root = resolve_library_root()
    if library_root is None:
        print(
            "ERROR: no metadata.db next to this script or in the current "
            "directory. Run from the library directory."
        )
        return 2

    from cquarry.db import CalibreDB

    try:
        db = CalibreDB(str(library_root / "metadata.db"))
    except Exception as e:
        print(f"ERROR: cannot open {library_root / 'metadata.db'}: {e}")
        return 2
    try:
        rec = db.get_book(book_id)
        if rec is None:
            print(f"ERROR: no book #{book_id} in {library_root}")
            return 2
        title = rec["title"]
        tags = rec["tags"]
        try:
            path = Path(db.get_format_path(book_id, "EPUB", verify=True))
        except (ValueError, FileNotFoundError) as e:
            print(f"ERROR: book #{book_id} ({title}): {e}")
            return 2
    finally:
        db.close()

    try:
        book = load_book(path)
    except Exception as e:
        print(f"ERROR reading {path.name}: {type(e).__name__}: {e}")
        if json_path is not None:
            write_audit_json(
                json_path,
                [
                    {
                        "id": book_id,
                        "title": title,
                        "path": str(path),
                        "status": "error",
                        "error": f"{type(e).__name__}: {e}",
                        "verdicts": {},
                    }
                ],
                root=library_root,
                selected=selected,
                min_chars=min_chars,
                thin_chars=thin_chars,
                max_doc_chars=max_doc_chars,
                scanned=0,
                error_count=1,
            )
        return 1  # the error record is best-effort; the run already failed

    tag_display = tags[0] if tags else "?"
    print(f"Auditing #{book_id} [{tag_display}] {title}\n  {path}\n")
    problems = 0
    multi = len(selected) > 1
    verdicts = []
    corrupt_r = analyze_corrupt(book)
    spine_r = spine_integrity(book)
    for key in ALL:
        if key not in selected:
            continue
        if key in ("emptytext", "completeness") and corrupt_r["n"]:
            # Corruption owns the body-text story for this book; reporting
            # EMPTY here would be the wrong disease.
            continue
        if key == "content":
            problem, status, lines = _content_dir(analyze_content(book))
        elif key == "pagenumbers":
            problem, status, lines = _pagenum_dir(analyze_pagenumbers(book))
        elif key == "emptytext":
            problem, status, lines = _empty_dir(
                analyze_emptytext(book), min_chars, thin_chars
            )
        elif key == "monolithic":
            problem, status, lines = _monolithic_dir(
                analyze_monolithic(book), max_doc_chars
            )
        elif key == "completeness":
            problem, status, lines = _completeness_dir(analyze_completeness(book))
        else:
            problem, status, lines = _ocr_dir(analyze_ocr(book))
        if problem:
            problems += 1
        verdicts.append((key, problem, status, lines))

    if corrupt_r["n"]:
        problem, status, lines = _corrupt_verdict(corrupt_r)
        problems += 1
        verdicts.append(("archive", problem, status, lines))

    if spine_r["class"] != "ok":
        problem, status, lines = _spine_verdict(spine_r)
        if problem:
            problems += 1
        verdicts.append(("spine", problem, status, lines))

    record: dict = {
        "id": book_id,
        "title": title,
        "path": str(path),
        "status": "clean",
        "verdicts": {},
    }
    for key, problem, status, lines in verdicts:
        _record_verdict(record, key, (problem, status, lines))
    if "archive" not in record["verdicts"]:
        _record_verdict(record, "archive", (False, "OK", []))
    if "spine" not in record["verdicts"]:
        _record_verdict(record, "spine", (False, "OK", []))
    json_ok = True
    if json_path is not None:
        json_ok = write_audit_json(
            json_path,
            [record],
            root=library_root,
            selected=selected,
            min_chars=min_chars,
            thin_chars=thin_chars,
            max_doc_chars=max_doc_chars,
            scanned=1,
            error_count=0,
        )

    for key, problem, status, lines in verdicts:
        if multi:
            print(f"  {key}")
        color = RED if problem else (YELLOW if status != "OK" else GREEN)
        prefix = "      " if multi else "  "
        print(f"{prefix}{color}{status:<6}{RESET} {key}")
        for ln in lines:
            print(f"{prefix}    {ln}")

    rc = 0 if json_ok else 1
    if problems:
        rc = 1
        print(f"\n{RED}{BOLD}FOUND{RESET}: {problems} problem(s) need review.")
    else:
        print(f"\n{GREEN}{BOLD}CLEAN{RESET}: book #{book_id} passed the audit.")
    if tag and problems:
        rc |= _apply_audit_tag(
            library_root,
            tag,
            {key: [book_id] for key, problem, _s, _l in verdicts if problem},
        )
    return rc
