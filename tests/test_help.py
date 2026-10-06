"""Tests for the help-surface truth set and the machine surface: the
fail-closed Calibre guard on the metadata.db write doors, the contracts
the AI-grokability probe found missing from help (exit codes, the gate,
NCX-001, --id scoping, the json envelopes), and --help-json."""

import argparse
import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from bindery import __version__, cli


class TestCalibreGuard(unittest.TestCase):
    """The lock-class refusal on audit --tag and library --apply
    --install-to-calibre: fail-closed pgrep, exit 1, before any work."""

    def test_refuses_when_calibre_runs(self):
        with mock.patch.object(cli, "_calibre_running", return_value=True):
            rc = cli._require_closed_calibre("the door")
        self.assertEqual(rc, 1)

    def test_passes_when_calibre_closed(self):
        with mock.patch.object(cli, "_calibre_running", return_value=False):
            rc = cli._require_closed_calibre("the door")
        self.assertEqual(rc, 0)

    def test_pgrep_failure_is_fail_closed(self):
        # an unrunnable pgrep means "can't tell": refuse
        with mock.patch.object(
            cli.subprocess,
            "run",
            side_effect=OSError("no pgrep"),
        ):
            self.assertTrue(cli._calibre_running())
        with mock.patch.object(
            cli.subprocess,
            "run",
            side_effect=cli.subprocess.TimeoutExpired(cmd="pgrep", timeout=10),
        ):
            self.assertTrue(cli._calibre_running())

    def test_library_guard_fires_before_anything_else(self):
        # a nonexistent path would fail "not a directory" (exit 1) anyway;
        # the stderr message proves the guard spoke first
        args = SimpleNamespace(
            apply=True,
            install_to_calibre=True,
            path="/nonexistent/library",
        )
        err = io.StringIO()
        with (
            mock.patch.object(cli, "_calibre_running", return_value=True),
            contextlib.redirect_stderr(err),
        ):
            rc = cli.run_library(args)
        self.assertEqual(rc, 1)
        self.assertIn("Calibre is running", err.getvalue())

    def test_library_without_install_door_is_not_gated(self):
        # a plain --apply (in-place) or dry run never consults the guard
        args = SimpleNamespace(
            apply=True,
            install_to_calibre=False,
            path="/nonexistent/library",
        )
        err = io.StringIO()
        with (
            mock.patch.object(cli, "_calibre_running", return_value=True),
            contextlib.redirect_stderr(err),
        ):
            rc = cli.run_library(args)
        self.assertEqual(rc, 1)
        self.assertIn("not a directory", err.getvalue())

    def test_audit_tag_guard_fires_first(self):
        args = SimpleNamespace(tag="Flagged")
        err = io.StringIO()
        with (
            mock.patch.object(cli, "_calibre_running", return_value=True),
            contextlib.redirect_stderr(err),
        ):
            rc = cli.run_audit_cmd(args)
        self.assertEqual(rc, 1)
        self.assertIn("audit --tag", err.getvalue())


class TestHelpTruth(unittest.TestCase):
    """The contracts the probe found missing, now stated where users and
    agents read them."""

    def _parser(self):
        return cli.build_parser()

    def _sub(self, parser, name):
        subparsers = next(
            a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
        )
        return subparsers.choices[name]

    def test_repair_page_states_the_contract(self):
        text = self._sub(self._parser(), "repair").description
        self.assertIn("Never prompts", text)
        self.assertIn("PARTIAL repair writes its best output and exits 0", text)
        self.assertIn("does not use", text)
        self.assertIn("without --force", text)

    def test_library_page_states_gate_and_codes(self):
        text = self._sub(self._parser(), "library").description
        self.assertIn("Never prompts", text)
        self.assertIn("closed Calibre", text)
        self.assertIn(
            "0 all clean, 2 any flagged/rejected/error/unreadable/partial", text
        )
        self.assertIn("keeps NO copy of the original", text)

    def test_install_to_calibre_names_the_gate(self):
        parser = self._parser()
        lib = self._sub(parser, "library")
        helps = " ".join(
            a.help or "" for g in lib._action_groups for a in g._group_actions
        )
        self.assertIn("Calibre must be closed", helps)

    def test_ncx001_is_defined(self):
        parser = self._parser()
        lib = self._sub(parser, "library")
        helps = " ".join(
            a.help or "" for g in lib._action_groups for a in g._group_actions
        )
        self.assertIn("dtb:uid", helps)

    def test_library_id_works_without_sweep(self):
        parser = self._parser()
        lib = self._sub(parser, "library")
        helps = " ".join(
            a.help or "" for g in lib._action_groups for a in g._group_actions
        )
        self.assertIn("works without --sweep", helps)

    def test_run_pages_carry_exit_codes(self):
        run = self._sub(self._parser(), "run")
        run_subs = next(
            a for a in run._actions if isinstance(a, argparse._SubParsersAction)
        )
        helps = {pa.dest: pa.help for pa in run_subs._choices_actions}
        for name in ("phase1", "phase3"):
            text = helps[name]
            self.assertIn("Exit codes", text, name)


class TestHelpJson(unittest.TestCase):
    def test_valid_complete_deterministic(self):
        payload_text = self._dump()
        data = json.loads(payload_text)
        self.assertEqual(data["tool"], "bindery")
        self.assertEqual(data["version"], __version__)
        self.assertEqual(
            [s["name"] for s in data["subcommands"]],
            ["repair", "doctor", "audit", "library", "run"],
        )
        run = next(s for s in data["subcommands"] if s["name"] == "run")
        self.assertEqual([s["name"] for s in run["slices"]], ["phase1", "phase3"])
        self.assertEqual(
            [g["group"] for g in data["repair_reference"]],
            list(cli._REPAIR_FLAG_TABLE),
        )
        self.assertEqual(
            data["calibre_gates"][0:2],
            ["audit --tag", "library --apply --install-to-calibre"],
        )
        # the third door is phase3, guarded transitively through library
        self.assertIn("run phase3", data["calibre_gates"][2])
        # the contract now carries the per-verb truth
        self.assertEqual(data["exit_contract"]["per_verb"]["repair"][-7:], "never 2")
        self.assertIn("1 findings", data["exit_contract"]["per_verb"]["audit"])
        self.assertIn("3", data["exit_contract"])
        self.assertEqual(payload_text, self._dump())

    def _dump(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                cli.main(["--help-json"])
        self.assertEqual(caught.exception.code, 0)
        return out.getvalue()

    def test_covers_every_subcommand_flag_dest(self):
        payload_text = self._dump()
        parser = cli.build_parser()
        subparsers = next(
            a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
        )
        for _name, sub in subparsers.choices.items():
            for group in sub._action_groups:
                for action in group._group_actions:
                    if action.dest == "help":
                        continue
                    for opt in action.option_strings:
                        self.assertIn(f'"{opt}"', payload_text, opt)

    def test_exit_contract_present(self):
        data = json.loads(self._dump())
        self.assertIn("0", data["exit_contract"])
        self.assertIn("2", data["exit_contract"])
