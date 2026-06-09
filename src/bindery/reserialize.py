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

import xml.etree.ElementTree as ET

XHTML_NS = "http://www.w3.org/1999/xhtml"


def reserialize_if_broken(s: str) -> tuple[str, int]:
    """If `s` is not well-formed XML, re-parse it leniently and re-emit as XHTML.

    Returns (text, 1) if it was rebuilt, or (s, 0) if it already parsed. Raises
    RuntimeError if html5lib is needed but not installed.
    """
    try:
        ET.fromstring(s)
        return s, 0
    except ET.ParseError:
        pass

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
