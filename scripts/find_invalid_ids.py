#!/usr/bin/env python3
import re
import sys
import zipfile
from pathlib import Path

# regex for finding id attributes that contain a colon.
# It matches id="something:else" or id='something:else'
ID_COLON_RE = re.compile(r'\bid\s*=\s*(["\'])([^"\']*:[^"\']*)\1', re.IGNORECASE)


def scan_book(path: Path) -> list[str]:
    issues = []
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.endswith((".html", ".xhtml", ".xml", ".opf", ".ncx")):
                    continue
                try:
                    content = zf.read(name).decode("utf-8", errors="ignore")
                    for m in ID_COLON_RE.finditer(content):
                        # filter out xml namespaces if they somehow got matched as ids
                        # e.g., id="foo:bar" is a match
                        val = m.group(2)
                        issues.append(f"{name}: id='{val}'")
                except Exception:
                    pass
    except Exception:
        pass
    return issues


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <directory_or_epub>")
        sys.exit(1)

    root = Path(sys.argv[1])
    if root.is_file():
        epubs = [root]
    else:
        epubs = root.rglob("*.epub")

    for path in epubs:
        issues = scan_book(path)
        if issues:
            print(f"\n{path}")
            for issue in set(issues):
                print(f"  {issue}")


if __name__ == "__main__":
    main()
