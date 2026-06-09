# Patch notes

## v0.1.0 (2026-06-09)

First release. A focused EPUB repair tool, sibling to oceanstrip, born from auditing a
3713-book Calibre library where 38 books carried fatal parse errors.

- Deterministic, semantics-preserving transforms: self-close void elements, named
  entity to numeric reference, escape bare `&`, strip pre-prolog junk, collapse a
  duplicated root `xmlns`.
- NCX-001 fix: sync `toc.ncx` `dtb:uid` to the OPF unique identifier.
- mimetype ordering/compression repair on rewrite.
- Two-mode epubcheck gate that understands fatal unmasking: when a book had fatals,
  success is fewer fatals, and the error count rising as hidden errors surface is not
  treated as a regression.
- `repair` (single file) and `library` (batch) CLI modes. Library mode is a dry run by
  default; `--apply` replaces accepted books in place, atomically, with optional backups.
  Only the `.epub` is touched, so Calibre's Quality Check sync can reconcile the database.
- stdlib `unittest` suite; no third-party dependencies.

Validated on the real library: 24 of ~40 fatal books fully de-fataled (they now open),
6 partially improved and flagged for manual finish, the rest left untouched, and zero
epubcheck regressions.
