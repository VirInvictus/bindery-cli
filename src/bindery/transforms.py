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
from html.entities import html5, name2codepoint

VOID = "area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr"
XML_PREDEFINED = {"amp", "lt", "gt", "quot", "apos"}

# Match an open void-element tag. The `\b` after the name is essential: without it
# `<col` matches inside `<colgroup>` and we self-close a non-void element, orphaning
# its end-tag (the bug that introduced fatals on real books). Attributes are matched
# quote-aware so a `>` inside an attribute value does not end the tag early. Group 3
# captures an existing trailing slash so already-self-closed tags are left untouched.
_VOID_RE = re.compile(
    rf"""<({VOID})\b((?:"[^"]*"|'[^']*'|[^>])*?)\s*(/?)>""",
    re.IGNORECASE | re.DOTALL,
)
_NAMED_ENTITY_RE = re.compile(r"&([a-zA-Z][a-zA-Z0-9]*);")
_BARE_AMP_RE = re.compile(r"&(?![a-zA-Z][a-zA-Z0-9]*;|#[0-9]+;|#[xX][0-9a-fA-F]+;)")
_HTML_TAG_RE = re.compile(r"<html\b[^>]*>", re.IGNORECASE)
_XMLNS_ATTR_RE = re.compile(r'\s+xmlns="[^"]*"')


def self_close_void(s: str) -> tuple[str, int]:
    """Self-close void elements that were left open (`<br>` -> `<br/>`).

    Already-self-closed tags are returned unchanged and not counted, so the fix is
    idempotent and reports only real changes.
    """
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        if m.group(3) == "/":  # already self-closed: leave exactly as-is
            return m.group(0)
        count += 1
        return f"<{m.group(1)}{m.group(2)}/>"

    return _VOID_RE.sub(repl, s), count


def _resolve_entity(name: str) -> int | None:
    """Codepoint for an HTML entity name, or None if it is not a single-char entity."""
    if name in name2codepoint:
        return name2codepoint[name]
    ch = html5.get(name + ";") or html5.get(name)
    return ord(ch) if ch and len(ch) == 1 else None


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
        cp = _resolve_entity(name)
        if cp is None:
            return m.group(0)
        count += 1
        return f"&#{cp};"

    return _NAMED_ENTITY_RE.sub(repl, s), count


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


def apply_transforms(s: str, transforms) -> tuple[str, dict[str, int]]:
    """Run a pipeline of transforms, returning the result and per-transform counts."""
    counts: dict[str, int] = {}
    for fn in transforms:
        s, n = fn(s)
        if n:
            counts[fn.__name__] = counts.get(fn.__name__, 0) + n
    return s, counts
