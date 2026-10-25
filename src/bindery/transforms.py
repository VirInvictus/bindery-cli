"""Deterministic repair transforms for (X)HTML/XML text.

Every transform is a pure function `str -> (str, int)` returning the rewritten text
and how many fixes it made. The five the core pipeline runs (HTML_TRANSFORMS /
XML_TRANSFORMS) change document semantics not at all: they only make
already-intended markup well-formed (self-close void elements, turn undeclared named
entities into numeric character references, escape stray ampersands, strip junk before
the XML prolog, drop a duplicated root xmlns). The same module also hosts the opt-in
repairs: structural ones that alter markup or fabricate minimal content
(--fix-empty-body, --unwrap-illegal-tags, ...) and the lossy strips this package
ships (--strip-broken-tags), all fenced behind explicit CLI flags and the
epubcheck acceptance bars. See spec.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from functools import wraps
from html.entities import html5, name2codepoint

Transform = Callable[[str], tuple[str, int]]

VOID = "area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr"
XML_PREDEFINED = {"amp", "lt", "gt", "quot", "apos"}

# Match an open void-element tag. The lookahead after the name is essential: without
# it `<col` matches inside `<colgroup>` and we self-close a non-void element, orphaning
# its end-tag (the bug that introduced fatals on real books). A plain `\b` is not
# enough either: `-`, `:`, and `.` are valid XML name characters but not \w, so `\b`
# still matched `<col` inside a custom `<col-group>`. Attributes are matched
# quote-aware so a `>` inside an attribute value does not end the tag early. Group 3
# captures an existing trailing slash so already-self-closed tags are left untouched.
_VOID_RE = re.compile(
    rf"""<({VOID})(?=[\s/>])((?:"[^"]*"|'[^']*'|[^>"'])*?)\s*(/?)>""",
    re.IGNORECASE | re.DOTALL,
)
_VOID_END_RE = re.compile(rf"""</(?:{VOID})\s*>""", re.IGNORECASE)
_NAMED_ENTITY_RE = re.compile(r"&([a-zA-Z][a-zA-Z0-9]*);")
_BARE_AMP_RE = re.compile(r"&(?![a-zA-Z][a-zA-Z0-9]*;|#[0-9]+;|#[xX][0-9a-fA-F]+;)")
# A start tag (quote-aware) and an attribute within it, for invalid-attribute stripping.
_START_TAG_RE = re.compile(r"""<[a-zA-Z][\w:.-]*(?:"[^"]*"|'[^']*'|[^>])*+>""")
# The unquoted-value branch stops at whitespace or the end of the tag, and the `/?>`
# lookahead is what keeps it off the tag's own self-closing slash: a plain `[^\s>]+`
# swallowed it, so dropping the attribute in `<img 31=x/>` left `<img>` and turned a
# well-formed tag into an unclosed one. A `/` mid-value (a URL) is still consumed,
# because only a `/` immediately before `>` can be the tag's.
_ATTR_RE = re.compile(
    r"""(\s+)([^\s=/>]+)(\s*=\s*)("[^"]*"|'[^']*'|[^\s>]*?(?=\s|/?>))"""
)
_XMLNS_DECL_RE = re.compile(r"xmlns:([A-Za-z_][\w.-]*)\s*=")
_HTML_TAG_RE = re.compile(r"<html\b[^>]*>", re.IGNORECASE)
_XMLNS_ATTR_RE = re.compile(r'\s+xmlns="[^"]*"')
_EPUB_PREFIX_ATTR_RE = re.compile(
    r"""(?:\bepub:)?prefix\s*=\s*(?:"([^"]*)"|'([^']*)')"""
)
_EPUB_PREFIX_VAL_RE = re.compile(r"(?:^|\s)([A-Za-z_][\w.-]*)\s*:\s*\S+")

# CDATA sections and comments hold literal text: a bare `&`, an entity name, or a
# `<br>` inside them is already legal XML, and rewriting it would change the content
# (e.g. `&` in CDATA-wrapped CSS/JS renders as `&`; escaped, it renders as `&amp;`).
# Splitting on this regex yields alternating outside/protected segments, so the
# markup transforms run only on the even (outside) indices.
#
# The first alternative is Calibre's inline-CSS wrapper idiom
# `/*<![CDATA[*/ ... /*]]>*/`: the bare CDATA alternative ends at the first `]]>`
# (inside the closing `/*]]>`), leaving the trailing `*/` as an outside segment,
# where strip_prolog_junk amputated it as "stray bytes before the first `<"` (a
# phantom fix that truncated the CSS comment and manufactured one CSS-008
# "Premature end of file" per wrapped file; the 2026-10-24 whole-library dry run
# gate-rejected 111 books on exactly this). The wrapper is protected as one unit.
_PROTECTED_RE = re.compile(
    r"(/\*<!\[CDATA\[\*/.*?/\*\]\]>\*/|<!\[CDATA\[.*?\]\]>|<!--.*?-->)",
    re.DOTALL,
)


def outside_protected_map(s: str, fn: Transform) -> tuple[str, int]:
    """Run a transform over every span that is not a CDATA section or comment.

    The decorator form (`_outside_protected`) only fits single-argument
    transforms; the context-carrying repairs (book-level file/id sets) call
    this directly with a partially-applied transform.
    """
    if "<!" not in s:  # fast path: nothing to protect
        return fn(s)
    parts = _PROTECTED_RE.split(s)
    total = 0
    for i in range(0, len(parts), 2):
        parts[i], n = fn(parts[i])
        total += n
    return "".join(parts), total


def _outside_protected(fn: Callable) -> Callable:
    """Wrap a transform so it never touches CDATA sections or comments.

    Extra args and keyword arguments are forwarded, so context-carrying
    transforms (unwrap_illegal_tags takes a protected_tags set) can be
    decorated too.
    """

    @wraps(fn)
    def wrapped(s: str, *args, **kwargs):
        return outside_protected_map(s, lambda part: fn(part, *args, **kwargs))

    return wrapped


@_outside_protected
def self_close_void(s: str) -> tuple[str, int]:
    """Self-close void elements that were left open (`<br>` -> `<br/>`).

    Already-self-closed tags are returned unchanged and not counted, so the fix is
    idempotent and reports only real changes.
    """
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        if m.group(0).endswith("/>"):
            return m.group(0)
        count += 1
        return f"<{m.group(1)}{m.group(2)}/>"

    s = _VOID_RE.sub(repl, s)

    # Strip any remaining end tags for void elements (e.g. </br>) which would
    # otherwise cause fatal XML parse errors since we self-closed their start tags.
    s, end_count = _VOID_END_RE.subn("", s)
    count += end_count

    return s, count


def strip_invalid_attributes(s: str) -> tuple[str, int]:
    """Remove attributes that make the XML unparseable: a name starting with a digit
    (e.g. a mangled `31=""`), or a namespaced name whose prefix is not declared anywhere
    in the document (e.g. Office VML `v:shapes` with no `xmlns:v`).

    A well-formed document has no such attributes by definition, so this is a no-op on
    good files and only touches already-malformed ones. The fix is surgical: only the
    offending attribute is dropped, everything else is preserved byte-for-byte.
    """
    # The declared-prefix set is computed over the whole document (over-collecting
    # from comments only makes the fix more conservative), but tags are rewritten
    # only outside CDATA/comment spans.
    declared = set(_XMLNS_DECL_RE.findall(s)) | {"xml", "xmlns"}
    for m in _EPUB_PREFIX_ATTR_RE.finditer(s):
        val = m.group(1) or m.group(2) or ""
        declared.update(_EPUB_PREFIX_VAL_RE.findall(val))
    count = 0

    def fix_tag(tag: re.Match) -> str:
        def drop(attr: re.Match) -> str:
            nonlocal count
            name = attr.group(2)
            prefix = name.split(":", 1)[0] if ":" in name else None
            if name[0].isdigit() or (prefix is not None and prefix not in declared):
                count += 1
                return ""
            return attr.group(0)

        return _ATTR_RE.sub(drop, tag.group(0))

    parts = _PROTECTED_RE.split(s)
    for i in range(0, len(parts), 2):
        parts[i] = _START_TAG_RE.sub(fix_tag, parts[i])
    return "".join(parts), count


def _entity_refs(name: str) -> str | None:
    """Numeric character reference(s) for an HTML entity name, or None if unknown.

    Most entities are one codepoint; the handful that expand to several (`&fjlig;`,
    `&NotEqualTilde;`, ...) become one numeric reference per codepoint, which renders
    identically.
    """
    if name in name2codepoint:
        return f"&#{name2codepoint[name]};"
    ch = html5.get(name + ";") or html5.get(name)
    if not ch:
        return None
    return "".join(f"&#{ord(c)};" for c in ch)


@_outside_protected
def fix_named_entities(s: str) -> tuple[str, int]:
    """Replace undeclared HTML named entities with numeric refs (`&nbsp;` -> `&#160;`).

    XML only predefines five entity names; everything else (`&nbsp;`, `&deg;`,
    `&eacute;`, ...) is a fatal "entity not declared" unless turned into a numeric
    reference, which every XML parser understands.
    """
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        name = m.group(1)
        if name in XML_PREDEFINED:
            return m.group(0)
        refs = _entity_refs(name)
        if refs is None:
            return m.group(0)
        count += 1
        return refs

    return _NAMED_ENTITY_RE.sub(repl, s), count


# A DOCTYPE internal subset can declare custom entities (<!DOCTYPE x [<!ENTITY ...>]>),
# making an "unknown" name legitimate; the escape skips such documents wholesale.
_INTERNAL_SUBSET_RE = re.compile(r"<!DOCTYPE[^>\[]*\[", re.IGNORECASE)


@_outside_protected
def _escape_unknown_part(s: str) -> tuple[str, int]:
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        name = m.group(1)
        if name in XML_PREDEFINED or _entity_refs(name) is not None:
            return m.group(0)
        count += 1
        return "&amp;" + m.group(0)[1:]

    return _NAMED_ENTITY_RE.sub(repl, s), count


def escape_unknown_entities(s: str) -> tuple[str, int]:
    """Escape entity references whose name is unknown (`&foo;` -> `&amp;foo;`).

    An undeclared entity outside the HTML5 table stays a fatal "entity not declared";
    escaping it renders exactly as browsers already render an unknown entity (the
    literal text `&foo;`). Conditionally semantics-preserving: identical rendering
    EXCEPT against a document whose DOCTYPE internal subset declares the entity, so
    any document carrying an internal subset is skipped wholesale. Opt-in
    (--escape-unknown-entities), never a core transform.
    """
    if _INTERNAL_SUBSET_RE.search(s):
        return s, 0
    return _escape_unknown_part(s)


# An <img> start tag, quote-aware like _VOID_RE; group 1 is the attribute block and
# group 2 an existing self-closing slash, so the tag is rebuilt without reordering.
_IMG_TAG_RE = re.compile(
    r"""<img(?=[\s/>])((?:"[^"]*"|'[^']*'|[^>"'])*?)\s*(/?)>""",
    re.IGNORECASE | re.DOTALL,
)
# Whitespace before `alt` keeps data-alt/xml:alt-style names from matching; a quoted
# value containing ` alt=` can false-positive, which only makes the fix skip that tag.
_ALT_PRESENT_RE = re.compile(r"""(?:^|\s)alt\s*=""", re.IGNORECASE)


@_outside_protected
def add_img_alt(s: str) -> tuple[str, int]:
    """Add `alt=""` to an <img> that has no alt attribute (the RSC-005 "missing
    required attribute" error).

    Rendering is unchanged (empty alt draws nothing), but this is the one transform
    that ADDS markup the author never wrote, and `alt=""` asserts "decorative" to a
    screen reader where a missing alt did not. Hence opt-in (--add-img-alt), never a
    core transform. Idempotent: a tag that already carries alt is left untouched.
    """
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        attrs, slash = m.group(1), m.group(2)
        if _ALT_PRESENT_RE.search(attrs):
            return m.group(0)
        count += 1
        return f'<img{attrs} alt=""{slash}>'

    return _IMG_TAG_RE.sub(repl, s), count


@_outside_protected
def escape_bare_amp(s: str) -> tuple[str, int]:
    """Escape a `&` that does not begin a valid entity/character reference."""
    return _BARE_AMP_RE.subn("&amp;", s)


@_outside_protected
def strip_prolog_junk(s: str) -> tuple[str, int]:
    """Remove a BOM or stray bytes before the first `<` ("content not allowed in prolog").

    Leading whitespace is junk only when an XML declaration follows it (a declaration
    must be the very first thing in the document). Before a DOCTYPE or the root element
    it is legal prolog whitespace and is left alone: counting it as a fix marked the
    document changed, which forced repair_epub's decode("utf-8", "replace") round-trip
    on a file that had nothing wrong with it. The protected-span wrapper keeps a
    leading comment's contents out of the search.
    """
    stripped = s.lstrip("﻿ \t\r\n")
    i = stripped.find("<")
    if i > 0:
        stripped = stripped[i:]
    if stripped == s:
        return s, 0
    removed = s[: len(s) - len(stripped)]
    if not removed.strip(" \t\r\n") and not stripped.startswith("<?xml"):
        return s, 0
    return stripped, 1


@_outside_protected
def drop_duplicate_xmlns(s: str) -> tuple[str, int]:
    """Keep only the first `xmlns="..."` on the root <html> element.

    The protected-span wrapper keeps an `<html ...>` mentioned inside a
    comment out of the rewrite: comment content is not markup.
    """
    m = _HTML_TAG_RE.search(s)
    if not m:
        return s, 0
    tag = m.group(0)
    seen = False
    count = 0

    def repl(mm: re.Match) -> str:
        nonlocal seen, count
        if seen:
            count += 1
            return ""
        seen = True
        return mm.group(0)

    new_tag = _XMLNS_ATTR_RE.sub(repl, tag)
    if count:
        s = s[: m.start()] + new_tag + s[m.start() + len(tag) :]
    return s, count


# The playOrder contract epubcheck enforces (the RSC-005 playOrder findings):
# elements whose <content src> point at the SAME target share one value,
# distinct targets carry distinct values, and the value set is contiguous
# 1..K across navPoint + navTarget + pageTarget elements together.
_PLAYORDER_EL_RE = re.compile(
    r"<(?:navPoint|navTarget|pageTarget)\b[^>]*>", re.IGNORECASE
)
_PLAYORDER_ATTR_RE = re.compile(
    r"\s+playOrder\s*=\s*(\"[^\"]*\"|'[^']*')", re.IGNORECASE
)
_CONTENT_SRC_RE = re.compile(
    r"<content\b[^>]*?\ssrc\s*=\s*(\"[^\"]*\"|'[^']*')", re.IGNORECASE
)


@_outside_protected
def fix_ncx_playorder(s: str) -> tuple[str, int]:
    """Re-sequence `playOrder` values so the NCX satisfies epubcheck's playOrder
    contract, and touch nothing when it already does.

    Contract-gated (the 2026-10-24 whole-library surgery): the previous form
    renumbered every `<navPoint>` to document-order 1..N unconditionally, which
    rewrote VALID TOCs into violations -- navPoints whose `<content src>` share
    a target must share one playOrder (splitting them is a finding), pageTarget/
    navTarget elements participate in the same sequence (ignoring them leaves
    gaps), and the gate rejected 211 of the 579 books it fired on, against 17
    real fixes. The resequencing here assigns each distinct target one value in
    first-appearance order and rewrites only the attributes whose value changes.

    Anchored to start tags of the three playOrder-bearing elements: the word
    `playOrder` inside a nav label's text is content, not markup, and is never
    touched. A navPoint with no playOrder attribute is not part of the sequence
    (adding attributes is not this repair), and an element whose `<content src>`
    cannot be located is treated as its own target. Known limit: the tag match
    ends at the first `>`, so a `>` inside a quoted attribute value before the
    playOrder attribute hides the element (the same single-class shape every
    tag matcher here documents).
    """
    elements: list[dict] = []  # (start, end, raw_value, target, order)
    current: dict | None = None
    for m in _PLAYORDER_EL_RE.finditer(s):
        tag = m.group(0)
        attr = _PLAYORDER_ATTR_RE.search(tag)
        if attr is None:
            continue
        if current is not None:
            elements.append(current)
        current = {
            "start": m.start(),
            "end": m.end(),
            "tag": tag,
            "raw": attr.group(1),
            "value": attr.group(1).strip("\"'"),
            "target": None,
        }
    if current is not None:
        elements.append(current)
    if not elements:
        return s, 0

    # Assign each element the src of the first <content> following its own start
    # tag and before the next playOrder-bearing element starts (flat and
    # parent-content-first nesting both assign correctly; children-content-first
    # nesting is the documented limit).
    for i, el in enumerate(elements):
        next_start = elements[i + 1]["start"] if i + 1 < len(elements) else len(s)
        m = _CONTENT_SRC_RE.search(s, el["end"], next_start)
        if m is not None:
            el["target"] = m.group(1).strip("\"'")

    def key_of(el: dict) -> str:
        # An element whose content src cannot be located is its own target
        # (keyed by position so two such elements never merge).
        return el["target"] if el["target"] is not None else f"\0{el['start']}"

    groups: dict[str, list[dict]] = {}
    for el in elements:
        groups.setdefault(key_of(el), []).append(el)

    # Contract check: every target group carries exactly one integer value,
    # groups never share a value, and the value set is exactly 1..len(groups).
    used: set[int] = set()
    contract_ok = True
    for members in groups.values():
        vals: set[int] = set()
        for el in members:
            try:
                vals.add(int(el["value"]))
            except ValueError:
                contract_ok = False
                break
        if not contract_ok or len(vals) != 1 or vals & used:
            contract_ok = False
            break
        used |= vals
    if contract_ok and used == set(range(1, len(groups) + 1)):
        return s, 0

    # Violating: re-sequence by first appearance of each target.
    assigned: dict[str, int] = {}
    out = []
    last = 0
    count = 0
    for el in elements:
        key = key_of(el)
        if key not in assigned:
            assigned[key] = len(assigned) + 1
        new = str(assigned[key])
        if el["value"] != new:
            count += 1
            attr = _PLAYORDER_ATTR_RE.search(el["tag"])
            new_tag = (
                el["tag"][: attr.start()]
                + f' playOrder="{new}"'
                + el["tag"][attr.end() :]
            )
            out.append(s[last : el["start"]])
            out.append(new_tag)
            last = el["end"]
    out.append(s[last:])
    return "".join(out), count


@_outside_protected
def unwrap_block_in_inline(s: str) -> tuple[str, int]:
    """Unwrap a <span> that illegally wraps a block element (`<span><div>...</div>
    </span>` -> the div alone).

    Opt-in (--unwrap-block-in-inline): it restructures nesting, not just
    well-formedness tokens, so it is never part of the core pass. Inner text is
    always preserved.
    """
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        count += 1
        return m.group(1)

    s, n = re.subn(
        # The quote-aware branches OVERLAP ([^>] also matches quote
        # characters), so a plain `*` lets the engine re-partition the start
        # tag exponentially across them on every failing candidate: on a
        # 843k-char doc full of <span>s whose closer never comes, 0.41.0
        # spun CPU indefinitely (the Physics-fixture regression,
        # roadmap 2026-09-15). The possessive `*+` commits to the
        # first-alternative consumption (ending at the first unquoted `>`,
        # which is the correct tag end) and never re-partitions: linear on
        # failure, same matches on success. `re` has had possessive
        # quantifiers since 3.11, the same floor as the plugin's minimum
        # Calibre.
        r"""<span(?:(?:"[^"]*"|'[^']*'|[^>])*+)>\s*"""
        r"""(<(div|p|blockquote)(?:(?:"[^"]*"|'[^']*'|[^>])*+)>.*?</\2>)\s*</span>""",
        repl,
        s,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return s, n


def strip_attrs_in_start_tags(s: str, attr_re: re.Pattern[str]) -> tuple[str, int]:
    """Remove attribute occurrences matching `attr_re`, but only inside real
    start tags, and never inside a CDATA section or comment.

    A bare attribute-name regex run over whole-document text also matches prose
    like ``Use epub:type="chapter" here``; anchoring on the quote-aware
    start-tag matcher and running outside the protected spans confines the
    edit to actual markup attributes.
    """
    count = 0

    def scrub_tag(m: re.Match) -> str:
        nonlocal count
        tag, n = attr_re.subn("", m.group(0))
        count += n
        return tag

    def run(text: str) -> tuple[str, int]:
        return _START_TAG_RE.sub(scrub_tag, text), count

    return outside_protected_map(s, run)


@_outside_protected
def strip_invalid_value(s: str) -> tuple[str, int]:
    """Strip misplaced `value="..."` attributes from non-form elements.

    Opt-in (--strip-invalid-value): deleting attributes goes beyond making markup
    parseable, so it is never part of the core pass.
    """
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        count += 1
        tag = m.group(1)
        before = m.group(2) or ""
        after = m.group(4) or ""
        return f"<{tag} {before}{after}>"

    s, n = re.subn(
        r"<(div|span|p|a|img|h[1-6]|ul|li|meta|table|tr|td|th)"
        r'(\s+[^>]*\b)?(?<![\w:.-])value\s*=\s*(["\'][^"\']*["\'])([^>]*)>',
        repl,
        s,
        flags=re.IGNORECASE,
    )
    return s, n


# The tags --unwrap-illegal-tags removes wherever they appear. A tag name that any
# stylesheet references as an element selector is skipped for that book (see
# css_protected_tags): unwrapping styled elements would silently destroy formatting,
# which no lossy convenience is worth.
ILLEGAL_TAGS = ("st", "sentence", "o", "w", "pagebreak")

_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)


def css_protected_tags(
    *sheets: str, tags: tuple[str, ...] = ILLEGAL_TAGS
) -> frozenset[str]:
    """Element-selector-referenced tag names in the given CSS text(s),
    from `tags` (default: the illegal-tag set).

    Selector-aware, so `.st { ... }` (a class) or `#w { ... }` (an id) does NOT
    protect anything while `st { ... }` or `p st, x > w { ... }` does. Comments are
    stripped first; matching is case-insensitive and returns lowercase names. This
    ports scripts/find_css_illegal_tags.py into the library so the transform can
    enforce its own precondition instead of trusting callers to scan.
    """
    found: set[str] = set()
    for css in sheets:
        stripped = _CSS_COMMENT_RE.sub("", css)
        # Candidate selector lists are whatever precedes a '{'. Scanning this way
        # (rather than splitting on '}') keeps nested at-rules working: in
        # `@media print { o > sentence {} }`, `o > sentence` precedes the INNER '{'
        # and is found, where a naive outer split would lose it. Declarations never
        # precede a '{'.
        for m in re.finditer(r"([^{}]*)\{", stripped):
            selectors = m.group(1)
            for tag in tags:
                # The trailing boundary includes . and #: `pagebreak.new:after`
                # styles pagebreak ELEMENTS, so it must protect the name, while a
                # LEADING . or # stays unprotected (`div.st` styles a class). The
                # leading boundary also covers namespaced (`svg|st`) and functional
                # (`:is(st, w)`) selector forms; the trailing one a closing paren.
                if re.search(
                    rf"(^|[\s,>+~|(]){tag}([\s,>+~:\[.#)|]|$)", selectors, re.IGNORECASE
                ):
                    found.add(tag)
    return frozenset(found)


def style_block_tags(doc: str, tags: tuple[str, ...] = ILLEGAL_TAGS) -> frozenset[str]:
    """css_protected_tags over every inline `<style>` block in a content document."""
    return css_protected_tags(*_STYLE_BLOCK_RE.findall(doc), tags=tags)


@_outside_protected
def unwrap_illegal_tags(
    s: str, protected_tags: frozenset[str] = frozenset()
) -> tuple[str, int]:
    """Remove illegal/deprecated tags (`<st>`, `<sentence>`, `<o>`, `<w>`,
    `<pagebreak>`) outright, keeping their inner text.

    Opt-in (`--unwrap-illegal-tags`) because deleting elements is more than
    well-formedness repair. Names in `protected_tags` (the union of every
    stylesheet's element selectors, see css_protected_tags/style_block_tags) are
    left untouched: if a book styles one of these tags, unwrapping it would change
    how the text renders, so preservation wins. CDATA sections and comments are
    never edited: a `<w>` mentioned in either is literal text, not markup.
    """
    count = 0
    for tag in ILLEGAL_TAGS:
        if tag in protected_tags:
            continue
        # quote-aware start-tag match (same shape as _VOID_RE): a `>` inside
        # an attribute value would otherwise end the match early and mangle
        # the tag into a malformed edit the gate then rejects. The branches
        # overlap ([^>] also matches quotes), so the possessive `*+` bounds
        # the re-partitioning that spun CPU on failing candidates in 0.41.0
        # (see unwrap_block_in_inline).
        s, n1 = re.subn(
            rf"""<{tag}\b(?:(?:"[^"]*"|'[^']*'|[^>])*+)>""",
            "",
            s,
            flags=re.IGNORECASE,
        )
        s, n2 = re.subn(rf"</{tag}\s*>", "", s, flags=re.IGNORECASE)
        count += n1 + n2
    return s, count


@_outside_protected
def fix_empty_body(s: str) -> tuple[str, int]:
    """Append `&nbsp;` to a strictly empty `<body></body>` ("body incomplete").

    Opt-in (--fix-empty-body): this ADDS visible content the author never wrote,
    the same reason --add-img-alt is opt-in; it is never part of the core pass.
    """
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        count += 1
        return m.group(1) + "&nbsp;" + m.group(2)

    s, n = re.subn(r"(<body[^>]*>\s*)(</body>)", repl, s, flags=re.IGNORECASE)
    return s, n


@_outside_protected
def fix_missing_title(s: str) -> tuple[str, int]:
    """Inject `<title>Unknown</title>` when a document has no usable title.

    Opt-in (--fix-missing-title): it fabricates content the author never wrote,
    so it is never part of the core pass. An existing non-empty <title> anywhere
    in the document means no-op.
    """
    count = 0
    if re.search(r"<title\b[^>]*>.*?</title>", s, re.IGNORECASE | re.DOTALL):
        return s, 0

    # Check for empty self-closing title
    def repl(m: re.Match) -> str:
        nonlocal count
        count += 1
        return "<title>Unknown</title>"

    s, n = re.subn(r"<title\b[^>]*>\s*</title>", repl, s, flags=re.IGNORECASE)
    if n > 0:
        return s, n

    # No title tag at all, inject into head
    def repl_head(m: re.Match) -> str:
        nonlocal count
        count += 1
        return m.group(1) + "\n<title>Unknown</title>"

    s, n = re.subn(r"(<head[^>]*>)", repl_head, s, flags=re.IGNORECASE)
    return s, n


# A reference target that is not a file inside this package: an absolute URI
# with a scheme (`http://`, `kindle:`, `file://`) or a protocol-relative
# `//host/path`. Its fragment names a position in that other document and is
# never this package's business.
_EXTERNAL_TARGET_RE = re.compile(r"^(?:[a-zA-Z][\w+.-]*:|//)")


@_outside_protected
def fix_id_colons(s: str) -> tuple[str, int]:
    """Translate illegal colons in `id="X:Y"` and matching internal `#X:Y`
    fragments to `_`.

    Opt-in (--fix-id-colons): it rewrites id-bearing attributes and internal
    fragment references, so it stays out of the core pass despite being
    rendering-neutral. Only the bare `id` attribute is matched (a lookbehind
    keeps `data-id` and `xml:id` values untouched: the former is arbitrary
    data, and renaming the latter would need its own reference graph), and a
    fragment is translated only in an internal reference: the fragment of an
    external URL (`http://example.com/page#sec:1`) names a position in that
    other document, so it must survive verbatim.
    """
    count = 0

    def repl_id(m: re.Match) -> str:
        nonlocal count
        if ":" not in m.group(2):
            return m.group(0)  # no colon, no rename, no phantom count
        count += 1
        return m.group(1) + m.group(2).replace(":", "_") + m.group(3)

    s, _ = re.subn(
        r'\b(?<![\w:.-])(id\s*=\s*["\'])([^"\']+)(["\'])',
        repl_id,
        s,
        flags=re.IGNORECASE,
    )

    def repl_href(m: re.Match) -> str:
        nonlocal count
        if ":" not in m.group(3) or _EXTERNAL_TARGET_RE.match(m.group(2)):
            return m.group(0)
        count += 1
        return m.group(1) + m.group(2) + "#" + m.group(3).replace(":", "_") + m.group(4)

    s, n2 = re.subn(
        r'\b(href\s*=\s*["\'])([^"#]*?)#([^"\']+)(["\'])',
        repl_href,
        s,
        flags=re.IGNORECASE,
    )
    return s, count


@_outside_protected
def fix_ncx_src_fragments(ncx: str) -> tuple[str, int]:
    """Translate colon-bearing fragments in NCX `content src` references to
    `_`, mirroring what fix_id_colons just did to the content documents.

    Opt-in (--fix-id-colons): a content id rename without this NCX half
    manufactures dangling ToC references end to end (every navPoint pointing
    at a colon-bearing id lands on nothing). External targets keep their
    fragments verbatim.
    """
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        if ":" not in m.group(3) or _EXTERNAL_TARGET_RE.match(m.group(2)):
            return m.group(0)
        count += 1
        return m.group(1) + m.group(2) + "#" + m.group(3).replace(":", "_") + m.group(4)

    ncx, _ = re.subn(
        r'\b(src\s*=\s*["\'])([^"#]*?)#([^"\']+)(["\'])',
        repl,
        ncx,
        flags=re.IGNORECASE,
    )
    return ncx, count


# A src/href attribute with a quoted value. A raw space can only survive inside
# quotes (an unquoted value ends at the first space). The optional namespaced
# prefix must end in a colon, so `xlink:href` is covered while `data-href` and
# friends are not. The value is the tempered-quote idiom (possessive, so a
# failing candidate cannot re-partition) rather than a [^"'] class: a
# double-quoted value may legitimately carry an apostrophe, and the class
# shape silently skipped every href that had one (the Theaetetus OPF,
# 2026-09-16: 270 raw-space hrefs survived the encode pass because their
# filenames carried apostrophes).
_SRC_HREF_ATTR_RE = re.compile(
    r"""((?:^|\s)(?:[\w.-]+:)?(?:src|href)\s*=\s*)(["'])((?:(?!\2)[\s\S])*+)(\2)""",
    re.IGNORECASE,
)


@_outside_protected
def encode_url_spaces(s: str) -> tuple[str, int]:
    """Percent-encode raw spaces in src/href attribute values
    (`href="a b.htm"` -> `href="a%20b.htm"`).

    Opt-in (--encode-url-spaces): it rewrites URL references across the
    package, so it never runs in the core pass. A URL with a literal space is
    not a valid URL (epubcheck RSC-020 'not a valid URL') and unresolvable on
    strict readers; the encoded form denotes the same file and renders
    identically. Scope is fixed to the space character: extend only with a
    named epubcheck finding, never speculatively.
    """
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        if " " not in m.group(3):
            return m.group(0)
        count += 1
        return m.group(1) + m.group(2) + m.group(3).replace(" ", "%20") + m.group(4)

    return _SRC_HREF_ATTR_RE.sub(repl, s), count


# XML comments end at the first `-->`; CDATA sections do not contain comments
# (a `<!--` inside one is literal text). The double-hyphen fix splits on CDATA
# only, so the comment spans it edits are the ones a parser would see.
_CDATA_ONLY_RE = re.compile(r"(<!\[CDATA\[.*?\]\]>)", re.DOTALL)
_COMMENT_SPAN_RE = re.compile(r"(<!--)(.*?)(-->)", re.DOTALL)

# The comment-body replacement for the RSC-016 fix: a `--` sequence is illegal
# inside an XML comment, and the en-dash is what a typographically-minded hand
# fix writes in its place (the 2026-09-16 Theaetetus hand-repair).
EN_DASH = "–"


def fix_comment_double_hyphen(s: str) -> tuple[str, int]:
    """Replace `--` inside XML comments with an en-dash (the RSC-016 fatal:
    "-- is not permitted within comments").

    Opt-in (--fix-comment-double-hyphen): it edits comment content, which
    every other transform protects, so it never runs in the core pass. The
    edit is confined to comment bodies: text nodes are never touched (a `--`
    in character data is legal XML), CDATA sections are never touched, and
    each comment's `-->` terminator is left intact (the span ends there, so
    the body replacement cannot reach it). Every replaced sequence is
    counted. An unclosed `<!--` runs the span to the first later `-->`, which is
    exactly what an XML parser sees (once the opener never closes, that whole
    region is comment content), so the fix rewrites wherever a parser reads a
    comment; CDATA is protected either way, and the fix claims only the
    double-hyphen class.
    """
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        body = m.group(2)
        if "--" not in body:
            return m.group(0)
        count += body.count("--")
        return m.group(1) + body.replace("--", EN_DASH) + m.group(3)

    if "<!" not in s:  # fast path: no comments, no CDATA
        return s, 0
    parts = _CDATA_ONLY_RE.split(s)
    for i in range(0, len(parts), 2):  # even indices are outside CDATA
        parts[i] = _COMMENT_SPAN_RE.sub(repl, parts[i])
    return "".join(parts), count


# A truncated CDATA-terminator comment inside an inline <style> block:
# `/*]]>` with no `*/` after it (optionally separated by whitespace, which a
# real closer may carry). The `/*` opens a CSS comment the parser never sees
# closed, so epubcheck answers CSS-008 'Premature end of file' once per
# affected document; completing the closer is byte-minimal and renders
# identically. Standalone (not _outside_protected): the token spans the
# protection machinery's own CDATA boundary, since `/*<![CDATA[*/ ... /*]]>`
# is exactly the shape whose closer got truncated.
_CDATA_TERMINATOR_RE = re.compile(r"/\*]]>(?!\s*\*/)")
_STYLE_BLOCK_SPAN_RE = re.compile(
    r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL
)


def fix_cdata_terminator(s: str) -> tuple[str, int]:
    """Complete a truncated CDATA-terminator comment in an inline <style>
    block: `/*]]>` -> `/*]]>*/` (the CSS-008 'Premature end of file' class).

    Opt-in (--fix-cdata-terminator). Deliberately narrow: only the
    malformed-terminator token class, inside <style> blocks only. Invalid CSS
    properties and selectors stay unfixed (renderers degrade gracefully), and
    stylesheet FILE entries are not touched; extend only with a named
    epubcheck finding.
    """
    count = 0

    def fix_block(m: re.Match) -> str:
        nonlocal count
        block, n = _CDATA_TERMINATOR_RE.subn("/*]]>*/", m.group(0))
        count += n
        return block

    return _STYLE_BLOCK_SPAN_RE.sub(fix_block, s), count


# An id attribute inside a real start tag (the tempered-quote value idiom: a
# double-quoted value may carry an apostrophe). Anchored to start tags so
# literal `id="x"` text in element content is never renamed.
_SVG_TAG_RE = re.compile(r"""<[a-zA-Z][\w:.-]*(?:"[^"]*"|'[^']*'|[^>])*+>""")
_SVG_ID_ATTR_RE = re.compile(r"""(\bid\s*=\s*)(["'])((?:(?!\2).)*+)(\2)""")


def _rename_duplicate_ids(s: str) -> tuple[str, int]:
    """The shared duplicate-id rename: first occurrence keeps its name, every
    later one gains `_2`, `_3`, ... (prefixed with `_` until free), in
    document order. Internal references are never rewritten because they
    already resolve to the first definition under every reader's first-match
    lookup, so first-keeps-id preserves exactly what was rendered."""
    values = [
        m.group(3)
        for tag in _SVG_TAG_RE.finditer(s)
        for m in [_SVG_ID_ATTR_RE.search(tag.group(0))]
        if m is not None
    ]
    counts: dict[str, int] = {}
    taken = set(values)
    count = 0

    def fix_tag(tag: re.Match) -> str:
        nonlocal count
        m = _SVG_ID_ATTR_RE.search(tag.group(0))
        if m is None:
            return tag.group(0)
        v = m.group(3)
        if not v:
            return tag.group(0)
        n = counts.get(v, 0) + 1
        counts[v] = n
        if n == 1:
            return tag.group(0)
        new = f"{v}_{n}"
        while new in taken:
            new = "_" + new
        taken.add(new)
        count += 1
        return tag.group(0)[: m.start(3)] + new + tag.group(0)[m.end(3) :]

    return _SVG_TAG_RE.sub(fix_tag, s), count


def fix_svg_dup_ids(s: str) -> tuple[str, int]:
    """Rename duplicate `id` values within one SVG document, keeping the
    FIRST occurrence (the RSC-005 'Duplicate "glNNNN"' class from old
    calibre SVG page renders).

    Opt-in (--fix-svg-dup-ids). The first occurrence keeps its name, every
    later one gains `_2`, `_3`, ... (prefixed with `_` until free), in
    document order; internal `xlink:href="#x"` / `url(#x)` references are
    never rewritten because they already resolve to the first definition
    under every reader's first-match lookup, so first-keeps-id preserves
    exactly what was rendered. Runs on standalone `.svg` entries only:
    content-document duplicates are the separate --fix-duplicate-ids flag.
    """
    return _rename_duplicate_ids(s)


def fix_duplicate_ids(s: str) -> tuple[str, int]:
    """Rename duplicate `id` values within one content document, keeping the
    FIRST occurrence (the RSC-005 'Duplicate id' class on .xhtml/.html
    carriers; the Calcutta Chromosome `heading_id_2` shape).

    Opt-in (--fix-duplicate-ids). The first occurrence keeps its name, so
    every reference that points at the name — internal `href="#x"` anchors,
    CSS `#x` selectors, cross-document and NCX fragment references — still
    resolves exactly as before, to the first definition: under a duplicate
    id the target was ambiguous anyway, and first-match is what readers
    actually did (the rule proven by the SVG fix). The later duplicates
    gain `_2`, `_3`, ... and lose whatever invalid double-styling the
    second definition carried; the book keeps the styling it could render
    deterministically.
    """
    return _rename_duplicate_ids(s)


# The mis-nested inline pair (the Mobipocket drop-cap shape `<i><b>S</i></b>`):
# two style tags opened, then closed in the WRONG order (`</i>` fires while
# `<b>` is still open, so the closers read `</\1></\2>`; a WELL-FORMED pair
# closes `</\2></\1>` and must not match, or the fix would phantom-count
# healthy markup). The Exile variant (Life, Txt_014) carries a short
# punctuation run between the reversed closers (`<i><b>xile</i>,</b>`); a
# LETTER run there is excluded on purpose (`[^<\w]`): `<i><b>S</i>ome</b>`
# spans b over `Some` and i over `S`, and which span the letters keep has no
# deterministic answer, so it stays with manual rescue. Any match is XML
# fatal, and swapping the closers is the unique well-formed form carrying
# the same two spans over the same shared text. The shared-text run is
# bounded (no tags inside, so `[^<]` alone; 64 covers every real drop-cap
# and short emphasis) and the tag names are bare (attribute-bearing starts
# keep their own shape).
_MISNESTED_INLINE_RE = re.compile(
    r"""<(i|b|em|strong)><(i|b|em|strong)>([^<]{1,64})</\1>([^<\w]{0,8})</\2>""",
    re.IGNORECASE,
)


@_outside_protected
def fix_misnested_inline(s: str) -> tuple[str, int]:
    """Rewrite `<i><b>X</i></b>` to `<i><b>X</b></i>` (and the em/strong
    variants, either order); a punctuation run between the reversed
    closers (`<i><b>xile</i>,</b>`) survives outside both spans.

    Opt-in (--fix-misnested-inline). Only the both-open-then-reversed-close
    shape is rewritten; general mis-nesting has no deterministic repair and
    stays with manual rescue. A same-tag pair (`<i><i>x</i></i>`) is already
    well-formed and is returned untouched and uncounted.
    """
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        if m.group(1).lower() == m.group(2).lower():
            return m.group(0)
        count += 1
        return (
            f"<{m.group(1)}><{m.group(2)}>{m.group(3)}"
            f"</{m.group(2)}></{m.group(1)}>{m.group(4)}"
        )

    return _MISNESTED_INLINE_RE.sub(repl, s), count


# One scan token: comments and CDATA sections are matched only when CLOSED
# (an unclosed opener is handled by the walk, which stops there, because the
# parser treats the whole rest as comment/CDATA content); start tags are the
# quote-aware possessive matcher; end tags are plain.
_STRAY_TOKEN_RE = re.compile(
    r"""<!--.*?-->|<!\[CDATA\[.*?\]\]>"""
    r"""|<([a-zA-Z][\w:.-]*)((?:"[^"]*"|'[^']*'|[^>])*+)>"""
    r"""|</([\w:.-]+)\s*>""",
    re.DOTALL,
)
_VOID_NAMES = frozenset(VOID.split("|"))
_RAW_TEXT_TAGS = frozenset({"script", "style"})


def fix_stray_close(s: str) -> tuple[str, int]:
    """Remove an end tag whose element has no open start tag anywhere above
    it (the stray extra `</div>` on a shell page, which cascades to 'body
    must be terminated by the matching end-tag').

    Opt-in (--fix-stray-close). The rule is deliberately narrower than a
    nesting repair: a close is removed only when NOTHING by that name is
    open, so a merely mis-nested pair (`<div><span></div></span>`) is left
    exactly as written. Open-bookkeeping skips self-closed and void starts;
    a close whose name is open but not on top pops nothing (conservative:
    the stack may be stale, and under-popping only suppresses removals,
    never causes them); raw-text elements (script/style) are skipped to
    their end tag so JS/CSS text is never read as markup; the walk stops at
    an unclosed comment or CDATA opener, where the parser stops reading
    markup too.
    """
    stack: list[str] = []
    removals: list[tuple[int, int]] = []
    pos = 0
    while True:
        m = _STRAY_TOKEN_RE.search(s, pos)
        if m is None:
            break
        # An unclosed comment/CDATA opener between tokens owns the rest of
        # the document: no markup after it, so nothing left to judge.
        opener = s.rfind("<!--", pos, m.start())
        cdata_op = s.rfind("<![CDATA[", pos, m.start())
        if max(opener, cdata_op) != -1:
            break
        token = m.group(0)
        if token.startswith("<!--") or token.startswith("<![CDATA["):
            pos = m.end()
            continue
        if m.group(1) is not None:  # start tag
            name = m.group(1).lower()
            if name in _RAW_TEXT_TAGS:
                end = re.search(rf"</{m.group(1)}\s*>", s[m.end() :], re.IGNORECASE)
                pos = m.end() + (end.end() if end else 0)
                if not end:
                    break
                continue
            attrs = m.group(2)
            if attrs.rstrip().endswith("/") or name in _VOID_NAMES:
                pos = m.end()
                continue
            stack.append(name)
        else:  # end tag
            name = m.group(3).lower()
            if name in stack:
                if stack[-1] == name:
                    stack.pop()
            else:
                removals.append(m.span())
        pos = m.end()
    if not removals:
        return s, 0
    out: list[str] = []
    last = 0
    for start, end in removals:
        out.append(s[last:start])
        last = end
    out.append(s[last:])
    return "".join(out), len(removals)


# An attribute value whose closing quote is missing before the tag's own `>`:
# `<p class="footnote>` — the parser swallows everything up to the NEXT quote
# as the value, meets a `<` inside it, and dies with RSC-016 ("the value of
# attribute ... must not contain the '<' character"). The rewrite re-closes
# the quote at the `>` that was meant to end the tag. Three guards keep it
# off legal `>`-bearing values: the value is one bare word (no whitespace, no
# angle brackets, no quotes), the `>` must be followed by markup or text but
# NOT by a quote before the next `<` (`(?![^<]*")` — a real multi-line value
# closes somewhere quote-first), and only double-quoted values are judged.
_UNTERMINATED_ATTR_RE = re.compile(r"""(\s[\w:.-]+\s*=\s*")([^\s"<>]+)>(?![^<]*")""")


@_outside_protected
def fix_unterminated_attr(s: str) -> tuple[str, int]:
    """Close an attribute value left open to the tag's own `>`
    (`<p class="footnote>` -> `<p class="footnote">`, the RSC-016
    'must not contain the < character' fatal).

    Opt-in (--fix-unterminated-attr). Run it before --fix-stray-close: the
    stack walk needs real tag boundaries, and an unterminated value makes
    the start-tag matcher swallow markup into one pseudo-tag.
    """
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        count += 1
        return f'{m.group(1)}{m.group(2)}">'

    return _UNTERMINATED_ATTR_RE.sub(repl, s), count


# --fix-unbound-prefix (issue #9). One tokenizer walks the whole document so
# prefix scope can be tracked: comments, CDATA sections, PIs, and doctypes are
# consumed as opaque tokens (markup-looking text inside them must never touch
# the element stack), end tags and start tags carry their names. The
# quote-aware loops are possessive per the house convention: the three
# branches are mutually exclusive, so a failing candidate fails linearly.
_OPF_TOKEN_RE = re.compile(
    r"""<!--(?:(?!-->).)*+-->"""
    r"""|<!\[CDATA\[(?:(?!\]\]>).)*+\]\]>"""
    r"""|<\?(?:(?!\?>).)*+\?>"""
    r"""|<![^>]*+>"""
    r"""|</([A-Za-z_][\w.:-]*)((?:"[^"]*+"|'[^']*+'|[^>"'])*+)>"""
    r"""|<([A-Za-z_][\w.:-]*)((?:"[^"]*+"|'[^']*+'|[^>"'])*+)>"""
)
# Any quoted attribute (declarations and uses alike); group 3 is the value
# exactly as spelled in the document, re-emitted verbatim when binding.
_OPF_ATTR_RE = re.compile(r"""([A-Za-z_][\w.:-]*)\s*=\s*(["'])((?:(?!\2).)*+)\2""")
_OPF_NS = "http://www.idpf.org/2007/opf"
_DC_NS = "http://purl.org/dc/elements/1.1/"
# Pre-bound in every XML document; never candidates for binding.
_PREBOUND_PREFIXES = frozenset({"xml", "xmlns"})


def _unescape_attr(v: str) -> str:
    """The five predefined XML entities, the only escapes a namespace URI
    could legitimately carry."""
    for ent, ch in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        v = v.replace(ent, ch)
    return v.replace("&amp;", "&")


def fix_unbound_prefixes(opf: str) -> tuple[str, int]:
    """Bind an undeclared namespace prefix inline on each element that uses it.

    Calibre's embed_metadata writer has been observed to leave one dc: element
    carrying an attribute prefix (``ns0:scheme``) whose xmlns declaration never
    made it into the document: the declaration sits on a sibling, and XML scope
    ends at the sibling's end tag. The unparseable package costs every
    downstream check (two RSC-016 fatals plus OPF-030 on the
    unique-identifier). The repair is the shape the file's own healthy
    siblings already use: declare the prefix on the element that uses it.

    The URI is evidence-based, never guessed: (1) the same prefix declared
    elsewhere in the document, every declaration agreeing on one URI; (2) the
    prefix declared nowhere, but the carrying element is Dublin Core (its
    element prefix resolves to the DC namespace, or the literal ``dc:``
    spelling when that is itself unbound) and the package element declares the
    OPF namespace, the canonical EPUB 2 ``opf:scheme``/``opf:role`` shape
    calibre's writer emits; (3) anything else refuses the whole document (an
    invented URI would be a guess, and a half-bound package is still fatal, so
    a partial fix would buy nothing). Declarations scoped to an ancestor (or
    to the element itself) satisfy the check without an edit, so clean
    documents are byte-identical.

    Opt-in (--fix-unbound-prefix). Runs on the OPF only: content documents
    have the invalid-attribute drop (--strip-bad-attrs) for the same defect
    class.
    """
    # Document-wide declaration evidence, prefix -> the URIs it is bound to.
    # Scanned over start-tag tokens only: a raw scan would also read
    # commented-out or prose-mentioned xmlns spellings, and a junk URI that
    # exists only inside a comment must never become binding evidence (nor
    # poison the agreement check against a real declaration).
    decl_uris: dict[str, set[str]] = {}
    for m in _OPF_TOKEN_RE.finditer(opf):
        if m.group(3) is None:
            continue
        for am in _OPF_ATTR_RE.finditer(m.group(4)):
            name = am.group(1)
            if name.startswith("xmlns:"):
                decl_uris.setdefault(name[len("xmlns:") :], set()).add(am.group(3))

    scope: list[tuple[str, dict[str, str]]] = []
    edits: list[tuple[int, str]] = []
    count = 0
    pkg_ns = False
    pkg_seen = False

    for m in _OPF_TOKEN_RE.finditer(opf):
        if m.group(3) is None:
            if m.group(1) is None:
                continue  # comment / CDATA / PI / doctype: opaque
            # End tag: pop to the matching start; a stray end tag with no
            # open element pops nothing. Mismatched nesting pops through
            # intermediate levels, which can only ever cause a redundant
            # inline re-declaration of a prefix that was still in scope,
            # never a semantic change: the evidence URI is document-wide,
            # so it equals whatever the dropped scope held.
            ename = m.group(1)
            for i in range(len(scope) - 1, -1, -1):
                if scope[i][0] == ename:
                    del scope[i:]
                    break
            continue

        name, body = m.group(3), m.group(4)
        # The possessive body loop always swallows a self-closing slash (the
        # [^>"'] catch-all is disjoint from `>` but not from `/`), so
        # self-closing is read off the body, never a group.
        slash = body.rstrip().endswith("/")
        decls: dict[str, str] = {}
        attr_names: list[str] = []
        default_uri = None
        for am in _OPF_ATTR_RE.finditer(body):
            aname = am.group(1)
            if aname == "xmlns":
                default_uri = am.group(3)
            elif aname.startswith("xmlns:"):
                decls[aname[len("xmlns:") :]] = am.group(3)
            else:
                attr_names.append(aname)
        if not pkg_seen and name.split(":")[-1] == "package":
            pkg_seen = True
            pkg_ns = any(
                u == _OPF_NS or _unescape_attr(u) == _OPF_NS
                for u in (default_uri, *decls.values())
                if u
            )
        # Push before the attribute check: the element's own declarations
        # apply to its own attributes.
        scope.append((name, decls))
        binds: list[str] = []
        for aname in attr_names:
            prefix, sep, _ = aname.partition(":")
            if not sep or prefix in _PREBOUND_PREFIXES:
                continue
            if any(prefix in d for _, d in scope):
                continue
            uris = decl_uris.get(prefix)
            if uris is not None:
                if len(uris) != 1:
                    return opf, 0  # one prefix, two URIs: intent unreadable
                uri = next(iter(uris))
            else:
                elem_prefix, _, _ = name.partition(":")
                elem_ns = None
                for _, d in reversed(scope):
                    if elem_prefix in d:
                        elem_ns = d[elem_prefix]
                        break
                if pkg_ns and (
                    elem_ns == _DC_NS or (elem_ns is None and elem_prefix == "dc")
                ):
                    uri = _OPF_NS
                else:
                    return opf, 0  # no evidence: refuse the whole document
            binds.append(f' xmlns:{prefix}="{uri}"')
        if binds:
            # Insert directly after the element name, the position the
            # file's own healthy inline declarations occupy.
            edits.append((m.start(4), "".join(binds)))
            count += len(binds)
        if slash:
            scope.pop()

    for at, text in reversed(edits):
        opf = opf[:at] + text + opf[at:]
    return opf, count


# The always-on core for full (X)HTML content documents, in order: prolog and
# root-tag fixes first, then ampersand/entity normalization, then void
# self-closing. Exactly the five semantics-preserving well-formedness fixes the
# spec names, nothing else. Everything that adds markup, deletes attributes, or
# removes/restructures elements lives behind an explicit CLI flag (threaded through
# repair_epub), so the default pass can never change more than parsing requires.
# See spec.md "Transforms" and the per-flag sections.
HTML_TRANSFORMS = (
    strip_prolog_junk,
    drop_duplicate_xmlns,
    escape_bare_amp,
    fix_named_entities,
    self_close_void,
)

# A lighter set for XML sidecars (NCX): no HTML-specific element rewriting.
XML_TRANSFORMS = (
    strip_prolog_junk,
    escape_bare_amp,
    fix_named_entities,
    fix_ncx_playorder,
)


def apply_transforms(
    s: str, transforms: Iterable[Transform]
) -> tuple[str, dict[str, int]]:
    """Run a pipeline of transforms, returning the result and per-transform counts."""
    counts: dict[str, int] = {}
    for fn in transforms:
        s, n = fn(s)
        if n:
            counts[fn.__name__] = counts.get(fn.__name__, 0) + n
    return s, counts


# Matches leaked closing tags without angle brackets (e.g. /div&gt;) OR common formatting tags with escaped angle brackets.
_BROKEN_TAGS_RE = re.compile(
    r"(?<!<)(?<!&lt;)/[a-zA-Z]+&gt;|&lt;/(?:p|div|span|h[1-6]|i|em|b|strong)&gt;",
    re.IGNORECASE,
)


@_outside_protected
def strip_broken_tags(text: str) -> tuple[str, int]:
    """Strip leaked HTML closing tags that render as raw text in readers.
    This lossily removes the leaked text fragment and should be gated behind
    `validate.no_worse`. CDATA sections and comments are never edited: a
    leaked-looking fragment inside either is literal text, not markup.
    """
    return _BROKEN_TAGS_RE.subn("", text)
