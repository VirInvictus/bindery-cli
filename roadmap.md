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
- [x] **Unclosed non-void elements** (`<p>`, `<span>`, `<div>`, `<blockquote>`, `<body>`)
      (v0.3.0): `--reserialize` rebuilds malformed docs via html5lib. Clears 10 of the 12
      markup-fatal library books to zero fatals.
- [x] **Foreign-content fatals** (v0.4.0): `--strip-bad-attrs` drops invalid attributes
      (digit-led names, unbound namespace prefixes), clearing the Office-VML (`v:shapes`)
      and broken-SVG (`31=""`) holdouts. The whole audit fatal set is now resolved.
- [ ] Report-only JSON output, and a `--manual-list` export for the partial/nochange set
- [ ] Re-audit integration: run an epubcheck sweep and feed results straight into
      candidate selection without a separate CSV step

## Phase 4: opt-in lossy content repair (shipped, v0.5.0)

*A deliberate, fenced-off exception to "semantics-preserving only": remove content a
converter injected, never content the author wrote.*

- [x] **`--strip-pagination`**: remove print page numbers and running headers a PDF/OCR
      conversion baked into the body text (they reflow mid-sentence). Rejoins paragraphs
      only on a confident interrupt (lowercase continuation, word split); deletes the
      whole arabic page-number layer when a book has both a dense number run and several
      interrupts; preserves roman chapter numbers, page-list anchors, and years. Guarded
      by character conservation, tag balance, and a `no_worse` epubcheck bar (the strip's
      gain is invisible to epubcheck). Validated on /tmp copies: Fingersmith 372 removed,
      Animal Farm 54 removed (roman chapters intact), zero prose characters changed.

## Phase 3: integration (maybe)

- [ ] Calibre post-import hook or plugin so books are repaired on add
- [ ] Optional metadata.db nudge so Calibre notices the new file size without a manual
      Quality Check sync
