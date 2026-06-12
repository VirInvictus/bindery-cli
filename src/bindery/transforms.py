"""Deterministic, well-formedness-only repair transforms for (X)HTML/XML text.

Every transform is a pure function `str -> (str, int)` returning the rewritten text
and how many fixes it made. None of them change document semantics: they only make
already-intended markup well-formed (self-close void elements, turn undeclared named
entities into numeric character references, escape stray ampersands, strip junk before
the XML prolog, drop a duplicated root xmlns). Anything deeper than that is out of
scope and is left for the epubcheck gate to reject. See spec.md.
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
    rf"""<({VOID})(?=[\s/>])((?:"[^"]*"|'[^']*'|[^>])*?)\s*(/?)>""",
    re.IGNORECASE | re.DOTALL,
)
_VOID_END_RE = re.compile(rf"""</(?:{VOID})\s*>""", re.IGNORECASE)
_NAMED_ENTITY_RE = re.compile(r"&([a-zA-Z][a-zA-Z0-9]*);")
_BARE_AMP_RE = re.compile(r"&(?![a-zA-Z][a-zA-Z0-9]*;|#[0-9]+;|#[xX][0-9a-fA-F]+;)")
# A start tag (quote-aware) and an attribute within it, for invalid-attribute stripping.
_START_TAG_RE = re.compile(r"""<[a-zA-Z][\w:.-]*(?:"[^"]*"|'[^']*'|[^>])*>""")
_ATTR_RE = re.compile(r"""(\s+)([^\s=/>]+)(\s*=\s*)("[^"]*"|'[^']*'|[^\s>]+)""")
_XMLNS_DECL_RE = re.compile(r"xmlns:([A-Za-z_][\w.-]*)\s*=")
_HTML_TAG_RE = re.compile(r"<html\b[^>]*>", re.IGNORECASE)
_XMLNS_ATTR_RE = re.compile(r'\s+xmlns="[^"]*"')
_EPUB_PREFIX_ATTR_RE = re.compile(r"""(?:\bepub:)?prefix\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_EPUB_PREFIX_VAL_RE = re.compile(r"(?:^|\s)([A-Za-z_][\w.-]*)\s*:\s*\S+")

# CDATA sections and comments hold literal text: a bare `&`, an entity name, or a
# `<br>` inside them is already legal XML, and rewriting it would change the content
# (e.g. `&` in CDATA-wrapped CSS/JS renders as `&`; escaped, it renders as `&amp;`).
# Splitting on this regex yields alternating outside/protected segments, so the
# markup transforms run only on the even (outside) indices.
_PROTECTED_RE = re.compile(r"(<!\[CDATA\[.*?\]\]>|<!--.*?-->)", re.DOTALL)


def _outside_protected(fn: Transform) -> Transform:
    """Wrap a transform so it never touches CDATA sections or comments."""

    @wraps(fn)
    def wrapped(s: str) -> tuple[str, int]:
        if "<!" not in s:  # fast path: nothing to protect
            return fn(s)
        parts = _PROTECTED_RE.split(s)
        total = 0
        for i in range(0, len(parts), 2):
            parts[i], n = fn(parts[i])
            total += n
        return "".join(parts), total

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


@_outside_protected
def escape_bare_amp(s: str) -> tuple[str, int]:
    """Escape a `&` that does not begin a valid entity/character reference."""
    return _BARE_AMP_RE.subn("&amp;", s)


def strip_prolog_junk(s: str) -> tuple[str, int]:
    """Remove a BOM or stray bytes before the first `<` ("content not allowed in prolog")."""
    stripped = s.lstrip("﻿ \t\r\n")
    i = stripped.find("<")
    if i > 0:
        stripped = stripped[i:]
    return (stripped, 1) if stripped != s else (s, 0)


def drop_duplicate_xmlns(s: str) -> tuple[str, int]:
    """Keep only the first `xmlns="..."` on the root <html> element."""
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


# Transforms applied to full (X)HTML content documents, in order. Prolog and root-tag
# fixes first, then ampersand/entity normalization, then void self-closing.
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
