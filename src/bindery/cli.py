"""Command-line interface for Bindery."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path

from tqdm import tqdm

from . import __version__
from .audit import (
    ALL,
    DEFAULT_MAX_DOC_CHARS,
    DEFAULT_MIN_CHARS,
    DEFAULT_THIN_CHARS,
    resolve_library_root,
    run_directory,
    run_single,
)
from .audit import (
    run_library as run_audit_library,
)
from .epub import RepairFlags, ncx_uid_mismatch, repair_epub
from .library import (
    CalibreIdResolver,
    atomic_replace,
    install_format,
    iter_epubs,
    make_backup,
)
from .validate import (
    CheckResult,
    epubcheck_available,
    gate,
    no_worse,
    run_epubcheck,
    set_daemon_pool_size,
)


@dataclass
class Outcome:
    epub: Path
    # accept | partial | reject | nochange | equal | unvalidated | error | unreadable
    status: str
    before: CheckResult | None
    after: CheckResult | None
    summary: str
    # The structured half of `summary`: the RepairReport's own data, so
    # consumers (run phase1's decisions, CalibreQuarry's lossy-consent
    # mirror) read fix classes as data instead of substring-matching the
    # rendered string.
    fixes: dict[str, int] = field(default_factory=dict)
    ncx_uid_synced: bool = False
    watermark_refusals: int = 0


def _flags_from_args(args: argparse.Namespace) -> RepairFlags:
    """The repair selection from parsed args: each flag OR'd with --all, so
    --all enables every opt-in. The one place the argparse-to-RepairFlags
    name mapping is spelled out; both verbs share it."""
    all_ = getattr(args, "all", False)
    return RepairFlags(
        fix_ids=args.fix_ids or all_,
        reserialize=args.reserialize or all_,
        strip_attrs=args.strip_bad_attrs or all_,
        strip_pagination=args.strip_pagination or all_,
        strip_brokentags=args.strip_broken_tags or all_,
        strip_watermarks=args.strip_watermarks or all_,
        strip_stub_docs=args.strip_stub_docs or all_,
        escape_entities=args.escape_unknown_entities or all_,
        img_alt=args.add_img_alt or all_,
        empty_body=args.fix_empty_body or all_,
        missing_title=args.fix_missing_title or all_,
        id_colons=args.fix_id_colons or all_,
        block_in_inline=args.unwrap_block_in_inline or all_,
        invalid_value=args.strip_invalid_value or all_,
        illegal_tags=args.unwrap_illegal_tags or all_,
        page_map=args.fix_page_map or all_,
        strip_epub3_attrs=args.strip_epub3_attrs or all_,
        downgrade_epub3=args.downgrade_epub3_tags or all_,
        prune_missing=args.prune_missing_resources or all_,
        strip_anchors=args.strip_broken_anchors or all_,
        url_spaces=args.encode_url_spaces or all_,
        fix_container=args.fix_container or all_,
        fix_media_types=args.fix_media_types or all_,
        fix_cover=args.fix_cover or all_,
        comment_double_hyphens=args.fix_comment_double_hyphen or all_,
        svg_dup_ids=args.fix_svg_dup_ids or all_,
        cdata_terminator=args.fix_cdata_terminator or all_,
        misnested_inline=args.fix_misnested_inline or all_,
        stray_close=args.fix_stray_close or all_,
        unterminated_attr=args.fix_unterminated_attr or all_,
    )


def process_book(
    epub: Path,
    workdir: Path,
    validate: bool,
    flags: RepairFlags | None = None,
    before: CheckResult | None = None,
) -> Outcome:
    """Repair `epub` into a temp file and decide whether the result is acceptable.

    `before` is a pre-measured epubcheck result for `epub` (from a --sweep pass),
    saving a second multi-second run; when None it is measured here."""
    repaired = workdir / "repaired.epub"
    report = repair_epub(epub, repaired, flags=flags)
    if not report:
        return Outcome(epub, "nochange", None, None, "no applicable fixes")

    summary = ", ".join(f"{k}:{v}" for k, v in report.fixes.items())
    if report.ncx_uid_synced:
        summary = (summary + ", " if summary else "") + "ncx_uid_synced"
    if report.watermark_refusals:
        summary = (summary + ", " if summary else "") + (
            f"watermark_refusals:{report.watermark_refusals}"
        )

    if not validate:
        return Outcome(
            epub,
            "unvalidated",
            None,
            None,
            summary,
            fixes=report.fixes,
            ncx_uid_synced=report.ncx_uid_synced,
            watermark_refusals=report.watermark_refusals,
        )

    if before is None:
        before = run_epubcheck(epub)
    after = run_epubcheck(repaired)
    if before is None or after is None:
        # Validation was requested but the oracle failed (crash, timeout, unparsable
        # output). This is "error", not "unvalidated": the gate did not accept the
        # repair, so it must never be applied. Only --no-validate skips the gate.
        return Outcome(
            epub,
            "error",
            before,
            after,
            summary + " (epubcheck failed)",
            fixes=report.fixes,
            ncx_uid_synced=report.ncx_uid_synced,
            watermark_refusals=report.watermark_refusals,
        )
    verdict = gate(before, after)
    if (
        report.fixes.get("stripped_pagination")
        or report.fixes.get("stripped_broken_tags")
        or report.fixes.get("stripped_watermarks")
        or report.fixes.get("dropped_marker")
        or report.fixes.get("stub_docs_dropped")
    ):
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
    if report.fixes.get("cover_meta_repointed") or report.fixes.get(
        "cover_meta_removed"
    ):
        # Cover wiring is invisible to epubcheck (a dangling meta is not a
        # schema finding), so a cover-only repair always answers 'equal'
        # from the improvement gate. The cover-wiring ruling treats the
        # wiring as worth fixing: same bar as the lossy strips, no_worse
        # with the partial rule intact.
        if not no_worse(before, after):
            verdict = "reject"
        elif after.fatals > 0:
            verdict = "partial"
        else:
            verdict = "accept"
    if report.fixes.get("entries_renamed"):
        # The rename half's gain sits on epubcheck's WARNING axis
        # (PKG-010 space-in-filename advisories), which the improvement
        # gate does not measure: a rename-only repair answers 'noop'
        # from the gate. Same bar as the cover wiring: no_worse, with
        # the partial rule intact (a book that still has fatals is
        # never auto-applied).
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
    return Outcome(
        epub,
        status,
        before,
        after,
        summary,
        fixes=report.fixes,
        ncx_uid_synced=report.ncx_uid_synced,
        watermark_refusals=report.watermark_refusals,
    )


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


def _sweep_select(epubs, only: str, root: Path, checks: dict, *, quiet: bool):
    """Candidate selection driven by a live epubcheck sweep instead of an audit CSV.

    Each result is cached in `checks` so process_book reuses it as the book's
    `before` measurement instead of running epubcheck twice. A book the oracle
    cannot read stays a candidate (it cannot be proven clean); process_book will
    classify it as an error."""
    for epub in epubs:
        if not quiet:
            tqdm.write(f"[sweep] {epub.relative_to(root)}", file=sys.stderr)
        counts = run_epubcheck(epub)
        if counts is not None:
            checks[epub] = counts
        if only == "fatals":
            if counts is not None and counts.fatals == 0:
                continue
        elif counts == CheckResult(0, 0, 0):  # only == "all": skip clean books
            continue
        yield epub


def _sweep_select_parallel(
    epubs, only: str, root: Path, checks: dict, *, quiet: bool, workers: int
):
    """--workers N: k concurrent epubcheck workers over the sweep pass.

    epubcheck runs in a subprocess that releases the GIL, so threads
    parallelize the oracle honestly (this pass measured ~4.4 s/book on the
    2026-08-27 full-library walk). Books are checked in windows of `workers`;
    each window is consumed (and its keepers yielded) before the next one is
    submitted, so a --limit still stops the sweep after at most one window of
    overshoot instead of checking the whole tree. Within a window, results are
    applied in input order, so the candidate set and every emitted line match
    the serial sweep. The repair phase stays serial: that is where the shared
    workdir and the atomic-replacement contract live."""
    from concurrent.futures import ThreadPoolExecutor

    todo = list(epubs)
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=workers) as ex:
        progress = (
            None if quiet else tqdm(total=len(todo), desc="Sweeping", unit="book")
        )
        for start in range(0, len(todo), workers):
            window = todo[start : start + workers]
            futures = [ex.submit(run_epubcheck, epub) for epub in window]
            for epub, fut in zip(window, futures, strict=True):
                c = fut.result()
                if not quiet:
                    tqdm.write(f"[sweep] {epub.relative_to(root)}", file=sys.stderr)
                    progress.update(1)
                if c is not None:
                    checks[epub] = c
                if only == "fatals":
                    keep = c is None or c.fatals > 0
                else:  # only == "all": skip clean books
                    keep = c != CheckResult(0, 0, 0)
                if keep:
                    yield epub
        if progress is not None:
            progress.close()


# Everything a run did not (or could not) auto-repair; the --manual-list export.
_MANUAL_STATUSES = frozenset(
    {"nochange", "equal", "partial", "reject", "error", "unreadable"}
)


def _counts_dict(r: CheckResult | None) -> dict | None:
    return (
        None
        if r is None
        else {"fatals": r.fatals, "errors": r.errors, "warnings": r.warnings}
    )


def _unreadable_reason(e: Exception) -> str:
    """Split the sweep's `unreadable` bucket by disease.

    The bucket used to lump corruption together with not-a-zip/truncated/
    encrypted; the sub-reason names the disease so a CRC-damaged download
    (re-source) is distinguishable from a DRM'd or truncated one without
    leaving the sweep. zipfile names the broken entry in its CRC message.
    """
    msg = str(e)
    if isinstance(e, RuntimeError) and "encrypted" in msg:
        return "encrypted"
    if "CRC" in msg:
        return "corrupt_entry"
    if isinstance(e, EOFError) or "truncated" in msg.lower():
        return "truncated"
    if isinstance(e, zipfile.BadZipFile) and "not a zip file" in msg:
        return "not_a_zip"
    return "unreadable"


def _epubs_for_ids(root: Path, id_csv: str) -> list[Path] | None:
    """Resolve comma-separated Calibre book ids to EPUB paths via cquarry's
    get_format_path (the audit --id contract, sweep-shaped). Unresolvable ids
    warn and skip: one wrong id must not sink the batch."""
    from cquarry.db import CalibreDB

    db_path = root / "metadata.db"
    if not db_path.is_file():
        print("error: --id needs metadata.db in the library root", file=sys.stderr)
        return None
    try:
        db = CalibreDB(str(db_path))
    except Exception as e:
        print(f"error: cannot open {db_path}: {e}", file=sys.stderr)
        return None
    epubs: list[Path] = []
    seen: set[int] = set()
    try:
        for raw in id_csv.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                bid = int(raw)
            except ValueError:
                print(
                    f"warning: --id {raw!r} is not a number; skipped", file=sys.stderr
                )
                continue
            if bid in seen:
                continue
            seen.add(bid)
            try:
                epubs.append(Path(db.get_format_path(bid, "EPUB", verify=False)))
            except (ValueError, FileNotFoundError) as e:
                print(f"warning: book #{bid}: {e}; skipped", file=sys.stderr)
    finally:
        db.close()
    return epubs


def run_library(args) -> int:
    root = Path(args.path).expanduser()
    # cquarry-backed id resolution for --install-to-calibre: one lazy map
    # build per run, read-only against metadata.db.
    id_resolver = CalibreIdResolver(root)
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 1
    if args.only == "fatals" and not (args.audit or args.sweep):
        # Without fatal-count data (a CSV or a live sweep), silently scanning every
        # book is not what --only fatals promised.
        print("error: --only fatals needs --audit CSV or --sweep", file=sys.stderr)
        return 1
    if args.sweep and args.audit:
        print("error: --sweep and --audit are mutually exclusive", file=sys.stderr)
        return 1
    if getattr(args, "id", "") and getattr(args, "audit", None):
        print("error: --id and --audit are mutually exclusive", file=sys.stderr)
        return 1
    if args.sweep and args.no_validate:
        print(
            "error: --sweep is an epubcheck sweep; drop --no-validate", file=sys.stderr
        )
        return 1
    if args.sweep and args.only == "ncx":
        print(
            "error: --sweep does not apply to --only ncx (NCX-001 detection "
            "needs no epubcheck data)",
            file=sys.stderr,
        )
        return 1

    backup_dir = Path(args.backup).expanduser() if args.backup else None
    if backup_dir is not None:
        # A backup directory inside the library root would have its .epub-named
        # copies swept as candidates on the next run (and replaced in place):
        # backups must live outside the tree being swept (2026-09-08).
        # Validated before the epubcheck check: argument errors are argument
        # errors, whatever the environment looks like.
        try:
            backup_dir.resolve().relative_to(root.resolve())
        except ValueError:
            pass
        else:
            print(
                "error: --backup directory is inside the library root; its "
                "copies would be swept as candidates on the next run. Use a "
                "directory outside the library.",
                file=sys.stderr,
            )
            return 1

    validate = not args.no_validate
    if validate and not epubcheck_available():
        print(
            "error: epubcheck not found. install it or pass --no-validate.",
            file=sys.stderr,
        )
        return 1

    keep = getattr(args, "backup_keep", None)
    if keep is not None:
        if backup_dir is None and not args.backup_inplace:
            print(
                "error: --backup-keep needs --backup DIR or --backup-inplace.",
                file=sys.stderr,
            )
            return 1
        if keep < 2:
            print(
                "error: --backup-keep must be at least 2: the author original "
                ".bak is never deleted.",
                file=sys.stderr,
            )
            return 1

    audit_path = Path(args.audit).expanduser() if args.audit else None
    if audit_path is not None and not audit_path.is_file():
        print(f"error: no such audit file: {audit_path}", file=sys.stderr)
        return 1
    audit = _load_audit(audit_path) if audit_path else None
    wants_backup = backup_dir is not None or args.backup_inplace
    if wants_backup and not args.apply:
        print(
            "note: dry run -- --backup/--backup-inplace do nothing without --apply",
            file=sys.stderr,
        )
    if (
        args.apply
        and not wants_backup
        and (args.strip_pagination or args.strip_broken_tags or args.strip_watermarks)
    ):
        print(
            "WARNING: the --strip-* modes are lossy; strongly consider --backup DIR "
            "or --backup-inplace when applying them.",
            file=sys.stderr,
        )

    if args.id:
        scoped = _epubs_for_ids(root, args.id)
        if scoped is None:
            return 1
        all_epubs = scoped
    else:
        all_epubs = list(iter_epubs(root))
    audit_hits: list[Path] = []
    checks: dict[Path, CheckResult] = {}
    workers = getattr(args, "workers", 1)
    if workers is None:
        workers = 1
    if workers < 1:
        print(f"error: --workers must be >= 1, got {workers}", file=sys.stderr)
        return 1
    # The daemon pool grows with the worker count: N workers on one warm JVM
    # serialized behind its pipe, so --workers N silently degraded to serial
    # exactly when epubcheck was present (reported 2026-09-08).
    set_daemon_pool_size(workers)
    if not args.sweep and workers > 1:
        print(
            "note: --workers applies to the --sweep candidate pass; ignored here.",
            file=sys.stderr,
        )
    if args.sweep:
        if workers > 1:
            selected = _sweep_select_parallel(
                all_epubs, args.only, root, checks, quiet=args.quiet, workers=workers
            )
        else:
            iterator = (
                all_epubs
                if args.quiet
                else tqdm(all_epubs, desc="Sweeping", unit="book")
            )
            selected = _sweep_select(
                iterator, args.only, root, checks, quiet=args.quiet
            )
    else:
        selected = _select(all_epubs, args.only, audit, audit_hits)
    if args.limit is not None:
        if args.limit < 1:
            print(f"error: --limit must be >= 1 (got {args.limit})", file=sys.stderr)
            return 1
        # islice keeps the scan lazy: draining it pulls at most `limit` candidates, so
        # --only ncx --limit 20 still stops opening archives after the 20th instead of
        # probing every book in the tree. Draining it here (rather than iterating it in
        # the loop) is what lets the progress line show the real denominator: a tree
        # with 3 candidates under --limit 20 used to count "[1/20]".
        candidates = list(islice(selected, args.limit))
        header = f"limit={args.limit}"
    else:
        candidates = list(selected)
        header = f"{len(candidates)} candidate book(s)"

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Bindery {mode}: {header}, only={args.only}, validate={validate}\n")

    accepted = applied = rejected = equal = nochange = unvalidated = partials = 0
    errors = unreadable = processed = 0
    reasons: dict[str, int] = {}
    still_fatal = []
    records: list[Outcome] = []  # every processed book, for --json / --manual-list
    applied_paths: set[Path] = set()

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        repair_iterator = (
            candidates
            if args.quiet
            else tqdm(candidates, desc="Repairing", unit="book")
        )
        for epub in repair_iterator:
            processed += 1
            try:
                rel = epub.relative_to(root)
            except ValueError:
                # a path-shape mismatch (symlinked root, normalized vs raw)
                # must not abort the run with a bare traceback; the display
                # path falls back to the full path
                rel = epub
            try:
                o = process_book(
                    epub,
                    work,
                    validate,
                    flags=_flags_from_args(args),
                    before=checks.get(epub),
                )
            except (zipfile.BadZipFile, OSError, RuntimeError) as e:
                # One corrupt (non-zip, truncated, encrypted) book must not abort a
                # multi-hour sweep; report it under its sub-reason and keep going.
                reason = _unreadable_reason(e)
                unreadable += 1
                reasons[reason] = reasons.get(reason, 0) + 1
                records.append(
                    Outcome(epub, "unreadable", None, None, f"{reason}: {e}")
                )
                tqdm.write(f"  ERROR   {rel}\n            unreadable ({reason}): {e}")
                continue
            records.append(o)
            if o.status == "nochange":
                nochange += 1
                continue
            if o.status == "reject":
                rejected += 1
                tqdm.write(
                    f"  REJECT  {rel}\n            {o.before} -> {o.after}  {o.summary}"
                )
                continue
            if o.status == "equal":
                equal += 1
                continue
            if o.status == "error":
                errors += 1
                tqdm.write(f"  ERROR   {rel}\n            {o.summary}; not applied")
                continue
            if o.status == "partial":
                # Fewer fatals but not zero: a real improvement, but the book still will
                # not open, so it needs manual work. Never auto-applied.
                partials += 1
                still_fatal.append((rel, o.after))
                tqdm.write(
                    f"  PARTIAL {rel}\n            {o.before} -> {o.after}  {o.summary}"
                )
                continue

            # accept or unvalidated
            if o.status == "unvalidated":
                unvalidated += 1
                ba = ""
            else:
                # counted after the apply attempt succeeds (or in a dry run,
                # where nothing can fail): a failed apply used to leave the
                # book in `accepted` AND append a second record for its path
                ba = f"{o.before} -> {o.after}  "

            tag = "ACCEPT"
            if args.apply:
                try:
                    if backup_dir is not None or args.backup_inplace:
                        make_backup(epub, backup_dir, keep=args.backup_keep)
                    if args.install_to_calibre:
                        # The id comes from cquarry's metadata.db view — accurate
                        # even when the (id) directory was renamed.
                        install_format(epub, work / "repaired.epub", id_resolver)
                    else:
                        atomic_replace(epub, work / "repaired.epub")
                except OSError as e:
                    # A full disk or a permission error partway through a
                    # multi-hour run must not abort it raw with no summary,
                    # no JSON, and no record of what was already applied.
                    # Record the failure as an error Outcome and keep going;
                    # the accepted outcome is popped so the JSON carries one
                    # record per path.
                    errors += 1
                    records.pop()
                    # Forward o's structured fields: the repair itself
                    # happened (its fixes are facts about the candidate),
                    # and the record contract promises them on every
                    # outcome, apply-failed included.
                    records.append(
                        Outcome(
                            epub,
                            "error",
                            o.before,
                            o.after,
                            f"apply failed: {e}",
                            fixes=o.fixes,
                            ncx_uid_synced=o.ncx_uid_synced,
                            watermark_refusals=o.watermark_refusals,
                        )
                    )
                    tqdm.write(
                        f"  ERROR   {rel}\n            apply failed: {e}; not applied"
                    )
                    continue
                applied += 1
                applied_paths.add(epub)
                if o.status != "unvalidated":
                    accepted += 1
                tag = "APPLIED"
            elif o.status != "unvalidated":
                accepted += 1
            tqdm.write(f"  {tag}  {rel}\n            {ba}{o.summary}")

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
    for reason in sorted(reasons):
        print(f"  {reason}:      {reasons[reason]}")
    print(f"REJECTED:        {rejected}")
    if still_fatal:
        print(f"\nimproved but STILL FATAL ({len(still_fatal)}) -- manual follow-up:")
        for rel, after in still_fatal:
            print(f"  {after}  {rel}")
    if not args.apply:
        print(
            "\n(dry run -- no files written. re-run with --apply to replace in place.)"
        )

    if args.manual_list:
        manual = [o for o in records if o.status in _MANUAL_STATUSES]
        Path(args.manual_list).expanduser().write_text(
            "".join(f"{o.epub}\n" for o in manual)
        )
        print(
            f"manual list: {len(manual)} book(s) -> {args.manual_list}",
            file=sys.stderr,
        )
    if args.json:
        payload = {
            "mode": "apply" if args.apply else "dry-run",
            "root": str(root),
            "only": args.only,
            "validate": validate,
            "candidates": processed,
            "summary": {
                "accepted": accepted,
                "applied": applied,
                "partial": partials,
                "nochange": nochange,
                "equal": equal,
                "unvalidated": unvalidated,
                "errors": errors,
                "unreadable": unreadable,
                "rejected": rejected,
            },
            "books": [
                {
                    "path": str(o.epub),
                    "status": o.status,
                    "before": _counts_dict(o.before),
                    "after": _counts_dict(o.after),
                    "summary": o.summary,
                    "fixes": o.fixes,
                    "ncx_uid_synced": o.ncx_uid_synced,
                    "watermark_refusals": o.watermark_refusals,
                    "applied": o.epub in applied_paths,
                }
                for o in records
            ],
        }
        Path(args.json).expanduser().write_text(json.dumps(payload, indent=2) + "\n")

    # 2 lets scripts and cron distinguish "ran fine but some books are in trouble"
    # from a clean sweep (0) and a usage error (1).
    # Exit codes per the documented contract, unified on "trouble" (Brandon's
    # option-A call, 2026-09-10): a partial book (improved but still unable
    # to open) IS trouble -- the run itself succeeded, but a book still needs
    # a human. phase1 already mapped partial to 2; library and phase3 (which
    # propagates this rc) now agree with it.
    return 2 if (rejected + errors + unreadable + partials) > 0 else 0


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

    def emit(record: dict) -> None:
        if getattr(args, "json_path", None):
            Path(args.json_path).expanduser().write_text(
                json.dumps(record, indent=2) + "\n"
            )

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        try:
            o = process_book(
                src,
                work,
                validate=not args.no_validate,
                flags=_flags_from_args(args),
            )
        except (zipfile.BadZipFile, OSError, RuntimeError) as e:
            print(f"error: cannot read {src}: {e}", file=sys.stderr)
            emit(
                {
                    "mode": "repair",
                    "path": str(src),
                    "output": str(dst),
                    "status": "error",
                    "applied": False,
                    "before": None,
                    "after": None,
                    "summary": "",
                    "fixes": {},
                    "ncx_uid_synced": False,
                    "watermark_refusals": 0,
                    "error": f"{type(e).__name__}: {e}",
                }
            )
            return 1
        base_record = {
            "mode": "repair",
            "path": str(src),
            "output": str(dst),
            "status": o.status,
            "applied": False,
            "before": _counts_dict(o.before),
            "after": _counts_dict(o.after),
            "summary": o.summary,
            "fixes": o.fixes,
            "ncx_uid_synced": o.ncx_uid_synced,
            "watermark_refusals": o.watermark_refusals,
        }
        if o.status == "nochange":
            print("no applicable fixes; nothing written.")
            emit(base_record)
            return 0
        if o.status == "reject":
            print(
                f"repair REJECTED (regression): {o.before} -> {o.after}; nothing written."
            )
            emit(base_record)
            return 1
        if o.status == "error":
            print(
                "epubcheck failed; nothing written (pass --no-validate to skip the gate).",
                file=sys.stderr,
            )
            emit(base_record)
            return 1
        # Copy the exact bytes the gate accepted. Re-repairing src here would silently
        # drop the opt-in flags (--fix-ids, --reserialize, --strip-bad-attrs) and write
        # a file that differs from the one epubcheck validated.
        shutil.copyfile(work / "repaired.epub", dst)
        base_record["applied"] = True
        emit(base_record)
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


def _library_argv(argv: list[str]) -> argparse.Namespace:
    """Parse a constructed argv through the real subparser so a run verb
    drives the shipped library runner with exactly the flags a user's
    equivalent command would carry."""
    return build_parser().parse_args(argv)


def _load_json_file(path: Path) -> dict:
    return json.loads(path.read_text())


# Repair outcomes that mean a phase-1 book is in trouble: the gate rejected the
# candidate repair, the oracle failed, the file could not be read, or the book
# improved but still will not open (partial). A plain reject is a normal, good
# outcome for the repair sweep itself, but in a vetting report it is trouble:
# the book ships as-is and needs eyes before import.
_PHASE1_REPAIR_TROUBLE = frozenset({"reject", "partial"})
_PHASE1_REPAIR_ERROR = frozenset({"error", "unreadable"})


def _phase1_status(audit_rec: dict | None, repair_rec: dict | None) -> str:
    if audit_rec is not None and audit_rec.get("status") == "error":
        return "error"
    if repair_rec is not None and repair_rec["status"] in _PHASE1_REPAIR_ERROR:
        return "error"
    if repair_rec is not None and repair_rec["status"] in _PHASE1_REPAIR_TROUBLE:
        return "problem"
    if audit_rec is not None and audit_rec.get("status") == "problem":
        return "problem"
    return "clean"


def _run_phase1_audit(root: Path) -> tuple[list[dict], int]:
    """Stage 1: the read-only audit battery (corruption sweep, content
    battery, monolithic, completeness spot-check) exactly as
    `bindery audit all DIR` runs it, with the report captured through its
    own --json payload in a temp file."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "audit.json"
        rc = run_directory(
            root, list(ALL), DEFAULT_MIN_CHARS, DEFAULT_THIN_CHARS, json_path=out
        )
        records = _load_json_file(out)["books"] if out.exists() else []
    return records, rc


def _repair_sweep(argv: list[str]) -> tuple[dict, int]:
    """Drive the shipped library runner and capture its --json payload."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "repair.json"
        rc = run_library(_library_argv(argv + ["--json", str(out)]))
        payload = _load_json_file(out) if out.exists() else {}
    return payload, rc


def _phase1_decisions(books: list[dict], apply: bool) -> list[dict]:
    """Open questions a human would be asked, for the calling agent. The run
    verbs never prompt; --non-interactive only declares what is already
    true off a TTY."""
    decisions: list[dict] = []
    manual_wm: list[str] = []
    if not apply:
        pending: list[str] = []
        watermarked: list[str] = []
        for b in books:
            r = b["repair"]
            if r is None:
                continue
            fixes = r.get("fixes") or {}
            if fixes.get("stripped_watermarks") or fixes.get("dropped_marker"):
                watermarked.append(b["path"])
            if r.get("watermark_refusals"):
                manual_wm.append(b["path"])
            if r["status"] in ("accept", "partial"):
                pending.append(b["path"])
        if pending or watermarked:
            decisions.append(
                {
                    "decision": "apply_lossy",
                    "detail": (
                        f"{len(pending)} book(s) have gate-accepted repairs "
                        f"pending ({len(watermarked)} watermarked); this run "
                        "was read-only. Re-run with --apply-lossy to record "
                        "the lossy-strip consent and apply."
                    ),
                    "books": sorted(set(pending + watermarked)),
                }
            )
    else:
        for b in books:
            r = b["repair"]
            if r is not None and r.get("watermark_refusals"):
                manual_wm.append(b["path"])
    if manual_wm:
        decisions.append(
            {
                "decision": "manual_watermark_repair",
                "detail": (
                    f"{len(manual_wm)} book(s) carry a watermark the strip "
                    "refused to remove (the anchored stamp match was too "
                    "large to be safe, likely an unclosed stamp anchor "
                    "swallowing prose); remove it by hand."
                ),
                "books": sorted(set(manual_wm)),
            }
        )
    return decisions


def _probe_version(cmd: list[str]) -> str | None:
    """First output line of a --version probe, or None on any failure."""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (r.stdout or r.stderr).strip().splitlines()
    return line[0].strip() if line else None


def run_doctor(args) -> int:
    """`bindery doctor`: the environment self-check.

    The stranded stranger's first command: it must always run, never
    traceback, and always exit 0 (a diagnosis is not a failure; the findings
    are the output). It reports every install-shape fact the other verbs
    assume: Python floor and stack tier, the epubcheck oracle, Java (the
    daemon), the optional html5lib, and whether a Calibre library is
    discoverable here. Deliberately imports none of the VirInvictus stack:
    on a 3.12/3.13 install their absence IS the finding.
    """
    problems: list[str] = []

    print("bindery doctor")
    print()

    py = sys.version.split()[0]
    print(f"python:      {py} (floor 3.12)  [{sys.executable}]")

    stack = []
    for mod in ("vir_tui", "cquarry"):
        if importlib.util.find_spec(mod) is not None:
            stack.append(mod)
    if len(stack) == 2:
        print("stack:       full (vir_tui + cquarry importable; every verb runs)")
    elif py.startswith(("3.12", "3.13")):
        print(
            "stack:       stack-free core (vir_tui/cquarry absent; they install "
            "on 3.14+ only): repair works in full, audit/library/run print a note "
            "instead of running"
        )
    else:
        problems.append(
            "vir_tui/cquarry are not importable on this interpreter; audit, "
            "library, and run cannot work. Install on Python 3.14+ for the "
            "full stack."
        )
        print("stack:       MISSING (audit/library/run cannot run; see note)")
    print()

    if epubcheck_available():
        path = shutil.which("epubcheck") or ""
        ver = _probe_version(["epubcheck", "--version"]) or "(version probe failed)"
        print(f"epubcheck:   {ver}  [{path}]")
    else:
        problems.append(
            "epubcheck was not found on PATH: every validated run refuses to "
            "start. Install it (see README, Install) or pass --no-validate to "
            "skip the gate."
        )
        print("epubcheck:   NOT FOUND (the gate refuses to run without it)")
    print()

    if shutil.which("java"):
        ver = _probe_version(["java", "-version"]) or "(version probe failed)"
        print(f"java:        {ver} (the epubcheck daemon can run)")
    else:
        print(
            "java:        not found (optional: runs fall back to the per-book "
            "epubcheck subprocess)"
        )

    if importlib.util.find_spec("html5lib") is not None:
        print("html5lib:    present (--reserialize works)")
    else:
        print(
            "html5lib:    not installed (optional: only --reserialize needs it; "
            'uv tool install "bindery-cli[reserialize]")'
        )
    print()

    root = resolve_library_root()
    if root is not None:
        print(f"library:     {root} (metadata.db found; audit/library/run work here)")
    else:
        print(
            "library:     no metadata.db in this directory (run audit/library "
            "from a Calibre library, or pass a directory to audit)"
        )
    print()

    if problems:
        for p in problems:
            print(f"problem: {p}")
        print(f"\n{len(problems)} problem(s) found; everything else works.")
    else:
        print("no problems found: the full repair toolchain is usable.")
    return 0


def run_phase1(args) -> int:
    root = Path(args.path).expanduser()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 1
    if not next(iter_epubs(root), None):
        print(f"error: no .epub files under {root}", file=sys.stderr)
        return 1
    apply = args.apply_lossy
    if args.backup and not apply:
        print(
            "note: dry run -- --backup does nothing without --apply-lossy",
            file=sys.stderr,
        )
    mode = "APPLY-LOSSY" if apply else "READ-ONLY"
    print(f"Bindery run phase1 ({mode}): {root}\n")

    print(
        "== stage 1/2: audit battery (corruption sweep, content battery, monolithic, completeness) =="
    )
    audit_records, audit_rc = _run_phase1_audit(root)

    print(
        "\n== stage 2/2: gated repair sweep (epubcheck, watermarks, repairability) =="
    )
    argv = ["library", str(root), "--sweep", "--only", "all", "--all"]
    if apply:
        argv += ["--apply"]
        if args.backup:
            argv += ["--backup", args.backup]
    repair_payload, repair_rc = _repair_sweep(argv)

    rep_by_key = {
        str(Path(r["path"]).resolve()): r for r in repair_payload.get("books", [])
    }
    books: list[dict] = []
    for arec in audit_records:
        key = str(Path(arec["path"]).resolve())
        rrec = rep_by_key.pop(key, None)
        books.append(
            {
                "path": arec["path"],
                "audit": arec,
                "repair": rrec,
                "status": _phase1_status(arec, rrec),
            }
        )
    # A book the repair sweep saw but the audit's case-sensitive rglob missed
    # (a hand-added Book.EPUB) still lands in the report.
    for rrec in rep_by_key.values():
        books.append(
            {
                "path": rrec["path"],
                "audit": None,
                "repair": rrec,
                "status": _phase1_status(None, rrec),
            }
        )

    decisions = _phase1_decisions(books, apply)
    trouble = sum(1 for b in books if b["status"] != "clean")

    print("\n========== PHASE 1 SUMMARY ==========")
    print(
        f"books: {len(books)}  clean: {sum(1 for b in books if b['status'] == 'clean')}"
        f"  problem: {sum(1 for b in books if b['status'] == 'problem')}"
        f"  error: {sum(1 for b in books if b['status'] == 'error')}"
    )
    if decisions:
        print("decisions needed:")
        for d in decisions:
            print(f"  - {d['decision']}: {d['detail']}")
    else:
        print("decisions needed: none")

    if args.json:
        payload = {
            "mode": "phase1",
            "root": str(root),
            "non_interactive": bool(getattr(args, "non_interactive", False)),
            "apply_lossy": apply,
            "summary": {
                "books": len(books),
                "clean": sum(1 for b in books if b["status"] == "clean"),
                "problem": sum(1 for b in books if b["status"] == "problem"),
                "error": sum(1 for b in books if b["status"] == "error"),
                "repair": repair_payload.get("summary", {}),
            },
            "decisions_needed": decisions,
            "books": books,
        }
        Path(args.json).expanduser().write_text(json.dumps(payload, indent=2) + "\n")

    # Exit codes per the library contract: a flagged or rejected book is
    # trouble (2), a broken invocation is (1), an all-clean run is (0).
    if repair_rc == 1:
        return 1
    if trouble or audit_rc != 0 or repair_rc != 0:
        return 2
    return 0


def _phase3_decisions(books: list[dict]) -> list[dict]:
    decisions: list[dict] = []
    partial = [b["path"] for b in books if b["status"] == "partial"]
    if partial:
        decisions.append(
            {
                "decision": "manual_repair",
                "detail": (
                    f"{len(partial)} book(s) improved but still have fatals; "
                    "manual repair or re-source needed."
                ),
                "books": partial,
            }
        )
    errored = [b["path"] for b in books if b["status"] in ("error", "unreadable")]
    if errored:
        decisions.append(
            {
                "decision": "investigate",
                "detail": f"{len(errored)} book(s) could not be read or validated.",
                "books": errored,
            }
        )
    return decisions


def _pre_post_summary(books: list[dict]) -> tuple[dict, dict, dict]:
    """Sum epubcheck counts over the swept books, before vs after.

    The after total only sums real post-run states: an applied repair
    contributes its after measurement; an unapplied book still reads at its
    before counts, because its file was never replaced. A refused candidate's
    projected after-state (reject/partial) is returned separately so the
    caller can print it as its own labeled line instead of mixing it into
    the totals (the 2026-09-13 OMW incident: a clean run printed 0f -> 1f
    from one REGRESSION reject's projection).
    """
    fields = ("fatals", "errors", "warnings")

    def measured(b: dict, key: str) -> dict | None:
        v = b.get(key)
        return v if isinstance(v, dict) else None

    before: list[dict] = []
    after: list[dict] = []
    projected: list[dict] = []
    for b in books:
        pre = measured(b, "before")
        if pre is not None:
            before.append(pre)
        if b.get("applied"):
            post = measured(b, "after")
            if post is not None:
                after.append(post)
        elif pre is not None:
            # not applied: the file still reads at its before counts
            after.append(pre)
            post = measured(b, "after")
            if post is not None and b.get("status") in ("reject", "partial"):
                projected.append(post)
    return (
        {f: sum(d[f] for d in before) for f in fields},
        {f: sum(d[f] for d in after) for f in fields},
        {f: sum(d[f] for d in projected) for f in fields},
    )


def run_phase3(args) -> int:
    ids_csv = (getattr(args, "ids", "") or "").strip()
    if not ids_csv or set(ids_csv) <= {","}:
        # The phase-3 skill's step-10 scope warning, mechanized: a library-wide
        # sweep is a dedicated hours-long task, never a verb call. Exit 2, not
        # a prose warning an agent can skip.
        print(
            "refused: run phase3 is scoped by --ids; an unscoped library-wide "
            "sweep is a dedicated long task (hours), not a verb call. Pass the "
            "batch's comma-separated book ids.",
            file=sys.stderr,
        )
        return 2
    root = resolve_library_root()
    if root is None:
        print(
            "error: no metadata.db next to this script or in the current "
            "directory. Run from the library directory.",
            file=sys.stderr,
        )
        return 1
    scoped = _epubs_for_ids(root, ids_csv)
    if not scoped:
        print(
            f"error: no scoped books resolved from --ids {ids_csv!r} in {root}",
            file=sys.stderr,
        )
        return 1

    print(f"Bindery run phase3: {len(scoped)} scoped book(s) in {root}\n")
    argv = [
        "library",
        str(root),
        "--id",
        ids_csv,
        "--sweep",
        "--only",
        "all",
        "--all",
        "--apply",
        "--install-to-calibre",
    ]
    repair_payload, repair_rc = _repair_sweep(argv)
    books = repair_payload.get("books", [])
    before, after, projected = _pre_post_summary(books)
    decisions = _phase3_decisions(books)

    print("\n========== PHASE 3 SUMMARY ==========")
    print(f"scoped book(s): {len(scoped)}  swept: {len(books)}")
    print(
        f"before: {before['fatals']}f/{before['errors']}e/{before['warnings']}w"
        f"  after: {after['fatals']}f/{after['errors']}e/{after['warnings']}w"
    )
    if any(projected.values()):
        print(
            f"rejected projections (not applied): "
            f"{projected['fatals']}f/{projected['errors']}e/{projected['warnings']}w"
        )
    summary = repair_payload.get("summary", {})
    print(
        f"applied: {summary.get('applied', 0)}  nochange: "
        f"{summary.get('nochange', 0)}  clean-skipped: "
        f"{max(0, len(scoped) - len(books))}"
    )
    if decisions:
        print("decisions needed:")
        for d in decisions:
            print(f"  - {d['decision']}: {d['detail']}")
    else:
        print("decisions needed: none")

    if args.json:
        payload = {
            "mode": "phase3",
            "root": str(root),
            "ids": ids_csv,
            "non_interactive": bool(getattr(args, "non_interactive", False)),
            "summary": {
                "scoped": len(scoped),
                "swept": len(books),
                "before": before,
                "after": after,
                "rejected_projection": projected,
                "repair": summary,
            },
            "decisions_needed": decisions,
            "books": books,
        }
        Path(args.json).expanduser().write_text(json.dumps(payload, indent=2) + "\n")

    if repair_rc == 1:
        return 1
    return 2 if repair_rc != 0 else 0


# The repair-flag inventory, one table, two renderings: `_add_repair_flags`
# registers the short one-liners into argparse argument groups (the compact
# `--help`), and `--help-repairs` renders the long descriptions as the full
# reference. Adding a flag means adding a row here; the dests below are the
# contract tests/test_flags_wiring.py pins.
# Row shape: (dest, short one-liner, long reference text).
_REPAIR_FLAG_TABLE: dict[str, list[tuple[str, str, str]]] = {
    "safe opt-ins (markup-neutral)": [
        (
            "fix_ids",
            "rewrite invalid OPF/NCX ids (RSC-005), every reference in sync",
            "rewrite ids that are not valid XML names (digit-led, colon-bearing, "
            "or carrying any character outside the XML NameChar set, like the "
            "apostrophes calibre copies from filenames) in the OPF manifest and "
            "the NCX, updating every reference to them (spine idref, spine toc, "
            "item fallback, media-overlay, the EPUB 2 cover meta). The dc: "
            "metadata is never altered.",
        ),
        (
            "add_img_alt",
            'add alt="" to <img> elements missing the required attribute',
            'add alt="" to <img> elements missing the required attribute. '
            "Renders identically, but it adds markup the author never wrote, and "
            'an empty alt asserts "decorative" to a screen reader where a '
            "missing alt did not; hence opt-in.",
        ),
        (
            "reserialize",
            "rebuild still-malformed documents via html5lib",
            "rebuild content documents that are still malformed by re-parsing "
            "them with html5lib and re-emitting XHTML, closing unclosed non-void "
            "elements the regex transforms cannot. Runs only on documents that "
            "are not already well-formed, so good files are untouched.",
        ),
        (
            "strip_bad_attrs",
            "drop invalid attributes (digit-led names, unbound prefixes)",
            "drop attributes that make the XML unparseable: a name starting "
            'with a digit (a mangled 31="") or a namespaced name whose prefix '
            "is not declared anywhere in the document (Office VML v:shapes). "
            "Surgical and a no-op on well-formed files.",
        ),
        (
            "escape_unknown_entities",
            "&foo; -> &amp;foo; for names outside the HTML5 table",
            "escape entity names outside the HTML5 table, which renders exactly "
            "as browsers already render an unknown entity. Documents whose "
            "DOCTYPE carries an internal subset (which can declare custom "
            "entities) are skipped wholesale.",
        ),
    ],
    "structural repairs: markup and nesting": [
        (
            "fix_empty_body",
            "append &nbsp; to a strictly empty <body></body>",
            "append &nbsp; inside a strictly empty <body></body> (the 'body "
            "incomplete' error). Adds visible content the author never wrote; "
            "hence opt-in.",
        ),
        (
            "fix_missing_title",
            "inject <title>Unknown</title> when the head has none",
            "inject a <title>Unknown</title> fallback when the head has no "
            "usable title: an empty <title/> is filled in, an absent tag is "
            "added to the head.",
        ),
        (
            "fix_id_colons",
            'id="X:Y" colons and matching #X:Y fragments become underscores',
            'translate illegal colons in id="X:Y" and their matching internal '
            "#X:Y fragment references to underscores, so a ToC never dangles "
            "against the ids it references (the NCX's content src fragments "
            "follow the rename). Only the bare id attribute is in scope; the "
            "fragment of an external URL names a position in that other "
            "document and survives verbatim.",
        ),
        (
            "unwrap_block_in_inline",
            "unwrap a <span> illegally wrapping a block element",
            "unwrap a <span> that illegally wraps a block element "
            "(<div>/<p>/<blockquote>), keeping the block element and its text.",
        ),
        (
            "strip_invalid_value",
            "strip misplaced value= attributes from non-form elements",
            'strip misplaced value="..." attributes from elements like <div>, '
            "<span>, <p> where they are schema violations.",
        ),
        (
            "unwrap_illegal_tags",
            "delete illegal tags (<st>, <w>...) keeping text; styled names safe",
            "delete illegal/deprecated tags (<st>, <sentence>, <o>, <w>, "
            "<pagebreak>) while keeping their inner text. Any of those names a "
            "stylesheet styles as an element selector (class/id selectors do "
            "not count) is protected book-wide, so styled formatting is never "
            "destroyed.",
        ),
        (
            "strip_epub3_attrs",
            "scrub EPUB3-only attributes an EPUB2 package rejects (fixed set)",
            "scrub the EPUB3-only attributes epubcheck rejects on an EPUB2 "
            "package: page-progression-direction, epub:type, aria-label (a "
            "fixed, documented set; rendering is unchanged). Gated on the "
            "package version: inert on EPUB 3 books.",
        ),
        (
            "downgrade_epub3_tags",
            "figure/section to div, figcaption to p; semantic name kept as class",
            "downgrade EPUB3/HTML5 semantic elements to EPUB2 equivalents "
            "(figure/section to div, figcaption to p), keeping existing classes "
            "and appending the semantic name as the styling hook. Names a "
            "stylesheet styles as an element selector are protected book-wide. "
            "Gated on the package version: inert on EPUB 3 books.",
        ),
        (
            "fix_misnested_inline",
            "rewrite the drop-cap mis-nest <i><b>X</i></b> to <i><b>X</b></i>",
            "rewrite the Mobipocket drop-cap mis-nest <i><b>X</i></b> to "
            "<i><b>X</b></i> (em/strong variants, either tag order): the unique "
            "well-formed form of the same two spans. A punctuation run caught "
            "between the reversed closers (<i><b>xile</i>,</b>) survives "
            "outside both spans; a letter run there is refused (which span "
            "keeps the letters has no deterministic answer).",
        ),
        (
            "fix_stray_close",
            "remove end tags whose element has no open start (stray </div>)",
            "remove an end tag whose element has no open start tag anywhere "
            "above it (the stray extra </div> on a shell page, which cascades "
            "into 'body must be terminated by the matching end-tag'). A merely "
            "mis-nested pair (<div><span></div></span>) is never touched: a "
            "close is removed only when nothing by that name is open.",
        ),
        (
            "fix_unterminated_attr",
            "close attribute values open to the tag's own '>' (RSC-016)",
            "close an attribute value left open to the tag's own '>' "
            '(<p class="footnote> -> <p class="footnote">): the parser '
            "swallows prose into the value until the next quote and dies on the "
            "first '<' it meets. Guards keep it off legal >-bearing values: "
            "bare-word values only, and no quote may close before the next '<'.",
        ),
        (
            "fix_cdata_terminator",
            "complete truncated /*]]> closers in inline <style> blocks (CSS-008)",
            "complete a truncated CDATA-terminator comment in an inline <style> "
            "block: /*]]> becomes /*]]>*/. A comment-wrapped CDATA block whose "
            "closing */ the converter dropped leaves the CSS parser inside an "
            "unterminated comment: one CSS-008 'Premature end of file' per "
            "affected document. Only the malformed-terminator token class; "
            "invalid CSS stays unfixed, and stylesheet files are not touched.",
        ),
    ],
    "structural repairs: references and resources": [
        (
            "fix_page_map",
            'drop the OPF page-map attribute; class="pages" on NCX pageLists',
            "normalize legacy page-map markup: drop the non-standard page-map "
            'attribute from the OPF <spine> and add class="pages" to classless '
            "NCX <pageList> elements (epubcheck rejects both on older "
            "HarperCollins / Anna's Archive conversions).",
        ),
        (
            "prune_missing_resources",
            "remove references to files the archive does not contain (RSC-007)",
            "remove references to files the archive does not contain "
            "(RSC-007/PKG-010): dead <link> elements, anchors' href to absent "
            "files (anchor text preserved), absent <img> sources (replaced by "
            "their alt text when they carry one), and orphaned non-spine OPF "
            "manifest items, with every package edge that pointed at them "
            "rewritten. Spine documents are never pruned: a missing spine "
            "document is reported as a damaged fragment, not silently dropped.",
        ),
        (
            "strip_broken_anchors",
            "strip hrefs that cannot resolve; anchor text always preserved",
            "strip href attributes that cannot resolve, keeping the anchor text "
            "byte-for-byte: a #fragment the target document does not define "
            "(RSC-020/RSC-012; NCX navTargets keep the document target, so "
            "chapter navigation survives) and unresolvable URI schemes "
            "(kindle:, file:).",
        ),
        (
            "encode_url_spaces",
            "encode raw spaces in URLs; rename space-bearing entries (PKG-010)",
            "repair raw-space URLs two ways: percent-encode raw spaces in "
            "src/href attribute values across the package (a literal space is "
            "not a valid URL, RSC-020), and rename the archive entries whose "
            "names carry raw spaces to their underscore spellings, rewriting "
            "every reference to them (underscore, never percent-encoding: "
            "epubcheck decodes references before entry lookup). Accepted under "
            "the no-worse bar: the rename half's gain sits on the warning axis.",
        ),
        (
            "fix_svg_dup_ids",
            "rename duplicate glyph ids in .svg entries (first keeps its name)",
            "rename duplicate id values inside standalone .svg entries (old "
            "calibre SVG page renders repeat glyph ids within one document; the "
            "intake wave that found this carried 445 to 1,339 'Duplicate "
            '"glNNNN"\' errors per book). The first occurrence keeps its name, '
            "later ones gain _2/_3, and internal references are deliberately "
            "untouched: they already resolve to the first definition under "
            "every reader's first-match lookup. Content-document ids are styled "
            "by CSS selectors and targeted by anchors; they stay out of scope.",
        ),
    ],
    "structural repairs: package wiring": [
        (
            "fix_container",
            "generate container.xml when missing or stale (the gateway defect)",
            "generate META-INF/container.xml at the located OPF when the "
            "container is missing or names a file the archive does not contain. "
            "This is the gateway defect: epubcheck stays fatal while the OPF is "
            "unfindable, so no repair can be gate-accepted on such a book until "
            "the container exists.",
        ),
        (
            "fix_media_types",
            "normalize wrong manifest media-types (magic-byte confirmed)",
            "normalize wrong manifest media-type declarations (OPF-029, e.g. a "
            "jpg stamped image/png by an aggregator). Attribute-only and "
            "quote-preserving; it fires only when the file's magic bytes "
            "confirm the extension, so a misnamed-but-consistent file is never "
            "made worse.",
        ),
        (
            "fix_cover",
            "re-point or remove a dangling EPUB2 cover meta (no-worse bar)",
            'repair dangling EPUB2 cover wiring: a <meta name="cover"> whose '
            "content names no manifest item is re-pointed when the OPF guide's "
            "own cover reference names an existing item, and removed when "
            'nothing identifies it. EPUB3 properties="cover-image" is '
            "deliberately audit-only (guessing which image is the cover is not "
            "deterministic). Cover wiring is invisible to epubcheck, so "
            "cover-only repairs are accepted under the same no-worse bar the "
            "lossy strips use.",
        ),
        (
            "fix_comment_double_hyphen",
            "replace -- inside XML comments with an en-dash (RSC-016)",
            "replace -- sequences inside XML comments with en-dashes. -- is "
            "illegal inside an XML comment (epubcheck fatal RSC-016: the book "
            "opens in lenient readers but never passes epubcheck). The edit "
            "stays inside the comment bodies: text nodes and CDATA sections are "
            "never touched, and each comment's terminator is left intact.",
        ),
    ],
    "lossy strips (these delete converter-injected content; accepted on a no-worse bar)": [
        (
            "strip_pagination",
            "LOSSY: page numbers/running heads baked into the body text",
            "remove print page numbers and running headers that a PDF/OCR "
            "conversion baked into the body text as literal paragraphs (so they "
            "reflow into the middle of a sentence). It removes only that "
            "injected furniture, never the author's prose: where a number split "
            "a sentence it rejoins the two paragraphs, and it preserves roman "
            "chapter numbers, page-list nav anchors, and years. Three safety "
            "nets guard every edit (character conservation, tag balance, and "
            "the epubcheck no-regression check); any failure leaves the "
            "document untouched.",
        ),
        (
            "strip_broken_tags",
            "LOSSY: leaked close tags rendered as text (</p> without <)",
            "remove leaked HTML closing tags missing their open brackets (e.g. "
            "</p> rendered as raw text in the reader).",
        ),
        (
            "strip_watermarks",
            "LOSSY: producer stamps (OceanofPDF ...) and marker files",
            "remove known producer and distributor watermarks and stray marker "
            "files. It locates the stamp and deletes the outermost wrapper "
            "whose entire visible text is the watermark, so prose that merely "
            "mentions the URL is preserved. An inline stamp link is deleted "
            "only when it holds nothing but the stamp; a match too large to be "
            "safe is refused and reported for manual repair rather than "
            "deleted.",
        ),
        (
            "strip_stub_docs",
            "LOSSY: repeated placeholder spine docs, full manifest/NCX cascade",
            "drop spine documents whose entire visible body text is one "
            "identical short placeholder repeated across the spine: the "
            "Bookmate-style export whose chapters are all the same 'content "
            "unavailable' notice. The drop cascades (archive entries, manifest "
            "items, spine order, NCX navPoints, nav toc entries), so the book "
            "opens straight into its real chapters. Deliberately conservative "
            "(the pool is the <body> span, image-carrier divider pages are "
            "exempt) and a book whose every spine doc is the stub is refused "
            "outright: that book is empty and needs a re-source, never a "
            "repair.",
        ),
    ],
}


# The --help-repairs page is custom-rendered (not argparse output), so it
# paints itself with the exact CPython 3.14 argparse default theme
# (ColorfulTheme: bold blue headings, magenta prog, bold cyan long options
# -- read off real 3.14 output, so the page reads like the argparse-rendered
# -h beside it). This is the lattice-music lesson (6b88a52 there): a custom
# help renderer that prints plain silently loses the colorization argparse
# gives every other surface.
_THEME = {
    "heading": "\x1b[1;34m",
    "prog": "\x1b[35m",
    "long_option": "\x1b[1;36m",
    "reset": "\x1b[0m",
}


def _can_color_help() -> bool:
    """True exactly when argparse itself would colorize -h here: CPython
    3.14's own detection (_colorize.can_colorize: PYTHON_COLORS, NO_COLOR,
    FORCE_COLOR, TERM=dumb, isatty), so the two help levels can never
    disagree about color. Plain False below 3.14, where argparse has no
    colorization to match."""
    if sys.version_info < (3, 14):
        return False
    from _colorize import can_colorize

    return can_colorize()


def _repair_flag_reference(color: bool | None = None) -> str:
    """The full repair-flag reference: the _REPAIR_FLAG_TABLE rows with
    their long descriptions. `color=None` decides by the same gate argparse
    uses; the disabled style emits empty codes, so one layout path serves
    both faces and the strip invariant (colored, codes stripped == plain)
    holds by construction."""
    if color is None:
        color = _can_color_help()
    if color:
        heading, prog, option, reset = (
            _THEME["heading"],
            _THEME["prog"],
            _THEME["long_option"],
            _THEME["reset"],
        )
    else:
        heading = prog = option = reset = ""
    lines = [
        prog
        + "bindery"
        + reset
        + " repair-flag reference (shared by `bindery repair` and "
        "`bindery library`)",
        "",
        "The always-on core pass is exactly five well-formedness fixes, the NCX",
        "pipeline, and the mimetype fix. Everything below is opt-in, and every",
        "repair is epubcheck-gated: applied only when the measured result improved",
        "(lossy strips: only when it did not get worse). Library mode is dry-run",
        "by default; `--all` enables every flag below at once. The README and",
        "spec.md carry the full rationale for each repair.",
    ]
    for title, rows in _REPAIR_FLAG_TABLE.items():
        lines.append("")
        lines.append(heading + title + reset)
        lines.append("")
        for dest, _short, long_help in rows:
            lines.append("  " + option + "--" + dest.replace("_", "-") + reset)
            for para in long_help.split("\n"):
                lines.extend(
                    textwrap.wrap(
                        para,
                        width=88,
                        initial_indent="      ",
                        subsequent_indent="      ",
                    )
                    or ["     "]
                )
    return "\n".join(lines)


class _HelpRepairsAction(argparse.Action):
    """The second help level: print the full repair-flag reference and exit.

    Deliberately NOT a store_true action: tests/test_flags_wiring.py counts
    every store_true dest in _add_repair_flags as a repair selection, and
    this is a help affordance like -h, not a selection."""

    def __init__(
        self,
        option_strings,
        dest=argparse.SUPPRESS,
        default=argparse.SUPPRESS,
        help=None,
    ):
        super().__init__(
            option_strings=option_strings,
            dest=dest,
            default=default,
            nargs=0,
            help=help,
        )

    def __call__(self, parser, namespace, values, option_string=None):
        print(_repair_flag_reference())
        parser.exit(0)


def _add_repair_flags(p: argparse.ArgumentParser) -> None:
    """The fix-selection and gate flags shared by both subcommands,
    registered from _REPAIR_FLAG_TABLE into titled groups (the compact
    --help); --help-repairs prints the same inventory with the long
    descriptions."""
    for title, rows in _REPAIR_FLAG_TABLE.items():
        g = p.add_argument_group(title)
        for dest, short, _long in rows:
            g.add_argument(
                "--" + dest.replace("_", "-"),
                dest=dest,
                action="store_true",
                help=short,
            )
    g = p.add_argument_group("gate and selection")
    g.add_argument(
        "--all",
        action="store_true",
        help="enable every opt-in fix flag (safe, structural, and lossy)",
    )
    g.add_argument(
        "--no-validate",
        action="store_true",
        help="skip the epubcheck gate",
    )
    p.add_argument(
        "--help-repairs",
        action=_HelpRepairsAction,
        help="print the full repair-flag reference (every flag's long "
        "description, grouped) and exit",
    )


def run_audit_cmd(args: argparse.Namespace) -> int:
    selected = list(ALL) if args.mode == "all" else [args.mode]
    max_doc = args.max_doc_chars
    if args.min_chars > args.thin_chars:
        # the EMPTY threshold sitting above the THIN threshold silently
        # shadows every THIN advisory
        print(
            f"error: --min-chars ({args.min_chars}) must not exceed "
            f"--thin-chars ({args.thin_chars})",
            file=sys.stderr,
        )
        return 2
    if args.id is not None:
        if args.path:
            print("ERROR: --id audits a library book; drop the directory argument.")
            return 2
        id_list = [s.strip() for s in str(args.id).split(",") if s.strip()]
        if args.json and len(id_list) != 1:
            # Each run_single writes the file wholesale; two ids would leave
            # only the second book's report behind with no warning.
            print(
                "ERROR: --json with --id supports exactly one book id.",
                file=sys.stderr,
            )
            return 2
        rc = 0
        for raw in id_list:
            try:
                bid = int(raw)
            except ValueError:
                print(f"ERROR: --id {raw!r} is not a book id.", file=sys.stderr)
                rc |= 2
                continue
            rc |= run_single(
                bid,
                selected,
                args.min_chars,
                args.thin_chars,
                tag=args.tag,
                max_doc_chars=max_doc,
                json_path=args.json,
            )
        return rc
    if args.path:
        return run_directory(
            Path(args.path).expanduser(),
            selected,
            args.min_chars,
            args.thin_chars,
            max_doc_chars=max_doc,
            json_path=args.json,
        )
    return run_audit_library(
        selected,
        args.min_chars,
        args.thin_chars,
        tag=args.tag,
        max_doc_chars=max_doc,
        json_path=args.json,
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="bindery",
        description="Repair EPUBs, epubcheck-gated.",
        epilog="both `repair` and `library` accept the same opt-in repair-flag\n"
        "set (--all enables every one): `bindery repair --help` lists them\n"
        "grouped, and `bindery repair --help-repairs` prints the full\n"
        "reference.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--version", action="version", version=f"bindery {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser(
        "repair",
        help="repair a single EPUB to a new file",
        usage="bindery repair [options] path [output]",
        description="Repair one EPUB into a new file with the epubcheck-gated "
        "core pass: the five well-formedness fixes, the NCX pipeline, and the "
        "mimetype fix run always; every other repair is opt-in (grouped "
        "below) and applied only when epubcheck confirms the result improved.",
        epilog="examples:\n"
        "  bindery repair book.epub out.epub\n"
        "  bindery repair book.epub out.epub --all\n"
        "  bindery repair book.epub out.epub --fix-svg-dup-ids "
        "--fix-cdata-terminator\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    r.add_argument("path")
    r.add_argument("output", nargs="?")
    rc = r.add_argument_group("run control")
    rc.add_argument(
        "--force",
        action="store_true",
        help="overwrite the output file if it already exists",
    )
    rc.add_argument(
        "--json",
        dest="json_path",
        metavar="FILE",
        help="write a machine-readable report (status, before/after counts, "
        "fix summary) in the library --json per-book shape",
    )
    _add_repair_flags(r)
    r.set_defaults(func=run_repair)

    doc = sub.add_parser(
        "doctor",
        help="check the environment: Python stack tier, epubcheck, Java, "
        "html5lib, and Calibre-library discovery",
    )
    doc.set_defaults(func=run_doctor)

    audit = sub.add_parser(
        "audit",
        help="audit EPUB body text to detect non-schema content flaws (OCR damage, hardcoded page numbers, empty books, non-English text)",
    )
    audit.add_argument(
        "mode",
        choices=(
            "content",
            "pagenumbers",
            "emptytext",
            "ocr",
            "monolithic",
            "completeness",
            "cover",
            "tocdrift",
            "all",
        ),
        help="which audit to run",
    )
    audit.add_argument(
        "path",
        nargs="?",
        help="vet loose .epub files under this directory instead of the library",
    )
    audit.add_argument(
        "--min-chars",
        type=int,
        default=DEFAULT_MIN_CHARS,
        help="emptytext EMPTY threshold",
    )
    audit.add_argument(
        "--thin-chars",
        type=int,
        default=DEFAULT_THIN_CHARS,
        help="emptytext THIN advisory threshold",
    )
    audit.add_argument(
        "--max-doc-chars",
        type=int,
        default=DEFAULT_MAX_DOC_CHARS,
        help="monolithic FLAG threshold (chars in ONE content document)",
    )
    audit.add_argument(
        "--tag",
        default=None,
        metavar="TAG",
        help="after a library-mode audit, tag every flagged book in metadata.db "
        "via cquarry's write module (Calibre must be closed; books are queued "
        "for OPF regeneration automatically)",
    )
    audit.add_argument(
        "--id",
        metavar="BOOK_IDS",
        default=None,
        help="audit library book(s) by Calibre id — one id or a comma-separated "
        "list (fetched via cquarry's single-entity get_book; cannot be "
        "combined with a directory)",
    )
    audit.add_argument(
        "--json",
        metavar="FILE",
        default=None,
        help="write a machine-readable report of the run to FILE: one record "
        "per book with per-analyzer verdicts (problem/status/details), in the "
        "library --json shape; with --id, exactly one book id",
    )
    audit.set_defaults(func=run_audit_cmd)

    lib = sub.add_parser(
        "library",
        help="scan/repair a Calibre library tree",
        usage="bindery library [options] path",
        description="Scan a Calibre library tree for repair candidates and "
        "repair them in place. Dry run by default; --apply atomically "
        "replaces accepted books. Shares the repair-flag set with `bindery "
        "repair` (--all enables every opt-in).",
        epilog="examples:\n"
        "  bindery library ~/docs/Calibre\\ Library --sweep\n"
        "  bindery library ~/docs/Calibre\\ Library --id 1234 --apply "
        "--backup /tmp/bindery-backups --install-to-calibre\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    lib.add_argument("path")
    rc = lib.add_argument_group("run control")
    rc.add_argument(
        "--apply",
        action="store_true",
        help="atomically replace accepted books in place (default: dry run)",
    )
    rc.add_argument(
        "--only",
        choices=("fatals", "ncx", "all"),
        default="all",
        help="restrict to books with fatals, NCX-001 mismatch, or all (default)",
    )
    rc.add_argument(
        "--limit", type=int, help="process at most N candidates (for sampling)"
    )
    rc.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="concurrent epubcheck workers for the --sweep candidate pass "
        "(default 1: serial, unchanged). The repair phase stays serial",
    )
    rc.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the per-book progress line on stderr",
    )
    cs = lib.add_argument_group("candidate selection")
    cs.add_argument(
        "--sweep",
        action="store_true",
        help="select candidates via a live epubcheck sweep instead of an "
        "--audit CSV (each sweep result doubles as that book's 'before' "
        "measurement)",
    )
    cs.add_argument(
        "--audit", help="audit CSV (fatals,errors,warnings,path) to filter candidates"
    )
    cs.add_argument(
        "--id",
        default="",
        metavar="IDS",
        help="comma-separated Calibre book ids to scope the sweep to "
        "(resolved via cquarry's get_format_path; mutually exclusive with "
        "--audit)",
    )
    bk = lib.add_argument_group("backups")
    bk.add_argument("--backup", help="directory to mirror backups into before --apply")
    bk.add_argument(
        "--backup-inplace",
        action="store_true",
        help="with --apply, write a .epub.bak beside each replaced file",
    )
    bk.add_argument(
        "--backup-keep",
        type=int,
        metavar="N",
        help="with backups, rotate: at most N backup files per book "
        "(minimum 2; the author original .bak is never deleted). Opt-in: "
        "without it the rotation grows unbounded",
    )
    out = lib.add_argument_group("output and library integration")
    out.add_argument(
        "--json",
        metavar="FILE",
        help="write a machine-readable JSON report of the run to FILE",
    )
    out.add_argument(
        "--manual-list",
        metavar="FILE",
        help="write the paths of books that were not auto-repaired "
        "(nochange/equal/partial/reject/error/unreadable), one per line",
    )
    out.add_argument(
        "--install-to-calibre",
        action="store_true",
        help="with --apply, re-register the format natively in metadata.db "
        "through cquarry's write module (remove + add in one transaction) "
        "instead of a bare filesystem replace",
    )
    _add_repair_flags(lib)
    lib.set_defaults(func=run_library)

    run = sub.add_parser(
        "run",
        help="the acquisition run slices: phase-1 pre-import vetting, phase-3 "
        "post-import scoped repair (the run verbs wrap shipped behavior; no "
        "new repair classes)",
    )
    run_sub = run.add_subparsers(dest="run_cmd", required=True)

    p1 = run_sub.add_parser(
        "phase1",
        help="the EPUB pre-import vetting slice over a directory of loose "
        "files: corruption sweep, epubcheck, content battery, monolithic, "
        "watermark detection, repairability. Read-only without --apply-lossy",
    )
    p1.add_argument("path")
    p1.add_argument(
        "--json",
        metavar="FILE",
        help="write a machine-readable report of the run to FILE "
        "(per-book audit verdicts, repair outcome, decisions_needed)",
    )
    p1.add_argument(
        "--apply-lossy",
        dest="apply_lossy",
        action="store_true",
        help="apply gate-accepted repairs (the --all set, lossy strips "
        "included); this IS the recorded lossy-strip consent. Without it the "
        "verb is read-only",
    )
    p1.add_argument(
        "--backup",
        metavar="DIR",
        help="with --apply-lossy, mirror originals into DIR before replacing "
        "(backups belong OUTSIDE the vetted directory)",
    )
    p1.add_argument(
        "--non-interactive",
        action="store_true",
        help="declare the caller is not a TTY: run verbs never prompt, open "
        "questions surface in the JSON as decisions_needed",
    )
    p1.set_defaults(func=run_phase1)

    p3 = run_sub.add_parser(
        "phase3",
        help="the post-import scoped repair sweep: library --id <ids> --sweep "
        "--only all --apply --all --install-to-calibre with a pre/post "
        "summary. Refuses unscoped library-wide sweeps (exit 2); run from "
        "the library directory",
    )
    p3.add_argument(
        "--ids",
        default="",
        metavar="IDS",
        help="comma-separated Calibre book ids for this batch (REQUIRED in "
        "practice: the verb mechanically refuses an unscoped sweep)",
    )
    p3.add_argument(
        "--json",
        metavar="FILE",
        help="write a machine-readable report of the run to FILE "
        "(pre/post counts, per-book outcome, decisions_needed)",
    )
    p3.add_argument(
        "--non-interactive",
        action="store_true",
        help="declare the caller is not a TTY: run verbs never prompt, open "
        "questions surface in the JSON as decisions_needed",
    )
    p3.set_defaults(func=run_phase3)
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
    except ModuleNotFoundError as e:
        # The VirInvictus stack (vir-tui, cquarry) is marker-gated to Python
        # 3.14+ since the v0.40.0 floor drop: a 3.12/3.13 install runs the
        # single-book repair core, and the audit/library surfaces say what
        # they need instead of dying with a traceback. Anything else missing
        # is a real bug and still raises.
        if e.name not in ("vir_tui", "cquarry"):
            raise
        print(
            f"this command needs the {e.name} library, which currently "
            "installs only on Python 3.14+; run it under Python 3.14",
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        # A library run can take a long time; end a Ctrl-C cleanly instead of with a
        # traceback. In-flight work is safe: the original is only ever touched by the
        # atomic os.replace.
        print("\ninterrupted", file=sys.stderr)
        return 130
