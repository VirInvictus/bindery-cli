#!/usr/bin/env python3
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

# A comprehensive list of valid HTML5 / EPUB 3 tags
VALID_TAGS = {
    "a",
    "abbr",
    "acronym",
    "address",
    "area",
    "article",
    "aside",
    "audio",
    "b",
    "base",
    "bdi",
    "bdo",
    "big",
    "blockquote",
    "body",
    "br",
    "button",
    "canvas",
    "caption",
    "cite",
    "code",
    "col",
    "colgroup",
    "data",
    "datalist",
    "dd",
    "del",
    "details",
    "dfn",
    "dialog",
    "dir",
    "div",
    "dl",
    "dt",
    "em",
    "embed",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "head",
    "header",
    "hgroup",
    "hr",
    "html",
    "i",
    "iframe",
    "img",
    "input",
    "ins",
    "kbd",
    "keygen",
    "label",
    "legend",
    "li",
    "link",
    "main",
    "map",
    "mark",
    "menu",
    "menuitem",
    "meta",
    "meter",
    "nav",
    "noscript",
    "object",
    "ol",
    "optgroup",
    "option",
    "output",
    "p",
    "param",
    "picture",
    "pre",
    "progress",
    "q",
    "rp",
    "rt",
    "ruby",
    "s",
    "samp",
    "script",
    "section",
    "select",
    "small",
    "source",
    "span",
    "strike",
    "strong",
    "style",
    "sub",
    "summary",
    "sup",
    "svg",
    "table",
    "tbody",
    "td",
    "template",
    "textarea",
    "tfoot",
    "th",
    "thead",
    "time",
    "title",
    "tr",
    "track",
    "tt",
    "u",
    "ul",
    "var",
    "video",
    "wbr",
    "math",
    "epub:switch",
    "epub:case",
    "epub:default",
}

# Ignore common namespaces like svg:, math:, or opf:
# (we only care about raw HTML tag names)
TAG_RE = re.compile(r"<\s*([a-zA-Z0-9]+)(?:[^>]*?)>", re.IGNORECASE)


def scan_library(root: Path):
    epubs = root.rglob("*.epub")
    illegal_counts = Counter()

    for path in epubs:
        try:
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if not name.endswith((".html", ".xhtml", ".htm")):
                        continue
                    try:
                        content = zf.read(name).decode("utf-8", errors="ignore")
                        for m in TAG_RE.finditer(content):
                            tag = m.group(1).lower()
                            if tag not in VALID_TAGS:
                                illegal_counts[tag] += 1
                    except Exception:
                        pass
        except Exception:
            pass

    print("Most common ILLEGAL tags found in the entire library:")
    for tag, count in illegal_counts.most_common(20):
        print(f"<{tag}> : {count} times")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    scan_library(Path(sys.argv[1]))
