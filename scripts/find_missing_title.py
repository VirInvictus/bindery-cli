#!/usr/bin/env python3
import re
import sys
import zipfile
from pathlib import Path

# regex for extracting the <head>...</head> block
HEAD_RE = re.compile(r"<head[^>]*>(.*?)</head>", re.IGNORECASE | re.DOTALL)
TITLE_RE = re.compile(r"<title[^>]*>.*?</title>", re.IGNORECASE | re.DOTALL)


def scan_book(path: Path) -> list[str]:
    issues = []
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.endswith((".html", ".xhtml", ".xml")):
                    continue
                try:
                    content = zf.read(name).decode("utf-8", errors="ignore")
                    m = HEAD_RE.search(content)
                    if m:
                        if not TITLE_RE.search(m.group(1)):
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
