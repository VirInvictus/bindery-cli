# CLAUDE.md (Bindery)

Per-project guidance. Overrides the global file where they conflict.

## What this is

A focused EPUB repair and diagnostic tool: deterministic well-formedness fixes, gated by epubcheck,
with atomic in-place replacement in a Calibre library. Sibling to oceanstrip. Born from
the 2026 library audit (see the user memory `calibre-library-epubcheck-audit`).

## Hard constraints

- **Minimal Dependencies, pinned.** Runtime deps are exactly: `tqdm` (progress/output),
  `vir-tui` (shared TUI rendering) and `cquarry` (read-only Calibre `metadata.db` access,
  adopted in v0.16.0) — the two git deps are PINNED to exact commit hashes in
  pyproject.toml, never floated onto a branch. `html5lib` remains the one approved
  heavy-parsing exception (used only for the `--reserialize` fix, imported lazily so every
  other mode runs without it). Tests use the standard `unittest` framework. epubcheck is an
  external CLI dependency, and `calibredb` is required for the `--install-to-calibre`
  feature. Before adding any further Python package, stop and ask.
- **Semantics-preserving transforms by default, everything else fenced behind a flag.**
  The always-on core is exactly five well-formedness fixes (prolog junk, duplicate
  `xmlns`, bare `&`, named entities, void self-closing) plus the NCX pipeline; every core
  fix must render identically to the author's intent: never add, remove, or reorder
  visible content. Two kinds of exceptions exist, both opt-in and off unless requested:
  * **Structural repairs** (`--fix-empty-body`, `--fix-missing-title`, `--fix-id-colons`,
    `--unwrap-block-in-inline`, `--strip-invalid-value`, `--unwrap-illegal-tags`;
    transforms.py, threaded through epub.py): they alter markup structure or fabricate
    minimal content. v0.14–v0.16 ran these unconditionally, which broke this rule;
    v0.17.0 restored it. `--unwrap-illegal-tags` additionally protects any illegal-tag
    name that an EPUB stylesheet styles as an element selector (`css_protected_tags`,
    book-wide, inline `<style>` blocks included).
  * **Lossy strips** (`--strip-pagination`, `--strip-broken-tags`, `--strip-watermarks`;
    pagination.py, watermark.py): remove only what a converter injected (page numbers,
    running headers, leaked tags, watermarks), fenced behind character conservation, tag
    balance, and the epubcheck no-regression bar.
  Do not let any NEW fix touch content without its own flag; if a candidate repair cannot
  be made deterministically safe, it does not belong here — report it for manual repair
  instead.
- **The gate is the safety contract.** Never apply a repair epubcheck has not accepted.
  Respect the two-mode logic in `validate.gate` (fatal-fixing tolerates error unmasking;
  error-cleanup does not). The lossy strips (`--strip-pagination`, `--strip-broken-tags`,
  `--strip-watermarks`) are accepted by `validate.no_worse` instead (their gain is
  invisible to epubcheck, so it only forbids a regression, never demands a measured
  improvement). The structural opt-ins go through the normal `gate`: their gain IS
  visible (they clear errors), so a run with no measurable improvement is a noop and
  nothing is applied. Changing either bar means re-running the library dry run.
- **Library writes are sacred.** Replacement must stay atomic (temp in same dir, then
  `os.replace`), touch only the `.epub`, preserve mode, and be dry-run by default. Calibre integrations should seamlessly swap formats using `calibredb add_format -replace`.
  Never write to the library without `-apply`. Test every change on `/tmp` copies first.

## Layout

- `src/bindery/transforms.py`: pure `str -> (str, int)` text transforms (including `strip_broken_tags`).
- `src/bindery/pagination.py`: the opt-in lossy page-number strip (runhead detection, page-layer decision, block-centric removal/merge, safety nets).
- `src/bindery/watermark.py`: the opt-in lossy watermark strip (anchored and anchorless signature removal).
- `src/bindery/reserialize.py`: structural repair via `html5lib`.
- `src/bindery/audit.py`: read-only body-text audits (`content`, `pagenumbers`, `emptytext`, `ocr`) behind the `audit` subcommand (v0.15.0).
- `src/bindery/epub.py`: archive rewrite, NCX uid sync, RepairReport, mismatch detection, and the opt-in structural-repair plumbing (including the CSS precondition scan).
- `src/bindery/validate.py`: epubcheck wrapper, the `gate` (improvement) and `no_worse` (no-regression, for the lossy strips) acceptance bars.
- `src/bindery/library.py`: Calibre walk, atomic replace, backups, and native Calibre replacement.
- `src/bindery/cli.py`: `repair` and `library` subcommands, including `--all` and `--install-to-calibre`.
- `tests/`: transforms, end-to-end repair, atomic replace, pagination, watermarks.

## Conventions

- Type hints, `from __future__ import annotations`, ruff for lint and format.
- `VERSION` lives in `src/bindery/__init__.py`, mirrored in `pyproject.toml`. Bump both.
- Run tests with `./run_tests.sh`.

## Validation workflow

The library is real data. The loop is always: dry run on `/tmp` copies, inspect the
report, then apply with backups. epubcheck is the oracle; a repaired book that still has
fatals is `partial` and must be left for manual work, never auto-applied.
