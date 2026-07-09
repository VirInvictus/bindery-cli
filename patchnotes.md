# Patch notes

## v0.9.0 (2026-07-09)

Two new fixes, born from a Dan Brown import batch whose books opened fine but
carried 89 and 225 epubcheck errors.

- **`--fix-ids` now covers the NCX.** Old conversions stamp navPoint ids from
  UUIDs (digit-led) or colon-bearing strings; epubcheck rejects every one as
  RSC-005. Invalid NCX ids are renamed with the same `id_` scheme as manifest
  ids. NCX ids are internal to the NCX (nothing in the OPF or content documents
  references them), so the rename needs no cross-file bookkeeping. Counted as
  `fix_ncx_ids` in reports.
- **New: `--add-img-alt` (opt-in).** Adds `alt=""` to `<img>` elements missing
  the required attribute. Rendering is unchanged (an empty alt draws nothing),
  but this is the one transform that adds markup the author never wrote, and
  `alt=""` asserts "decorative" to a screen reader where a missing alt did not;
  hence opt-in, never a core transform. Quote-aware, idempotent, CDATA and
  comments never rewritten; counted as `img_alt_added`.

Real-world validation: the two motivating books went 89 errors to 0 and 225 to
190 (the remainder are dead NCX fragment identifiers, a fix candidate we
deliberately passed on), both accepted by the normal gate.


## v0.8.0 (2026-07-02)

**New: `--escape-unknown-entities` (opt-in).** The last fix candidate from the
v0.5.0 audit. An entity name that is neither XML-predefined nor in the HTML5 table
stays a fatal "entity not declared" (the core `fix_named_entities` deliberately
leaves it); with this flag such references are escaped (`&foo;` -> `&amp;foo;`),
which renders exactly as browsers already render an unknown entity: the literal
text.

- Conditionally semantics-preserving, hence opt-in: rendering is identical except
  against a document whose DOCTYPE internal subset *declares* the entity, so any
  document carrying an internal subset (`<!DOCTYPE ... [`) is skipped wholesale.
- CDATA sections and comments are never rewritten (the standing transform
  invariant), the normal epubcheck gate applies, and the fix is idempotent (the
  `&amp;` it emits is predefined and stays put on a re-run).
- Available on both `repair` and `library`; counted as `escape_unknown_entities`
  in reports.

## v0.7.0 (2026-07-02)

Phase 2 closes out: the audit workflow is now self-contained, and the mimetype fix
joins the core repair set.

- **`--sweep`: re-audit integration.** `library --only fatals --sweep` runs a live
  epubcheck sweep for candidate selection, replacing the separate CSV step (and with
  it the whole audit-path-mismatch bug class). Each sweep result is reused as that
  book's before-measurement, so no book is epubchecked twice. Mutually exclusive with
  `--audit` and `--no-validate`; `--limit` keeps the sweep lazy.
- **`--json FILE`: machine-readable run report.** Per-book path, status, before/after
  counts, fix summary, and applied flag, plus the summary totals, for scripting and
  cross-run comparison.
- **`--manual-list FILE`: the manual follow-up export.** One path per line for every
  book the run did not (or could not) auto-repair: nochange, equal, partial, reject,
  error, unreadable.
- **A missing `mimetype` entry is added, and wrong or whitespace-padded content is
  normalized** to the OCF constant `application/epub+zip`. The content is
  spec-constant, so this is deterministic and semantics-preserving; it is counted
  (`mimetype_added` / `mimetype_normalized`) and gate-checked like any other fix.
- **spec.md documents void end-tag swallowing** (the 5.6 gap): `self_close_void` also
  removes orphaned end tags for void elements (`</br>`, `</col>`), which are always
  invalid and cannot change what renders. Behavior unchanged since v0.4.2; the spec
  now says so.

## v0.6.0 (2026-07-02)

The Phase 5 audit sweep: three confirmed safety bugs fixed, packaging honesty, and a
round of CLI hardening and UX. Every fix ships with a stdlib-unittest regression test.

**Safety and correctness:**

- **`--strip-pagination` can no longer auto-apply a still-fatal book.** The `no_worse`
  acceptance bar for the lossy strip used to overwrite the gate's verdict outright, so
  a book going 3 fatals -> 1 fatal was classified `accept` and `library --apply`
  atomically replaced a book that still does not open. `no_worse` now relaxes only the
  improvement demand: a result with remaining fatals is demoted to `partial` and never
  applied. This closes a hole in the hard rule that still-fatal books are manual work.
- **One corrupt `.epub` no longer aborts an entire `library` run.** A non-zip,
  truncated, or encrypted book raised out of the sweep and killed a multi-hour run with
  a traceback. Each book is now guarded individually; unreadable books are reported,
  counted in a new `unreadable:` summary line, and skipped. `repair` prints a clean
  error for the same case instead of a traceback.
- **The pagination strip no longer deletes `<p id=...>` navigation targets.** An id on
  the removed paragraph itself (the common `<p id="page7">7</p>` page-anchor shape)
  vanished with the block, breaking NCX page-lists and internal links; only inner
  `<a id=...>` anchors were rescued. Both removal paths now preserve it: delete-only
  keeps an emptied `<p id=...></p>` shell, and a merge hoists the id as an empty
  anchor. Single-quoted ids are recognized too.

**Packaging:**

- **`html5lib` is now the optional extra the docs always promised.** It moved from
  `dependencies` to `[project.optional-dependencies]`, so a plain install is genuinely
  stdlib-only; `bindery[reserialize]` pulls it in for `--reserialize`.

**CLI hardening:**

- **`repair` labels partial output honestly.** A book whose fatals were reduced but not
  cleared was written with a `repaired:` line that read as fixed; it now prints
  `PARTIAL (still has fatals; needs manual work):`.
- **`repair` refuses to overwrite an existing output file** unless `--force` is given.
- **Single-quoted attributes are visible to the OPF/NCX regexes.** The NCX-001 sync,
  OPF location, and unique-identifier lookup all required double quotes, so a
  single-quoting toolchain made them silently no-op. All accept either quote now.
- **`Book.EPUB` is found.** The library scan matches the `.epub` suffix
  case-insensitively (Calibre emits lowercase, but a hand-added file should not be
  invisible).

**UX:**

- **Progress output for long runs.** A `[123/4051] Author/Title.epub` line per book
  goes to stderr, so a mostly-clean library no longer shows hours of silence; stdout
  stays a clean report. `--quiet` suppresses it.
- **A warning fires when the audit CSV matches zero scanned books** (the silent
  path-mismatch trap that read as "library is clean"). Paths are resolved on both
  sides first, so relative-vs-absolute mismatches no longer occur at all.
- **Backup flags warn when inert.** `--backup`/`--backup-inplace` without `--apply`
  print a note; `--apply --strip-pagination` without any backup flag prints a loud
  recommendation (the one lossy mode deserves a backup).
- **`library` exits 2 when any book was rejected, unreadable, or failed epubcheck**,
  so scripts and cron can detect trouble; 0 is a clean sweep, 1 a usage error.
- **`--limit` limits the scan, not just the work.** Candidates are consumed lazily, so
  `--only ncx --limit 20` stops opening archives after the 20th candidate instead of
  probing every book in the tree.

## v0.5.0 (2026-06-14)

**New: `--strip-pagination` (opt-in, lossy).** Removes print page numbers and running headers that a PDF/OCR conversion baked into the body text as literal paragraphs, which reflow into the middle of sentences ("where the hay cart 16 was taking him"). This is the first mode that removes visible content, so it is a deliberate, fenced-off exception to Bindery's semantics-preserving rule: off by default, and accepted by a new `no_worse` bar (no net-new fatals or errors) instead of the improvement-demanding `gate`, because a baked page number is valid markup that epubcheck cannot see.

- Removes only injected furniture, never the author's prose. Where a number split a sentence it rejoins the two paragraphs (closing up a word split like `compli-`/`mentary`); page-list `id` anchors are hoisted into the merged paragraph so navigation still resolves.
- A book is treated as paginated only when it has both a dense run of standalone arabic numbers (>= 20) and several confident mid-sentence interrupts (>= 3), so a merely chapter-numbered book is never touched. Roman chapter/front-matter numerals and year-range values are preserved.
- Three independent safety nets guard every edit, any failure leaving the document unchanged: character conservation (no prose character lost or fabricated), `<p>`/`<a>` tag balance, and the epubcheck no-regression check.
- Validated on /tmp copies of the real library: Fingersmith 372 numbers removed (zero left), Animal Farm 54 removed with all ten roman chapter numbers intact, zero prose characters changed in either, epubcheck no worse.

## v0.4.2 (2026-06-11)

- **EPUB 3 namespace prefixes are preserved.** `--strip-bad-attrs` no longer drops perfectly valid EPUB 3 prefixed attributes. It now correctly parses `epub:prefix` and `prefix` declarations (e.g. `epub:prefix="math: ..."`) instead of strictly requiring an `xmlns:` declaration to bind a prefix.
- **Nested orphaned void end tags are swallowed globally.** `self_close_void` now completely strips *all* explicit end tags for void elements globally (like `</br>` and `</img>`) after self-closing the start tag, preventing fatal XML parse errors when tools generate deeply nested orphaned void end tags (e.g., `<br><br></br></br>`).

## v0.4.1 (2026-06-11)

Bugfix and cleanup sweep. No new fixes or flags; several of these close real holes in
the safety contract.

- **CDATA sections and comments are never rewritten.** The transforms used to escape
  `&`, convert entities, and self-close `<br>` inside `<![CDATA[...]]>` and
  `<!-- -->`, where that content is literal and already legal XML; escaping a `&`
  in CDATA-wrapped CSS/JS changes what renders. All body-text transforms (including
  `--strip-bad-attrs`) now skip these spans. This is now a spec invariant.
- **Hyphenated custom elements are no longer mangled.** `-`, `:`, and `.` are valid
  XML name characters but not word characters, so the v0.2.0 `\b` boundary still let
  `<col` match inside `<col-group>` and self-close it. The matcher now requires
  whitespace, `/`, or `>` after the element name.
- **The OPF is located via `META-INF/container.xml`** instead of "first `.opf` in
  archive order", so a stray duplicate OPF can no longer win and sync the wrong uid
  into the NCX. Falls back to the old behavior when container.xml is absent.
- **`--fix-ids` updates all references, not just the spine.** `fallback=`,
  `media-overlay=`, and the EPUB 2 `<meta name="cover" content="...">` also point at
  manifest ids; leaving them stale orphaned fallback chains and broke Calibre's cover
  detection when the cover item's id was renamed.
- **Duplicate entry names survive the rewrite.** `ZipFile.read(name)` returns the
  first entry's bytes for every same-named duplicate (seen in broken EPUBs); entries
  are now read individually.
- **Multi-codepoint entities are converted** (`&NotEqualTilde;` and friends become
  one numeric reference per codepoint) instead of being skipped.
- **`atomic_replace` cleans up after itself and syncs the directory.** A failure
  mid-replace no longer leaves a `.bindery.tmp` in the library, and the rename is
  fsynced so a crash right after a replace cannot lose it.
- `--limit 0` now means "process nothing" instead of being ignored; Ctrl-C during a
  long run exits cleanly (130) instead of dumping a traceback.

- **`repair` now writes the exact bytes the gate accepted.** It used to produce the
  final output with a second repair pass that dropped `--fix-ids`, `--reserialize`,
  and `--strip-bad-attrs`, so the written file could be missing the very repairs
  epubcheck had just validated. The gated temp file is now copied to the output.
- **An epubcheck failure no longer bypasses the gate.** When epubcheck crashed, timed
  out, or produced unparsable output mid-run, the book was classified `unvalidated`
  and `library --apply` replaced it as if it had passed. Such books are now a distinct
  `error` outcome: reported, counted, and never applied or written. Only an explicit
  `--no-validate` skips the gate.
- **Unchanged archive entries are copied byte-for-byte.** Eligible entries were
  decoded with `utf-8/replace` and re-encoded even when no transform fired, which
  would silently swap non-UTF-8 bytes for U+FFFD in otherwise untouched files.
- **`library --only fatals` without `--audit` is an error.** It used to silently
  treat every book in the library as a candidate; the README always said `fatals`
  needs the audit CSV, and now the CLI enforces it.
- Audit CSVs without a header row no longer lose their first book; blank rows are
  skipped instead of crashing the load.
- NCX `dtb:uid` replacement inserts the uid literally (a uid containing `\1` was
  previously parsed as a regex replacement template), and per-file change accounting
  no longer leaks across multiple `.ncx` entries in one archive.
- Cleanup: shared CLI flags are defined once for both subcommands, the transform
  pipeline is properly typed, and the spec/README document the new `error` outcome
  and the byte-preservation guarantee.

## v0.4.0 (2026-06-09)

- **New `--strip-bad-attrs`.** Drops attributes that are invalid XML and so make a
  document unparseable: a name starting with a digit (e.g. a mangled `31=""`) or a
  namespaced name whose prefix is never declared (e.g. Office VML `v:shapes` with no
  `xmlns:v`). It is surgical (only the offending attribute is removed) and a no-op on
  well-formed files, since those cannot contain such attributes. Off by default.
- This cleared the last 2 markup-fatal library books that survived `--reserialize`:
  The Selfish Gene (`v:shapes`) and The Rustonomicon (broken SVG `31=""`). Both now
  validate with zero fatals, open in Calibre, and preserve their full text. With this,
  the entire 38-book fatal set from the original audit is resolved.

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
