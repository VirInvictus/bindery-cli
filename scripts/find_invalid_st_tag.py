#!/usr/bin/env python3
import sys
import zipfile
import re
from pathlib import Path

# regex for finding <st> or </st> tags
ST_TAG_RE = re.compile(r'</?st\b[^>]*>', re.IGNORECASE)

def scan_book(path: Path) -> list[str]:
    issues = []
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.endswith((".html", ".xhtml", ".xml", ".htm")):
                    continue
                try:
                    content = zf.read(name).decode("utf-8", errors="ignore")
                    if ST_TAG_RE.search(content):
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
