# Bindery roadmap

## Phase 1: deterministic repair engine (shipped, v0.1.0)

- [x] Transforms: self-close void elements, named entity to numeric, escape bare `&`,
      strip prolog junk, drop duplicate `xmlns`
- [x] NCX-001 fix (dtb:uid sync to OPF unique identifier)
- [x] mimetype ordering/compression repair on rewrite
- [x] Two-mode epubcheck gate (fatal-fixing vs error-cleanup)
- [x] `repair` (single file) and `library` (batch) CLI modes
- [x] Atomic in-place library replacement with optional backups; dry run by default
- [x] `--only {fatals,ncx,all}` and `--audit CSV` candidate filtering
- [x] stdlib `unittest` suite (transforms, archive rewrite, atomic replace)
- [x] Validated on the real library: 24 of ~40 fatal books fully de-fataled with zero
      epubcheck regressions; the rest reported for manual follow-up

## Phase 2: the long tail (planned)

- [ ] Unclosed **non-void** elements (`<p>`, `<span>`, `<body>`, `<blockquote>`): needs
      a forgiving HTML parser that re-serializes as XHTML. Evaluate a stdlib
      `html.parser` rebuild vs. asking to add `lxml`.
- [ ] Strip unbound namespace cruft (`v:shapes` and friends from Office HTML)
- [x] **Harden `self_close_void`** (v0.2.0): word-boundary + quote-aware matcher, fixing
      the `<col`-in-`<colgroup>` bug that introduced fatals on 19 books.
- [x] **Digit-led / colon id fix (RSC-005)** (v0.2.0): `--fix-ids` renames invalid
      manifest ids and updates their spine references. Off by default (OPF-touching).
- [ ] **Unclosed non-void elements** (`<p>`, `<span>`, `<div>`, `<blockquote>`, `<body>`):
      the remaining markup-fatal class. Needs a forgiving HTML reserializer. Dependency
      decision pending (lxml / html5lib vs a conservative stdlib html.parser inserter).
      Note: every affected book already reads fine in lenient readers, so this is polish.
- [ ] Report-only JSON output, and a `--manual-list` export for the partial/nochange set
- [ ] Re-audit integration: run an epubcheck sweep and feed results straight into
      candidate selection without a separate CSV step

## Phase 3: integration (maybe)

- [ ] Calibre post-import hook or plugin so books are repaired on add
- [ ] Optional metadata.db nudge so Calibre notices the new file size without a manual
      Quality Check sync
