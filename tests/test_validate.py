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
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from bindery.validate import CheckResult, run_epubcheck

_BOOK = Path("/tmp/anything.epub")


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


if __name__ == "__main__":
    unittest.main()
