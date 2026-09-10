# bindery-cli roadmap

## Phase 1: deterministic repair engine (shipped, v0.1.0)

- [x] Transforms: self-close void elements, named entity to numeric, escape bare `&`,
      strip prolog junk, drop duplicate `xmlns`
- [x] NCX-001 fix (dtb:uid sync to OPF unique identifier)
- [x] mimetype ordering/compression repair on rewrite
- [x] Two-mode epubcheck gate (fatal-fixing vs error-cleanup)
- [x] `repair` (single file) and `library` (batch) CLI modes
- [x] Atomic in-place library replacement with optional backups; dry run by default
- [x] `--only {fatals,ncx,all}` and `--audit CSV` candidate filtering
- [x] `unittest` suite (transforms, archive rewrite, atomic replace)
- [x] Validated on the real library: 24 of ~40 fatal books fully de-fataled with zero
      epubcheck regressions; the rest reported for manual follow-up

## Phase 2: the long tail (shipped across v0.2.0-v0.7.0)

- [x] Unclosed **non-void** elements (`<p>`, `<span>`, `<body>`, `<blockquote>`): needs
      a forgiving HTML parser that re-serializes as XHTML. Evaluate a standard
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

## Phase 5: audit fixes and hardening (shipped, v0.6.0-v0.8.0)

*A full bugfix/UX/usefulness audit of v0.5.0. The three items in 5.1 were confirmed
bugs, reproduced by executing the real code paths (not just by reading). Sections
5.1 through 5.4 shipped in v0.6.0, each fix with a standard-unittest regression test;
the mimetype fix (5.5) and the spec documentation (5.6) followed in v0.7.0, and the
unknown-entity escape (5.5) in v0.8.0, and the last survivor, the epubcheck
locale hardening (5.2), in v0.10.0. Phase 5 is closed.*

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
      core is minimal and html5lib is needed only for `--reserialize` (it is
      imported lazily in `reserialize.py`). Fix: move it to
      `[project.optional-dependencies] reserialize = ["html5lib>=1.1"]`, update the
      README install section (`uv tool install "bindery[reserialize]"` for the full
      kit), and regenerate `uv.lock`. No code change; `reserialize_if_broken` already
      raises a clear RuntimeError when the import is missing.

- [x] **Harden epubcheck output parsing against non-English locales** *(shipped
      v0.10.0, 2026-08-08)*. Both options landed together: counts parse from
      epubcheck's locale-independent `--json -` output first
      (`checker.nFatal/nError/nWarning`, shape verified against the installed
      5.3.0), the English summary-line regex stays as the fallback for
      pre-`--json` epubchecks, and the subprocess env pins the JVM to English
      via an appended `JAVA_TOOL_OPTIONS` so that fallback stays meaningful.
      Tested exactly as prescribed: `tests/test_validate.py` (9 tests) feeds
      canned JSON, a canned localized (German) summary, and malformed JSON
      through a mocked `subprocess.run`.

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
      third-party progress bars (minimal core); a simple counter is enough.

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

- [x] **Opt-in: escape unknown named entities (`&foo;` -> `&amp;foo;`).**
      *(done, v0.8.0: `--escape-unknown-entities`, internal-subset guard as specified)* An
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

- [x] **Extend `--fix-ids` to the NCX (RSC-005 invalid navPoint ids).**
      *(done, v0.9.0)* Old conversions stamp navPoint ids from UUIDs (digit-led) or
      colon-bearing strings; epubcheck rejects every one (80 errors on a single real
      book). `epub.py:fix_ncx_ids` renames them with the same `id_` scheme as
      `fix_manifest_ids`; NCX ids are internal to the NCX (nothing in the OPF or
      content docs references them), so no cross-file bookkeeping. Behind the
      existing `--fix-ids` flag; counted as `fix_ncx_ids`.

- [x] **Opt-in: add missing `img alt` (`--add-img-alt`).**
      *(done, v0.9.0)* `<img>` without the required `alt` is an RSC-005 error per
      occurrence. `transforms.add_img_alt` adds `alt=""` (quote-aware, idempotent,
      CDATA/comments protected). Renders identically, but it ADDS markup the author
      never wrote and asserts "decorative" to screen readers, hence opt-in, never a
      core transform. Counted as `img_alt_added`.

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

- [ ] Calibre plugin so books are repaired on add, as a `FileTypePlugin` with
      `on_import = True`.
- [x] ~~Optional metadata.db nudge so Calibre notices the new file size~~
      **Dropped 2026-08-09: unnecessary under `on_import`.** See the decision below.

**DECISION 2026-08-09: settled for both tools at once, using `FileTypePlugin`
with `on_import = True`.** Researched against the Calibre source
(`src/calibre/customize/__init__.py`), not guessed at.

Calibre offers several hooks and only one of them is the right shape:

| hook | when | signature |
|---|---|---|
| `on_import` + `run(path)` | while the file is being added, BEFORE it lands | returns the path to a modified file |
| `on_postimport` + `postimport(book_id, fmt, db)` | after the file is in the database | gets a db handle |
| `on_postadd` + `postadd(book_id, fmt_map, db)` | after a whole book record is first created | gets a db handle |

**`on_import` is the one.** `run()` is handed the file being imported and returns
the path to a modified copy, built with the plugin's own `temporary_file()`;
Calibre then imports *that* instead. So the repaired or stripped EPUB is what
enters the library, the user's original on disk is never touched, and no write to
`metadata.db` happens at all.

Three consequences worth stating, because they change existing plans:

1. **bindery-cli's separate "metadata.db nudge" item is unnecessary and is dropped.**
   It existed so Calibre would notice a changed file size after an in-place
   replacement. Under `on_import` the file is modified *before* Calibre reads it,
   so the size it records is already correct. The nudge was solving a problem
   created by replacing files behind Calibre's back.
2. **The plugin must carry its own code.** It runs inside Calibre's bundled
   Python, which will not have either tool pip-installed. Both being minimal-dependency
   is what makes this practical: the module vendors into the plugin zip. bindery-cli's
   optional `html5lib` path and its epubcheck gate cannot come along, so the
   plugin must degrade honestly rather than half-run.
3. **epubcheck cannot gate the import.** It is an external Java process taking
   seconds per book; running it on every add would make importing a shelf
   unusable. The plugin therefore applies only the deterministic, safe transforms
   and leaves the gated work to the CLI, which is where a human is watching.

This is one plugin *shape* serving both tools, not one shared plugin: they are
separate repos with separate licences and no dependency between them.

**RESEARCH 2026-09-05 (against the calibre 9.14 reference clone, which matches the
installed Calibre). The decision holds. Three corrections and a scoped spec; the
plugin is approved to build (Brandon, 2026-09-05).**

- The `on_import` contract is exactly as decided: `run(path)` returns a
  replacement built with `temporary_file()`, Calibre swaps which path flows
  downstream, and the original on disk is never touched. A raising plugin cannot
  abort an import and cannot lose the file (Calibre falls back to the original),
  but the failure is silent: tracebacks land only in Calibre's debug log. The
  plugin therefore never raises and keeps its own log under
  `calibre.constants.config_dir`.
- Loader trap the decision missed: `supported_platforms` defaults to empty and
  the loader rejects any plugin that declares no platforms. The plugin must set
  `supported_platforms = ['linux']`, which also makes other OSes a
  loader-enforced non-goal.
- The hook also fires when a format is added to an existing book
  (`db.add_format(..., run_hooks=True)`), so the plugin must be byte-idempotent.
  The core pass already is (deterministic since v0.10.2).
- The "both tools" wording is vacuous today: oceanstrip was absorbed at v0.12.0
  and the standalone repo is gone. One plugin, in this repo, honoring the shape
  claim retroactively.

Scoped spec:

- **Vendor slice**, generated at release from the tagged tree with a
  byte-equality drift test: `transforms.py`, `epub.py`, `pagination.py`,
  `watermark.py`, `reserialize.py` (~2,300 lines) plus a new plugin
  `__init__.py`. `cli.py`/`library.py`/`audit.py`/`validate.py` stay out: they
  carry tqdm, cquarry, vir-tui, and the epubcheck/JDK machinery, none of which
  a plugin zip can carry (plugins cannot pip-install).
- **Active fix set = the gate-safe default pass only**: the five well-formedness
  transforms plus the NCX pipeline. All twelve structural repairs and the three
  lossy strips stay CLI-only: their acceptance IS the epubcheck gate, which
  cannot run inside Calibre (no jar, no JVM, and seconds-per-book latency).
  Opt-in flags are never enabled by the plugin; nothing runs ungated.
- **Behavior**: core pass on `*.epub` imports; zero fixes or any exception
  returns the original path unchanged; the rewritten archive is re-opened and
  `testzip()`-verified before it is handed back; one log line per book (fixed /
  no fixes / refused / errored). Config via `site_customization` JSON
  (`log`, `log_path`, `max_size_mb` refusal cap). No Qt config widget, no
  `postimport` hook (that would write `metadata.db`, breaking the decision).
- **Release**: `publish.yml` gains a job that regenerates the vendor slice from
  the tag, runs the tests against it, zips it, and attaches
  `BinderyRepair-v<VERSION>.zip` to the GitHub release beside the PyPI artifact.

Open questions (Brandon): epubcheck-on-PATH experimental mode (default no, the
latency ruling stands); plugin identity `Bindery Repair` / `bindery_repair`;
size-cap default.


## Supplementary Phase (Based on Library Audit)
Following a comprehensive epubcheck sweep of a 5,043-book Calibre library on 2026-08-22 (~3,228 candidates for repair), several recurring error schemas were identified; all have since shipped. (This section absorbs the one-off `BINDERY_REPORT.md`, retired in v0.17.0 — its performance notes live in the v0.13.0 patchnotes, and its findings are the checked items below.)

- [x] **XML NCName Violation (`id` contains colons):** Fix IDs that contain invalid characters (specifically colons like `id="foo:bar"`). These must be string-replaced in the declaration and across all referencing `href` or `idref` attributes throughout the EPUB. *(shipped v0.14.0 as `--fix-id-colons`; made genuinely opt-in in v0.17.0 — see Phase 6 below)*
- [x] **Empty Body (`<body></body>`):** Inject a placeholder so parsers accept the document. *(shipped v0.14.0 as `--fix-empty-body` appending `&nbsp;`; opt-in since v0.17.0)*
- [x] **Missing `<title>` in `<head>`:** Inject a placeholder `<title>` tag in XHTML documents where it is entirely omitted. *(shipped v0.14.0 as `--fix-missing-title`; opt-in since v0.17.0)*
- [x] **Block-in-Inline Nesting (`<span><div></div></span>`):** Develop a strategy to unwrap or restructure inline elements that improperly contain block-level children. *(shipped v0.14.0 as `--unwrap-block-in-inline`; opt-in since v0.17.0)*
- [x] **NCX Duplicate `playOrder`:** Re-sequence `playOrder` integers in the `toc.ncx` file to ensure they are strictly sequential without gaps or duplicates. *(shipped v0.14.0 as `fix_ncx_playorder`, part of the always-on NCX pipeline — NCX-internal attribute normalization)*
- [x] **Invalid Attributes (`value` in lists):** Strip invalid `value` attributes from tags where they don't belong according to EPUB schemas (such as arbitrary `<li>` markers). *(shipped v0.14.0 as `--strip-invalid-value`; opt-in since v0.17.0)*

Considered from the same sweep and deliberately **not** scheduled: enabling `--add-img-alt`
by default (missing `alt` was the sweep's #2 recurring error at 5x sample frequency).
Rejected because `alt=""` asserts "decorative" to screen readers and adds markup the author
never wrote — exactly the class of fix that must stay opt-in under the safety contract.

## Phase 4: cquarry Integration (complete 2026-08-28, v0.19.0)

With the transition to the `cquarry` shared library (v0.16.0), bindery-cli inherits the ability to perform complex search and batch resolution natively. As the `cquarry` library adds new write capabilities, bindery-cli is slated to adopt the following upgrades:

- [x] **Format Path Resolution:** Transition to `cquarry.get_format_path()` to remove manual path concatenations during file discovery. *(done: v0.18.0 for library-mode audits; v0.19.0 finishes the job — `CalibreIdResolver` builds the id→EPUB-path map through `get_format_path()` so `--install-to-calibre` resolves the book id from `metadata.db` instead of the `(id)` directory fragment.)*
- [x] **Safe Tag Application:** When `cquarry` implements safe DB writes (`add_tag`), bindery-cli will gain the ability to automatically tag books in the Calibre UI as "Audited" or "Flagged" when issues are found, rather than relying strictly on console output. *(done: v0.18.1 — `audit --tag` applies via `cquarry.write.WritableCalibreDB.add_tag`, OPF-resync queued through `metadata_dirtied`; v0.19.0 extends `--tag` to the new `--id` single-book mode.)*
- [x] **Single-Entity Fetching:** Utilize `get_book(book_id)` for faster isolated audits when analyzing a single book, avoiding the overhead of caching the entire library layout. *(done: v0.19.0 — `audit --id BOOK_ID` fetches one row via `get_book()`, resolves the EPUB via `get_format_path()`, and reports the same verdicts as directory mode.)*

## Phase 6: Code Sweep & Fixes (2026-08-23)
*Context: Fixing severe data loss and platform incompatibility issues found during sweep.*

### Bugs to Fix
- [x] **Critical Content Loss:** Fix `unwrap_block_in_inline` to preserve inner text blocks rather than replacing them with literal strings. *(fixed, v0.16.3)*
- [x] **Missing `--replace` Flag:** Update `calibredb_replace` to pass `--replace` so Calibre doesn't crash on existing formats. *(fixed, v0.16.3)*
- [x] **Non-Existent Method Call:** Fix `analyze_brokentags` in `audit.py` calling `iter_html_text` on `Book` (which doesn't exist). *(fixed, v0.16.3)*
- [x] **Hardcoded Java Version:** Remove `--release 25` from `EpubcheckDaemon` so it boots on standard Java 17/21 systems. *(fixed, v0.16.3)*
- [x] **Unbounded URL Mutation:** Fix `fix_id_colons` regex so it checks word boundaries and ignores non-id attributes and external fragment links. *(fixed, v0.16.3)*
- [x] **Trailing Whitespace Deletion:** Fix `strip_invalid_value` wiping consecutive spaces in valid attributes. *(fixed, v0.16.3)*
- [x] **Empty `<title>` Tag Failure:** Fix `fix_missing_title` failing to replace `<title></title>` with `<title>Unknown</title>`. *(fixed, v0.16.3)*

### Refactoring & Growth
- [x] **Decouple Opt-In Transforms:** Remove experimental/lossy transforms from the default `HTML_TRANSFORMS` block and gate them strictly behind their documented CLI flags. *(done, v0.17.0: all six structural repairs behind real flags; the core pipeline is well-formedness only again)*
- [x] **cquarry Integration:** Use `cquarry` to accurately build Calibre file paths instead of guessing. *(done, v0.19.0: `CalibreIdResolver` maps EPUB paths to book ids through `cquarry.get_format_path()` — the `(id)` directory-name regex now only covers the no-catalog fallback.)*
- [x] **True CSS-Aware Unwrapping:** Read EPUB CSS stylesheets to skip `unwrap_illegal_tags` on styled custom elements. *(done, v0.17.0: `transforms.css_protected_tags` scans stylesheets and inline `<style>` blocks; protected names are skipped book-wide)*

## Phase 7: monolithic-document audit + version pin (proposed 2026-08-27)

*Context: the one remaining defect class that is still caught by hand. Phase 1 of the
acquisition pathway (`~/docs/Calibre Library/.claude/skills/phase-1-import/SKILL.md`,
§ 2 "Chars per content document") must check characters-per-content-document on every
EPUB and currently does it with an inline script because no analyzer covers it — the
skill says so verbatim ("no tool; run inline"). The motivating incident: a "clean"
Oxford Dictionary EPUB — ~30M chars across 21 content docs, epubcheck silent, all four
audit analyzers silent — that would not render past a point on real readers.
Monolithic documents are invisible to `emptytext` (whole-book volume, not per-doc
shape) and to epubcheck (which never sees renderer memory limits). Every session that
re-implements the inline check is a session that can silently skip it.*

- [x] **`monolithic` analyzer** in `src/bindery/audit.py`, joining the existing
      `content|pagenumbers|emptytext|ocr|all` set:
  - Count characters PER content document (spine items), not per book, inside the
    existing single decompression pass — `emptytext` already walks every doc to get
    book volume, so extend that loop rather than adding a second pass. Track the max
    and keep the worst doc's href for the detail column.
  - Flag when any single content doc is >= 300,000 chars. Default per the phase-1
    skill's "roughly 300-500k" advisory floor; expose the threshold as a flag
    following the existing `--min-chars`/`--thin-chars` naming style (propose
    `--max-doc-chars N` — "flag any doc over N chars").
  - Output mirrors the other analyzers: a `max_doc_chars:N` field on the book's line
    plus the offending doc href, so `--tag` (v0.18.1 write path) and `--id`
    (v0.19.0 single-book mode) compose unchanged. Only the >= threshold defect
    flags/tags; high-but-under-threshold books stay silent (advisory, like THIN).
  - Tests in `tests/test_audit.py` following the existing synthetic-EPUB pattern:
    one 400k-char doc → flagged; 20 docs x 20k chars → clean; threshold override
    respected; `--tag` applies only to flagged books; `--id` mode reports the same
    verdict as directory mode.
  - Same-release doc sync: `spec.md` audit section, the `.clinerules` layout line for
    `audit.py`, `README.md` audit section — and then the phase-1 skill's "no tool;
    run inline" paragraph should be updated to name the analyzer (that file lives in
    the library directory, not this repo; flag it to Brandon in the release note).
    *(Shipped in v0.21.0. Doc sync done — spec/README/phase-1 skill §2; the
    `.clinerules` half is moot, the file was retired with the AGENTS.md symlink
    taking over.)*
- [x] **Version-sync pin.** `src/bindery/__init__.py` has shipped stale more than
      once — the phase-1 skill literally warns "`bindery --version` may print one
      release behind the real code (a stale constant in `__init__`)", and at this
      writing (2026-08-27) pyproject reads 0.19.0 in-tree while `__init__.py` still
      reads 0.18.0. Port CalibreQuarry's `tests/test_version.py` pattern: assert
      pyproject.toml's `version` == `bindery.VERSION`, so AGENTS.md's "bump both"
      rule is enforced by tests instead of memory.
      *(Done in v0.19.2: `tests/test_version.py` asserts the pin.)*

### v0.19.2 bugfix note (2026-08-30, from the ecosystem audit)

- [x] **`audit --id` was unreachable from the console script.** v0.19.0 documented
      `--id` and shipped `run_single`, but only the module's own argparse main
      registered the flag; the `bindery` entry point died with "unrecognized
      arguments". The subparser now carries `--id` and routes to `run_single`,
      with wiring tests mirroring the v0.18.1 `--tag` battery (same bug class:
      a shipped flag the CLI never registered). The dead `args.dry_run` branch
      in the module main went with it (no parser defines that flag).

Non-goals: no auto-splitting of monolithic docs (content surgery is outside the
charter — flag and re-source); no PDF equivalent (page-count sanity there stays a
manual completeness spot-check per the skill).

Landing note: v0.19.0 (`audit --id` single-book mode, `CalibreIdResolver` for
`--install-to-calibre`, cquarry dep floated to `@main`) shipped in commit fdf5e7f on
2026-08-27. Land this phase on top of that release, not beside it; do not rework its
files.

## Phase 8: batch-scoped sweeps + spine-integrity reporting (proposed 2026-08-28)

*Context: the 2026-08-27 acquisition batch hit two tooling gaps. (1) Phase 3's
repair step has no batch scoping: `bindery library` walks the whole library — a
full sweep ran ~4.4 s/book cold (5,070 EPUBs ≈ 6 hours) and had to be killed,
even though only 8 books were in scope. (2) The official Wandering Inn editions
ship series-wide ToC manifests (~750 references vs 14-19 real content docs,
~740 RSC-001s), which produced a wrong "fragment" quarantine during the phase-1
run until a chapter-span comparison against the sibling volume reversed it —
the tooling should classify that pattern instead of every agent re-deriving it
by hand.*

- [x] **`library --id <ids>`**: comma-separated book-id scoping for the sweep,
  mirroring `audit --id` (v0.19.0). Resolve EPUBs through cquarry's
  `get_format_path()` via `CalibreIdResolver`; the gate, backups, and
  `--install-to-calibre` behavior are unchanged.
- [x] **Spine-integrity reporting**: audit (and repair reports) gain a field
  counting manifest/NCX references whose target files are absent from the
  archive, with a classification: `convention` when the absent count ≈ navPoint
  count and the present docs' chapter span is consecutive (the WI
  official-build pattern), `fragment` when the span itself is broken. Encodes
  the 2026-08-27 lesson so the near-miss cannot recur.
- [x] **Tests**: synthetic EPUBs — convention-shaped ToC bloat (reported, not
  flagged), a true fragment (flagged), and a repaired-fatal composition.
- [x] **Skill sync**: phase-1-import's "ToC bloat" trap entry and phase-3-import's
  step 10 (both in `~/docs/Calibre Library/.claude/skills/`) should point at the
  spine-integrity classification and the `--id` scoping once shipped, replacing
  the hand-run chapter-span comparison. **Floor, not ceiling**: anything the
  build surfaces that changes agent-facing behavior — thresholds, verdict
  classes, output shapes — gets documented in the affected skills in the same
  release.
- [x] **`audit --id` comma lists**: v0.19.0 took a single book id; support the
  same comma-separated form as the new `library --id` so the two modes match.
- [x] **Concurrent validation workers (stretch; not shipped)**: the 2026-08-27 full-library
  walk measured ~4.4 s/book on a single daemon (5,070 EPUBs ≈ 6 h). Gate k
  validation workers behind `--workers N`, default 1 (behavior unchanged).
  *(Un-ticked in the 2026-09-02 docs re-read (NEW-AUDIT.md Stage 0): the box was
  ticked at the v0.23.0 ship, but no `--workers` code, test, or doc line ever
  landed.)*
  *(Shipped for real in v0.30.0: `library --workers N` runs the --sweep
  candidate pass through N concurrent epubcheck workers, default 1 (serial,
  unchanged). Threads parallelize the oracle honestly (the subprocess releases
  the GIL); books are checked in windows of N consumed in input order, so the
  candidate set matches the serial sweep and `--limit` stays lazy within one
  window of overshoot. The repair phase stays serial, where the shared workdir
  and the atomic-replacement contract live.)*

Non-goals: no auto-trim of bloated ToCs (semantics-preserving charter); no
content-doc synthesis; no PDF equivalent.

Landing note: lands on the v0.19.0+ lineage (fdf5e7f). Phase 7's monolithic
analyzer and version pin are independent of this phase and may land in either
order.

## Phase 9: archive-integrity reporting in audit (proposed 2026-08-28)

*Context: the phase-1 skill (`~/docs/Calibre Library/.claude/skills/phase-1-import/SKILL.md`,
§ 2 "Corruption sweep FIRST") still runs a hand-rolled stdlib `zipfile` sweep over
every loose file before anything else, because `bindery audit` cannot see this
defect: a CRC-broken entry decompresses to garbage or nothing, and `emptytext`
then reports the book EMPTY — the right alarm for the wrong disease. The
re-source advice that follows from EMPTY ("content-less stub") mislabels a file
whose problem is a damaged archive, not missing content. `library --sweep`
reports such books `unreadable`, but lumps corruption together with
not-a-zip/truncated/encrypted and does not name the broken entry. Same shape as
Phase 7: a check every session re-implements inline is a check a session can
silently skip.*

- [x] **`audit` integrity check**: inside the existing single decompression
      pass, fully read every entry (CRC + decompression via a real read, not
      just the central directory's word) before the text analysis runs. A
      corrupt entry is reported as its own verdict (`corrupt:N` plus the first
      bad entry's name on the book's line), NOT fed to `emptytext` as empty
      body text. A corrupt book's re-source outcome is the same, but the batch
      report must name the right disease.
- [x] **`library` sweep sub-reasons**: split the `unreadable` bucket into
      `not_a_zip` / `truncated` / `encrypted` / `corrupt_entry` so the two
      modes report consistently and a corrupt book is distinguishable from a
      DRM'd or truncated one without leaving audit mode.
- [x] **Tests** in the `tests/test_audit.py` synthetic-EPUB pattern: a
      flipped-CRC entry (reported CORRUPT, not EMPTY), a truncated archive, an
      encrypted entry, and a clean book (silent).
- [x] **Skill sync**: phase-1 § 2's inline "Corruption sweep FIRST" step names
      the analyzer once shipped and retires the hand-rolled sweep, exactly as
      Phase 7 retires the chars-per-document script. Floor, not ceiling: any
      behaviour-affecting discovery made while building gets documented in the
      affected skill in the same release.

Non-goals: no repair of corrupt entries (a damaged archive is re-sourced, never
rewritten); no ZIP-recovery attempts; no PDF/DJVU equivalent (their integrity
stays with `qpdf --check` / `djvused -e n` per the skill).

## Phase 10: OPF/NCX edge cases (proposed 2026-08-30)

- [x] **Strip illegal `page-map` attributes from `content.opf`.** Older conversions (like HarperCollins / Anna's Archive files) include `<spine ... page-map="page-map">`, which fails epubcheck because the attribute is not standard. *(Shipped in v0.25.0 under the opt-in `--fix-page-map` flag (counter `page_map_stripped`), following the structural-repair gating: epubcheck-validity fixes are opt-in, never part of the well-formedness-only core.)*
- [x] **Inject a fallback `class` attribute into `<pageList>` in `toc.ncx`.** Older conversions often leave `<pageList>` without a class, causing RSC-005 `missing required attribute "class"` errors. Adding `class="pages"` to classless elements cleanly bypasses this without regression. *(Shipped in v0.25.0 under the same flag (counter `pagelist_class_added`); a pageList already carrying a class is untouched, and `--all` includes both.)*

### v0.22.0 ship note (2026-08-30)

All four Phase 9 boxes shipped in one release: the audit's full-entry CRC
sweep with the CORRUPT verdict (never mislabeled EMPTY; emptytext steps
aside), the sweep's sub-reason split, the flipped CRC test plus
`_unreadable_reason` unit coverage, and the phase-1 §2 sync retiring the
hand-rolled zipfile sweep.

### Pending Repair Implementations
- **Strip Invalid/Deprecated Attributes (RSC-005):** `testing_facility/books_to_fix/Lost Lambs (Madeline Cash) (z-library.sk, 1lib.sk, z-lib.sk).epub` throws `RSC-005` errors for `page-progression-direction` on `<package>`, `epub:type` on `<body>`, `aria-label` outside of allowed namespaces, and unwrapping illegal `<span>` tags. bindery-cli currently intercepts `<li value="..">` via `--strip-invalid-value`, but needs a broader pass to scrub these specific deprecated HTML5/EPUB3 attributes when targeting EPUB2 compliance, or just to silence strict validation errors. **DECISION 2026-09-02 (Brandon, on the NEW-AUDIT brief): Option B — the attribute scrub ships as its own opt-in flag (v0.26.0), and the element downgrade ships separately with the CSS-protection work (v0.27.0); no per-book flags, no silent tolerance.**
  *(Attribute scrub shipped in v0.26.0 as `--strip-epub3-attrs` (counter
  `epub3_attrs_stripped`): the three named attributes, fixed documented set,
  content docs and the OPF both, lookalikes survive. The illegal `<span>`
  unwrapping half ships with the element-downgrade flag below.)*
- **Element downgrade shipped in v0.27.0 as `--downgrade-epub3-tags`**
  (counter `epub3_tags_downgraded`): `figure`/`section` to `div`,
  `figcaption` to `p`, existing classes kept and the semantic name appended
  (`class="figure"`); `css_protected_tags`/`style_block_tags` are
  parameterized over the tag set, so a book styling `figure { ... }` keeps
  its markup and its RSC-005 findings (preservation wins). Both flags are
  included by `--all` and epubcheck-gated.

### Convert `<figure>` / HTML5 elements in EPUB2 (RSC-005)
- **Target**: `The Cemetery of Untold Stories` throws 14 `RSC-005` errors for `element "figure" not allowed anywhere`.
- **Implementation**: Write a repair flag that detects EPUB 2 documents containing EPUB 3/HTML5 `<figure>` or `<figcaption>` elements and cleanly downgrades them to `<div>` and `<p>` tags with classes.
- **Target**: `Julie Chan Is Dead` throws `RSC-005` errors for `element "section" not allowed anywhere`. Same repair pattern: downgrade `<section>` to `<div>` with classes.

## Bug Reports

### calibredb add_format --replace crash (2026-08-31)
**Bug in `bindery library --install-to-calibre`:** `calibredb add_format` does not have a `--replace` flag (replacement is its default behavior when an existing format is found unless `--dont-replace` is used). This causes `bindery library ... --install-to-calibre` to crash with `calibredb: error: no such option: --replace` when attempting to swap repaired files back into the library, leading to `subprocess.CalledProcessError` on exit status 2.
**Fix:** Remove `--replace` from the subprocess call in `bindery/library.py` line 158.
*(Fixed in v0.23.1; RETIRED entirely by Phase 11 in v0.24.0 — the calibredb
subprocess is gone, so the crash class no longer exists.)*

### watermark anchored pass can delete whole paragraphs of real prose (2026-09-08 sweep)
**Bug in `--strip-watermarks`:** the anchored stamp regex
`<a\b[^>]*?oceanofpdf[^>]*?>.*?</a>` is DOTALL with no tag budget, so an
unclosed stamp anchor (exactly the kind of broken book this tool repairs)
swallows everything up to the next unrelated `</a>`, and when no pure wrapper
exists the fallback deletes the entire match. Demonstrated: two paragraphs of
real content vanish with count 1; the output is well-formed with equal or
fewer fatals, so the `no_worse` bar accepts it. `watermark.py:207-213`.
**Fix:** require the anchored match to be tag-free (or stamp-length visible
text) before falling back to whole-match deletion; anything larger goes to
`decisions_needed` for manual repair. *(Fixed in v0.32.0: the fallback now
fires only when the match holds nothing but the stamp, a tag-free body within
stamp length or a body whose visible text is exactly the watermark, and a
second `<a>` opening inside the match refuses it outright (the close belongs
to that other anchor). Larger matches are left in place and counted in
`RepairReport.watermark_refusals`, surfaced in every sweep summary and as a
`manual_watermark_repair` decision by `run phase1` on both the read-only and
apply paths. The anchored pass now decides all matches left to right and
applies deletions right to left, so a refusal can no longer be re-counted on
rescan. Regression tests pin the reported two-paragraph loss, the tag-free
long swallow, and the safe inline shapes.)*

### roman-numeral detector reads ordinary English words as page numbers (2026-09-08 sweep)
**Bug in `--strip-pagination`:** `_ROMAN_RE = [ivxlcdm]{2,7}\Z` (IGNORECASE)
matches words like `mid`, `dim`, `mix`, `lid`, `civil` (`number_value("mid")
= 1499`), so a standalone `<p>mid</p>` followed by a lowercase-starting
paragraph is a confident page-number hit and is deleted and merged away.
epubcheck is blind to deleted text, so `no_worse` passes. `pagination.py:44`,
`70-78`, `180-184`.
**Fix:** require uppercase romans, or validate with a strict roman grammar
(`M?(D?C{0,3}|C[MD])(X?L{0,3}|L?X{0,3})(I?V{0,3}|V?I{0,3})`). *(Fixed in
v0.32.0, taking the grammar option to keep lowercase front-matter romans
working, with one addition the demonstrated words forced: the grammar alone
still accepts `mix` (M+IX = 1009), so a roman is a page number only when it is
well-formed AND its value stays under 100, where real roman page numbers live
and words do not. The audit pagenumbers analyzer's verbatim-twin detector got
the same fix; the audit copy's deliberately different year handling is
untouched (still its own question). Tests pin the word list, the surviving
numerals, and the cap.)*

### install_format can write a wrong size into metadata.db for stray files (2026-09-08 sweep)
**Bug in `library --apply --install-to-calibre` without `--id`:**
`iter_epubs` yields uncatalogued stray `.epub` files inside book dirs; the
resolver returns None, the `(id)` directory-name guess fires, and the
remove+add batch re-registers the catalogued format name with the stray
file's size. Demonstrated on a synthetic library: the catalogued 999-byte
file untouched on disk while `data` recorded 8123 bytes. Silent,
order-dependent catalog corruption; a stale `(N)` directory for a deleted
book instead raises an uncaught `TypeError` at the `book["path"]` access.
`library.py:169-231`.
**Fix:** when the id came from the directory guess, verify the target
matches the DB's book path and format name before the batch; otherwise save
in place and warn without touching the row, and catch the `TypeError` path.
*(Fixed in v0.32.0: a guessed id now drives a row update only when
metadata.db corroborates it — the books row exists, the file sits in that
book's catalogued directory, and when an EPUB row exists it carries that
row's stored name. Any other guess (a stray file in a book directory, a
stale `(N)` directory whose book is gone, the resolver's id gone from
`books` mid-run) saves the repair in place with a warning and leaves the
catalog untouched; the `TypeError` on the missing books row is gone. The
existing verified-guess fresh-placement and wrong-directory-degrades tests
are unchanged in behavior. Also rode along here: spec.md's install section
still described the v0.31.0-retired remove+add `batch()`; it now describes
`set_format`. Tests pin the stray-size incident shape and the stale-directory
save.)*

### EPUB2-targeted structural fixes fire on EPUB3 books under --all (2026-09-08 sweep)
**Bug in `--strip-epub3-attrs` / `--downgrade-epub3-tags` via the run verbs
and `--all`:** neither fix checks `package@version`, so every EPUB3 book in
the sweep gets `epub:type`/`aria-label`/`page-progression-direction` stripped
and EPUB3 tags downgraded. Two demonstrated outcomes: (a) a repairable EPUB3
book gains a net-new error from stripping `epub:type="toc"` off the nav and
the whole repair is rejected as a regression; (b) a legal count-neutral
`epub:type="chapter"` is silently removed because the gate only sees
epubcheck counts. phase1 and phase3 always pass `--all`.
`epub.py:251-260`, `903-937`, `957-967`; `cli.py:830`, `976-987`.
**Fix:** gate both fixes on the package version carried in `opf_text`, or
make them finding-driven (only strip what the before-pass epubcheck flagged).
*(Fixed in v0.32.0, taking the version-gate option: both fixes are licensed
by `package_version(opf_text)` and are inert on EPUB 3 packages and when no
version can be read. This kills both demonstrated outcomes: the repairable
EPUB3 book no longer gains a net-new error from a stripped nav `epub:type`,
and the legal count-neutral `epub:type="chapter"` survives. The finding-
driven alternative was rejected because it would still silently mutate legal
markup whenever an unrelated finding existed; the gate keeps EPUB3 books
byte-identical.)*

## Phase 11: Migrate install-to-calibre to native cquarry API (proposed 2026-08-31)

*Context: `bindery library --install-to-calibre` currently shells out to the external `calibredb add_format` CLI binary. This is fragile (it crashed on 2026-08-31 due to a non-existent `--replace` flag) and bypasses the transaction and trigger-safety guarantees built into the `cquarry` library. Since bindery-cli already imports `cquarry` for path resolution, it should adopt the native write module.*

- [x] **Adopt `WritableCalibreDB` for format installation:** Replace the `subprocess.run(["calibredb", ...])` call in `bindery/library.py` with `cquarry.write.WritableCalibreDB.add_format()`. This keeps all database writes safely centralized in `cquarry` and eliminates the dependency on the external Calibre CLI for structural sweeps.
  *(Shipped in v0.24.0 as `install_format()`: the file is placed atomically
  first — an in-place replace over the catalogued file (same path, same
  `data.name`) or a fresh placement under the repaired file's stem when no
  EPUB row exists — then the row is re-registered via `remove_format` +
  `add_format` in one `batch()` (`add_format` refuses duplicates), so the
  stored size stays truthful and the book lands in `metadata_dirtied`
  (neither happened under the old raw-CLI flow). The guess-only fallback
  honors `CALIBRE_DBPATH` (the old calibredb library contract) and degrades
  to the in-place save when no library can be located; a database failure
  degrades the same way rather than losing the repair. `uv.lock` moved to
  cquarry 1.9.0 in the same release, closing NEW-AUDIT Stage 1's pending
  note. Tests 240 → 244.)*

## Phase 12: Top epubcheck error resolution from full-library sweep (proposed 2026-09-05)

*Context: A diagnostic sweep of the full Calibre library on 2026-09-05 (`testing_facility/top500candidates/REPORT.md`) catalogued the top recurring error codes across all candidates: RSC-005 (164,302), RSC-020 (8,431), PKG-010 (4,112), RSC-007 (3,219), RSC-012 (1,940), and HTM-025 (800). Evaluating these against spec.md establishes which are safe to automate deterministically and which must remain rejected under the safety charter.*

- [x] **Opt-in missing resource pruning (`--prune-missing-resources`):**
  - **PKG-010 (missing package resource):** When the OPF manifest lists an `<item>` whose file is absent from the archive, prune the orphaned declaration. Strictly scoped to non-spine assets (fonts, stylesheets, unused media); missing spine documents remain classified as damaged fragments and are never silently dropped.
  - **RSC-007 (referenced resource not found):** When content documents reference absent external assets, prune dead `<link rel="stylesheet">` tags and strip `href` attributes from anchors targeting non-existent files. Missing `<img>` elements are pruned or replaced with their `alt` text under character conservation.
  *(Shipped in v0.28.0. Fixture reality check: Swann's Way, the staged RSC-007 book, carries exactly one defect shape — 29 identical `<link rel="stylesheet" href="../page-template.xpgt">` elements for an Adobe page template the converter never copied in; all 29 prune, zero regression. Death Masks, the staged PKG-010 book, has NO missing resources at all: its 34 PKG-010s are "file name contains spaces" warnings about the zip entry names, not absent files, so nothing is prunable there — see the `--encode-url-spaces` note below. Counters: `manifest_items_pruned`, `dead_links_pruned`, `missing_file_hrefs_stripped`, `missing_imgs_unwrapped`, `missing_imgs_pruned`.)*
- [x] **Opt-in broken anchor stripping (`--strip-broken-anchors`):**
  - **RSC-020 (fragment identifier not defined):** When an `href` points to a non-existent `#fragment` target, remove the `href` attribute rather than fabricating an ID. Inner anchor text is preserved byte-for-byte under character conservation.
  - **RSC-012 (fragment points to wrong element):** For content anchors targeting illegal elements, strip the invalid fragment. For NCX `<content src="doc.xhtml#bad_id"/>` references, strip the fragment to point directly to the containing document, preserving chapter navigation.
  *(Shipped in v0.28.0. Sodom and Gomorrah, the staged RSC-012 book, is Mobipocket `filepos` drift: the converter re-split the flow and every NCX target points one file late. Per the charter the repair strips the fragment and keeps the document target — it never re-points at the guessed sibling document, because the 9th navPoint's id exists nowhere at all. Fixture run: 9 NCX fragments + 2 in-content Notes anchors stripped, 11 RSC-012 cleared, zero regression. Counters: `broken_fragment_hrefs_stripped`, `nonfile_scheme_hrefs_stripped`, `ncx_fragments_stripped`.)*
- [x] **URI and entity normalization (HTM-025):**
  - **Attribute ampersand escaping:** Ensure `transforms.escape_bare_amp` covers bare ampersands inside URL attributes (such as query strings `?a=1&b=2` converted to `?a=1&amp;b=2`).
  - **Non-registered URI schemes:** Strip unresolvable proprietary or local URI schemes (e.g. `scrivcmt://`, local `file:///` paths) from anchor `href` attributes under `--strip-broken-anchors`, retaining visible prose.
  *(Shipped in v0.28.0. The ampersand half already worked — the transforms run on raw text, so attribute values were always covered; the behavior is now pinned by an explicit test rather than an assumption. The scheme half ships inside `--strip-broken-anchors` with a fixed resolvable set (http, https, mailto); Swann's Way's single HTM-025 was a `kindle:embed:` anchor href, stripped with the anchor text kept.)*
- [x] **Fast sweep testing harness (`FastSweep.java` JVM parallelization):**
  - **Objective:** Integrate parallel JVM validation sweeps directly into the testing/benchmarking suite to bypass Python subprocess I/O bottlenecks during massive 7,000+ book dry-runs.
  - **Context:** Standard `epubcheck` execution in a Python `subprocess.run` loop takes ~3 seconds per book (6.5 hours for the library). The `FastSweepExtract.java` prototype proved that instantiating `com.adobe.epubcheck.api.EpubCheck` directly and routing `CheckingReport` to a `StringWriter` within a Java `parallelStream()` reduces this to minutes by sharing the JVM startup cost and saturating all CPU cores.
  - **Implementation Details for Future Agents:**
    1. Expand `scripts/FastSweep.java` (or merge `FastSweepExtract.java` into it) to parse CLI flags (e.g., `--mode=audit`, `--mode=extract`).
    2. Write an integration wrapper in `bindery-cli` that compiles the Java file (targeting `--release 25`) and invokes it against a provided directory.
    3. **Test Fixtures:** Use the sample candidate EPUBs pre-staged in `testing_facility/top500candidates/` (which includes confirmed instances of `RSC-005`, `RSC-020`, `PKG-010`, `RSC-007`, `RSC-012`, and `HTM-025`) to verify the Java AST parsing and error-code extraction before running it across the live library.
  *(Shipped in v0.28.0 as scripts/FastSweep.java + scripts/fast_sweep.py — see patchnotes for the full notes; the prototype's extract mode never actually worked: CheckingReport buffers its messages and writes nothing until generate(), which NPEs without a preceding initialize(); the merged harness follows the CLI's own initialize → validate → generate lifecycle and extracts codes from the JSON. The two prototypes' stdin + parallelStream shape was otherwise kept. Measured on the 14 staged fixtures: 14 books in ~4.5 s (all cores) vs ~3 s/book sequential.)*

### Phase 12 findings postscript (2026-09-05)

The REPORT.md code labels do not describe the actual defects in the staged fixtures;
the underlying conditions are what the repairs target:

- **Death Masks ("RSC-020 fragment not defined" + "PKG-010 resource missing"):** all 39
  manifest items exist. The 67 RSC-020s are "not a valid URL" (literal spaces in OPF
  hrefs and NCX srcs; fixed by `--encode-url-spaces`), and the 34 PKG-010s are
  "file name contains spaces" WARNINGS about the zip entry names themselves. Clearing
  those would mean renaming archive entries and rewriting every reference — a viable
  future flag, deliberately not attempted here. After repair: 0f/1e/34w (the one error
  is an `<img id="Picture 0">` RSC-005, out of scope).
- **Swann's Way ("RSC-007 missing resource" + HTM-025):** 29 identical dead Adobe
  page-template `<link>` elements plus one `kindle:embed:` anchor. After
  `--prune-missing-resources --strip-broken-anchors`: 0f/2e/0w.
- **Sodom and Gomorrah (RSC-012):** Mobipocket `filepos` anchors pointing one split
  file late. After `--strip-broken-anchors`: 0f/42e/0w (the 42 survivors are RSC-005,
  the declared non-goal).
- **epubcheck 5.3.0 surfaces disagree on counts:** the human listing reports raw
  message totals (Swann's Way: 31 errors) while the `--json` checker block reports
  deduplicated ones (2). bindery's gate reads the JSON surface consistently for both
  measurements, so gate decisions are unaffected — but audit CSVs produced by
  FastSweep's audit mode carry the raw CheckingReport counts, which are larger than
  the JSON surface the gate re-measures with. Candidate selection only (fatals > 0,
  clean-book skipping), so the mixing is safe today.
- Related discovery, unchanged on purpose: `validate.py`'s persistent daemon launches
  `java -cp ".:epubcheck.jar"` without epubcheck's `lib/` dependencies, so on this
  machine it has always failed its first check and silently fallen back to the
  subprocess JSON path. Fixing the classpath would switch gate measurements from the
  deduplicated JSON counts to raw CheckingReport counts — a behavior change to the
  gate that deserves its own decision, not a drive-by edit.

Non-goals: No bulk repair of RSC-005 schema/content-model violations. With 164,302 instances, arbitrary schema repair violates the explicit non-goal in spec.md ("Fixing RSC-005 schema/content-model violations in bulk"). Restructuring arbitrary markup requires semantic human judgment; attempting to automate it risks silent content corruption, tag swallowing, or destroying valid styling. Specific, narrowly bounded RSC-005 sub-cases remain opt-in under their own flags (`--strip-epub3-attrs`, `--downgrade-epub3-tags`, `--unwrap-block-in-inline`, `--strip-invalid-value`, `--unwrap-illegal-tags`, `--add-img-alt`, `--fix-id-colons`); arbitrary bulk repair is rejected. No spine-item manifest pruning (missing spine documents indicate corrupt archives or broken fragments, not safe pruning candidates). No text deletion in anchor unwrapping (character conservation is absolute).

## Phase 13: machine-readable audit + the acquisition run slices (proposed 2026-09-05)

*Context: the acquisition pathway (`~/docs/Calibre Library/.claude/skills/`) is
being promoted from agent-prose into first-class CLI subcommands across the lane.
Brandon's design: `bindery run phase1` for the EPUB slice of pre-import vetting,
`bindery run phase3` for the scoped post-import repair pass, with CalibreQuarry
owning the orchestration (`cquarry run phase1/2/3`, its roadmap Phase 17) and the
manifest that hands state between phases. The prerequisite on this side is a
machine-readable audit; the run verbs wrap what already ships. No new repair
classes, no behavior change to existing modes.*

- [x] **`audit --json FILE`**: per-file analyzer verdicts in the same shape
      `library --json` already emits (audit is console-only today; the
      subparser has no JSON flag). This is the contract every downstream
      consumer of the phase-1 EPUB slice reads.
      *(Shipped in v0.29.0. All three modes write it: directory, library,
      and single-book. The always-on archive/spine verdicts appear OK when
      they were silent; emptytext is omitted from a record when the archive
      verdict owns the body-text story; a scan error becomes its own record
      shape (`status: "error"`). `--json` with `--id` accepts exactly one id,
      because each single-book run writes the file wholesale.)*
- [x] **`run phase1 DIR [--json OUT] [--apply-lossy] [--backup DIR]`**: the
      EPUB slice in the phase-1 skill's documented order (corruption sweep,
      epubcheck, content battery, monolithic, watermark dry-run,
      repairability). Gated repair applies only under `--apply-lossy`, which is
      the recorded lossy-strip consent; without it the verb is read-only.
      Exit codes 0/1/2 per the `library` contract. No DRM scan, no duplicate
      screen, no manifest assembly (CalibreQuarry's by the frontend split).
      *(Shipped in v0.29.0. Two composed stages: the audit battery through its
      own --json payload, then ONE fused repair sweep with the --all set: the
      skill ran watermark detection and repairability as two sweeps, the verb
      pays epubcheck once and reads watermark hits out of the per-book fix
      summary. Read-only until --apply-lossy; --backup passes through (keep
      backups outside the vetted directory). A consent question is not an
      error: it lands in decisions_needed and the exit code stays on the
      library contract.)*
- [x] **`run phase3 --ids CSV [--json OUT]`**: wraps
      `library --id ... --sweep --only all --apply --all --install-to-calibre`
      with a pre/post summary and mechanically refuses unscoped library-wide
      sweeps (encodes the phase-3 skill's step-10 scope warning as an exit 2
      instead of a prose warning).
      *(Shipped in v0.29.0. Run from the library directory (cwd-resolved like
      audit library mode). The verb drives the shipped library runner through
      the real parser, so its flags cannot drift from the subcommand it wraps.
      The pre/post summary sums before/after epubcheck counts over the swept
      books; books left partial or unreadable surface as decisions_needed.
      The verb is the apply step by design: the batch was vetted in phase 1,
      and the gate plus the atomic-replacement contract still govern every
      write.)*
- [x] **Non-interactive contract**: every `run` verb takes
      `--non-interactive`; prompts never fire off a TTY, and open questions
      surface in the JSON as `decisions_needed` for the calling agent or user.
      *(Shipped in v0.29.0 with the verbs themselves. Nothing in a run verb
      prompts (none ever did; the flag makes the orchestrator contract
      explicit and is recorded in the payload). decisions_needed entries: the
      apply_lossy consent question in a read-only phase1, manual_repair and
      investigate for partial/unreadable books in phase3.)*
- [x] **Skill sync**: the phase-1 and phase-3 skills name the run verbs once
      shipped. Floor, not ceiling, per the standing rule.
      *(Done for v0.29.0, 2026-09-06: the phase-1 skill's EPUB section names
      `run phase1` as the one-verb wrapper (the fused sweep pays epubcheck
      once; the JSON contract and `--apply-lossy` consent named) and the
      phase-3 skill's step 10 names `run phase3` with the mechanized scope
      refusal. The skill files live in the library directory, outside this
      repo; per the floor rule their per-check commands still stand on their
      own.)*

Non-goals: no manifest format ownership (CalibreQuarry owns the
`acquisition-manifest` schema and the orchestration; this repo emits reports
and consumes id lists); no TUI beyond the existing audit rendering; no PDF/DJVU
work (never this repo's charter).

## Phase 14: hardening backlog from the 2026-09-08 audit sweep (proposed 2026-09-08, digging only)

*Context: a five-agent adversarial sweep of the whole repo (repair pipeline,
audit, CLI/library/validate, tests and scripts, docs). No code was changed;
every finding below was demonstrated against the shipped code, most with a
live run. The four sharpest bugs are written up under Bug Reports above; this
phase is the rest of the backlog. Nothing here is committed work: it is the
map of what the sweep found, for when work resumes.*

*Verification postscript (2026-09-08, an independent second batch re-derived
the five sharpest claims; all five CONFIRMED, with scoping notes): the
watermark loss is invisible to `no_worse` and can even clear a fatal, so the
book ships as a success; the window is the standalone `--strip-watermarks`
flag (the anchorless pass never fallback-deletes, and under `--all`
`--reserialize` closes the anchor first). The roman deletion needs a
120+ char prose neighbor (`PROSE_MIN`) and a lowercase-starting continuation
paragraph, and no test acknowledges word-shaped romans. The `install_format`
exposure is exactly directory-mode `--apply --install-to-calibre`; phase3
passes `--id` and is not exposed. The `fix_id_colons` dangling NCX ref
survives the gate whenever colon fixes outnumber NCX references (the minimal
1:1 case is refused as a noop), and the phantom count additionally triggers
the re-encode that the untouched-file guard exists to prevent. The EPUB3
hard reject needs a clean book; in already-error books the attribute strip
can be net-neutral and ships silently.*

### Repair-pipeline correctness

- [x] **Anchor attribute-name regexes to real start tags.** Three findings
      share one root cause: bare attribute-name regexes that are not
      quote-aware and not tag-anchored. `strip_invalid_value` matches the
      `value` in `data-value="42"` (the `\b` matches between `-` and `v`)
      and rewrites it to a malformed `<span  data->`
      (`transforms.py:375`); `_EPUB3_ATTR_RE` is not anchored to tags at all,
      so `strip_epub3_attributes` deletes visible prose like
      `Use epub:type="chapter" here` down to `Use here` (`epub.py:245-248`),
      contradicting that fix's own docstring. Reuse the quote-aware
      start-tag matcher + protected-span machinery the module already has,
      and a `(?<![\w:.-])value` lookbehind.
      *(Done in v0.32.0: `strip_epub3_attributes` now runs through the new
      `transforms.strip_attrs_in_start_tags`, which anchors the edit on the
      module's quote-aware `_START_TAG_RE` and never touches CDATA/comments;
      `strip_invalid_value` gained the `(?<![\w:.-])value` lookbehind, so
      `data-value`, `xvalue`, and `xml:value` all survive untouched. Tests
      pin the prose-mention case, CDATA/comment protection, multi-line start
      tags, and the lookalike attribute names.)*
- [x] **Make `fix_id_colons` consistent and honest.** It rewrites fragments
      of external URLs (`http://example.com/page#sec:1` becomes `#sec_1`,
      breaking the link; `transforms.py:524`, `536`, docstring at 509 claims
      otherwise) and rewrites colon-bearing `data-id`/`xml:id`; it never
      translates the NCX's `content src` fragments, so renaming
      manufactures dangling NCX refs end to end
      (`epub.py:976-979` vs the NCX branch at `873-901`); and it counts
      every matched `id` attribute even when no colon was replaced, so
      byte-identical reruns report phantom changes (`transforms.py:513-516`).
      *(Done in v0.32.0: only the bare `id` attribute is in scope (a
      `(?<![\w:.-])` lookbehind keeps `data-id` and `xml:id` untouched), a
      fragment is translated only when the target is not an absolute URI or
      protocol-relative reference (`_EXTERNAL_TARGET_RE`), the new
      `fix_ncx_src_fragments` runs in the NCX branch under `--fix-id-colons`
      so ToC `content src` fragments follow the rename, and the id replacer
      counts only actual colons replaced, so byte-identical reruns report 0
      and the untouched-file guard holds. Live evidence for this box showed
      up in the v0.32.0 dry run itself: two staged books were gate-REJECTED
      on `fix_id_colons` regressions manufactured the old way. Tests pin
      the external-URL, data-id/xml:id, phantom-count, and NCX-follow
      shapes.)*
- [x] **Run anchor stripping last and protect CDATA/comments everywhere.**
      `strip_broken_anchors`' id snapshot predates `unwrap_block_in_inline`
      and `unwrap_illegal_tags`, which can delete ids the snapshot thinks
      exist (`epub.py:980-997`); `strip_broken_tags` and
      `unwrap_illegal_tags` are not wrapped in the module's own
      protected-span machinery, so they edit inside CDATA (rendered text)
      and comments (`transforms.py:444-450`, `619-623`).
      *(Done in v0.32.0: the anchor pass now runs last in the content
      pipeline, and the id snapshot pre-pass replicates every id-moving fix
      ahead of it (reserialize, id-colon renames, both unwraps, the
      resource prune), so a fragment whose target an earlier fix deleted is
      stripped instead of left dangling; `_outside_protected` now forwards
      arguments, so `strip_broken_tags` and `unwrap_illegal_tags` (with its
      protected_tags set) are both decorated and never edit inside CDATA or
      comments. Tests pin the dangling-fragment shape and the protected
      spans for both transforms.)*
- [x] **Stop re-encoding non-UTF-8 documents.** Any fix that fires on a
      windows-1252 or UTF-16 document decodes with `replace` and re-encodes
      UTF-8, materializing mojibake under an XML declaration that still
      names the old encoding; usually well-formed, so the gate cannot see
      it (`epub.py:874`, `902`, `939`, `1027`). Detect and skip with a
      report entry, per the project's manual-repair philosophy.
      *(Done in v0.32.0: the write loop strict-decodes each NCX, OPF, and
      content document before any fix runs; a document that is not valid
      UTF-8 is copied byte-for-byte, counted in the new
      `non_utf8_docs_skipped` report entry, and left for manual repair.
      UTF-8 siblings in the same book are still repaired.)*
- [x] **Scope `--reserialize` to HTML roots.** A broken non-HTML `.xml`
      sidecar gets html5lib's HTML algorithm and comes back
      html/body-wrapped with `ns0:` prefixes, structurally rewritten while
      staying well-formed (`epub.py:938`, `reserialize.py:39-42`). Require
      an `html` root (or manifest media-type) before rebuilding.
      *(Done in v0.32.0, taking the html-root check: `reserialize_if_broken`
      requires an `<html` root before it will rebuild, so a broken page
      template or other XML sidecar is returned untouched instead of
      structurally rewritten. Tests pin the sidecar shape.)*
- [x] **Smaller repair papercuts:** `fix_ncx_playorder` can rewrite
      `playOrder="N"`-shaped text inside nav labels (unanchored pattern,
      `transforms.py:326-328`); mimetype fixes land in `report.fixes`
      without incrementing `files_changed` (`epub.py:837-840`);
      `fix_manifest_ids` is a silent count-0 no-op on single-quoted OPFs
      (`epub.py:59`); `css_protected_tags` misses namespaced
      (`svg|st`) and functional (`:is(st, w)`) selectors, and doesn't scan
      `.xpgt` templates (`transforms.py:419-421`, `epub.py:790-794`); the
      watermark `_norm` doesn't unescape entities so `OceanofPDF.com&nbsp;`
      stamps are missed; drop_duplicate_xmlns/strip_prolog_junk edit inside
      comments; the archive comment field is dropped by the rewrite.
      *(Done in v0.32.0, all seven: playorder resequencing is anchored to
      `<navPoint>` start tags so nav-label text is untouchable; mimetype
      fixes increment `files_changed`; `fix_manifest_ids` and its reference
      rewrites are quote-agnostic (the shadowed `_IDREF_ATTR_RE` definitions
      were unified into one quote-aware regex); the css selector boundary
      recognizes `svg|st`, `:is(st, w)`, and `:where(...)` forms, and the
      stylesheet scan collects `.xpgt` templates too; watermark `_norm`
      decodes entities so `OceanofPDF.com&nbsp;` pure-matches; both
      prolog/xmlns fixes run under the protected-span wrapper; and the
      archive comment rides the rewrite (`zout.comment = zin.comment`).
      Tests pin every one.)*

### audit.py correctness

- [x] **Read nav/NCX inside the open zip and resolve NCX srcs against the
      NCX's own directory.** The post-close `_read` of a manifest-declared
      but absent nav/NCX poisons `Book.corrupt`, so an otherwise healthy
      book with a leftover `toc.ncx` manifest entry is branded CORRUPT
      "re-source" (`audit.py:252-272`, `_read` at `173-177`). And `full()`
      resolves NCX `content src` against the OPF's directory, not the NCX's,
      so a spec-compliant nested NCX counts every target absent and can
      reach the FRAGMENT "quarantine" alarm (`audit.py:187`, `205-213`,
      `249`, `263-272`). Also `html.unescape` captured ToC hrefs, which are
      currently compared raw against archive names (`audit.py:258`, `268-272`).
      *(Done in v0.33.0: the whole ToC-accounting block moved inside the
      open-zip context, a declared-but-absent nav/NCX is recognized by a
      nameset check and skipped instead of poisoning `corrupt`, `full()`
      takes a doc-relative base override used for nav and NCX hrefs, and
      captured hrefs are entity-decoded before comparison. Tests pin all
      three shapes.)*
- [x] **Make archive/spine first-class in library mode.** `run_library`'s
      report loop iterates the content-analyzer tuple only, so the
      `archive`/`spine` branches and their section builders are dead code:
      a CRC-corrupt book prints `emptytext CLEAN` and exits 0 in library
      mode while directory mode says CORRUPT and exits 1, contradicting the
      v0.22.0 "its own verdict in every mode" promise (`audit.py:1565`,
      `1783-1803`, `1520-1558`).
      *(Done in v0.33.0: the archive and spine sections run unconditionally
      after the selected content sections — they are always-on verdicts and
      were already scanned and recorded in the JSON; only the console/rc
      path was dead. A CRC-corrupt book now prints its CORRUPT section and
      fails library mode with exit 1, matching directory mode. Test pins
      the corrupt-book asymmetry with surgical CRC damage.)*
- [x] **Harden the edges of the record pipeline:** a failed `--json` write
      raises after the scan and loses the whole run's summary and exit code
      (`audit.py:1616`, `2023-2043`); `--tag` tags books whose content hits
      the run itself marked expected-foreign (`audit.py:1830-1832` vs the
      rc filter); a decoded-href vs stored-name spine miss silently reads
      as EMPTY on a book full of prose, with no diagnostic
      (`audit.py:219`); DRM-encrypted entries report as CORRUPT
      "re-source" instead of their own status (`audit.py:173-177`); the
      broken-span heuristic treats duplicate trailing numbers
      (`part1a/part1b/part2`) and duplicate spine itemrefs as FRAGMENT
      (`audit.py:1066-1079`).
      *(Done in v0.33.0, all five: `write_audit_json` returns whether the
      write landed and every mode folds a failure into a trouble exit
      instead of a post-scan traceback; `--tag`'s content list filters
      expected-foreign hits (injection signatures stay always-defect); an
      unresolvable itemref is counted in `Book.spine_missing`, surfaced in
      the emptytext detail so EMPTY carries its diagnostic; entries
      declared in META-INF/encryption.xml get their own ENCRYPTED verdict
      ("DRM-protected; not repairable, skip") instead of CORRUPT
      re-source; and the broken-span heuristic dedupes trailing numbers
      so part1a/part1b/part2 reads as a consecutive span. Tests pin each.)*
- [x] **Delete the dead surfaces:** `audit.main()` + its argparse block
      duplicate `cli.run_audit_cmd` (`audit.py:2212-2315`);
      `analyze_brokentags` is unreachable (`audit.py:1260-1274`); duplicate
      imports at the top of the file; and standalone `audit <dir>` silently
      skips uppercase `.EPUB` files (`audit.py:1937`).
      *(Done in v0.33.0: main() and its argparse block deleted (the CLI
      subcommand is the only entry point), analyze_brokentags deleted, the
      duplicated import blocks collapsed, and run_directory's rglob is
      case-insensitive so a hand-added Book.EPUB is visible in directory
      mode too, matching library.iter_epubs.)*
- [x] **Smaller audit papercuts:** `_Blocks.handle_endtag` pops the stack
      top regardless of which tag closed, so mis-nested blockquotes lose
      their in_quote guard (`audit.py:739-743`); nav selection matches
      `data-nav` by substring (`audit.py:196`); tagging failure can surface
      an undocumented exit 3 (`audit.py:2203-2208`); the JSON `analyzers`
      list omits the always-on archive/spine verdicts and the console
      problem counter can disagree with `summary.problems`
      (`audit.py:1603`); `--min-chars`/`--thin-chars` are not validated
      against each other; duplicate zip entries silently resolve last-wins
      with no note in the record.
      *(Done in v0.33.0: handle_endtag pops only the matching open tag (a
      stray closer can no longer pop an unrelated ancestor); the nav
      property matches whole tokens so properties="data-nav" is not the
      nav; exit 3 (flagged run + failed tagging) is documented in the
      module contract instead of being changed; the JSON analyzers list
      includes the always-on archive/spine and the directory-mode console
      counter counts books, matching summary.problems; min-chars above
      thin-chars is a usage error (it silently shadowed every THIN
      advisory); and duplicate archive entries are counted into
      Book.dup_entries and noted in the archive verdict. Tests pin the
      nav token, duplicate counting, and the threshold refusal.)*

### Apply-path and oracle safety

- [x] **Move the write-back inside the per-book exception isolation.**
      `make_backup`/`atomic_replace`/`install_format` sit bare in the
      sweep loop, so an ENOSPC or EACCES partway through a multi-hour run
      aborts raw with no summary, no JSON, and no record of what was
      already applied (`cli.py:498-508` vs `544-557`). Wrap the block,
      record an error Outcome, and still emit the report; a run journal
      (one line per applied book, resumable) is the stronger version.
      *(Done in v0.33.0: the write-back is wrapped, an OSError records an
      `apply failed:` error Outcome and the run continues to its summary
      and JSON with exit 2; the resumable run journal is deliberately not
      built, since isolation plus `--json` already preserves the record
      and the journal is a second write surface next to the library.)*
- [ ] **Drive the exit code from partial books.** The documented contract
      says exit 2 means "ran fine but some books are in trouble", but
      partial books land in `still_fatal` and return 0, in both `library`
      and phase3, while phase1 maps partial to problem/exit 2; the layers
      disagree and scripts can miss trouble (`cli.py:260`, `526-534`, `634`,
      `1030-1032`). **GATED on Brandon: this changes the documented exit
      contract. Both options, for the decision:**
      (A) *Unify on "trouble"*: `library` and phase3 return 2 when
      `still_fatal` is non-empty, exactly as phase1 already does. Scripts
      get an honest trouble signal for books that were repaired but still
      cannot open; cost: existing cron/scripts that treat exit 2 as "do not
      apply, review" see no behavior change, but ones that treat 2 as
      "rejected, re-run after fixing" now also stop on still-fatal books.
      This is the recommendation: the layers should agree, and "a book
      still cannot open" IS trouble by the contract's own words.
      (B) *Document the divergence*: keep `library`/phase3 at 0 for partial
      books and write the asymmetry into README/spec (partial books are
      advisory; read the report or --json for them). Zero behavior change;
      cost: scripts that care must parse the report, and the contract's
      exit-2 sentence stays subtly incomplete.
      Either way the exit-code documentation lands with the decision.
- [x] **Give backups overwrite protection and keep them out of the
      candidate set.** `make_backup` clobbers an existing backup, so a
      second `--apply` destroys the only copy of the author original
      (`library.py:37-41`); a `--backup` dir inside the library root gets
      its `.epub`-named copies swept as candidates on the next run
      (`library.py:29-34`). Refuse overwrite (or rotate) and reject an
      in-tree backup path.
      *(Done in v0.33.0: `make_backup` rotates (book.epub.bak, .bak2, .bak3,
      ...) so the first backup always keeps the author original, and
      `run_library` refuses a `--backup` directory resolved inside the
      library root with a usage error (exit 1). The phase1 apply test's
      fixture, which had been violating phase1's own keep-backups-outside
      contract, was corrected in the same commit.)*
- [x] **Make `--workers` parallelism real and bound the daemon.** The
      epubcheck daemon holds its lock across the whole blocking
      round-trip, so every worker serializes behind it and `--workers N`
      degrades to serial exactly when epubcheck is present
      (`validate.py:155-169`, `cli.py:213-255`); the daemon path ignores
      `timeout` entirely, so a JVM hang hangs the sweep forever
      (`validate.py:196`); a dead daemon never resets `_proc`, so every
      later book reads as `error` for the life of the process
      (`validate.py:160-170`); a failed daemon start leaks its
      `/tmp/bindery-daemon-*` workdir and retries the javac compile per
      book (`validate.py:130-153`); and `--only ncx` crashes with a raw
      RuntimeError on encrypted archives because `_select` probes outside
      the exception net (`epub.py:688`, `cli.py:184`).
      *(Done in v0.33.0, the daemon half: the daemon is now a bounded pool
      (`set_daemon_pool_size(workers)`, default 1 = the historical single
      daemon) so N workers check on N warm JVMs instead of serializing
      behind one pipe, a busy pool falls back to the subprocess oracle
      rather than blocking, and the roundtrip is bounded by `timeout` via
      select. A daemon that dies or wedges is torn down and marked final,
      a failed start is never retried and leaves no workdir, and a pool
      whose first daemon dies without ever answering exhausts itself once
      so the sweep degrades to pure subprocess checks instead of
      respawning a JVM per book. The leaky `__import__` calls became real
      imports. Measured on the staged fixtures: 8 books, 4 workers, two
      ~10s waves = 20s total (was ~4.4s/book serialized). The recorded
      evidence for Brandon's daemon-classpath decision grew sharper: this
      machine's javac is 27-ea while java is 25, so the compiled class can
      never load and the daemon has silently never served a single count
      here; and when the daemon is forced to run (single-file source
      launcher), its CheckingReport counts diverge from the CLI JSON
      oracle on the same book (243 vs 4 errors), so enabling it would
      change gate outcomes. Both facts belong in the decision. The
      `--only ncx` encrypted-archive crash is also fixed here:
      `ncx_uid_mismatch` now catches the RuntimeError that `z.read`
      raises on zip-encrypted entries, so a DRM'd book is not a candidate
      rather than a crash.)*
- [x] **Smaller apply papercuts:** `--limit -1` and a missing
      `--audit` file raise tracebacks instead of usage errors
      (`cli.py:434`, `373`); the fresh-format branch of `install_format`
      registers the format under the name `repaired`
      (`library.py:223-230`); phase1 ignores `--backup` without
      `--apply-lossy` silently; the lossy override can upgrade a noop to
      partial in the still-fatal listing (conservative direction only).
      *(Done in v0.33.0, all four actionable items: `--limit < 1` and a
      missing `--audit` file are usage errors with exit 1; the fresh-format
      branch registers under the catalogued file's own name instead of the
      temp file's throwaway `repaired` stem; and phase1 prints the usual
      dry-run note when `--backup` comes without `--apply-lossy`. The last
      item (lossy override upgrading a noop to partial) is conservative-
      direction-only output labeling and stays as is: the still-fatal
      listing over-reporting a book that needs eyes is the safe side.)*

### Tests

- [x] **Add the missing direct tests for the safety contract:** `gate()` and
      `no_worse()` have zero direct tests (the subtle fatal-fixing
      error-unmasking branch is asserted nowhere); `_EpubcheckDaemon` is
      completely untested (and hides the no-reset liveness bug above);
      `--strip-broken-tags` has zero tests anywhere despite being one of
      the three named lossy strips; `--strip-watermarks`' gated apply path
      has no analogue of the pagination gate test; `fix_ncx_playorder`
      (core, always-on) is untested; `atomic_replace`'s failure path
      (temp cleaned, target untouched, re-raise) has no failure-injection
      test; `--install-to-calibre` CLI wiring and library-mode `--tag`
      end-to-end are untested.
      *(Done in v0.33.0. Landed with their fixes earlier in the batch: the
      daemon pool logic and bounds tests, the watermark protected-span and
      broken-tags CDATA tests, and the playorder nav-label tests. This
      commit adds the rest: direct gate()/no_worse() tests including the
      fatal-fixing error-unmasking branch, the watermark gated-apply
      analogue of the pagination gate test, atomic_replace failure
      injection (temp cleaned, target untouched, re-raise), the
      --install-to-calibre parser wiring, and a library-mode --tag
      end-to-end run through cquarry's write path against a real temporary
      metadata.db.)*
- [x] **Clean the weak 2%:** delete the three mid-file
      `if __name__ == "__main__": unittest.main()` blocks
      (`test_audit.py:806`, `:943`; `test_cli.py:475`) that make direct
      file runs silently skip ~40 tests; drop the constant-assertion
      `test_all_tuple_has_ocr`; consolidate the verbatim-duplicated
      roman/number implementations and their twin test classes (audit copy
      lacks pagination's year exclusion, which is its own question);
      remove the dead `sys.argv` patches in three CLI tests; strengthen or
      delete `test_non_interactive_flag_wiring` (asserts argparse, not the
      runner); de-alias the `pagenum`/`emptytext`/`ocr` module aliases.
      *(Done in v0.33.0, except the consolidation, which is deliberately
      not a consolidation: the two roman/number implementations are no
      longer verbatim twins (the audit copy's year handling differs on
      purpose, see the Bug Reports fix note), so merging them would force
      the year question without a decision; the aliases are gone instead,
      so the twin classes now name one module honestly. All three
      `__main__` blocks deleted, the constant-assertion OCR test dropped,
      and test_non_interactive_flag_wiring's coverage folded into the
      phase1 runner test, which now passes --non-interactive and asserts
      the flag reaches the JSON payload.)*
- [x] **Add a ruff step to run_tests.sh** so the local loop matches CI
      (`uvx ruff check . && uvx ruff format --check .`), and note the
      html5lib-dependent tests silently skip without uv.
      *(Done in v0.33.0: run_tests.sh runs `uvx ruff@0.16.2 check .` and
      `format --check .` (CI's exact pin) before the suite, with a stderr
      note when uvx is missing, and notes when the html5lib-dependent
      tests will skip without uv. This caught today's own UP012/format
      CI failures' root cause: the local loop did not run what CI runs.)*

### Scripts and repo hygiene

- [x] **Retire the subsumed find_* diagnostics.** All ten `scripts/find_*.py`
      detection wedges became shipped flags with tests
      (the css one says so in its own docstring port note); keeping both
      copies means future behavior changes land in one only. Delete (git
      preserves) or move to an attic note; also delete `sweep.sh` and
      `FastSweepExtract.java` (superseded by `fast_sweep.py --mode=extract`
      and `FastSweep.java`), keeping `fast_sweep.py` + `FastSweep.java`
      (a live, different tool: one JVM saturated across cores vs the
      library sweep's N subprocesses). The untracked `.class` files are
      local build leftovers; already gitignored.
      *(Done in v0.33.0: all ten find_*.py, sweep.sh, and
      FastSweepExtract.java deleted; fast_sweep.py + FastSweep.java stay.
      README's companion-scripts section notes the removal and where the
      capabilities live now.)*
- [x] **Decide what test_facility/ is.** It carries 10 tracked commercial
      EPUBs (muse-of-nightmares, last-man-out, etc.; only leaves-of-grass is
      public domain), a second never-run "suite" of bare functions that
      `unittest discover` reports as 0 tests, and it mutates the tracked
      books in place. `.gitignore`'s own comment says books are ignored to
      prevent IP leaks, but that rule only covers `testing_facility/`;
      `test_facility/` predates it. At minimum stop tracking the EPUBs and
      delete or properly convert the dead suite; stripping them from
      history is the thorough option (repo is public).
      *(Done in v0.33.0, executing the recorded recommended default:
      `git rm --cached` on all 14 tracked EPUBs (files stay on disk),
      gitignored forward-only, and the dead never-run suite files deleted.
      History stripping (the force-push option) remains Brandon's separate
      call and is recorded as an open gated item.)*
- [x] **Mention validate.py's Java epubcheck daemon in the docs.** It is
      the first oracle tried at runtime, it compiles `_DAEMON_JAVA`
      (`validate.py:73-105`) into a tempdir, and nothing in README/spec
      admits it exists; it is also the scariest untested code in the repo.
      *(Done in v0.33.0: the README gate section and spec's epubcheck-gate
      section both describe the daemon (bounded pool sized by --workers,
      per-roundtrip timeout, fail-safe fallback to the subprocess oracle),
      and README's --sweep bullet stopped describing the pre-43d9e26
      broken classpath as current behavior. Pool logic and bounds are now
      tested hermetically; the "scariest untested code" verdict was true
      and is now much less true.)*

### Documentation (drift is real but narrow)

- [ ] **Reconcile spec.md with the shipped opt-ins.** The contract's
      non-goals forbid exactly what `--reserialize` (v0.3.0, the lone
      html5lib dependency) and `--strip-bad-attrs` (v0.4.0) do, and neither
      has a spec section; the OPF half of `--fix-ids` is missing;
      "the OPF is left untouched" is true only of the default pass; the
      NCX sentence at spec.md:52-53 is garbled (a dangling "to the OPF
      unique identifier" tail). A contributor reading spec.md first would
      conclude two shipped flags violate the charter.
      **GATED on Brandon: spec.md is the contract file. Draft proposal,
      ready to apply on a go (do not apply without one):**
      (1) Non-goals bullet 2 ("Repairing genuinely mangled structure
      (unclosed non-void elements, corrupted tag names, embedded VML/SVG)")
      currently contradicts two shipped flags. Replace with: "Repairing
      genuinely mangled structure remains opt-in and bounded: `--reserialize`
      (v0.3.0) re-parses malformed documents carrying an `<html>` root via
      html5lib and re-emits XHTML; it refuses non-HTML XML sidecars, and
      wholesale structural rewrites without a root to anchor on stay out of
      scope. Corrupted tag names stay covered by `--strip-bad-attrs`
      (v0.4.0) and `--unwrap-illegal-tags`."
      (2) Non-goals bullet 1 ("Fixing RSC-005 schema/content-model
      violations in bulk") gains a clause: "in bulk" is the operative
      words; the scoped, opt-in RSC-005 repairs
      (`--strip-epub3-attrs`, `--downgrade-epub3-tags`, version-gated)
      ship and stay.
      (3) Add the missing spec sections: `--fix-ids`'s OPF half (manifest
      item ids, spine idref/toc, fallback, media-overlay, EPUB 2 cover
      meta, single- and double-quoted) has no section; add one under the
      opt-in structural repairs.
      (4) Fix the garbled NCX sentence at spec.md:52-53 (drop the dangling
      "to the OPF unique identifier" tail).
      (5) Adjust "the OPF is left untouched" to "left untouched by the
      default pass; `--fix-ids`, `--strip-epub3-attrs`, `--fix-page-map`,
      `--prune-missing-resources`, and `--encode-url-spaces` edit it when
      requested."
      Everything in this draft matches shipped, tested behavior as of
      v0.33.0; applying it is a pure docs change waiting on the go.
- [x] **Fix the exit-code contract.** README and spec document usage
      errors as exit 1, but argparse-level misuse exits 2 (the same code
      as "book in trouble"); either a custom parser exit or updated docs.
      *(Done in v0.33.0, taking the docs option: README and spec now state
      that argparse-level misuse exits 2 before the tool's validation runs
      while the tool's own usage validations exit 1, and that partial
      books are advisory in library mode but trouble in phase1. The custom
      parser exit was rejected: remapping argparse's exit code would
      diverge from every other Python CLI's muscle memory for no script-
      visible gain, since the collision (2 = argparse misuse vs 2 =
      trouble) never occurs for a run that got past argument parsing.)*
- [x] **README completeness:** document `--min-chars`, `--thin-chars`,
      `--max-doc-chars`, and `--limit` in the flag reference; fix the
      `--only fatals` bullet to "needs `--audit` or `--sweep`" (it
      currently contradicts the `--sweep` bullet one line over).
      *(Done in v0.33.0: all four flags documented in the library flag
      reference and the `--only fatals` bullet corrected. The `--sweep`
      bullet's stale daemon description (written before the 43d9e26
      classpath fix and today's pool bounding) was refreshed in the same
      pass, which also completes the :1027 daemon-documentation box.)*
- [x] **CLAUDE.md's exception taxonomy** lists twelve structural repairs
      and three lossy strips but omits the four safe opt-ins
      (`--fix-ids`, `--add-img-alt`, `--strip-bad-attrs`,
      `--escape-unknown-entities`), which reads as a complete inventory.
      *(Done in v0.33.0: CLAUDE.md's hard-constraints section now names
      the safe opt-ins as their own group, including the URL-space
      encoder, and notes the twelve/three phrasing is not the full
      inventory.)*

### Completeness verdict from the sweep

*The engine is close to complete for its charter: the five-fixture core is
solid under adversarial attack (zip machinery, splice engine, entity
handling, and the gate all held), the suite is fast, deterministic, and about
95% load-bearing, and the docs are truthful everywhere except spec.md's
never-absorbed v0.3/v0.4 era. The gap between "almost as complete as it can
be" and "done" is one coherent theme, not scattered work: several opt-in
repairs trust their regexes where they should trust the parse (the
anchored-tag cluster), the audit's newest machinery (archive/spine) is not
wired into every mode it promises, and the apply path's failure modes (crash
mid-run, backup clobber, daemon stall) are unpadded. Fix the four Bug
Reports entries and the anchored-regex cluster before any further features;
everything else is backlog, not danger.*

## Phase 15: completeness spot-check analyzer (proposed 2026-09-10, from the phase-1 skill's own doctrine)

*(RAISED with Brandon 2026-09-10, batched with the session-start raises;
recommendation: APPROVE. Execution is gated on that go; the proposal
below is filed and costed. The Redwall run it names is a ready-made
fixture set, and the analyzer is one more member of the audit's existing
single-decompression-pass battery.)*

*The phase-1 skill's step 2 has a judgment step with no tool owner: the
completeness spot-check ("sample early, middle, and late pages ... read the
LAST content page to confirm it reaches real back matter rather than cutting
off mid-chapter"). Two consecutive phase-1 runs (2026-09-08 Strauss batch,
2026-09-10 Redwall batch) used a hand-rolled zipfile sampler in /tmp for it,
which is exactly the "hand-rolled substitute becomes permanent" pattern the
skill forbids; the skill allows only two inline checks, both with filed
phases here. The sampler also already needed one repair (href unquoting)
that the shipped audit gets for free. Proposal: an `audit completeness`
analyzer that, in the audit's existing single decompression pass, reports
per book: spine doc count, prose-doc count (>400 visible chars), the first /
middle / last prose doc's opening and closing text, trailing-ToC detection
(a final spine doc that is mostly chapter-heading lines), and the fraction of
unreadable docs. The 2026-09-10 Redwall run is the fixture set: Lord
Brocktree (real Epilogue doc before a trailing 520-char ToC), Mattimeo
(Chapter 50 verified as a real heading with prose after, inside a 174k-char
split doc), The Long Patrol (percent-encoded hrefs defeated the hand-rolled
reader; 55/57 prose docs).*
