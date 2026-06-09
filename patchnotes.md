# Patch notes

## v0.3.0 (2026-06-09)

- **New `--reserialize` (structural repair).** Rebuilds content documents that are still
  not well-formed by re-parsing them with html5lib (lenient HTML5 recovery, like a
  browser) and re-emitting XHTML. This closes unclosed non-void elements (`<p>`, `<div>`,
  `<span>`, `<blockquote>`, `<body>`) that the regex transforms cannot, and even recovers
  some corrupted tag names. It runs only on documents that are not already well-formed,
  so good files are left byte-for-byte unchanged, and only when opted in.
- **New dependency: html5lib** (for `--reserialize` only). Imported lazily; every other
  mode runs with no third-party dependency. This is the one approved exception to the
  stdlib-first design.
- Verified on the 12 markup-fatal library books: `--reserialize --fix-ids` clears 10 of
  12 to zero fatals (content preserved; the 2 holdouts are Office-VML and broken-SVG
  foreign content). All gate-accepted.

## v0.2.0 (2026-06-09)

- **Hardened `self_close_void`.** The matcher now requires a word boundary after the
  element name and is quote-aware, fixing a bug where `<col` matched inside `<colgroup>`
  (self-closing it and orphaning the end-tag) and where a `>` inside an attribute value
  ended the tag early. This introduced fatals on 19 books during the library run; the
  gate rejected them, and they are now repaired cleanly. Already-self-closed tags are
  left untouched and not counted.
- **New `--fix-ids` (RSC-005).** Optionally rewrite manifest item ids that are not valid
  XML names (start with a digit, contain a colon) and update their spine references.
  Off by default, since it touches the OPF; the dc: metadata is never altered. On a
  real book this cleared 36 bad ids (794 to 723 errors), gate-accepted.

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
