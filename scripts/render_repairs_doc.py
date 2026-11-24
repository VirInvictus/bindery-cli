#!/usr/bin/env python3
"""Regenerate docs/repairs.md from the live repair-flag table.

docs/repairs.md is the checked-in render of `_REPAIR_FLAG_TABLE` in
src/bindery/cli.py: the same rows `bindery repair --help-repairs` prints, so
the page and the terminal reference cannot drift apart.
tests/test_repairs_doc.py pins the committed file to the live table, so run
this in any change that touches a flag's short or long text:

    .venv/bin/python scripts/render_repairs_doc.py

It needs the project environment (cli.py imports tqdm at module level) and
resolves the repo root from its own location, so it runs from anywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bindery.cli import render_repairs_doc  # noqa: E402


def main() -> int:
    target = ROOT / "docs" / "repairs.md"
    target.write_text(render_repairs_doc(), encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
