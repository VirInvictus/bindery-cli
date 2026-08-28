# Bindery Patch Notes

## v0.19.0 (2026-08-28)

### Phase 4 complete: full cquarry integration

- **Accurate Calibre id resolution (no more guessing).** `--install-to-calibre` previously
  extracted the book id from the `(123)` directory-name fragment — a heuristic that replaces
  the wrong book whenever a directory was hand-renamed or its number no longer matches the
  catalog. New `library.CalibreIdResolver` builds a lazy, one-shot EPUB-path → id map through
  cquarry's own layout logic (`CalibreDB.get_format_path()`), so the id comes from
  `metadata.db` and renamed directories cannot misroute a replacement. The `(id)` regex
  survives only as the no-catalog fallback; with neither source the repaired file is saved
  atomically in place (previous behaviour).
- **Single-entity audits: `bindery audit all --id 1234`.** New `--id` mode fetches exactly one
  row through cquarry's `CalibreDB.get_book()` (no library-wide cache), resolves the EPUB via
  `get_format_path()`, and reports the same verdicts as directory mode. Composes with
  `--tag`: the book is tagged only if the audit flags it. Mutually exclusive with the
  directory argument.
- **Dependency unpinned.** `cquarry` moved from a frozen pre-1.1 commit to `@main`, matching
  Hermitage and CalibreQuarry — CI now always exercises current cquarry (1.6.x) instead of a
  year-old snapshot that lacked `get_book`/`add_tag`.
- **Roadmap:** Phase 4 (Format Path Resolution, Safe Tag Application, Single-Entity Fetching)
  and the Phase 6 "cquarry Integration" checkbox are closed.
- **Tests:** 204 → 216 (12 new) — the resolver (catalogued hit, DB-truth-over-directory-name,
  unknown file, missing catalog), the regex fallback, and the replace routing (resolver id
  wins, regex fallback, atomic in-place fallback with `subprocess` mocked); `run_single`
  (clean pass, unknown id, book without an EPUB) against a real minimal `metadata.db` +
  synthetic EPUB.

## v0.18.1 (2026-08-26)

**cquarry 1.2 adoption: audit tagging now actually reaches the write path, and tagged books regenerate their OPFs.**

- **Fixed: `audit --tag` was unreachable from the CLI.** v0.18.0 documented `--tag TAG` and shipped
  `_apply_audit_tag()`, but the `audit` subparser never registered the flag and `run_audit_cmd`
  never passed it — the write path existed only for Python callers. The flag now parses, defaults
  off (read-only audits stay read-only), and reaches `run_audit_library(tag=...)`; regression tests
  cover all three properties.
- **Tagged books are queued for OPF regeneration.** The pinned cquarry bump (1.1 → 1.2) means every
  `WritableCalibreDB.add_tag()` now records the book id in Calibre's `metadata_dirtied` queue —
  the table upstream consumes to decide which sidecar `.opf`s to regenerate (and re-push to wireless
  readers) at next startup. Previously a Bindery-applied tag would appear in the GUI but never reach
  OPF/wireless sync. Verified end-to-end against a synthetic library: flagged book tagged,
  `metadata_dirtied` gains exactly that id, clean books untouched.
- **Requires `cquarry` @ e19c24c** (v1.2.0); pin bumped accordingly.

## v0.18.0 (2026-08-25)

**cquarry 1.1 adoption: canonical format-path resolution and an opt-in tagging pass.**

- **Canonical EPUB resolution:** library-mode audits (`bindery audit ...` with no directory
  argument) now resolve each book's EPUB through `cquarry.db.CalibreDB.get_format_path()` instead of
  hand-building `<root>/<books.path>/<name>.epub`. The storage-layout logic lives in exactly one
  place across the ecosystem, and a missing file surfaces as a normal scan error naming the expected
  path.
- **`--tag TAG` (opt-in):** after a library-mode audit, apply `TAG` to every flagged book via
  cquarry's separate `WritableCalibreDB` write module (trigger-safe: registers Calibre's
  `title_sort`/`uuid4` SQL functions, bumps `last_modified`, cleans link tables before tags). The
  audit itself remains strictly read-only; nothing is written unless `--tag` is passed, and THIN
  emptytext advisories stay untagged. Books already carrying the tag are skipped (idempotent).
  Calibre must be closed so the write does not fight its lock.
- **Requires `cquarry>=1.1`.**
- **Tests:** suite still green (201 tests) plus an end-to-end smoke run against a synthetic library:
  foreign-language body text flagged, tagged `[Flagged]`, `last_modified` bumped, second run reports
  the book already tagged.

## v0.17.0 (2026-08-25)

**The default repair pass is well-formedness only again.** v0.14.0 had promoted six structural
repairs into the always-on `HTML_TRANSFORMS`, so a plain `bindery repair` could delete
attributes, unwrap elements, and fabricate content on a book whose only defect was a stray
ampersand — exactly what spec.md forbids. Each now requires its own flag (on both `repair` and
`library`); `--all` still enables everything: `--fix-empty-body`, `--fix-missing-title`,
`--fix-id-colons`, `--unwrap-block-in-inline`, `--strip-invalid-value`,
`--unwrap-illegal-tags`.

**The CSS precondition is real.** `--unwrap-illegal-tags` no longer trusts callers to scan
stylesheets first. Every `.css` entry in the book plus each document's inline `<style>` blocks
are scanned for illegal-tag names used as *element selectors* (`w { }`, `p st, x > w { }`,
`pagebreak.new:after {}`; class/id selectors like `.st` and `#w` do not protect), and those
names are protected for the whole book — styled formatting can never be silently destroyed.
Nested at-rules work (`@media print { o > sentence {} }`). This ports
`scripts/find_css_illegal_tags.py` into the library as `transforms.css_protected_tags`.

**Housekeeping:** git dependencies pinned to exact commits (vir-tui `d13ad0ed` = 2.0.0,
cquarry `4b771aa` = 1.0.2) instead of floating branches, so builds are reproducible;
`__init__.py VERSION` resynced with pyproject.toml (drifted three releases behind at 0.15.0 vs
0.16.3); first tags cut (`v0.16.3` backfilled on its release commit, `v0.17.0` now);
spec.md / CLAUDE.md / README.md brought back in line with the code (audit subcommand, the pins,
the restored opt-in contract); roadmap Phase 6 boxes closed; `run_epubcheck`'s docstring moved
above the daemon call where it is an actual docstring. Suite: 183 -> 201 tests.

## v0.16.3 (2026-08-24)
- **Fix**: Resolved `test_audit.py` import path failure.
- **Fix**: Fixed `unwrap_block_in_inline` data loss regex logic by capturing full text blocks.
- **Fix**: Added `--replace` flag to `calibredb_replace` to prevent DB crash when file exists.
- **Fix**: Corrected `analyze_brokentags` loop to properly analyze spines and docs instead of nonexistent iter method.
- **Fix**: Removed hardcoded JVM release target in `EpubcheckDaemon`.
- **Fix**: Added regex word boundaries in `fix_id_colons` to prevent URL corruption.
- **Fix**: Corrected `strip_invalid_value` regex to safely strip whitespace.
- **Fix**: Fixed empty tag replacement regex in `fix_missing_title`.

## v0.16.2 (2026-08-24)
- Replaced local `ui.py` module with standardized `vir-tui` package.

## v0.16.1 (2026-08-24)

- **Build:** Cleaned up orphaned scratch files to resolve strict ruff linting failures.
## v0.16.0 (2026-08-23)

---

### Core Upgrades

**Centralized Calibre DB Access:** Bindery has transitioned to the unified `cquarry` library for all read-only `metadata.db` accesses. The internal `bindery.audit` DB connection logic has been completely replaced with `cquarry.db.CalibreDB`. This inherits robust Calibre lock handling, database snapshotting, and ensures query logic stays perfectly synchronized with `CalibreQuarry` and `Hermitage`.


## v0.15.0 (2026-08-23)

### Features
- **Integrated EPUB Auditing (`bindery audit`):** Successfully absorbed the external `audit_epub` codebase directly into Bindery as a native subcommand. This transforms Bindery from a pure repair tool into a complete EPUB lifecycle toolkit (Audit -> Repair -> Validate). 
  - **`content`:** Identifies non-English text by sniffing the text blocks, reporting the predominant script block (e.g., Cyrillic, CJK) to catch untranslated or mis-encoded EPUBs.
  - **`pagenumbers`:** Detects books polluted with hardcoded print page numbers (using sliding window regex to find high-density sequential digits interrupting prose), seamlessly bridging into `bindery repair --strip-pagination` for the fix.
  - **`emptytext`:** Audits EPUBs to find effectively empty books or spine stubs (below configurable `--min-chars` and `--thin-chars` thresholds).
  - **`ocr`:** Scans for systemic OCR damage by detecting disproportionately high densities of hyphenation, disjointed characters, or garbage sequences.
  - **Library Integration:** Directly operates on a Calibre library tree or loose directories, producing actionable console reports designed to be piped into `bindery library --audit`.

## v0.14.0 (2026-08-22)

### Features
- **Supplementary Phase Structural Repairs:** Integrated six highly targeted regex-based transforms to resolve the most common markup-level EPUB errors across the library, all safely guarded by the EPUBCheck gate:
  - **Duplicate NCX `playOrder`:** Safely rewrites duplicated playOrder attributes sequentially.
  - **XML `id` Colon Violations:** Translates illegal colons in `id="X:Y"` and their matching `#X:Y` fragment references to valid underscores.
  - **Empty `<body>`:** Appends a non-breaking space `&nbsp;` to strictly empty body tags to satisfy parser requirements.
  - **Missing `<title>`:** Injects a `<title>Unknown</title>` fallback in the `<head>` if missing, handling both empty `<title/>` self-closing tags and entirely absent tags.
  - **Block-in-Inline Nesting:** Safely unwraps `<span>` tags that illegally contain a block-level element (e.g. `<div>` or `<p>`), leaving the block element intact.
  - **Invalid `value` Attributes:** Systematically strips invalid `value="..."` attributes from elements like `<div>`, `<span>`, `<p>`, etc.
- **Illegal Tags Unwrapper:** Added a sweeping transform that targets completely invalid or deprecated HTML tags that break EPUB3 validation (like `<st>`, `<font>`, `<sentence>`, `<o>`, `<w>`, and `<pagebreak>`). Unwraps the tags without deleting their inner text. Automatically excludes tags when referenced by an EPUB CSS stylesheet to guarantee 100% format preservation.


## v0.13.0 (2026-08-22)

### Structural Changes
- **Epubcheck Daemon:** Radically accelerated the `bindery library` validation gate by implementing a transparent, persistent Java daemon. Bindery now automatically compiles `FastDaemon.java` in the background and pipes EPUB paths to it, eliminating the JVM startup penalty.
- Validation time dropped from ~5 seconds per book down to under ~0.05 seconds per book, cutting library sweeps from 6 hours to 10 minutes.

## v0.12.3 (2026-08-22)

### Improvements
- **UI Upgrade:** Integrated `tqdm` progress bars directly into the core `bindery library` command, providing a real-time visual progress bar and ETA during the long-running sweep and candidate-processing phases.
- Standardized documentation to reflect that `tqdm` is a core required dependency, formally moving away from the "strictly stdlib-only" constraint.

## v0.12.2 (2026-08-22)

### Improvements
- **CLI Help Upgrade:** Explicitly surfaced the `--install-to-calibre` option on the root `bindery --help` menu by creating a dedicated `library-specific integration` argparse group.
- **Documentation:** Added a prominent code example for `--install-to-calibre` to the Usage section of the `README.md`.

## v0.12.1 (2026-08-22)

### Improvements
- **CLI Help Formatting:** Restructured the root `bindery --help` menu to natively render the shared fix flags as a properly aligned `argparse` table, matching the clean visual style of the subcommands and other portfolio tools.

## v0.12.0 (2026-08-22)

### Features
- **Oceanstrip Merged:** Fully incorporated the `oceanstrip` standalone tool directly into Bindery's primary pipeline.
- Added `--strip-watermarks` flag to systematically detect and remove producer/distributor watermarks (e.g. OceanofPDF stamps and zero-byte marker files, ABC Amber LIT Converter injections) from EPUBs.
- This lossy transform is correctly governed by the `validate.no_worse` EPUBCheck oracle, ensuring clean structural extraction without regressing file validity.
- The `--strip-watermarks` argument is automatically engaged when running Bindery with `--all`.

## v0.11.0 (2026-08-21)

### Features
- **`--strip-broken-tags`**: A new lossy transform to find and safely remove leaked HTML closing tags missing their open brackets (e.g., `</p>`) that render as raw text in older uncorrected EPUBs. Guarded by the `validate.no_worse` gate.
- **`--install-to-calibre`**: A safer alternative to atomic replacement for Calibre users. Uses `calibredb add_format --replace` to swap the corrected EPUB natively into the database, preserving all metadata, custom IDs, and read progress. Falls back to atomic replacement if the Calibre ID cannot be parsed from the filepath.
- **`--all`**: Added a meta-flag to automatically enable all opt-in non-fatal fixes and lossy strips (pagination, watermarks, tags, attributes, entities, image alt tags, etc.) simultaneously for a comprehensive repair.

## v0.10.3 (2026-08-21)

### Improvements
- **UI Upgrade:** CLI scripts now feature rich terminal output with ANSI formatting, `tqdm` progress bars, and clear summary blocks. The project now relies on `tqdm` for output formatting.

## v0.10.2 (2026-08-09)

**Repairing the same book twice now produces identical bytes.** It did not before,
and had not for as long as the archive rewrite has existed. The mimetype entry was
written as `zout.writestr("mimetype", ...)` with a bare string arcname, and a string
arcname makes `zipfile` mint a fresh entry stamped with the current clock. Every other
entry in the archive is written from its source `ZipInfo` and keeps its own timestamp,
so this was the single entry that moved: two repairs seconds apart differed in exactly
those two bytes, with every entry's content identical.

Nothing malfunctioned because of it. No reader and no epubcheck run cares about that
field. What it cost was the ability to checksum or diff two repairs to confirm they
agree, which is exactly the check the v0.10.1 sweep leaned on, and it quietly made
"deterministic repair" false at the byte level. spec.md now says determinism is meant
at the byte level, so the claim is testable rather than aspirational.

The entry now carries the source entry's timestamp, or 1980-01-01 (the earliest a zip
can represent) when the entry is being added and there is nothing to inherit. It is
still built as a fresh `ZipInfo` rather than reusing the source one, because OCF
requires the mimetype entry to carry no extra field and reusing a broken book's entry
wholesale would propagate that violation; `external_attr` is set to what `writestr`
used to apply, so the timestamp is the only change to the output.

Measured, not assumed: three separate processes spaced over five seconds now produce
one identical hash where they produced three. Over 99 real books, content, per-entry
metadata, and fix reports are unchanged from v0.10.1, while timestamp preservation
goes from 0/99 to 99/99. Three tests cover it, one of which documents that the naive
"repair twice and compare" check passed by luck before the fix whenever both runs
landed in the same clock second. Suite 122 to 125 tests.

## v0.10.1 (2026-08-09)

A maintenance sweep, plus the roadmap decision recorded below.

**Three bugs.** Each fix was measured against the real library rather than argued
from a fixture: the old and new code were run side by side over all 4,926 books
(268,190 documents, content plus NCX). One of the three was actively firing; the
other two are traps that were waiting for the right input.

- **`--strip-bad-attrs` could destroy a tag's self-closing slash.** The
  unquoted-attribute-value pattern `[^\s>]+` swallowed the `/` in `<img 31=x/>`,
  so dropping the offending attribute left `<img>`: the one fix whose whole promise
  is "only the offending attribute is dropped" turned a well-formed self-closed tag
  into an unclosed one, introducing the very fatal it exists to remove. The gate
  would then reject the book, costing it every other repair it had earned. The
  pattern now stops before a `/` that ends the tag, while a `/` inside a value (a
  URL) is still consumed. No book in the library carries the pattern today, so this
  one was latent.
- **`strip_prolog_junk` counted a phantom fix for legal whitespace.** Whitespace in
  the prolog is legal XML unless an XML declaration follows it, but any leading
  whitespace was stripped and counted. That marked the document changed, which
  forces `repair_epub`'s `decode("utf-8", "replace")` round-trip on a file that had
  nothing wrong with it, and turned books that should report `nochange` into books
  that spend two multi-second epubcheck runs to conclude `equal`. Whitespace before
  a declaration (where it really is fatal), a BOM, and any non-whitespace junk are
  still stripped. This is the one that was live: across the library the transform
  fired 119 times before and 106 after, so 13 phantom fixes in 7 books are gone, and
  every genuine case (a BOM ahead of an XML declaration, the real form in this
  library) still fires. Running the whole pipeline over those 7 books before and
  after confirms the change is surgical: every other fix count is untouched
  (`fix_ncx_ids` 10 stays 10, `fix_named_entities` 30 stays 30, `stripped_pagination`
  1 stays 1), only the phantom rewrites disappear.
- **Invalid-id renaming was not deterministic.** `--fix-ids` iterated the id *set*,
  so when two invalid ids wanted the same replacement (`1:2` and `1_2` both yield
  `id_1_2`) which one received the extra `_` prefix depended on the hash seed: the
  same book repaired to different bytes from run to run, against a project that
  sells itself on deterministic repair. Both the OPF and NCX paths now plan renames
  through one sorted helper, `_plan_renames`.

**The lint result no longer depends on which ruff is installed.** There was no
`[tool.ruff]` section, so the rule set came from ruff's defaults: CI's pinned
0.15.20 passed clean while 0.16.2 reported eight findings on the same tree. The
selection is now pinned in `pyproject.toml` (ruff's default set plus import
sorting, pyupgrade, and bugbear), with a note on the two families deliberately left
out because they fight intentional code. CI moves to `actions/checkout@v5`, pins
ruff 0.16.2, and lints `.` rather than three named directories so a new top-level
script cannot escape.

Smaller: `repair_epub` opens the source archive once instead of twice; the dead
`skip_empty` parameter is gone from `pagination._nearest`; `library --limit 20` on
a tree with 3 candidates counts `[1/3]` instead of `[1/20]`; and `pyproject.toml`
catches up to the `VERSION` bump in `__init__.py`. Suite 114 to 122 tests.

**The Calibre hook question is settled, and it is `FileTypePlugin` with
`on_import = True`.** Researched against the Calibre source rather than guessed:
`run(path_to_ebook)` is handed the file being imported and returns the path to a
modified copy, and Calibre imports that instead. So a repaired EPUB is what enters
the library, the user's original on disk is never touched, and nothing writes to
`metadata.db`.

**That kills a roadmap item.** The "optional metadata.db nudge so Calibre notices
the new file size" existed only because in-place replacement changes a file behind
Calibre's back. Under `on_import` the repair happens before Calibre reads the
file, so the size it records is already right. The nudge was solving a problem
that the correct hook does not create.

Two constraints recorded with the decision, because they shape what the plugin can
be: it runs inside Calibre's bundled Python, so it must vendor its own code and
cannot assume Bindery is installed (the minimal-dependency rule is what makes this
practical, and the optional `html5lib` path cannot come along); and epubcheck
cannot gate an import, being an external Java process of seconds per book, so the
plugin does the deterministic transforms and leaves gated work to the CLI where a
human is watching.


## v0.10.0 (2026-08-08)

Locale hardening for the epubcheck wrapper (roadmap 5.2, the last open Phase 5
item). The wrapper parsed counts from epubcheck's human-readable summary line,
which the JVM localizes: on a non-English locale every book parsed as None and
was reported as `error`. Counts now come from epubcheck's locale-independent
`--json -` output first (the `checker` totals, verified against epubcheck
5.3.0), with the English summary-line regex kept as the fallback for
epubchecks too old for `--json`, and the JVM pinned to English via
`JAVA_TOOL_OPTIONS` (appended, so existing JVM flags survive) so that fallback
stays meaningful. New `tests/test_validate.py` (9 tests) mocks
`subprocess.run` itself to pin the JSON path, both fallbacks, the env pin, and
the failure modes; suite grows 105 to 114.

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
round of CLI hardening and UX. Every fix ships with a unittest regression test.

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
  minimal; `bindery[reserialize]` pulls it in for `--reserialize`.

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
  XML name characters but not word characters, so the v0.2.0 `` boundary still let
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
  minimal-dependency design.
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
- `unittest` suite; minimal dependencies.

Validated on the real library: 24 of ~40 fatal books fully de-fataled (they now open),
6 partially improved and flagged for manual finish, the rest left untouched, and zero
epubcheck regressions.
