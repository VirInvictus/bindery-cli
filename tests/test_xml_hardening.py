"""The v0.43.0 stdlib XML hardening: untrusted metadata files are parsed
through _safe_xml_parse, which refuses DOCTYPE/ENTITY declarations (the
stdlib equivalent of defusedxml's entity protection), and single archive
entries above _MAX_ENTRY_BYTES are refused before decompression. A
refused metadata file returns the shell book -- the archive is the
book's whole story -- instead of hanging or crashing."""

import tempfile
import unittest
from pathlib import Path

from bindery.audit import _MAX_ENTRY_BYTES, _safe_xml_parse, load_book

BOMB_CONTAINER = (
    '<?xml version="1.0"?>\n<!DOCTYPE container [<!ENTITY a "'
    + "A" * 100000
    + '"]>\n<container/>'
)


def _write_epub(path: Path, container: bytes, opf: bytes | None = None):
    import zipfile

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        if opf is not None:
            z.writestr("content.opf", opf)


class SafeXmlParseTests(unittest.TestCase):
    def test_doctype_is_refused(self):
        self.assertIsNone(_safe_xml_parse(BOMB_CONTAINER.encode()))

    def test_entity_inline_is_refused(self):
        self.assertIsNone(_safe_xml_parse(b"<x>&entity;</x>"))

    def test_clean_xml_parses(self):
        root = _safe_xml_parse(b"<container><rootfile/></container>")
        self.assertIsNotNone(root)

    def test_none_blob_passes_through_as_none(self):
        self.assertIsNone(_safe_xml_parse(None))


class EntityBombContainerTests(unittest.TestCase):
    def test_entity_bomb_container_returns_shell_book(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bomb.epub"
            _write_epub(p, BOMB_CONTAINER.encode())
            book = load_book(p)  # must not hang, must not raise
            self.assertEqual(book.spine, [])
            self.assertEqual(book.corrupt, [])

    def test_max_entry_bytes_is_sane(self):
        # a real book's largest component is nowhere near this; the cap
        # exists to refuse bomb entries before decompression.
        self.assertGreaterEqual(_MAX_ENTRY_BYTES, 8 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
