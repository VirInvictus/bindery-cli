"""Command-line interface for Bindery."""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from . import __version__
from .epub import ncx_uid_mismatch, repair_epub
from .library import atomic_replace, iter_epubs, make_backup
from .validate import (
    CheckResult,
    epubcheck_available,
    gate,
    no_worse,
    run_epubcheck,
)


@dataclass
class Outcome:
    epub: Path
    status: str  # accept | partial | reject | nochange | equal | unvalidated | error
    before: CheckResult | None
    after: CheckResult | None
    summary: str


def process_book(
    epub: Path,
    workdir: Path,
    validate: bool,
    fix_ids: bool = False,
    reserialize: bool = False,
    strip_attrs: bool = False,
    strip_pagination: bool = False,
) -> Outcome:
    """Repair `epub` into a temp file and decide whether the result is acceptable."""
    repaired = workdir / "repaired.epub"
    report = repair_epub(
        epub,
        repaired,
        fix_ids=fix_ids,
        reserialize=reserialize,
        strip_attrs=strip_attrs,
        strip_pagination=strip_pagination,
    )
    if not report:
        return Outcome(epub, "nochange", None, None, "no applicable fixes")

    summary = ", ".join(f"{k}:{v}" for k, v in report.fixes.items())
    if report.ncx_uid_synced:
        summary = (summary + ", " if summary else "") + "ncx_uid_synced"

    if not validate:
        return Outcome(epub, "unvalidated", None, None, summary)

    before, after = run_epubcheck(epub), run_epubcheck(repaired)
    if before is None or after is None:
        # Validation was requested but the oracle failed (crash, timeout, unparsable
        # output). This is "error", not "unvalidated": the gate did not accept the
        # repair, so it must never be applied. Only --no-validate skips the gate.
        return Outcome(epub, "error", before, after, summary + " (epubcheck failed)")
    verdict = gate(before, after)
    if report.fixes.get("stripped_pagination"):
        # The strip's gain (in-body page numbers removed) is invisible to epubcheck, so
        # 'no measurable gain' is expected; accept as long as nothing regressed. But a
        # book that still has fatals will not open: no_worse must never promote it past
        # the gate's 'partial' (still-fatal books are never auto-applied).
        if not no_worse(before, after):
            verdict = "reject"
        elif after.fatals > 0:
            verdict = "partial"
        else:
            verdict = "accept"
    if verdict == "reject":
        summary += " (REGRESSION)"
    elif verdict == "noop":
        summary += " (no measurable gain)"
    status = "equal" if verdict == "noop" else verdict
    return Outcome(epub, status, before, after, summary)


def _load_audit(path: Path) -> dict[str, tuple[int, int, int]]:
    out: dict[str, tuple[int, int, int]] = {}
    with path.open() as fh:
        for row in csv.reader(fh):
            if len(row) != 4:
                continue
            f, e, w, p = row
            try:
                # Resolved, so a CSV written with one path shape still matches a scan
                # run with another (relative vs. absolute, symlinked mounts).
                out[str(Path(p).expanduser().resolve())] = (int(f), int(e), int(w))
            except ValueError:  # the header row, if present
                continue
    return out


def _select(epubs, only: str, audit: dict | None, audit_hits: list | None = None):
    """Filter the candidate list by --only and an optional audit CSV."""
    for epub in epubs:
        counts = None
        if audit is not None:
            counts = audit.get(str(epub.resolve()))
            if counts is not None and audit_hits is not None:
                audit_hits.append(epub)
        if only == "fatals":
            if audit is not None and (counts is None or counts[0] == 0):
                continue
        elif only == "ncx":
            if not ncx_uid_mismatch(epub):
                continue
        else:  # all
            if audit is not None and counts == (0, 0, 0):
                continue
        yield epub


def run_library(args) -> int:
    root = Path(args.path).expanduser()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 1
    if args.only == "fatals" and not args.audit:
        # Without an audit CSV there is no fatal-count data, and silently scanning
        # every book is not what --only fatals promised.
        print("error: --only fatals needs --audit CSV", file=sys.stderr)
        return 1

    validate = not args.no_validate
    if validate and not epubcheck_available():
        print(
            "error: epubcheck not found. install it or pass --no-validate.",
            file=sys.stderr,
        )
        return 1

    audit = _load_audit(Path(args.audit).expanduser()) if args.audit else None
    backup_dir = Path(args.backup).expanduser() if args.backup else None
    wants_backup = backup_dir is not None or args.backup_inplace
    if wants_backup and not args.apply:
        print(
            "note: dry run -- --backup/--backup-inplace do nothing without --apply",
            file=sys.stderr,
        )
    if args.apply and args.strip_pagination and not wants_backup:
        print(
            "WARNING: --strip-pagination is the one lossy mode; strongly consider "
            "--backup DIR or --backup-inplace when applying it.",
            file=sys.stderr,
        )

    audit_hits: list[Path] = []
    selected = _select(iter_epubs(root), args.only, audit, audit_hits)
    if args.limit is not None:
        # islice keeps the scan lazy, so --only ncx --limit 20 stops opening archives
        # after the 20th candidate instead of probing every book in the tree.
        candidates = islice(selected, args.limit)
        header = f"limit={args.limit}"
        total = args.limit
    else:
        candidates = list(selected)
        header = f"{len(candidates)} candidate book(s)"
        total = len(candidates)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Bindery {mode}: {header}, only={args.only}, validate={validate}\n")

    accepted = applied = rejected = equal = nochange = unvalidated = partials = 0
    errors = unreadable = processed = 0
    still_fatal = []

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        for epub in candidates:
            processed += 1
            rel = epub.relative_to(root)
            if not args.quiet:
                # Progress goes to stderr so stdout stays a clean report; nochange and
                # equal books print nothing there, and with validation each book costs
                # seconds of epubcheck time.
                print(f"[{processed}/{total}] {rel}", file=sys.stderr)
            try:
                o = process_book(
                    epub,
                    work,
                    validate,
                    fix_ids=args.fix_ids,
                    reserialize=args.reserialize,
                    strip_attrs=args.strip_bad_attrs,
                    strip_pagination=args.strip_pagination,
                )
            except (zipfile.BadZipFile, OSError, RuntimeError) as e:
                # One corrupt (non-zip, truncated, encrypted) book must not abort a
                # multi-hour sweep; report it and keep going.
                unreadable += 1
                print(f"  ERROR   {rel}\n            unreadable: {e}")
                continue
            if o.status == "nochange":
                nochange += 1
                continue
            if o.status == "reject":
                rejected += 1
                print(
                    f"  REJECT  {rel}\n            {o.before} -> {o.after}  {o.summary}"
                )
                continue
            if o.status == "equal":
                equal += 1
                continue
            if o.status == "error":
                errors += 1
                print(f"  ERROR   {rel}\n            {o.summary}; not applied")
                continue
            if o.status == "partial":
                # Fewer fatals but not zero: a real improvement, but the book still will
                # not open, so it needs manual work. Never auto-applied.
                partials += 1
                still_fatal.append((rel, o.after))
                print(
                    f"  PARTIAL {rel}\n            {o.before} -> {o.after}  {o.summary}"
                )
                continue

            # accept or unvalidated
            if o.status == "unvalidated":
                unvalidated += 1
                ba = ""
            else:
                accepted += 1
                ba = f"{o.before} -> {o.after}  "

            tag = "ACCEPT"
            if args.apply:
                if backup_dir is not None or args.backup_inplace:
                    make_backup(epub, backup_dir)
                atomic_replace(epub, work / "repaired.epub")
                applied += 1
                tag = "APPLIED"
            print(f"  {tag}  {rel}\n            {ba}{o.summary}")

    if audit is not None and not audit_hits:
        print(
            "\nWARNING: no scanned book matched any path in the audit CSV. The CSV "
            "was probably generated against a different path (absolute vs. relative, "
            "another mount point), so candidate selection saw no fatal counts.",
            file=sys.stderr,
        )

    print("\n========== SUMMARY ==========")
    print(f"candidates:      {processed}")
    print(
        f"accepted:        {accepted}"
        + (f"  (applied: {applied})" if args.apply else "")
    )
    print(f"partial (manual):{partials}")
    print(f"no change:       {nochange}")
    print(f"equal (skipped): {equal}")
    print(f"unvalidated:     {unvalidated}")
    print(f"epubcheck errors:{errors}")
    print(f"unreadable:      {unreadable}")
    print(f"REJECTED:        {rejected}")
    if still_fatal:
        print(f"\nimproved but STILL FATAL ({len(still_fatal)}) -- manual follow-up:")
        for rel, after in still_fatal:
            print(f"  {after}  {rel}")
    if not args.apply:
        print(
            "\n(dry run -- no files written. re-run with --apply to replace in place.)"
        )
    # 2 lets scripts and cron distinguish "ran fine but some books are in trouble"
    # from a clean sweep (0) and a usage error (1).
    return 2 if (rejected + errors + unreadable) > 0 else 0


def run_repair(args) -> int:
    src = Path(args.path).expanduser()
    if not src.is_file():
        print(f"error: no such file: {src}", file=sys.stderr)
        return 1
    dst = (
        Path(args.output).expanduser()
        if args.output
        else src.with_name(f"{src.stem} (repaired).epub")
    )
    if dst.resolve() == src.resolve():
        print("error: refusing to overwrite the input in place", file=sys.stderr)
        return 1
    if dst.exists() and not args.force:
        print(
            f"error: output exists: {dst} (pass --force to overwrite)",
            file=sys.stderr,
        )
        return 1

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        try:
            o = process_book(
                src,
                work,
                validate=not args.no_validate,
                fix_ids=args.fix_ids,
                reserialize=args.reserialize,
                strip_attrs=args.strip_bad_attrs,
                strip_pagination=args.strip_pagination,
            )
        except (zipfile.BadZipFile, OSError, RuntimeError) as e:
            print(f"error: cannot read {src}: {e}", file=sys.stderr)
            return 1
        if o.status == "nochange":
            print("no applicable fixes; nothing written.")
            return 0
        if o.status == "reject":
            print(
                f"repair REJECTED (regression): {o.before} -> {o.after}; nothing written."
            )
            return 1
        if o.status == "error":
            print(
                "epubcheck failed; nothing written (pass --no-validate to skip the gate).",
                file=sys.stderr,
            )
            return 1
        # Copy the exact bytes the gate accepted. Re-repairing src here would silently
        # drop the opt-in flags (--fix-ids, --reserialize, --strip-bad-attrs) and write
        # a file that differs from the one epubcheck validated.
        shutil.copyfile(work / "repaired.epub", dst)
        ba = f"{o.before} -> {o.after}  " if o.before else ""
        if o.status == "partial":
            # The file is a real improvement and worth writing, but calling it
            # "repaired" would read as fixed; it still will not open.
            print(
                f"PARTIAL (still has fatals; needs manual work): {ba}{o.summary}\n"
                f"wrote {dst}"
            )
        else:
            print(f"repaired: {ba}{o.summary}\nwrote {dst}")
    return 0


def _add_repair_flags(p: argparse.ArgumentParser) -> None:
    """The fix-selection and gate flags shared by both subcommands."""
    p.add_argument(
        "--fix-ids",
        action="store_true",
        help="also rewrite invalid manifest ids in the OPF (RSC-005)",
    )
    p.add_argument(
        "--reserialize",
        action="store_true",
        help="rebuild still-malformed documents via html5lib (closes unclosed elements)",
    )
    p.add_argument(
        "--strip-bad-attrs",
        action="store_true",
        help="drop invalid attributes (digit-led names, unbound namespace prefixes)",
    )
    p.add_argument(
        "--strip-pagination",
        action="store_true",
        help="LOSSY: remove print page numbers/running headers baked into the body "
        "text by a bad conversion, rejoining sentences they split (epubcheck-gated, "
        "accepted when no worse)",
    )
    p.add_argument("--no-validate", action="store_true", help="skip the epubcheck gate")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="bindery", description="Repair EPUBs, epubcheck-gated."
    )
    ap.add_argument("--version", action="version", version=f"bindery {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("repair", help="repair a single EPUB to a new file")
    r.add_argument("path")
    r.add_argument("output", nargs="?")
    r.add_argument(
        "--force",
        action="store_true",
        help="overwrite the output file if it already exists",
    )
    _add_repair_flags(r)
    r.set_defaults(func=run_repair)

    lib = sub.add_parser("library", help="scan/repair a Calibre library tree")
    lib.add_argument("path")
    lib.add_argument(
        "--apply",
        action="store_true",
        help="atomically replace accepted books in place (default: dry run)",
    )
    lib.add_argument(
        "--only",
        choices=("fatals", "ncx", "all"),
        default="all",
        help="restrict to books with fatals, NCX-001 mismatch, or all (default)",
    )
    lib.add_argument(
        "--audit", help="audit CSV (fatals,errors,warnings,path) to filter candidates"
    )
    lib.add_argument("--backup", help="directory to mirror backups into before --apply")
    lib.add_argument(
        "--backup-inplace",
        action="store_true",
        help="with --apply, write a .epub.bak beside each replaced file",
    )
    lib.add_argument(
        "--limit", type=int, help="process at most N candidates (for sampling)"
    )
    lib.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the per-book progress line on stderr",
    )
    _add_repair_flags(lib)
    lib.set_defaults(func=run_library)
    return ap


def main(argv: list[str] | None = None) -> int:
    # Line-buffer stdout so per-book progress is visible live even when redirected to a
    # file or pipe (otherwise a long library run shows nothing until the buffer fills).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        # A library run can take a long time; end a Ctrl-C cleanly instead of with a
        # traceback. In-flight work is safe: the original is only ever touched by the
        # atomic os.replace.
        print("\ninterrupted", file=sys.stderr)
        return 130
