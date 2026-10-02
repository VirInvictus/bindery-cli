"""The RepairFlags wiring invariants (added after the v0.46.0 refactor,
from the 2026-10-02 test-gap sweep): the flag inventory lives in three
places that must never drift apart silently -- the RepairFlags dataclass
fields, the argparse dests in cli._add_repair_flags, and the
dest-to-field mapping in cli._flags_from_args. Before this module, no
test failed when a flag was added without a field, a field without a
flag, or a mapping line was forgotten; a disconnected flag shipped
silently. The dest-to-field pairs are spelled here on purpose: an
intentional remap updates both copies, and an accidental one fails
here."""

import unittest
from argparse import ArgumentParser, _StoreTrueAction
from dataclasses import fields
from pathlib import Path
from unittest import mock

from bindery import cli
from bindery.epub import RepairFlags, RepairReport

# argparse dest on the left, RepairFlags field on the right.
DEST_TO_FIELD = {
    "fix_ids": "fix_ids",
    "add_img_alt": "img_alt",
    "reserialize": "reserialize",
    "strip_bad_attrs": "strip_attrs",
    "escape_unknown_entities": "escape_entities",
    "fix_empty_body": "empty_body",
    "fix_missing_title": "missing_title",
    "fix_id_colons": "id_colons",
    "unwrap_block_in_inline": "block_in_inline",
    "strip_invalid_value": "invalid_value",
    "unwrap_illegal_tags": "illegal_tags",
    "fix_page_map": "page_map",
    "strip_epub3_attrs": "strip_epub3_attrs",
    "downgrade_epub3_tags": "downgrade_epub3",
    "prune_missing_resources": "prune_missing",
    "strip_broken_anchors": "strip_anchors",
    "encode_url_spaces": "url_spaces",
    "fix_container": "fix_container",
    "fix_media_types": "fix_media_types",
    "fix_cover": "fix_cover",
    "fix_comment_double_hyphen": "comment_double_hyphens",
    "strip_pagination": "strip_pagination",
    "strip_broken_tags": "strip_brokentags",
    "strip_watermarks": "strip_watermarks",
    "strip_stub_docs": "strip_stub_docs",
}

# The two store_true dests in _add_repair_flags that are not repair flags.
NON_SELECTION_DESTS = {"all", "no_validate"}


def _repair_flag_dests() -> set[str]:
    """The dests cli._add_repair_flags defines: every flag it registers is
    a store_true opt-in (the two non-selection dests are filtered by the
    caller). Checked by action class, not the .action attribute: 3.14's
    argparse actions do not carry it."""
    dummy = ArgumentParser(add_help=False)
    cli._add_repair_flags(dummy)
    return {
        a.dest
        for a in dummy._actions
        if isinstance(a, _StoreTrueAction) and a.dest != "help"
    }


class DefaultsTests(unittest.TestCase):
    def test_every_field_defaults_false(self):
        # The all-off instance IS the default pass, and the plugin's bare
        # repair_epub(src, dst) call constructs exactly it: one field
        # defaulting True would ship an ungated fix inside Calibre.
        wrong = [
            f.name
            for f in fields(RepairFlags)
            if getattr(RepairFlags(), f.name) is not False
        ]
        self.assertEqual(wrong, [])


class WiringTests(unittest.TestCase):
    def _flags_for_argv(self, *option_flags: str) -> RepairFlags:
        """The RepairFlags object repair_epub receives for the given argv
        (repair_epub mocked to a falsy report, so process_book stops
        before the epubcheck leg)."""
        args = cli.build_parser().parse_args(["repair", "x.epub", *option_flags])
        with mock.patch(
            "bindery.cli.repair_epub", return_value=RepairReport()
        ) as repair:
            cli.process_book(
                Path("x.epub"),
                Path("."),
                validate=False,
                flags=cli._flags_from_args(args),
            )
        return repair.call_args.kwargs["flags"]

    def test_dest_inventory_matches_the_dataclass(self):
        self.assertEqual(_repair_flag_dests() - NON_SELECTION_DESTS, set(DEST_TO_FIELD))

    def test_each_dest_maps_to_its_pinned_field(self):
        for dest, field in sorted(DEST_TO_FIELD.items()):
            flags = self._flags_for_argv(f"--{dest.replace('_', '-')}")
            truthy = [f.name for f in fields(RepairFlags) if getattr(flags, f.name)]
            self.assertEqual(truthy, [field], f"--{dest.replace('_', '-')}")

    def test_all_enables_every_field(self):
        flags = self._flags_for_argv("--all")
        self.assertTrue(all(getattr(flags, f.name) for f in fields(RepairFlags)))


if __name__ == "__main__":
    unittest.main()
