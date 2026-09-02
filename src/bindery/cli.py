"""Command-line interface for Bindery."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from tqdm import tqdm

from . import __version__
from .audit import (
    ALL,
    DEFAULT_MAX_DOC_CHARS,
    DEFAULT_MIN_CHARS,
    DEFAULT_THIN_CHARS,
    run_directory,
    run_single,
)
from .audit import (
    run_library as run_audit_library,
)
from .epub import ncx_uid_mismatch, repair_epub
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
)


@dataclass
class Outcome:
    epub: Path
    # accept | partial | reject | nochange | equal | unvalidated | error | unreadable
    status: str
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
    strip_brokentags: bool = False,
    strip_watermarks: bool = False,
    escape_entities: bool = False,
    img_alt: bool = False,
    empty_body: bool = False,
    missing_title: bool = False,
    id_colons: bool = False,
    block_in_inline: bool = False,
    invalid_value: bool = False,
    illegal_tags: bool = False,
    page_map: bool = False,
    strip_epub3_attrs: bool = False,
    downgrade_epub3: bool = False,
    before: CheckResult | None = None,
) -> Outcome:
    """Repair `epub` into a temp file and decide whether the result is acceptable.

    `before` is a pre-measured epubcheck result for `epub` (from a --sweep pass),
    saving a second multi-second run; when None it is measured here."""
    repaired = workdir / "repaired.epub"
    report = repair_epub(
        epub,
        repaired,
        fix_ids=fix_ids,
        reserialize=reserialize,
        strip_attrs=strip_attrs,
        strip_pagination=strip_pagination,
        strip_brokentags=strip_brokentags,
        strip_watermarks=strip_watermarks,
        escape_entities=escape_entities,
        img_alt=img_alt,
        empty_body=empty_body,
        missing_title=missing_title,
        id_colons=id_colons,
        block_in_inline=block_in_inline,
        invalid_value=invalid_value,
        illegal_tags=illegal_tags,
        page_map=page_map,
        strip_epub3_attrs=strip_epub3_attrs,
        downgrade_epub3=downgrade_epub3,
    )
    if not report:
        return Outcome(epub, "nochange", None, None, "no applicable fixes")

    summary = ", ".join(f"{k}:{v}" for k, v in report.fixes.items())
    if report.ncx_uid_synced:
        summary = (summary + ", " if summary else "") + "ncx_uid_synced"

    if not validate:
        return Outcome(epub, "unvalidated", None, None, summary)

    if before is None:
        before = run_epubcheck(epub)
    after = run_epubcheck(repaired)
    if before is None or after is None:
        # Validation was requested but the oracle failed (crash, timeout, unparsable
        # output). This is "error", not "unvalidated": the gate did not accept the
        # repair, so it must never be applied. Only --no-validate skips the gate.
        return Outcome(epub, "error", before, after, summary + " (epubcheck failed)")
    verdict = gate(before, after)
    if (
        report.fixes.get("stripped_pagination")
        or report.fixes.get("stripped_broken_tags")
        or report.fixes.get("stripped_watermarks")
        or report.fixes.get("dropped_marker")
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
    if args.sweep:
        iterator = (
            all_epubs if args.quiet else tqdm(all_epubs, desc="Sweeping", unit="book")
        )
        selected = _sweep_select(iterator, args.only, root, checks, quiet=args.quiet)
    else:
        selected = _select(all_epubs, args.only, audit, audit_hits)
    if args.limit is not None:
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
            rel = epub.relative_to(root)
            try:
                o = process_book(
                    epub,
                    work,
                    validate,
                    fix_ids=args.fix_ids or getattr(args, "all", False),
                    reserialize=args.reserialize or getattr(args, "all", False),
                    strip_attrs=args.strip_bad_attrs or getattr(args, "all", False),
                    strip_pagination=args.strip_pagination
                    or getattr(args, "all", False),
                    strip_brokentags=args.strip_broken_tags
                    or getattr(args, "all", False),
                    strip_watermarks=args.strip_watermarks
                    or getattr(args, "all", False),
                    escape_entities=args.escape_unknown_entities
                    or getattr(args, "all", False),
                    img_alt=args.add_img_alt or getattr(args, "all", False),
                    empty_body=args.fix_empty_body or getattr(args, "all", False),
                    missing_title=args.fix_missing_title or getattr(args, "all", False),
                    id_colons=args.fix_id_colons or getattr(args, "all", False),
                    block_in_inline=args.unwrap_block_in_inline
                    or getattr(args, "all", False),
                    invalid_value=args.strip_invalid_value
                    or getattr(args, "all", False),
                    illegal_tags=args.unwrap_illegal_tags
                    or getattr(args, "all", False),
                    page_map=args.fix_page_map or getattr(args, "all", False),
                    strip_epub3_attrs=args.strip_epub3_attrs
                    or getattr(args, "all", False),
                    downgrade_epub3=args.downgrade_epub3_tags
                    or getattr(args, "all", False),
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
                accepted += 1
                ba = f"{o.before} -> {o.after}  "

            tag = "ACCEPT"
            if args.apply:
                if backup_dir is not None or args.backup_inplace:
                    make_backup(epub, backup_dir)
                if args.install_to_calibre:
                    # The id comes from cquarry's metadata.db view — accurate
                    # even when the (id) directory was renamed.
                    install_format(epub, work / "repaired.epub", id_resolver)
                else:
                    atomic_replace(epub, work / "repaired.epub")
                applied += 1
                applied_paths.add(epub)
                tag = "APPLIED"
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
                    "applied": o.epub in applied_paths,
                }
                for o in records
            ],
        }
        Path(args.json).expanduser().write_text(json.dumps(payload, indent=2) + "\n")

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
                fix_ids=args.fix_ids or getattr(args, "all", False),
                reserialize=args.reserialize or getattr(args, "all", False),
                strip_attrs=args.strip_bad_attrs or getattr(args, "all", False),
                strip_pagination=args.strip_pagination or getattr(args, "all", False),
                strip_brokentags=args.strip_broken_tags or getattr(args, "all", False),
                strip_watermarks=args.strip_watermarks or getattr(args, "all", False),
                escape_entities=args.escape_unknown_entities
                or getattr(args, "all", False),
                img_alt=args.add_img_alt or getattr(args, "all", False),
                empty_body=args.fix_empty_body or getattr(args, "all", False),
                missing_title=args.fix_missing_title or getattr(args, "all", False),
                id_colons=args.fix_id_colons or getattr(args, "all", False),
                block_in_inline=args.unwrap_block_in_inline
                or getattr(args, "all", False),
                invalid_value=args.strip_invalid_value or getattr(args, "all", False),
                illegal_tags=args.unwrap_illegal_tags or getattr(args, "all", False),
                page_map=args.fix_page_map or getattr(args, "all", False),
                strip_epub3_attrs=args.strip_epub3_attrs or getattr(args, "all", False),
                downgrade_epub3=args.downgrade_epub3_tags
                or getattr(args, "all", False),
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
        help="also rewrite invalid ids in the OPF manifest and the NCX (RSC-005)",
    )
    p.add_argument(
        "--add-img-alt",
        action="store_true",
        help='add alt="" to <img> elements missing the required attribute '
        "(renders identically; asserts 'decorative' to screen readers)",
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
        "--escape-unknown-entities",
        action="store_true",
        help="escape entity names outside the HTML5 table (&foo; -> &amp;foo;), "
        "rendering as browsers already render them; documents with a DOCTYPE "
        "internal subset (which can declare custom entities) are skipped",
    )
    p.add_argument(
        "--fix-empty-body",
        action="store_true",
        help="append &nbsp; to a strictly empty <body></body> (adds visible "
        "content; hence opt-in)",
    )
    p.add_argument(
        "--fix-missing-title",
        action="store_true",
        help="inject a <title>Unknown</title> fallback when the head has none",
    )
    p.add_argument(
        "--fix-id-colons",
        action="store_true",
        help='translate illegal colons in id="X:Y" and their matching #X:Y '
        "fragment references to underscores",
    )
    p.add_argument(
        "--unwrap-block-in-inline",
        action="store_true",
        help="unwrap a <span> that illegally wraps a block element (<div>/<p>/"
        "<blockquote>), keeping the block and its text",
    )
    p.add_argument(
        "--strip-invalid-value",
        action="store_true",
        help='strip misplaced value="..." attributes from non-form elements',
    )
    p.add_argument(
        "--unwrap-illegal-tags",
        action="store_true",
        help="delete illegal/deprecated tags (<st>, <sentence>, <o>, <w>, "
        "<pagebreak>) keeping inner text; any tag a stylesheet styles as an "
        "element selector is protected book-wide",
    )
    p.add_argument(
        "--fix-page-map",
        dest="fix_page_map",
        action="store_true",
        help="normalize legacy page-map markup: drop the non-standard page-map "
        'attribute from the OPF spine and add class="pages" to classless NCX '
        "<pageList> elements (epubcheck rejects both)",
    )
    p.add_argument(
        "--strip-epub3-attrs",
        dest="strip_epub3_attrs",
        action="store_true",
        help="scrub the EPUB3-only attributes epubcheck rejects on an EPUB2 "
        "package (page-progression-direction, epub:type, aria-label; fixed set)",
    )
    p.add_argument(
        "--downgrade-epub3-tags",
        dest="downgrade_epub3_tags",
        action="store_true",
        help="downgrade EPUB3/HTML5 semantic elements to EPUB2 equivalents "
        "(figure/section to div, figcaption to p; semantic name kept as a "
        "class; names a stylesheet styles as an element selector are "
        "protected book-wide)",
    )
    p.add_argument(
        "--strip-pagination",
        action="store_true",
        help="LOSSY: remove print page numbers/running headers baked into the body "
        "text by a bad conversion, rejoining sentences they split (epubcheck-gated, "
        "accepted when no worse)",
    )
    p.add_argument(
        "--strip-broken-tags",
        action="store_true",
        help="LOSSY: remove leaked HTML closing tags missing their open bracket (e.g. </p> rendered as text) (epubcheck-gated)",
    )
    p.add_argument(
        "--strip-watermarks",
        action="store_true",
        help="LOSSY: remove producer/distributor watermarks (e.g. OceanofPDF) (epubcheck-gated)",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="enable every opt-in fix flag (the safe structural repairs above "
        "and all lossy strips)",
    )
    p.add_argument("--no-validate", action="store_true", help="skip the epubcheck gate")


def run_audit_cmd(args: argparse.Namespace) -> int:
    selected = list(ALL) if args.mode == "all" else [args.mode]
    max_doc = args.max_doc_chars
    if args.id is not None:
        if args.path:
            print("ERROR: --id audits a library book; drop the directory argument.")
            return 2
        rc = 0
        for raw in str(args.id).split(","):
            raw = raw.strip()
            if not raw:
                continue
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
            )
        return rc
    if args.path:
        return run_directory(
            Path(args.path).expanduser(),
            selected,
            args.min_chars,
            args.thin_chars,
            max_doc_chars=max_doc,
        )
    return run_audit_library(
        selected, args.min_chars, args.thin_chars, tag=args.tag, max_doc_chars=max_doc
    )


def build_parser() -> argparse.ArgumentParser:
    # Generate an attractive, perfectly aligned help block for the shared flags
    dummy = argparse.ArgumentParser(add_help=False, usage=argparse.SUPPRESS)
    group = dummy.add_argument_group(
        "shared fix flags (can be passed to either repair or library)"
    )
    _add_repair_flags(group)
    lib_group = dummy.add_argument_group("library-specific integration")
    lib_group.add_argument(
        "--install-to-calibre",
        action="store_true",
        help="with --apply, use calibredb to natively replace the format in the Calibre database instead of filesystem replace",
    )
    shared_help = dummy.format_help().strip()

    ap = argparse.ArgumentParser(
        prog="bindery",
        description="Repair EPUBs, epubcheck-gated.",
        epilog=shared_help,
        formatter_class=argparse.RawDescriptionHelpFormatter,
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

    audit = sub.add_parser(
        "audit",
        help="audit EPUB body text to detect non-schema content flaws (OCR damage, hardcoded page numbers, empty books, non-English text)",
    )
    audit.add_argument(
        "mode",
        choices=("content", "pagenumbers", "emptytext", "ocr", "monolithic", "all"),
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
    audit.set_defaults(func=run_audit_cmd)

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
    lib.add_argument(
        "--sweep",
        action="store_true",
        help="select candidates via a live epubcheck sweep instead of an --audit CSV "
        "(each sweep result doubles as that book's 'before' measurement)",
    )
    lib.add_argument(
        "--json",
        metavar="FILE",
        help="write a machine-readable JSON report of the run to FILE",
    )
    lib.add_argument(
        "--manual-list",
        metavar="FILE",
        help="write the paths of books that were not auto-repaired "
        "(nochange/equal/partial/reject/error/unreadable), one per line",
    )
    lib.add_argument(
        "--install-to-calibre",
        action="store_true",
        help="with --apply, use calibredb to natively replace the format in the Calibre database instead of filesystem replace",
    )
    lib.add_argument(
        "--id",
        default="",
        metavar="IDS",
        help="comma-separated Calibre book ids to scope the sweep to "
        "(resolved via cquarry's get_format_path; mutually exclusive with --audit)",
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
