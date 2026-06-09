# CLAUDE.md (Bindery)

Per-project guidance. Overrides the global file where they conflict.

## What this is

A focused EPUB repair tool: deterministic well-formedness fixes, gated by epubcheck,
with atomic in-place replacement in a Calibre library. Sibling to oceanstrip. Born from
the 2026 library audit (see the user memory `calibre-library-epubcheck-audit`).

## Hard constraints

- **Stdlib only.** No third-party runtime or test dependencies. Tests are stdlib
  `unittest`. epubcheck is an external CLI dependency, not a Python one. If something
  seems to need a Python package (e.g. lxml for the Phase 2 long tail), stop and ask.
- **Semantics-preserving transforms only.** Every fix must render identically to the
  author's intent (self-close void, numeric entities, escaped `&`). Never add, remove,
  or reorder visible content. If a fix cannot be made deterministically safe, it does
  not belong here; report it for manual repair instead.
- **The gate is the safety contract.** Never apply a repair epubcheck has not accepted.
  Respect the two-mode logic in `validate.gate` (fatal-fixing tolerates error unmasking;
  error-cleanup does not). Changing the gate means re-running the library dry run.
- **Library writes are sacred.** Replacement must stay atomic (temp in same dir, then
  `os.replace`), touch only the `.epub`, preserve mode, and be dry-run by default.
  Never write to the library without `--apply`. Test every change on `/tmp` copies first.

## Layout

- `src/bindery/transforms.py`: pure `str -> (str, int)` text transforms.
- `src/bindery/epub.py`: archive rewrite, NCX uid sync, RepairReport, mismatch detection.
- `src/bindery/validate.py`: epubcheck wrapper and the acceptance `gate`.
- `src/bindery/library.py`: Calibre walk, atomic replace, backups.
- `src/bindery/cli.py`: `repair` and `library` subcommands.
- `tests/`: transforms, end-to-end repair, atomic replace.

## Conventions

- Type hints, `from __future__ import annotations`, ruff for lint and format.
- `VERSION` lives in `src/bindery/__init__.py`, mirrored in `pyproject.toml`. Bump both.
- Run tests with `./run_tests.sh`.

## Validation workflow

The library is real data. The loop is always: dry run on `/tmp` copies, inspect the
report, then apply with backups. epubcheck is the oracle; a repaired book that still has
fatals is `partial` and must be left for manual work, never auto-applied.
