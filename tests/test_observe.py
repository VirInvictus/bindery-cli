"""The observe census: census-line parsing, tabulation, trend deltas, the
artifact build with its keep-root-clean output guard, and (toolchain-gated)
the embedded census harness. Read-only by contract: nothing here touches a
scanned tree."""

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from bindery.observe import (
    SCHEMA,
    CensusRecord,
    _resolve_output_path,
    build_trend,
    parse_census_line,
    run_census,
    run_observe,
    tabulate,
)


def _rec(path, f=0, e=0, w=0, codes=(), unreadable=False):
    return CensusRecord(
        path=path,
        fatals=f,
        errors=e,
        warnings=w,
        codes=tuple(codes),
        unreadable=unreadable,
    )


class TestParseCensusLine(unittest.TestCase):
    def test_flagged_book_with_codes(self):
        r = parse_census_line("1,3,0 ||| RSC-005,RSC-005,NCX-001 ||| /lib/a.epub")
        self.assertEqual((r.fatals, r.errors, r.warnings), (1, 3, 0))
        self.assertEqual(r.codes, ("RSC-005", "RSC-005", "NCX-001"))
        self.assertFalse(r.unreadable)

    def test_clean_book_has_empty_codes(self):
        r = parse_census_line("0,0,0 |||  ||| /lib/clean.epub")
        self.assertEqual(r.codes, ())
        self.assertFalse(r.unreadable)

    def test_sentinel_is_unreadable(self):
        r = parse_census_line("-1,-1,-1 |||  ||| /lib/broken.epub")
        self.assertTrue(r.unreadable)
        self.assertEqual((r.fatals, r.errors, r.warnings), (0, 0, 0))

    def test_path_carrying_the_separator_survives(self):
        r = parse_census_line("0,0,0 |||  ||| /od d/X ||| Y.epub")
        self.assertEqual(r.path, "/od d/X ||| Y.epub")

    def test_garbage_is_none(self):
        self.assertIsNone(parse_census_line("not a census line"))


class TestTabulate(unittest.TestCase):
    def test_totals_severity_and_codes(self):
        records = [
            _rec("/root/a.epub"),
            _rec("/root/b.epub", e=1, codes=("NCX-001",)),
            _rec("/root/c.epub", e=3, codes=("RSC-005", "RSC-005")),
            _rec("/root/d.epub", f=1, e=200, codes=("RSC-016",)),
            _rec("/root/e.epub", w=2),
            _rec("/root/f.epub", unreadable=True),
        ]
        tab = tabulate(records, Path("/root"))
        lib = tab["library"]
        self.assertEqual(lib["epubs_total"], 6)
        self.assertEqual(lib["clean"], 1)
        self.assertEqual(lib["with_findings"], 4)
        self.assertEqual(lib["unreadable"], 1)
        sev = tab["severity"]
        self.assertEqual(sev["fatal_books"], 1)
        self.assertEqual(sev["warnings_only"], 1)
        self.assertEqual(sev["unreadable"], 1)
        self.assertEqual(sev["error_mass"], 204)
        # occurrences count repeats; books counts each title once
        self.assertEqual(tab["codes"]["occurrences"]["RSC-005"], 2)
        self.assertEqual(tab["codes"]["books"]["RSC-005"], 1)
        # worst is ordered by error mass, paths relative to the root
        self.assertEqual(tab["worst"][0]["path"], "d.epub")
        self.assertEqual(tab["worst"][0]["codes"], ["RSC-016"])

    def test_empty_census(self):
        tab = tabulate([], Path("/root"))
        self.assertEqual(tab["library"]["epubs_total"], 0)
        self.assertEqual(tab["codes"]["occurrences"], {})
        self.assertEqual(tab["worst"], [])


class TestBuildTrend(unittest.TestCase):
    def _artifact(self, clean, findings, code_books, mass):
        return {
            "schema": SCHEMA,
            "generated": "2026-10-24T00:00:00Z",
            "root": "/root",
            "library": {
                "epubs_total": clean + findings,
                "clean": clean,
                "with_findings": findings,
                "unreadable": 0,
            },
            "severity": {"error_mass": mass},
            "codes": {"books": code_books},
        }

    def test_deltas_and_code_changes(self):
        prev = self._artifact(10, 5, {"RSC-005": 4, "NCX-001": 2}, 100)
        cur = self._artifact(12, 3, {"RSC-005": 2, "PKG-010": 1}, 40)
        trend = build_trend(prev, cur)
        self.assertEqual(trend["clean_delta"], 2)
        self.assertEqual(trend["with_findings_delta"], -2)
        self.assertEqual(trend["error_mass_delta"], -60)
        self.assertEqual(
            trend["code_changes"]["RSC-005"], {"books_before": 4, "books_after": 2}
        )
        self.assertEqual(trend["new_codes"], ["PKG-010"])
        self.assertEqual(trend["resolved_codes"], ["NCX-001"])

    def test_unchanged_codes_are_absent(self):
        prev = self._artifact(1, 1, {"RSC-005": 1}, 1)
        cur = self._artifact(1, 1, {"RSC-005": 1}, 1)
        trend = build_trend(prev, cur)
        self.assertEqual(trend["code_changes"], {})
        self.assertEqual(trend["new_codes"], [])
        self.assertEqual(trend["resolved_codes"], [])


class TestOutputGuard(unittest.TestCase):
    def test_explicit_output_outside_root_is_taken(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "lib"
            root.mkdir()
            out = _resolve_output_path(root, str(Path(td) / "base.observe"))
            self.assertEqual(out.name, "base.observe")

    def test_output_inside_root_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "lib"
            root.mkdir()
            with self.assertRaises(ValueError):
                _resolve_output_path(root, str(root / "base.observe"))

    def test_default_output_lands_in_cwd_not_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "lib"
            cwd = Path(td) / "elsewhere"
            root.mkdir()
            cwd.mkdir()
            old = Path.cwd()
            try:
                import os

                os.chdir(cwd)
                out = _resolve_output_path(root, None)
            finally:
                os.chdir(old)
            self.assertTrue(str(out).startswith(str(cwd)))
            self.assertEqual(out.suffix, ".observe")


def _preview_payload_stub(root, workers):
    return {
        "mode": "dry-run",
        "candidates": 2,
        "summary": {"accepted": 1, "applied": 0},
        "books": [
            {
                "path": str(root / "a.epub"),
                "status": "accept",
                "before": {"fatals": 0, "errors": 2, "warnings": 0},
                "after": {"fatals": 0, "errors": 0, "warnings": 0},
                "fixes": {"fix_ncx_playorder": 4},
            },
            {
                "path": str(root / "b.epub"),
                "status": "nochange",
                "before": None,
                "after": None,
                "fixes": {},
            },
        ],
    }, 0


class TestRunObserve(unittest.TestCase):
    """The orchestration with the census and preview stubbed: the artifact
    carries what the contract promises and the tree is never written to."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "lib"
        self.root.mkdir()
        (self.root / "a.epub").write_bytes(b"x")
        (self.root / "b.epub").write_bytes(b"x")
        self.out = Path(self.tmp.name) / "base.observe"
        self.records = [
            _rec(str(self.root / "a.epub"), e=2, codes=("RSC-005",)),
            _rec(str(self.root / "b.epub")),
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, **kw):
        opts = dict(
            path=str(self.root),
            output=str(self.out),
            against=None,
            workers=1,
            limit=None,
            no_preview=False,
        )
        opts.update(kw)
        args = mock.Mock(**opts)
        with (
            mock.patch("bindery.observe.run_census", return_value=list(self.records)),
            mock.patch(
                "bindery.observe._preview_payload", side_effect=_preview_payload_stub
            ),
            mock.patch("bindery.observe._epubcheck_version", return_value="5.3.0-test"),
        ):
            rc = run_observe(args)
        return rc

    def test_artifact_written_and_read_only(self):
        before = sorted((p, p.stat().st_mtime) for p in self.root.iterdir())
        rc = self._run()
        after = sorted((p, p.stat().st_mtime) for p in self.root.iterdir())
        self.assertEqual(rc, 0)
        self.assertEqual(before, after)  # the tree is untouched
        art = json.loads(self.out.read_text())
        self.assertEqual(art["schema"], SCHEMA)
        self.assertEqual(art["library"]["epubs_total"], 2)
        self.assertEqual(art["preview"]["summary"]["accepted"], 1)
        self.assertEqual(art["preview"]["books"][0]["path"], "a.epub")
        self.assertEqual(art["codes"]["occurrences"]["RSC-005"], 1)
        self.assertNotIn("trend", art)
        # the per-book census rows: every book lands here, clean or not
        rows = {b["path"]: b for b in art["books"]}
        self.assertEqual(set(rows), {"a.epub", "b.epub"})
        self.assertEqual(rows["a.epub"]["errors"], 2)
        self.assertEqual(rows["a.epub"]["codes"], ["RSC-005"])
        self.assertEqual(rows["b.epub"]["codes"], [])
        self.assertFalse(rows["b.epub"]["unreadable"])

    def test_no_preview_omits_the_block(self):
        rc = self._run(no_preview=True)
        self.assertEqual(rc, 0)
        art = json.loads(self.out.read_text())
        self.assertNotIn("preview", art)

    def test_trend_block_composed_from_against(self):
        self._run()
        json.loads(
            self.out.read_text()
        )  # the artifact exists; the trend reads it in run_observe
        second = Path(self.tmp.name) / "next.observe"
        # The tree keeps both files but the second census measures the flagged
        # book clean (the repair that stage 2 would write): clean 1 -> 2.
        repaired = [
            _rec(str(self.root / "a.epub")),
            _rec(str(self.root / "b.epub")),
        ]
        args = mock.Mock(
            path=str(self.root),
            output=str(second),
            against=str(self.out),
            workers=1,
            limit=None,
            no_preview=True,
        )
        with (
            mock.patch("bindery.observe.run_census", return_value=repaired),
            mock.patch("bindery.observe._epubcheck_version", return_value="5.3.0-test"),
        ):
            rc = run_observe(args)
        self.assertEqual(rc, 0)
        art = json.loads(second.read_text())
        self.assertIn("trend", art)
        self.assertEqual(art["trend"]["clean_delta"], 1)

    def test_no_epubs_is_usage_error(self):
        (self.root / "a.epub").unlink()
        (self.root / "b.epub").unlink()
        rc = self._run()
        self.assertEqual(rc, 1)

    def test_output_inside_root_is_refused(self):
        rc = self._run(output=str(self.root / "x.observe"))
        self.assertEqual(rc, 1)
        self.assertFalse((self.root / "x.observe").exists())


@unittest.skipUnless(
    shutil.which("java") and shutil.which("epubcheck"),
    "java or epubcheck not installed",
)
class TestCensusHarness(unittest.TestCase):
    def test_real_book_answers_one_line(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            opf = (
                '<?xml version="1.0"?>'
                '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
                'unique-identifier="id">'
                '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                "<dc:title>t</dc:title><dc:language>en</dc:language>"
                '<dc:identifier id="id">u</dc:identifier>'
                '<meta property="dcterms:modified">2016-01-01T00:00:00Z</meta>'
                "</metadata>"
                "<manifest>"
                '<item id="nav" href="nav.xhtml" '
                'media-type="application/xhtml+xml" properties="nav"/>'
                '<item id="c1" href="text.xhtml" '
                'media-type="application/xhtml+xml"/>'
                "</manifest>"
                '<spine><itemref idref="c1"/></spine></package>'
            )
            doc = (
                '<?xml version="1.0"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml">'
                "<head><title>t</title></head><body><p>prose</p></body></html>"
            )
            nav = (
                '<?xml version="1.0"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:epub="http://www.idpf.org/2007/ops">'
                "<head><title>t</title></head><body>"
                '<nav epub:type="toc"><ol><li><a href="text.xhtml">c</a></li>'
                "</ol></nav></body></html>"
            )
            book = root / "t.epub"
            with zipfile.ZipFile(book, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr(
                    "META-INF/container.xml",
                    '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="content.opf" '
                    'media-type="application/oebps-package+xml"/></rootfiles>'
                    "</container>",
                )
                z.writestr("content.opf", opf)
                z.writestr("nav.xhtml", nav)
                z.writestr("text.xhtml", doc)
            missing = root / "gone.epub"
            records = run_census([str(book), str(missing)])
        self.assertEqual(len(records), 2)
        got = {r.path: r for r in records}
        real = next(r for p, r in got.items() if p.endswith("t.epub"))
        ghost = next(r for p, r in got.items() if p.endswith("gone.epub"))
        self.assertFalse(real.unreadable)
        self.assertEqual((real.fatals, real.errors, real.warnings), (0, 0, 0))
        self.assertTrue(ghost.unreadable)


if __name__ == "__main__":
    unittest.main()
