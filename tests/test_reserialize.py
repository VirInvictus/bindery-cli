"""Tests for the html5lib-backed structural reserializer. Skipped if html5lib is not
installed, since it is an optional dependency used only by --reserialize."""

import re
import unittest
import xml.etree.ElementTree as ET

from bindery.reserialize import reserialize_if_broken

try:
    import html5lib  # noqa: F401

    HAVE_HTML5LIB = True
except ImportError:
    HAVE_HTML5LIB = False

WELL_FORMED = (
    '<?xml version="1.0"?>'
    '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>fine</p></body></html>'
)
# Unclosed <p> and <div>: not well-formed XML, but recoverable by an HTML parser.
BROKEN = (
    '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
    "<p>one<p>two<div>three</body></html>"
)


class TestReserialize(unittest.TestCase):
    def test_well_formed_untouched(self):
        out, n = reserialize_if_broken(WELL_FORMED)
        self.assertEqual(n, 0)
        self.assertEqual(out, WELL_FORMED)

    @unittest.skipUnless(HAVE_HTML5LIB, "html5lib not installed")
    def test_broken_is_rebuilt_well_formed(self):
        out, n = reserialize_if_broken(BROKEN)
        self.assertEqual(n, 1)
        ET.fromstring(out)  # must now parse as XML
        # content survives
        text = re.sub(r"<[^>]+>", " ", out)
        for word in ("one", "two", "three"):
            self.assertIn(word, text)

    @unittest.skipUnless(HAVE_HTML5LIB, "html5lib not installed")
    def test_idempotent(self):
        once, _ = reserialize_if_broken(BROKEN)
        twice, n = reserialize_if_broken(once)
        self.assertEqual(n, 0)
        self.assertEqual(twice, once)


if __name__ == "__main__":
    unittest.main()
