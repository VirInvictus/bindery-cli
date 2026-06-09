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
- [ ] **Harden `self_close_void`**: on the real-library NCX run it introduced fatals on
      19 of 523 books (e.g. *Purr*: 0 fatals to 4), which the gate correctly rejected.
      The regex is matching a `>` it should not (likely inside an attribute value or a
      comment/CDATA). Tighten it so those books become fixable instead of rejected.
- [ ] **Digit-led id fix (RSC-005 "must be an XML name")**: Calibre-converted books carry
      manifest `id`s that start with a digit (invalid NCName); prefix the `id` and update
      its spine `idref` in lockstep. OPF-touching, so it needs the same gate and care.
      Reader-invisible polish; only worth it for a green-er library.
- [ ] Report-only JSON output, and a `--manual-list` export for the partial/nochange set
- [ ] Re-audit integration: run an epubcheck sweep and feed results straight into
      candidate selection without a separate CSV step

## Phase 3: integration (maybe)

- [ ] Calibre post-import hook or plugin so books are repaired on add
- [ ] Optional metadata.db nudge so Calibre notices the new file size without a manual
      Quality Check sync
