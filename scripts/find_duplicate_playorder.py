#!/usr/bin/env python3
import sys
import zipfile
import re
from pathlib import Path

# regex for finding playOrder attributes
PLAYORDER_RE = re.compile(r'\bplayOrder\s*=\s*(["\'])(.*?)\1', re.IGNORECASE)

def scan_book(path: Path) -> list[str]:
    issues = []
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.endswith(".ncx"):
                    continue
                try:
                    content = zf.read(name).decode("utf-8", errors="ignore")
                    seen = set()
                    dupes = set()
                    for m in PLAYORDER_RE.finditer(content):
                        order = m.group(2)
                        if order in seen:
                            dupes.add(order)
                        seen.add(order)
                    if dupes:
                        issues.append(f"{name} duplicates: {', '.join(dupes)}")
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
