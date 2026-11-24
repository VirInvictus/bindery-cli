"""docs/repairs.md is the checked-in render of the live repair-flag table.

The page is generated from `_REPAIR_FLAG_TABLE` (the rows `--help-repairs`
prints) by scripts/render_repairs_doc.py. Before this module the flag
reference existed as two hand-written paraphrases, the CLI's and the page's,
and they drifted: six flags' long help went stale against the code while the
page stayed right. One text is canonical now, and this pin fails the suite
the moment the committed page differs from the table."""

import unittest
from pathlib import Path

from bindery import cli

DOC = Path(__file__).resolve().parents[1] / "docs" / "repairs.md"


class RepairsDocSyncTests(unittest.TestCase):
    def test_repairs_doc_matches_the_live_table(self) -> None:
        self.assertEqual(
            DOC.read_text(encoding="utf-8"),
            cli.render_repairs_doc(),
            "docs/repairs.md is stale: run scripts/render_repairs_doc.py",
        )

    def test_every_flag_has_its_heading(self) -> None:
        # the heading shape is the anchor other docs and tools link to
        text = DOC.read_text(encoding="utf-8")
        for rows in cli._REPAIR_FLAG_TABLE.values():
            for dest, _short, _long in rows:
                self.assertIn(f"### `--{dest.replace('_', '-')}`", text, dest)


if __name__ == "__main__":
    unittest.main()
