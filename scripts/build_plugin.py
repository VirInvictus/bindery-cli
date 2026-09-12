#!/usr/bin/env python3
"""Build the Bindery Repair Calibre plugin zip from the repo tree.

The plugin vendors bindery-cli's gate-safe repair core: transforms.py,
epub.py, pagination.py, watermark.py, reserialize.py (byte-identical copies;
the zip root is a package, so their relative imports resolve unchanged) plus
plugin/__init__.py with the version tuple substituted from
src/bindery/__init__.py. A `plugin-import-name-bindery_repair.txt` marker
names the import package calibre_plugins.bindery_repair.

stdlib only; a release-time tool (publish.yml runs it against the tag), not
part of the repair contract.

Example:
  python scripts/build_plugin.py --out dist/BinderyRepair-v0.36.0.zip
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN_ENTRY = REPO / "plugin" / "__init__.py"
VENDOR_MODULES = (
    "transforms.py",
    "epub.py",
    "pagination.py",
    "watermark.py",
    "reserialize.py",
)
IMPORT_NAME_MARKER = "plugin-import-name-bindery_repair.txt"

# Keep in sync with __PLUGIN_VERSION__ in plugin/__init__.py.
_VERSION_LINE = re.compile(r"^__PLUGIN_VERSION__ = .*$", re.MULTILINE)


def repo_version() -> tuple[int, int, int]:
    """The single source of truth: src/bindery/__init__.py's VERSION."""
    text = (REPO / "src" / "bindery" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION = "(\d+)\.(\d+)\.(\d+)"$', text, re.MULTILINE)
    if not match:
        sys.exit('error: no VERSION = "X.Y.Z" line in src/bindery/__init__.py')
    return tuple(int(part) for part in match.groups())


def plugin_entry_bytes(version: tuple[int, int, int]) -> bytes:
    text = PLUGIN_ENTRY.read_text(encoding="utf-8")
    if "__PLUGIN_VERSION__ = " not in text:
        sys.exit("error: plugin/__init__.py carries no __PLUGIN_VERSION__ line")
    substituted = _VERSION_LINE.sub(f"__PLUGIN_VERSION__ = {version!r}", text, count=1)
    return substituted.encode("utf-8")


def build(out: Path) -> Path:
    version = repo_version()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(IMPORT_NAME_MARKER, "bindery_repair\n")
        z.writestr("__init__.py", plugin_entry_bytes(version))
        for name in VENDOR_MODULES:
            src = REPO / "src" / "bindery" / name
            z.writestr(name, src.read_bytes())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output zip path (default: dist/BinderyRepair-v<VERSION>.zip)",
    )
    args = ap.parse_args()
    version = repo_version()
    out = (
        args.out or REPO / "dist" / f"BinderyRepair-v{'.'.join(map(str, version))}.zip"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    build(out)
    print(f"wrote {out} (Bindery Repair v{'.'.join(map(str, version))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
