"""Tests to ensure version numbers are synchronized across the repository.

v0.19.1 exists because `__init__.py` drifted to 0.18.0 while pyproject said
0.19.0; this pin is the Phase 7 guard so that class of drift fails a test
instead of shipping.
"""

import ast
import unittest
from pathlib import Path

from bindery import VERSION as CODE_VERSION


class TestVersionSync(unittest.TestCase):
    def test_versions_match(self):
        """Ensure pyproject.toml's version mirrors the code's VERSION.

        `__init__.py` is the declared single source (there is no VERSION
        file by design); pyproject.toml must repeat it exactly.
        """
        root_dir = Path(__file__).parent.parent

        pyproject_path = root_dir / "pyproject.toml"
        pyproject_version = None
        if pyproject_path.exists():
            with open(pyproject_path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("version = "):
                        pyproject_version = line.split("=")[1].strip().strip('"')
                        break

        self.assertIsNotNone(
            pyproject_version, "Could not find version in pyproject.toml"
        )
        self.assertEqual(
            pyproject_version,
            CODE_VERSION,
            f"pyproject.toml ({pyproject_version}) does not match code VERSION ({CODE_VERSION})",
        )


class TestSyntaxFloor(unittest.TestCase):
    """The Python floor is a package contract, enforced by parse, not trust.

    v0.39.0 shipped the PEP 758 unparenthesized except form; the Calibre
    plugin vendors those modules, so the plugin failed to load with a bare
    SyntaxError on every released Calibre (7.x/8.x embed Python 3.11, 9.x
    embeds 3.14) and CI could not see it because the suite only ever ran
    under 3.14. This guard parses every module in the repo's Python roots
    under a pinned grammar level, so interpreter-only syntax fails a test
    instead of shipping. The pinned level is a deliberate contract point:
    update it together with pyproject's requires-python and the CI
    interpreter legs.
    """

    GRAMMAR_FLOOR = (3, 12)
    ROOTS = ("src", "plugin", "tests", "scripts")

    def test_tree_parses_under_the_declared_grammar(self):
        repo = Path(__file__).parent.parent
        checked = 0
        for root in self.ROOTS:
            for path in sorted((repo / root).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                try:
                    ast.parse(
                        path.read_text(encoding="utf-8"),
                        filename=str(path),
                        feature_version=self.GRAMMAR_FLOOR,
                    )
                except SyntaxError as e:
                    self.fail(f"{path.relative_to(repo)}: {e}")
                checked += 1
        # the tree has dozens of modules; a broken path glob must not
        # let the guard pass vacuously
        self.assertGreaterEqual(checked, 20)
