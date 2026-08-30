"""Tests to ensure version numbers are synchronized across the repository.

v0.19.1 exists because `__init__.py` drifted to 0.18.0 while pyproject said
0.19.0; this pin is the Phase 7 guard so that class of drift fails a test
instead of shipping.
"""

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
