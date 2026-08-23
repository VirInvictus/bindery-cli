#!/usr/bin/env python3
import re
import sys
import zipfile
from pathlib import Path

illegal_tags = ["st", "font", "sentence", "o", "w", "pagebreak"]


def scan_book(path: Path):
    issues = []
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.endswith(".css"):
                    continue
                try:
                    content = zf.read(name).decode("utf-8", errors="ignore")

                    # very naive CSS selector parsing: look for the tag name at the start of a line or after a comma,
                    # before a { or a comma or a colon.
                    # Actually, just looking for \btag\b outside of { } is hard with regex.
                    # Let's just do a naive check: if the word exists outside of a block.
                    # We can remove all comments /* ... */
                    no_comments = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
                    # We can extract all selectors by splitting by { and }
                    parts = no_comments.split("}")
                    for part in parts:
                        if "{" in part:
                            selectors = part.split("{")[0]
                            for tag in illegal_tags:
                                # regex to find tag as a selector (not a class .tag, not an id #tag)
                                # must be preceded by start of string, whitespace, comma, or >
                                # must be followed by end of string, whitespace, comma, or : or [ or >
                                if re.search(
                                    rf"(^|[\s,>+~]){tag}([\s,>+~:\[]|$)",
                                    selectors,
                                    re.IGNORECASE,
                                ):
                                    issues.append(f"{name} styles <{tag}>")
                except Exception:
                    pass
    except Exception:
        pass
    return issues


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    epubs = [root] if root.is_file() else root.rglob("*.epub")
    found_any = False
    for path in epubs:
        issues = scan_book(path)
        if issues:
            found_any = True
            print(f"\n{path}")
            for issue in set(issues):
                print(f"  {issue}")
    if not found_any:
        print("No illegal tags are styled in any CSS files!")


if __name__ == "__main__":
    main()
