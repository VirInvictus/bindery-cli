#!/usr/bin/env python3
import re
import sys
import zipfile
from pathlib import Path

# regex for finding value="..." attribute on elements where it might be invalid (e.g. <span>, <div>, <p>)
VALUE_ATTR_RE = re.compile(
    r'<(div|span|p|a|img|h[1-6]|ul|table|tr|td|th)\s+[^>]*\bvalue\s*=\s*["\'][^"\']*["\']',
    re.IGNORECASE,
)


def scan_book(path: Path) -> list[str]:
    issues = []
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.endswith((".html", ".xhtml", ".xml")):
                    continue
                try:
                    content = zf.read(name).decode("utf-8", errors="ignore")
                    if VALUE_ATTR_RE.search(content):
                        issues.append(name)
                except Exception:
                    pass
    except Exception:
        pass
    return issues


def main():
    if len(sys.argv) < 2:
        sys.exit(1)
    root = Path(sys.argv[1])
    epubs = [root] if root.is_file() else root.rglob("*.epub")
    for path in epubs:
        issues = scan_book(path)
        if issues:
            print(f"\n{path}")
            for issue in set(issues):
                print(f"  {issue}")


if __name__ == "__main__":
    main()
