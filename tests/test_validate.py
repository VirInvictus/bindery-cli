"""Locale hardening for the epubcheck wrapper (roadmap 5.2).

epubcheck (Java) localizes its human-readable summary line, so the English-only
`_SUMMARY_RE` parsed every book as None on a non-English locale and the CLI
reported it as `error`. The hardened wrapper asks for epubcheck's
locale-independent JSON output and pins the JVM to English so the regex
fallback stays meaningful on epubchecks too old for `--json`.

These tests mock `subprocess.run` itself (unlike test_cli.py, which mocks
`run_epubcheck`) because the parsing internals are exactly what is under test.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from bindery.validate import (
    CheckResult,
    _counts_from_json,
    _DaemonPool,
    _english_locale_env,
    _EpubcheckDaemon,
    _readline_timeout,
    gate,
    no_worse,
    run_epubcheck,
    set_daemon_pool_size,
)

_BOOK = Path("/tmp/anything.epub")


def setUpModule() -> None:
    # The pool compiles a JVM helper and launches a JVM; none of the parsing
    # tests exercise it (they mock the subprocess oracle), and CI has no
    # epubcheck at all. Daemon tests re-enable it locally.
    set_daemon_pool_size(0)


def _daemon_toolchain_available() -> bool:
    import shutil

    return shutil.which("epubcheck") is not None and shutil.which("javac") is not None


def _fixture_epub(dirpath: Path, name: str) -> Path:
    """A minimal (not-schema-valid) EPUB: enough for a real epubcheck run
    with real counts."""
    path = dirpath / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("t.xhtml", "<html><body><p>x</p></body></html>")
    return path


def _completed(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["epubcheck"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _json_output(fatals: int, errors: int, warnings: int) -> str:
    # The shape epubcheck 5.3.0 actually emits for `--json -` (verified
    # against the installed jar): counts live under "checker".
    return json.dumps(
        {
            "messages": [],
            "checker": {
                "path": str(_BOOK),
                "filename": _BOOK.name,
                "checkerVersion": "5.3.0",
                "nFatal": fatals,
                "nError": errors,
                "nWarning": warnings,
                "nUsage": 0,
            },
        }
    )


class JsonParsing(unittest.TestCase):
    def test_json_counts_are_parsed(self) -> None:
        with mock.patch(
            "bindery.validate.subprocess.run",
            return_value=_completed(_json_output(1, 2, 3)),
        ):
            self.assertEqual(run_epubcheck(_BOOK), CheckResult(1, 2, 3))

    def test_json_wins_even_with_noise_on_stderr(self) -> None:
        # JAVA_TOOL_OPTIONS makes the JVM print a pickup notice on stderr.
        with mock.patch(
            "bindery.validate.subprocess.run",
            return_value=_completed(
                _json_output(0, 0, 0),
                stderr="Picked up JAVA_TOOL_OPTIONS: -Duser.language=en\n",
            ),
        ):
            self.assertEqual(run_epubcheck(_BOOK), CheckResult(0, 0, 0))

    def test_invocation_asks_for_json_and_pins_the_jvm_locale(self) -> None:
        with mock.patch(
            "bindery.validate.subprocess.run",
            return_value=_completed(_json_output(0, 0, 0)),
        ) as run:
            run_epubcheck(_BOOK)
        args, kwargs = run.call_args
        self.assertIn("--json", args[0])
        env = kwargs.get("env")
        self.assertIsNotNone(env, "subprocess must get an explicit env")
        self.assertIn("-Duser.language=en", env.get("JAVA_TOOL_OPTIONS", ""))

    def test_existing_java_tool_options_are_preserved(self) -> None:
        with (
            mock.patch.dict("os.environ", {"JAVA_TOOL_OPTIONS": "-Xmx512m"}),
            mock.patch(
                "bindery.validate.subprocess.run",
                return_value=_completed(_json_output(0, 0, 0)),
            ) as run,
        ):
            run_epubcheck(_BOOK)
        env = run.call_args.kwargs["env"]
        self.assertIn("-Xmx512m", env["JAVA_TOOL_OPTIONS"])
        self.assertIn("-Duser.language=en", env["JAVA_TOOL_OPTIONS"])


class RegexFallback(unittest.TestCase):
    def test_english_summary_still_parses_without_json(self) -> None:
        # An epubcheck too old for --json prints usage noise plus the
        # classic summary line; the regex fallback must keep working.
        with mock.patch(
            "bindery.validate.subprocess.run",
            return_value=_completed(
                stdout="Check finished with errors\nMessages: 1 fatals / 2 errors / 3 warnings\n",
                returncode=1,
            ),
        ):
            self.assertEqual(run_epubcheck(_BOOK), CheckResult(1, 2, 3))

    def test_localized_summary_without_json_is_unparseable(self) -> None:
        # The residual limit this hardening documents: a pre-JSON
        # epubcheck on a German locale still parses as None (reported
        # as error by the CLI), never as fake counts.
        with mock.patch(
            "bindery.validate.subprocess.run",
            return_value=_completed(
                stdout="Meldungen: 1 schwerwiegende Fehler / 2 Fehler / 3 Warnungen\n",
                returncode=1,
            ),
        ):
            self.assertIsNone(run_epubcheck(_BOOK))

    def test_malformed_json_falls_back_to_regex(self) -> None:
        with mock.patch(
            "bindery.validate.subprocess.run",
            return_value=_completed(
                stdout='{"checker": truncated\nMessages: 0 fatals / 1 errors / 0 warnings\n'
            ),
        ):
            self.assertEqual(run_epubcheck(_BOOK), CheckResult(0, 1, 0))


class FailureModes(unittest.TestCase):
    def test_missing_binary_is_none(self) -> None:
        with mock.patch(
            "bindery.validate.subprocess.run", side_effect=FileNotFoundError
        ):
            self.assertIsNone(run_epubcheck(_BOOK))

    def test_timeout_is_none(self) -> None:
        with mock.patch(
            "bindery.validate.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="epubcheck", timeout=1),
        ):
            self.assertIsNone(run_epubcheck(_BOOK))


class ReadlineTimeout(unittest.TestCase):
    """The daemon roundtrip is bounded: a wedged JVM can no longer hang the
    whole sweep forever (reported 2026-09-08; needs no JVM to test)."""

    def test_ready_line_is_read(self) -> None:
        r, w = os.pipe()
        os.write(w, b"0,1,2\n")
        os.close(w)
        with os.fdopen(r, "rb") as fh:
            self.assertEqual(_readline_timeout(fh, 5), "0,1,2")

    def test_silent_pipe_times_out(self) -> None:
        r, w = os.pipe()
        os.close(w)
        with os.fdopen(r, "rb") as fh:
            with self.assertRaises(TimeoutError):
                _readline_timeout(fh, 0)

    def test_closed_pipe_raises_not_blocks(self) -> None:
        r, w = os.pipe()
        os.close(w)
        with os.fdopen(r, "rb") as fh:
            with self.assertRaises(RuntimeError):
                _readline_timeout(fh, 5)


@unittest.skipUnless(_daemon_toolchain_available(), "epubcheck not installed")
class DaemonMatchesJsonOracle(unittest.TestCase):
    """The counting-parity contract, pinned on real books: the daemon must
    answer with the SAME numbers the subprocess --json oracle produces,
    because the gate is calibrated on those numbers. FastDaemon v2 extracts
    them from the JSON document CheckingReport.generate() itself serializes,
    so the match holds by construction; this test holds it empirically,
    including on a book engineered with repeated identical defects (the
    exact shape where the old occurrence-counting daemon diverged)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        # A clean book carrying exactly two distinct defects, one of them
        # repeated three times: the JSON oracle deduplicates the repeats, so
        # the true count is 2 errors from 4 occurrences. The old
        # occurrence-counting daemon answered 4 here; by-construction
        # counting must answer 2.
        path = Path(self.tmp.name) / "dedup.epub"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr(
                "META-INF/container.xml",
                '<container version="1.0" '
                'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles><rootfile full-path="content.opf" '
                'media-type="application/oebps-package+xml"/></rootfiles>'
                "</container>",
            )
            z.writestr(
                "content.opf",
                '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
                'unique-identifier="bid"><metadata '
                'xmlns:dc="http://purl.org/dc/elements/1.1/">'
                "<dc:title>t</dc:title><dc:language>en</dc:language>"
                '<dc:identifier id="bid">pub-id</dc:identifier></metadata>'
                "<manifest>"
                '<item id="c1" href="d.xhtml" '
                'media-type="application/xhtml+xml"/>'
                '<item id="ncx" href="toc.ncx" '
                'media-type="application/x-dtbncx+xml"/></manifest>'
                '<spine toc="ncx"><itemref idref="c1"/></spine></package>',
            )
            z.writestr(
                "toc.ncx",
                '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
                'version="2005-1"><head>'
                '<meta name="dtb:uid" content="pub-id"/>'
                '<meta name="dtb:depth" content="1"/>'
                '<meta name="dtb:totalPageCount" content="0"/>'
                '<meta name="dtb:maxPageNumber" content="0"/></head>'
                "<docTitle><text>t</text></docTitle><navMap>"
                '<navPoint id="n1" playOrder="1"><navLabel><text>c</text>'
                "</navLabel>"
                '<content src="d.xhtml"/></navPoint></navMap></ncx>',
            )
            z.writestr(
                "d.xhtml",
                '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t'
                "</title></head><body>"
                '<p id="1:x">a</p><p id="2:x">b</p><p id="3:x">c</p>'
                "<bogus>x</bogus>"
                "</body></html>",
            )
        self.book = path

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_daemon_counts_match_the_subprocess_json_oracle(self) -> None:
        import subprocess

        d = _EpubcheckDaemon()
        try:
            daemon = d.check(self.book, timeout=180)
            self.assertIsInstance(daemon, CheckResult)
            out = subprocess.run(
                ["epubcheck", str(self.book), "--json", "-"],
                capture_output=True,
                text=True,
                timeout=180,
                env=_english_locale_env(),
                check=False,
            )
            oracle = _counts_from_json(out.stdout)
            self.assertEqual(
                (daemon.fatals, daemon.errors, daemon.warnings),
                (oracle.fatals, oracle.errors, oracle.warnings),
            )
            # the dedup pin: three identical id defects plus one different
            # error are 4 occurrences but 2 aggregated messages, and the
            # daemon reports the aggregated count, not the occurrences
            self.assertEqual(daemon.errors, 2)
        finally:
            d.stop()


class DaemonPoolLogic(unittest.TestCase):
    """The pool's degrade-to-subprocess logic, hermetic (no JVM needed).

    The oracle must measure every book the same way, so a daemon that cannot
    serve counts must fall back to the subprocess oracle permanently, never
    respawn a JVM per book, and never serve counts from a different
    measurement method than the gate was calibrated against."""

    def test_size_zero_disables_the_daemon_path(self) -> None:
        with mock.patch("bindery.validate._EpubcheckDaemon") as daemon_cls:
            pool = _DaemonPool(max_size=0)
            self.assertIsNone(pool.check(_BOOK))
            daemon_cls.assert_not_called()

    def test_broken_toolchain_exhausts_after_one_attempt(self) -> None:
        # reported 2026-09-08: a failed daemon start re-ran javac per book
        with mock.patch("bindery.validate._EpubcheckDaemon") as daemon_cls:
            inst = daemon_cls.return_value
            inst.alive.return_value = False
            inst._start.return_value = False
            inst._proc = None
            pool = _DaemonPool(max_size=2)
            self.assertIsNone(pool.check(_BOOK))
            self.assertIsNone(pool.check(_BOOK))
            self.assertTrue(pool._exhausted)
            self.assertEqual(inst._start.call_count, 1)  # one attempt, not one per book

    def test_busy_pool_falls_back_to_subprocess(self) -> None:
        pool = _DaemonPool(max_size=1)
        pool._live = 1  # the one slot is busy checking another book
        with mock.patch("bindery.validate._EpubcheckDaemon") as daemon_cls:
            self.assertIsNone(pool.check(_BOOK))
            daemon_cls.assert_not_called()

    def test_dead_daemon_without_a_success_exhausts_the_pool(self) -> None:
        with mock.patch("bindery.validate._EpubcheckDaemon") as daemon_cls:
            inst = daemon_cls.return_value
            inst.alive.return_value = False  # died on its first check
            inst._start.return_value = True
            inst._proc = None
            inst.check.return_value = None
            inst.succeeded = False
            pool = _DaemonPool(max_size=1)
            self.assertIsNone(pool.check(_BOOK))
            self.assertTrue(pool._exhausted)
            self.assertIsNone(pool.check(_BOOK))

    def test_alive_daemon_returns_to_the_idle_pool(self) -> None:
        with mock.patch("bindery.validate._EpubcheckDaemon") as daemon_cls:
            inst = daemon_cls.return_value
            inst.alive.return_value = True
            inst._start.return_value = True
            inst.check.return_value = CheckResult(0, 0, 0)
            inst.succeeded = True
            pool = _DaemonPool(max_size=1)
            self.assertEqual(pool.check(_BOOK), CheckResult(0, 0, 0))
            self.assertEqual(pool._idle.qsize(), 1)  # reusable, not discarded


if __name__ == "__main__":
    unittest.main()


class TestGateDirect(unittest.TestCase):
    """gate() and no_worse() had zero direct tests; the subtle fatal-fixing
    error-unmasking branch was asserted nowhere."""

    def test_fatal_fixing_accepts_unmasked_errors(self) -> None:
        # fatals were masking five errors; clearing the fatal surfaces them.
        # That is success, not regression.
        self.assertEqual(gate(CheckResult(1, 0, 0), CheckResult(0, 5, 0)), "accept")

    def test_fatal_reduction_without_clearance_is_partial(self) -> None:
        self.assertEqual(gate(CheckResult(2, 9, 0), CheckResult(1, 9, 0)), "partial")

    def test_unchanged_fatals_is_noop(self) -> None:
        self.assertEqual(gate(CheckResult(1, 0, 0), CheckResult(1, 0, 0)), "noop")

    def test_net_new_fatal_is_reject_even_from_fatal(self) -> None:
        self.assertEqual(gate(CheckResult(1, 9, 0), CheckResult(2, 9, 0)), "reject")

    def test_clean_book_error_rise_is_reject(self) -> None:
        self.assertEqual(gate(CheckResult(0, 1, 0), CheckResult(0, 2, 0)), "reject")

    def test_clean_book_error_drop_is_accept(self) -> None:
        self.assertEqual(gate(CheckResult(0, 3, 0), CheckResult(0, 1, 0)), "accept")

    def test_no_worse_forbids_fatal_rise(self) -> None:
        self.assertFalse(no_worse(CheckResult(0, 2, 0), CheckResult(1, 2, 0)))

    def test_no_worse_forbids_error_rise_on_a_clean_book(self) -> None:
        self.assertFalse(no_worse(CheckResult(0, 2, 0), CheckResult(0, 3, 0)))

    def test_no_worse_tolerates_error_unmasking_under_fatals(self) -> None:
        self.assertTrue(no_worse(CheckResult(1, 0, 0), CheckResult(0, 9, 0)))

    def test_no_worse_allows_identical_counts(self) -> None:
        self.assertTrue(no_worse(CheckResult(0, 2, 5), CheckResult(0, 2, 5)))
