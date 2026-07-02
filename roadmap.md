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

## Phase 2: the long tail (shipped across v0.2.0-v0.7.0)

- [x] Unclosed **non-void** elements (`<p>`, `<span>`, `<body>`, `<blockquote>`): needs
      a forgiving HTML parser that re-serializes as XHTML. Evaluate a stdlib
      `html.parser` rebuild vs. asking to add `lxml`. *(shipped as `--reserialize`
      via html5lib, v0.3.0; duplicate of the entry below)*
- [x] Strip unbound namespace cruft (`v:shapes` and friends from Office HTML)
      *(shipped as `--strip-bad-attrs`, v0.4.0; duplicate of the entry below)*
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
- [x] Report-only JSON output, and a `--manual-list` export for the partial/nochange set
      *(v0.7.0: `library --json FILE` writes the full machine-readable run report;
      `--manual-list FILE` exports every book that was not auto-repaired)*
- [x] Re-audit integration: run an epubcheck sweep and feed results straight into
      candidate selection without a separate CSV step *(v0.7.0: `--sweep`; each sweep
      result is reused as that book's `before` measurement, so nothing is checked
      twice, and the audit-CSV path-mismatch bug class does not exist on this path)*

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

## Phase 5: audit fixes and hardening (shipped, v0.6.0)

*A full bugfix/UX/usefulness audit of v0.5.0. The three items in 5.1 were confirmed
bugs, reproduced by executing the real code paths (not just by reading). Sections
5.1 through 5.4 shipped in v0.6.0, each fix with a stdlib-unittest regression test;
the mimetype fix (5.5) and the spec documentation (5.6) followed in v0.7.0. Still
open: the low-priority epubcheck locale hardening (5.2) and the opt-in
unknown-entity escape (5.5, awaiting a flag-name/spec decision).*

### 5.1 Confirmed bugs (safety and correctness)

- [x] **`--strip-pagination` bypasses the `partial` classification and can auto-apply
      a still-fatal book.** *(fixed, v0.6.0)* The most serious finding: it violates the hard rule that a
      book with remaining fatals is `partial` and never auto-applied. In
      `cli.py:process_book`, `verdict = gate(before, after)` is computed and then
      unconditionally overwritten whenever the report contains `stripped_pagination`:
      `verdict = "accept" if no_worse(before, after) else "reject"`. `no_worse` only
      forbids regression, so a book going 3 fatals -> 1 fatal (a `partial` by the
      gate) becomes `accept`, and `library --apply --strip-pagination` atomically
      replaces a book that still does not open. Reproduced with mocked epubcheck
      results: (3,0,0) -> (1,0,0) with the strip fired returns status `accept`.
      Fix (one conditional): when the strip fired, reject if `no_worse` fails,
      **demote to `partial` if `after.fatals > 0`**, else accept.
      Test: mock `repair_epub` to return a `RepairReport` whose fixes include
      `stripped_pagination`, mock `run_epubcheck` to return CheckResult(3,0,0) then
      CheckResult(1,0,0); assert `process_book(..., strip_pagination=True).status ==
      "partial"`. Also assert (0,0,0)->(0,0,0) still accepts (the normal no-gain case).

- [x] **One corrupt `.epub` aborts an entire `library` run.** *(fixed, v0.6.0)* `cli.py:run_library`
      calls `process_book` with no per-book exception handling, so a non-zip or
      truncated file raises `zipfile.BadZipFile` out of `repair_epub` and kills a
      multi-hour sweep with a traceback (reproduced with a 9-byte fake `.epub`). An
      encrypted entry would similarly raise `RuntimeError` from `zin.read`. Fix: wrap
      the per-book `process_book` call in try/except catching `zipfile.BadZipFile`,
      `OSError`, and zipfile's `RuntimeError`; print an `ERROR` line with the relative
      path and exception, count it in a new `unreadable:` summary counter (or fold
      into `epubcheck errors:`), and continue the sweep. `epub.py:ncx_uid_mismatch`
      already catches `zipfile.BadZipFile, OSError` defensively; same idea. Apply the
      same guard to `run_repair` (single file) so it prints a clean error instead of a
      traceback. Test: a temp library tree with one good book and one garbage
      `.epub`; the run completes, the good book is processed, the bad one is reported.

- [x] **The pagination strip deletes `<p id=...>` navigation targets.**
      *(fixed, v0.6.0)* The comment
      above `_ID_ANCHOR_RE` in `pagination.py` promises that an element carrying an id
      survives, but the regex only rescues `<a id=...>` elements *inside* the removed
      block. When the removed paragraph itself carries the id (`<p id="page7">7</p>`,
      a common page-anchor shape), the delete-only path in `strip_pagination_doc`
      replaces the whole block with `""` and the id vanishes (reproduced: `page7`
      absent from output). Any NCX page-list or internal link to that fragment breaks.
      The `no_worse` gate only catches this when the book started with zero fatals
      (broken fragment refs are epubcheck errors, and error rises are tolerated as
      "unmasking" when fatals were present). Fix, in both removal paths:
      * delete-only path: if the dropped block's `open_tag` contains an `id`
        attribute, keep an emptied `<p id=...></p>` shell (exactly as the inner-anchor
        path already does) instead of deleting outright;
      * merge path: for non-member blocks between `first` and `last`, hoist an
        `open_tag` id as well as the inner `<a id>` anchors (e.g. emit an empty
        `<a id="..."/>`, or preserve the emptied shell before the merged paragraph).
      Also widen `_ID_ANCHOR_RE` to accept single-quoted `id='...'`.
      Note: the character-conservation net is unaffected (ids are invisible text).
      Test: strip a doc containing `<p id="page7">7</p>` in a confident-interrupt
      position; assert the id attribute survives somewhere in the output and the
      number text is gone. Cover both the merge and delete-only paths.

### 5.2 Packaging and environment

- [x] **Make `html5lib` an optional extra, as the docs promise.** *(done, v0.6.0)* `pyproject.toml`
      declares `dependencies = ["html5lib>=1.1"]`, so `uv tool install` always drags
      it in, while README, CLAUDE.md, and the pyproject comment itself all say the
      core is stdlib-only and html5lib is needed only for `--reserialize` (it is
      imported lazily in `reserialize.py`). Fix: move it to
      `[project.optional-dependencies] reserialize = ["html5lib>=1.1"]`, update the
      README install section (`uv tool install "bindery[reserialize]"` for the full
      kit), and regenerate `uv.lock`. No code change; `reserialize_if_broken` already
      raises a clear RuntimeError when the import is missing.

- [ ] **Harden epubcheck output parsing against non-English locales.** (Low priority;
      note first, act if it ever bites.) `validate._SUMMARY_RE` matches the English
      summary line `Messages: N fatals / N errors / N warnings`; epubcheck (Java)
      localizes its messages, so on a non-English locale every book would parse as
      `None` and be reported as `error`. Options: pass
      `env={..., "JAVA_TOOL_OPTIONS": "-Duser.language=en"}` to the subprocess, or
      switch to epubcheck's locale-independent JSON output (`--json -`), parsing
      counts from the JSON instead. JSON is the sturdier fix but changes the wrapper;
      keep the regex as fallback. Test: feed a canned localized summary through the
      parser path via a mocked `subprocess.run`.

### 5.3 Minor bugs and hardening

- [x] **`run_repair` labels `partial` and `equal` output as success.**
      *(fixed, v0.6.0)* A partial
      repair (fatals reduced, not cleared) falls through the status checks in
      `cli.py:run_repair` and is written to the output file with the message
      `repaired: 3f/0e/0w -> 1f/0e/0w ...`, which reads as fixed. Writing the file is
      correct (nothing is replaced in place); the labeling is not. Fix: branch on
      `o.status` and print `PARTIAL (still has fatals; needs manual work):` for
      partial, and keep/clarify the `(no measurable gain)` wording for `equal`.
      Consider exit code 0 for both (a file was legitimately written) but say clearly
      what was written. Test: mocked epubcheck (3,0,0)->(1,0,0); assert stdout
      contains a PARTIAL marker and the file exists.

- [x] **Single-quoted attributes are invisible to the OPF/NCX regexes.**
      *(fixed, v0.6.0)*
      `_UID_ATTR_RE` (`unique-identifier="..."`), `_DTB_UID_RE`/`_DTB_UID_RE_REV`
      (`name="dtb:uid"`, `content="..."`), and `_ROOTFILE_RE` (`full-path="..."`) in
      `epub.py` all require double quotes. A single-quoting toolchain makes the
      NCX-001 sync and OPF location silently no-op (missed fix, never corruption).
      Fix: accept either quote (`"([^"]+)"|'([^']+)'` with a helper to pick the
      non-None group), or normalize quotes before matching. While in there: also
      accept `.EPUB` uppercase in `library.iter_epubs` (`rglob` is case-sensitive;
      Calibre emits lowercase, but a hand-added file should not be invisible).
      Test: an OPF/NCX pair using single quotes round-trips through
      `opf_unique_id`/`sync_ncx_uid`/`ncx_uid_mismatch`.

- [x] **`repair` silently overwrites an existing output file.** *(fixed, v0.6.0)* `run_repair` copies
      onto `dst` unconditionally. Fix: refuse when `dst` exists unless a new
      `--force` flag is given (or at minimum print an overwrite notice). Keep the
      existing refusal to overwrite the input in place. Test: pre-create `dst`,
      assert refusal without `--force` and success with it.

### 5.4 UX

- [x] **Progress output for long library runs.** *(done, v0.6.0)* Books resolving to `nochange` or
      `equal` print nothing, and with validation each book costs seconds of epubcheck
      time, so a mostly-clean library shows the header and then hours of silence.
      Fix: print a lightweight progress line per book or every N books, e.g.
      `[123/4051] Author/Title.epub`, to stderr so stdout stays a clean report (stdout
      is already line-buffered via `main`). Consider `--quiet` to suppress it. No
      third-party progress bars (stdlib-only core); a simple counter is enough.

- [x] **Warn when the audit CSV overlaps zero candidates.** *(done, v0.6.0)* `_load_audit` keys must
      equal `str(epub)` exactly; running `library` with a relative path (or an audit
      generated from a different mount point) silently matches nothing, and with
      `--only fatals` the run quietly processes 0 books, reading as "library is
      clean". Fix: after candidate selection, if an audit was given and no scanned
      path hit a CSV key, print a loud warning suggesting a path mismatch (absolute
      vs. relative). Cheap adjacent improvement: resolve both sides
      (`Path(...).resolve()`) before comparing. The Phase 2 re-audit integration
      item would dissolve this class of bug entirely; this warning is the stopgap.
      Test: audit CSV with absolute paths, library scanned via a relative path;
      assert the warning fires.

- [x] **Warn when backup flags do nothing, and recommend backups for the lossy
      mode.** *(done, v0.6.0)* `--backup DIR` / `--backup-inplace` only act inside the `--apply`
      branch of `run_library`; given without `--apply` they are silently inert. Fix:
      print a note ("dry run: --backup has no effect without --apply"). Additionally,
      when `--apply --strip-pagination` is given with no backup flag, print a loud
      recommendation (not a refusal) that the one lossy mode deserves a backup.
      Test: assert both notices appear in the right flag combinations.

- [x] **Meaningful exit code from `library`.** *(done, v0.6.0)* `run_library` always returns 0, so
      scripts and cron cannot detect trouble. Fix: return a distinct nonzero code
      (e.g. 2) when `rejected + errors > 0` (and the new unreadable count from 5.1),
      0 otherwise; document it in README. Keep 1 for usage errors as today. Test:
      force a reject via mocked epubcheck and assert the exit code.

- [x] **Make `--limit` limit the scan, not just the work.** *(done, v0.6.0)* Candidates are fully
      materialized (`list(_select(...))`) before the limit slices, so
      `--only ncx --limit 20` still opens every archive in the tree
      (`ncx_uid_mismatch` per book). Fix: `itertools.islice` over the `_select`
      generator. Wrinkle to handle: the candidate count printed in the header and
      summary currently comes from `len(candidates)`; with lazy slicing, count as you
      go (the header can say `limit=20` instead of a total). Test: a tree of N books
      with a limit of 2; assert only 2 are opened (mock or count `ncx_uid_mismatch`
      calls).

### 5.5 New fix candidates (usefulness)

- [x] **Add a missing `mimetype` entry (and normalize wrong content).**
      *(done, v0.7.0)* The archive
      rewrite in `epub.py:repair_epub` writes `mimetype` first and stored only *if
      present*; when absent, the output still has no mimetype (epubcheck PKG/OCF
      error). The content is a constant (`application/epub+zip`, no trailing
      newline), so adding it when missing, and normalizing wrong or
      whitespace-padded content, is deterministic, semantics-preserving, and
      gate-checked; squarely inside the charter. Count it in the RepairReport
      (`mimetype_added` / `mimetype_normalized`) so it shows in summaries and trips
      the gate like any other fix. Update spec.md "Archive rewrite". Test: archives
      with no mimetype and with `application/epub+zip\n`; assert the entry exists,
      is first, ZIP_STORED, exact bytes.

- [ ] **Opt-in: escape unknown named entities (`&foo;` -> `&amp;foo;`).** An
      undeclared entity that is not in the HTML5 table stays a fatal today:
      `fix_named_entities` leaves unknown names, and `_BARE_AMP_RE` deliberately
      skips anything entity-shaped. Escaping unknowns renders exactly as a browser
      renders an unknown entity (the literal text `&foo;`), so it is arguably
      semantics-preserving; but it changes visible text relative to a document whose
      internal DTD subset *declares* the entity. Guard: skip any document containing
      an internal subset (`<!DOCTYPE` with `[`), and make the whole thing opt-in
      (`--escape-unknown-entities`) rather than a core transform. Gate applies as
      usual. Decide the flag name and spec wording before coding. Test: `&foo;`
      escaped under the flag, untouched without it, untouched when an internal
      subset declares it.

### 5.6 Documentation debt

- [x] **Spec gap: void end-tag swallowing.** *(documented in spec.md, v0.7.0)* Since v0.4.2, `self_close_void` also
      deletes orphaned void end tags (`</br>`, `</col>`; the `_VOID_END_RE.subn` in
      `transforms.py`), and counts them in the fix total, but spec.md's transform
      list only documents the self-closing of open tags. Either document the
      swallowing in spec.md ("end tags for void elements are removed; they are
      always invalid") or narrow the behavior; the auto-memory
      `spec-gap-void-end-tag-swallowing` has the full analysis. Decision is
      Brandon's; the roadmap item is to make spec and code agree.

## Phase 3: integration (maybe)

- [ ] Calibre post-import hook or plugin so books are repaired on add
- [ ] Optional metadata.db nudge so Calibre notices the new file size without a manual
      Quality Check sync
