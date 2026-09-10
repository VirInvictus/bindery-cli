"""Last-resort structural repair: re-parse a malformed document and re-emit it as
well-formed XHTML.

This is the only part of Bindery that is not a minimal, byte-level edit: it parses the
whole document with html5lib's lenient HTML5 parser (the same recovery a browser does)
and serializes the result back as XHTML, which closes unclosed elements (`<p>`, `<div>`,
`<span>`, `<blockquote>`, ...) that the regex transforms cannot. Because it reformats,
it runs only on documents that are *not* already well-formed, leaving good files exactly
as they are, and only when the user opts in with --reserialize. html5lib is imported
lazily so the rest of Bindery has no third-party dependency.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

XHTML_NS = "http://www.w3.org/1999/xhtml"

# An HTML document carries an `<html>` root. A broken document without one is
# some other kind of XML (a page template, a sidecar), and html5lib's HTML
# algorithm must never touch it.
_HTML_ROOT_RE = re.compile(r"<html[\s>]", re.IGNORECASE)


def reserialize_if_broken(s: str) -> tuple[str, int]:
    """If `s` is not well-formed XML and carries an `<html>` root, re-parse it
    leniently and re-emit as XHTML.

    Returns (text, 1) if it was rebuilt, or (s, 0) if it already parsed or is
    not HTML. A broken non-HTML XML sidecar is left untouched: html5lib's
    HTML algorithm would structurally rewrite it (html/body-wrapped,
    ns0:-prefixed) while leaving it well-formed, which is corruption, not
    repair (reported 2026-09-08). Raises RuntimeError if html5lib is needed
    but not installed.
    """
    try:
        ET.fromstring(s)
        return s, 0
    except ET.ParseError:
        pass

    if not _HTML_ROOT_RE.search(s):
        return s, 0

    try:
        import html5lib
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "--reserialize requires html5lib (install it: uv pip install html5lib)"
        ) from e

    root = html5lib.parse(s, treebuilder="etree", namespaceHTMLElements=False)
    root.set("xmlns", XHTML_NS)
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n{body}', 1
