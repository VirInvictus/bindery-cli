# CLAUDE.md (Bindery)

Per-project guidance. Overrides the global file where they conflict.

## What this is

A focused EPUB repair tool: deterministic well-formedness fixes, gated by epubcheck,
with atomic in-place replacement in a Calibre library. Sibling to oceanstrip. Born from
the 2026 library audit (see the user memory `calibre-library-epubcheck-audit`).

## Hard constraints

- **Stdlib-first.** The core (transforms, archive rewrite, gate, library replace) has
  minimal third-party dependencies (`tqdm` for output rendering) and stdlib `unittest` tests. The one approved exception is
  **html5lib**, used only by the opt-in `-reserialize` structural repair and imported
  lazily (so every other mode runs without it). epubcheck is an external CLI dependency,
  not a Python one. Before adding any further Python package, stop and ask.
- **Semantics-preserving transforms only, with ONE carved-out exception.** Every core
  fix must render identically to the author's intent (self-close void, numeric entities,
  escaped `&`): never add, remove, or reorder visible content. The deliberate exceptions
  are the opt-in `-strip-pagination`, `-strip-broken-tags`, and `-strip-watermarks` modes (`pagination.py`, `transforms.py`, and `watermark.py`), which remove
  visible content the author never wrote (a PDF/OCR converter's baked-in page numbers
  and running headers) and rejoins sentences they split. It is lossy by design, off
  unless requested, and fenced behind three independent safety nets (character
  conservation, tag balance, the epubcheck no-regression bar). Do not let any OTHER fix
  remove or reorder content; if a new fix cannot be made deterministically safe, it does
  not belong here, report it for manual repair instead.
- **The gate is the safety contract.** Never apply a repair epubcheck has not accepted.
  Respect the two-mode logic in `validate.gate` (fatal-fixing tolerates error unmasking;
  error-cleanup does not). The lossy `-strip-pagination`, `-strip-broken-tags`, and `-strip-watermarks` modes are accepted by
  `validate.no_worse` instead (its gain is invisible to epubcheck, so it only forbids a
  regression, never demands a measured improvement). Changing either means re-running the
  library dry run.
- **Library writes are sacred.** Replacement must stay atomic (temp in same dir, then
  `os.replace`), touch only the `.epub`, preserve mode, and be dry-run by default. Calibre integrations should seamlessly swap formats using `calibredb add_format -replace`.
  Never write to the library without `-apply`. Test every change on `/tmp` copies first.

## Layout

- `src/bindery/transforms.py`: pure `str -> (str, int)` text transforms (including `strip_broken_tags`).
- `src/bindery/pagination.py`: the opt-in lossy page-number strip (runhead detection, page-layer decision, block-centric removal/merge, safety nets).
- `src/bindery/watermark.py`: the opt-in lossy watermark strip (anchored and anchorless signature removal).
- `src/bindery/reserialize.py`: structural repair via `html5lib`.
- `src/bindery/epub.py`: archive rewrite, NCX uid sync, RepairReport, mismatch detection.
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
