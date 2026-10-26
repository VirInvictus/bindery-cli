"""The spine-resolution guard (issue #10): the manifest prune's existence
check must read the package in the spelling its own text carries (the pre-
rename layout when --encode-url-spaces renames entries), the prune's spine
protection must describe the text as the prune reads it (fix_manifest_ids
renames ids ahead of it in the OPF pipeline), and process_book must veto any
candidate whose spine (itemref -> item -> archive) dangles more than the
source's — the collapse that used to measure as an error reduction."""

import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from bindery.cli import process_book
from bindery.epub import (
    RepairFlags,
    RepairReport,
    fix_manifest_ids,
    prune_missing_manifest_items,
    repair_epub,
    spine_dangles,
)
from bindery.validate import CheckResult

CONTAINER = (
    '<?xml version="1.0"?><container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles></container>'
)

DOC = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
    "<title>p</title></head><body><p>word word word word</p></body></html>"
)


def _opf(items: str, refs: str) -> str:
    return (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" '
        'unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>t</dc:title><dc:language>en</dc:language>"
        '<dc:identifier id="id">u</dc:identifier></metadata>'
        f"<manifest>{items}</manifest>"
        f"<spine>{refs}</spine></package>"
    )


class _TempBook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bindery_spine_")
        self.addCleanup(self.tmp.cleanup)

    def _path(self, name):
        return Path(self.tmp.name) / name

    def _build(self, name, entries):
        path = self._path(name)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
            for entry, data in entries.items():
                z.writestr(entry, data)
        return path

    def _fan_book(self, splits=3):
        """The issue #10 shape: fan-conversion filenames with raw spaces and
        manifest ids carrying commas and brackets (invalid XML names)."""
        names = [
            f"Author, Anne - [Series 1] - Title_split_{i:03d}.htm"
            for i in range(splits)
        ]
        items = "".join(
            f'<item href="{n}" id="Author,_Anne_-_[Series_1]_-_Title_split_{i:03d}0" '
            'media-type="application/xhtml+xml"/>'
            for i, n in enumerate(names)
        )
        items += '<item href="titlepage.xhtml" id="titlepage" media-type="application/xhtml+xml"/>'
        refs = '<itemref idref="titlepage"/>' + "".join(
            f'<itemref idref="Author,_Anne_-_[Series_1]_-_Title_split_{i:03d}0"/>'
            for i in range(splits)
        )
        entries = {
            "META-INF/container.xml": CONTAINER,
            "OEBPS/content.opf": _opf(items, refs),
            "OEBPS/titlepage.xhtml": DOC,
        }
        for n in names:
            entries[f"OEBPS/{n}"] = DOC
        return self._build("fan.epub", entries)


class SpineDanglesTests(_TempBook):
    def test_healthy_spine_dangles_nothing(self):
        self.assertEqual(spine_dangles(self._fan_book()), 0)

    def test_pruned_manifest_item_orphans_its_itemref(self):
        # The issue's post-repair shape: the item is gone, the itemref stays.
        src = self._fan_book()
        broken = self._path("broken.epub")
        with (
            zipfile.ZipFile(src) as zin,
            zipfile.ZipFile(broken, "w", zipfile.ZIP_DEFLATED) as zout,
        ):
            for item in zin.infolist():
                data = zin.read(item)
                if item.filename == "OEBPS/content.opf":
                    text = data.decode()
                    start = text.index("<item ", text.index("split_000"))
                    end = text.index("/>", start) + 2
                    text = text[:start] + text[end:]
                    data = text.encode()
                zout.writestr(item, data)
        self.assertEqual(spine_dangles(broken), 1)

    def test_item_whose_file_is_missing_dangles(self):
        src = self._fan_book()
        broken = self._path("missing.epub")
        with (
            zipfile.ZipFile(src) as zin,
            zipfile.ZipFile(broken, "w", zipfile.ZIP_DEFLATED) as zout,
        ):
            for item in zin.infolist():
                if item.filename.endswith("split_001.htm"):
                    continue
                zout.writestr(item, zin.read(item))
        self.assertEqual(spine_dangles(broken), 1)

    def test_unreadable_archive_probes_none(self):
        # The veto needs evidence: an unreadable package answers None and
        # never fires the guard.
        self.assertIsNone(spine_dangles(self._path("nope.epub")))


class PruneManifestGuardTests(unittest.TestCase):
    def test_spine_item_survives_renamed_ids(self):
        # The regression: fix_manifest_ids renames item ids and idrefs before
        # the prune runs; a spine snapshot taken from the pre-rename spelling
        # missed every renamed idref and spine documents were pruned. The
        # guard is derived from the text the prune reads, so the renamed
        # spine item is protected even with every file "missing".
        text = _opf(
            '<item href="gone1.htm" id="a,1" media-type="application/xhtml+xml"/>'
            '<item href="gone2.htm" id="b,2" media-type="application/xhtml+xml"/>',
            '<itemref idref="a,1"/>',
        )
        text, n = fix_manifest_ids(text)
        self.assertEqual(n, 2)
        out, pruned, pruned_ids = prune_missing_manifest_items(text, "", frozenset())
        self.assertEqual(pruned, 1)
        self.assertEqual(len(pruned_ids), 1)
        # The spine document's item tag survives whatever its id is spelled
        # like now; only the orphaned non-spine declaration goes.
        self.assertIn("gone1.htm", out)
        self.assertNotIn("gone2.htm", out)

    def test_non_spine_item_with_absent_file_is_pruned(self):
        text = _opf(
            '<item href="font.woff" id="f1" media-type="application/font-woff"/>',
            "",
        )
        out, pruned, pruned_ids = prune_missing_manifest_items(
            text, "", frozenset({"content.opf"})
        )
        self.assertEqual(pruned, 1)
        self.assertEqual(pruned_ids, {"f1"})


class RawSpaceManifestRepairTests(_TempBook):
    def test_repair_renames_instead_of_pruning_the_spine(self):
        src = self._fan_book()
        dst = self._path("out.epub")
        report = repair_epub(
            src, dst, RepairFlags(fix_ids=True, prune_missing=True, url_spaces=True)
        )
        self.assertIsNone(report.fixes.get("manifest_items_pruned"))
        self.assertEqual(report.fixes.get("entries_renamed"), 3)
        with zipfile.ZipFile(dst) as z:
            names = set(z.namelist())
            self.assertNotIn(
                "OEBPS/Author, Anne - [Series 1] - Title_split_000.htm", names
            )
            self.assertIn(
                "OEBPS/Author,_Anne_-_[Series_1]_-_Title_split_000.htm", names
            )
            opf = z.read("OEBPS/content.opf").decode()
        # The manifest hrefs follow the entries to the renamed spelling, and
        # every itemref still walks item -> file in the output archive. The
        # brackets are percent-encoded in the reference (issue #19: epubcheck
        # answers RSC-020 for raw '[' in a URL path) while the ENTRY keeps
        # them raw; epubcheck decodes before lookup, so both spellings meet.
        self.assertIn('href="Author,_Anne_-_%5BSeries_1%5D_-_Title_split_000.htm"', opf)
        self.assertNotIn("manifest_items_pruned", opf)
        self.assertEqual(spine_dangles(dst), 0)


class SpineVetoTests(_TempBook):
    """process_book wiring: real files for the probe, mocked oracle counts."""

    def _run(self, src, candidate, fixes, before, after, write_candidate=True):
        work = self._path("work")
        work.mkdir(exist_ok=True)

        def fake_repair(epub, dst, flags=None):
            if write_candidate:
                shutil.copyfile(candidate, dst)
            return RepairReport(fixes=fixes)

        with (
            mock.patch("bindery.cli.repair_epub", side_effect=fake_repair),
            mock.patch("bindery.cli.run_epubcheck", side_effect=[before, after]),
        ):
            return process_book(src, work, validate=True)

    def test_spine_collapse_rejects_improving_counts(self):
        # The issue's verdict shape: 0f/17e/8w -> 0f/10e/0w measures as a
        # gain while the spine has been pruned to the title page. The veto
        # must override the counts and the entries_renamed promotion.
        src = self._fan_book()
        collapse = self._path("collapse.epub")
        with (
            zipfile.ZipFile(src) as zin,
            zipfile.ZipFile(collapse, "w", zipfile.ZIP_DEFLATED) as zout,
        ):
            for item in zin.infolist():
                data = zin.read(item)
                if item.filename == "OEBPS/content.opf":
                    text = data.decode()
                    for _ in range(3):
                        start = text.index("<item ", text.index("split_"))
                        end = text.index("/>", start) + 2
                        text = text[:start] + text[end:]
                    data = text.encode()
                zout.writestr(item, data)
        o = self._run(
            src,
            collapse,
            {"entries_renamed": 1},
            CheckResult(0, 17, 8),
            CheckResult(0, 10, 0),
        )
        self.assertEqual(o.status, "reject")
        self.assertIn("(spine collapse)", o.summary)

    def test_intact_spine_keeps_the_normal_verdict(self):
        src = self._fan_book()
        fine = self._path("fine.epub")
        shutil.copyfile(src, fine)
        o = self._run(
            src,
            fine,
            {"entries_renamed": 1},
            CheckResult(0, 17, 8),
            CheckResult(0, 10, 0),
        )
        self.assertEqual(o.status, "accept")
        self.assertNotIn("spine collapse", o.summary)

    def test_probe_failure_vetoes_nothing(self):
        # A candidate the probe cannot read carries no evidence: the counts
        # alone decide (fail-open, like the probe's contract).
        src = self._fan_book()
        o = self._run(
            src,
            None,
            {"entries_renamed": 1},
            CheckResult(0, 17, 8),
            CheckResult(0, 10, 0),
            write_candidate=False,
        )
        self.assertEqual(o.status, "accept")


try:
    _FIXTURE = Path(__file__).resolve().parent.parent / (
        "testing_facility/books_to_fix/my-enemy-my-ally-raw-space-href-manifest-prune.epub"
    )
    HAVE_FIXTURE = _FIXTURE.exists()
except OSError:
    HAVE_FIXTURE = False


@unittest.skipUnless(HAVE_FIXTURE, "local issue fixture not present")
class FixtureRegressionTests(_TempBook):
    def test_fixture_spine_survives_the_rename_pipeline(self):
        # The real pre-repair bytes from the 2026-10-21 re-source wave.
        src = self._path("fixture.epub")
        shutil.copyfile(_FIXTURE, src)
        dst = self._path("fixed.epub")
        report = repair_epub(
            src, dst, RepairFlags(fix_ids=True, prune_missing=True, url_spaces=True)
        )
        self.assertIsNone(report.fixes.get("manifest_items_pruned"))
        self.assertEqual(report.fixes.get("entries_renamed"), 8)
        self.assertEqual(spine_dangles(dst), 0)


if __name__ == "__main__":
    unittest.main()
