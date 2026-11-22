<div align="center">
  <img src="logo.svg" width="96" height="96" alt="bindery-cli logo"/>
  <h1>bindery-cli</h1>
  <p>Audit EPUB body text and repair broken EPUBs with safe, deterministic fixes, validated by epubcheck, with optional in-place replacement in a Calibre library.</p>
</div>

<p align="center">
  <img src="docs/screenshots/library-sweep.png" alt="bindery-cli's dry-run library sweep: per-book epubcheck results and a summary table ending in 'no files written'">
</p>

**Status: 1.0.** The flag surface is additive from here — new opt-in flags may
appear, existing flags' names and documented semantics are stable, and the gate
semantics plus the JSON record vocabulary are the contract (see the stability
section in spec.md). The observatory keeps the library census measured; the
remaining findings are the documented leave-measured classes.

## What it fixes

bindery-cli makes accidentally broken markup well-formed again. It does not rewrite or reflow content. The **default pass** applies only a small set of deterministic, semantics-preserving well-formedness fixes that real-world EPUBs (especially Calibre conversions) trip over; nothing else changes without an explicit flag:

- **Unclosed void elements** (`<link>`, `<br>`, `<img>`, ...) get self-closed.
- **Undeclared named entities** (`&nbsp;`, `&deg;`, `&eacute;`, ...) become numeric character references that every XML parser understands.
- **Bare `&`** (common in `toc.ncx`) is escaped to `&amp;`.
- **Junk before the XML prolog** (BOM, stray bytes) is stripped.
- **Duplicate `xmlns`** on the root `<html>` is collapsed to one.
- **NCX-001**: `toc.ncx` `dtb:uid` is synced to the OPF unique identifier; duplicated NCX `playOrder` values are resequenced whenever the NCX is processed (part of the always-on NCX pipeline, not a flag).
- **mimetype** is rewritten first and stored, fixing the common ordering defect; a missing entry is added and wrong or whitespace-padded content is normalized to the OCF constant.

### Opt-in repairs

The remaining 45 fixes are opt-in: off until their flag is passed, or all at
once via `--all`. They go further than well-formedness (altering structure or
adding minimal content), so they never run by default. They group the way the
CLI help groups them:

- **Safe opt-ins** (6, markup-neutral): `--fix-ids`, `--add-img-alt`,
  `--reserialize`, `--strip-bad-attrs`, `--escape-unknown-entities`,
  `--fix-double-colon-tags`.
- **Structural repairs: markup and nesting** (13): from `--fix-empty-body`,
  `--fix-missing-title`, and `--fix-id-colons` to the mis-nest/stray-close/
  unterminated-attr family, the EPUB2 downgrades, and
  `--strip-presentational-attrs`.
- **Structural repairs: references and resources** (11): the prune/declare/
  encode family, from `--prune-missing-resources` and `--encode-url-spaces` to
  `--strip-dead-font-faces` and the `--prune-dead-spine` shell repair.
- **Structural repairs: package wiring** (11): `--fix-container` (the gateway
  defect), `--fix-media-types`, `--declare-missing-items`, `--fix-cover`,
  `--fix-unique-identifier`, and the OPF-version/doctype/guide family.
- **Lossy strips** (4), which stand apart because they delete converter-injected
  content rather than correcting XML:

  - **`--strip-pagination`**: remove print page numbers and running headers that a PDF/OCR conversion baked into the body text as literal paragraphs (so they reflow into the middle of a sentence: "where the hay cart **16** was taking him"). Roman chapter numbers, page-list anchors, and years are preserved; every removal requires prose flow, and four safety nets guard every edit.
  - **`--strip-broken-tags`**: remove leaked HTML closing tags missing their open brackets (e.g. `</p>`) that render as raw text.
  - **`--strip-watermarks`**: remove known producer and distributor watermarks (OceanofPDF, ABC Amber LIT Converter) and stray marker files, deleting only wrappers whose entire visible text is the stamp.
  - **`--strip-stub-docs`**: drop spine documents whose whole body is the same short placeholder repeated across the spine (the Bookmate-style export), cascading the drop through archive, manifest, spine, and ToC. A book whose *every* spine doc is the stub is refused outright: it needs a re-source, never a repair.

All lossy edits are invisible to epubcheck, so they are accepted when the result is *no worse* rather than measurably better.

The full per-flag reference (each flag's rules, refusals, and history) is
[docs/repairs.md](docs/repairs.md); `bindery repair --help-repairs` renders the
same reference in the CLI, and `bindery repair --help` lists the one-line
summaries.

## The safety contract

Every repair is gated by [epubcheck](https://github.com/w3c/epubcheck). The acceptance rule is two-mode, because a fatal parse error makes epubcheck stop reading a file and hides every downstream error:

- If a book **had fatals**, success means **fewer fatals**. The error count may rise as previously-hidden schema warnings become visible once the file parses; that is the book going from "won't open" to "opens with nits," not a regression.
- If a book had **no fatals**, an error increase is a real regression, so a strict error decrease is required.

Introducing a net-new fatal is always rejected. One structural veto rides above the counts, with two arms: the repairer probes source and candidate with a spine-resolution check (itemref to manifest item to archive entry), and any candidate whose spine dangles more than the original's is rejected no matter what the counts say, because a repair that orphans live reading-order references can otherwise measure as an improvement. The second arm (v1.2.2) catches the shape the dangle count cannot see: a dead-spine prune whose candidate resolves fewer than half the documents the source resolved is rejected too, because pruning removes the dangling declarations along with the rest, so an amputation can lower the dangle count to zero while the error count improves on the smaller book. A genuine shell (only never-resolving positions dropped) never trips it. If epubcheck itself fails to run (crash, timeout, unparsable output), the book is reported as an error and never applied; only an explicit `--no-validate` skips the gate. Originals are never modified except by an explicit, atomic in-place replace (see below), and even then only after the gate accepts the result.

Under the hood, the oracle can run two ways: the usual `epubcheck --json -` subprocess, or a persistent daemon that writes `FastDaemon.java` into a temp directory at runtime, compiles it in memory with the running JVM's own compiler (Java's single-file source launcher: no javac, no javac/java version skew), and drives the warm JVM over a pipe. The daemon reports the counts it reads from the JSON document that epubcheck's own `CheckingReport` serializes, so it measures exactly what the subprocess measures, just ~15x faster. It is a pool bounded by `--workers` (serial runs use one), every roundtrip is bounded by the caller's timeout, and any failure tears it down and falls back to the subprocess for good.

The lossy modes (`--strip-pagination`, `--strip-broken-tags`, `--strip-watermarks`, and `--strip-stub-docs`) are the exception to the "must improve" rule. Since they remove converter-injected content rather than correcting XML schema violations, epubcheck counts often remain unchanged. They are accepted when the result is **no worse** (no net-new fatals or errors), relying on strict programmatic safety nets instead.

## Install

Python 3.12+, plus epubcheck on `PATH` for the gate. Dependencies: `tqdm` for
progress output, plus the VirInvictus libraries `vir-tui` (TUI rendering) and
`cquarry` (read-only Calibre database access), which install on Python 3.14+
only; `html5lib` remains an optional extra, needed only for `--reserialize`.

epubcheck (W3C's conformance checker) is the gate's oracle and the one
dependency pip cannot install for you. It needs a Java runtime. Any of these
works:

- your package manager, where it ships: `apt install epubcheck` (Debian/Ubuntu),
  `brew install epubcheck` (macOS), `epubcheck` in the AUR (Arch); or
- the release zip from [w3c/epubcheck](https://github.com/w3c/epubcheck/releases):
  unzip it and put its `epubcheck` launcher script somewhere on `PATH`.

Verify with `epubcheck --version`. A validated run refuses to start without it
(exit 1: "epubcheck not found") rather than silently skipping the gate; only
`--no-validate` bypasses it, and the Calibre plugin never needs it (its default
pass runs ungated by design). `scripts/fast_sweep.py` also accepts
`EPUBCHECK_JAR` pointing at the jar directly.

`bindery doctor` runs all of these checks for you: it reports the Python
stack tier, the epubcheck version, Java, the optional html5lib, and whether a
Calibre library is discoverable from the current directory. It always works
and always exits 0; the findings are the output.

The floor is two-tier by those markers: on Python 3.14 everything runs. On
3.12/3.13 the install carries the repair core only, which is the tool's core
job: `bindery repair` on a single EPUB works in full, while `bindery audit`,
`bindery library` and `bindery run` print a one-line note that the command
needs the Python 3.14 stack instead of dying in a traceback. (Every vir-tui
and cquarry release on PyPI currently declares `requires-python >=3.14`, so
the stack itself cannot follow the floor down until they move.) CI runs the
stack-free test modules on real 3.12 and 3.13 interpreters.

```sh
uv tool install bindery-cli                            # core tools
uv tool install "bindery-cli[reserialize]"             # incl. --reserialize
```

## The Calibre plugin (Bindery Repair)

Each release also ships **BinderyRepair-v<VERSION>.zip**, a Calibre plugin
that repairs EPUBs as they are imported (attached to the GitHub release beside
the PyPI wheel; load it via Calibre's Preferences, Plugins, "Load plugin from
file"). The plugin runs the CLI's always-on core pass only: the five
well-formedness fixes, the NCX pipeline, and the mimetype fix. Nothing runs
ungated: the structural repairs and lossy strips stay CLI-only, because their
acceptance is the epubcheck gate and epubcheck cannot run inside Calibre.
Minimum Calibre 7.0, Linux. Behavior guarantees, the JSON configuration
string, and compatibility notes are in [docs/plugin.md](docs/plugin.md).

## Usage

Six verbs: `bindery repair` fixes a single EPUB epubcheck-gated, `bindery audit`
reports content flaws without touching anything, `bindery library` sweeps a Calibre
library tree (dry run by default; `--apply` replaces accepted books atomically in place),
`bindery observe` validates every EPUB under a tree in one warm-JVM pass and writes one
versioned `.observe` census artifact (counts and error codes together, tabulated, with
the gated repair preview and `--against` trend diffing),
`bindery run` composes the acquisition pathway's EPUB slices (phase-1 pre-import
vetting, phase-3 scoped post-import repair) for a calling agent, and `bindery doctor`
checks the installation (see Install).
The sections below take each in turn.

Help is two-level: `bindery repair --help` (or `library --help`) lists the
shared repair flags grouped with one-line descriptions, and
`bindery repair --help-repairs` prints the full reference carrying every
flag's long rationale, and `bindery --help-json` dumps the complete
surface (every subcommand and flag, the repair reference, the per-verb
exit contract, the Calibre gates) as machine-readable JSON. On a terminal both are ANSI-colored with the same
theme Python 3.14's argparse uses; piped output stays plain, and `NO_COLOR`,
`FORCE_COLOR`, and `TERM=dumb` are honored exactly as `python --help` honors
them.

## Auditing

bindery-cli's auditing tool inspects EPUB body text for non-schema flaws that epubcheck cannot catch. It extracts and analyzes the visible text to detect content issues, producing console reports that can be used to filter your library or feed into `bindery repair`.

Scan a Calibre library for specific issues:

```sh
# Detect non-English content (e.g., Cyrillic or CJK in an English library)
cd ~/docs/Calibre\ Library && bindery audit content

# Find books with hardcoded print page numbers interrupting the text
bindery audit pagenumbers ~/docs/Calibre\ Library

# Find books that are empty or severely truncated
bindery audit emptytext ~/docs/Calibre\ Library

# Find books with severe OCR damage (garbage characters, excessive hyphenation)
bindery audit ocr ~/docs/Calibre\ Library

# Flag books where a single content document is too large for readers to render
bindery audit monolithic ~/docs/Calibre\ Library

# Spot-check completeness: prose coverage, first/middle/last excerpts, trailing ToC
bindery audit completeness ~/docs/Calibre\ Library

# Cover-art quality and ToC-drift audits (advisory; never move the exit code)
bindery audit cover ~/docs/Calibre\ Library
bindery audit tocdrift ~/docs/Calibre\ Library

# Fan-bundle contamination: foreign-title template stamps, ad pages, review paste
bindery audit contamination ~/docs/Calibre\ Library

# Run all audits and report to the console (--json FILE for machine output)
cd ~/docs/Calibre\ Library && bindery audit all
```

A directory of loose `.epub` files works too (`bindery audit pagenumbers /path/to/loose/epubs`), and one or more library books can be audited by Calibre id (`cd <library> && bindery audit all --id 1234,1235`; `--id` composes with `--tag`: the book is tagged only if the audit flags it).

Tagging is the audit's only write path and it is opt-in: `--tag "Audit Flagged"` tags every flagged book through cquarry's trigger-safe write module, and the write is REFUSED with exit 1 while Calibre is running (a fail-closed pgrep guard: close Calibre and re-run).

The analyzer detail (completeness and contamination sweeps, thresholds, the machine-readable `--json` shape, spine-integrity and archive-integrity verdicts, the tagging contract) is in [docs/audits.md](docs/audits.md).


## Repairing a book

Repair one book to a new file (gated; writes only if it is an improvement). Without an
output path the verb is a probe: the gate runs and the report says what would change,
and nothing is written anywhere, so probing a file can never litter the directory
under vetting:

```sh
bindery repair broken.epub                 # probe: report only, nothing written
bindery repair broken.epub --all --json report.json   # probe with a machine-readable record
bindery repair broken.epub fixed.epub      # rescue form: write the repaired file
bindery repair scanned.epub --strip-pagination   # also remove baked-in page numbers
bindery repair stubs.epub --strip-stub-docs      # drop repeated placeholder chapters
bindery repair broken.epub fixed.epub --json report.json  # machine-readable record
```

`repair --json FILE` writes one record in the `library --json` per-book
vocabulary (`status`, `applied`, `before`/`after` counts, the fix `summary`),
on every processing outcome including nochange and reject: a calling agent
gets the same facts the console lines carry. On a probe the record's
`output` is null and `applied` stays false; the counts and the fix
breakdown still describe the gate-accepted projection.

## Sweeping a library

Scan a Calibre library and see what would be fixed, writing nothing:

```sh
bindery library ~/docs/Calibre\ Library --only fatals --audit epub_audit.csv
```

Apply accepted repairs in place, atomically, with backups:

```sh
bindery library ~/docs/Calibre\ Library --only fatals --apply --backup ~/bindery-backups
```

Apply all safe and lossy repairs directly into the Calibre database natively:

```sh
bindery library ~/docs/Calibre\ Library --only all --apply --all --install-to-calibre
```

- `--only {fatals,ncx,all}` restricts the candidate set. `ncx` targets NCX-001 mismatches (detected without epubcheck); `fatals` needs `--audit` or `--sweep`.
- `--id <ids>` limits the sweep to a comma-separated list of Calibre book IDs, skipping the full library walk.
- `--audit CSV` (the `fatals,errors,warnings,path` format produced by an epubcheck sweep) skips clean books so a run is fast. Paths are resolved on both sides, and a CSV that matches nothing triggers a loud warning instead of silently selecting zero books.
- `--sweep` replaces the CSV step entirely: it runs a live epubcheck sweep for candidate selection and reuses each result as that book's before-measurement, so no book is checked twice. Checks are served from a small persistent Java daemon where possible (see the gate section above for how it is bounded and how it fails safe); wherever the daemon cannot serve, the run falls back to the standard per-book epubcheck subprocess. Combine with `--only fatals` for a self-contained "find and fix the broken books" run.
- `--limit N` processes at most N candidates and stops opening archives after them (a value below 1 is a usage error). `--workers N` parallelizes the sweep's candidate pass in windows of N consumed in order, so the candidate set matches the serial sweep and `--limit` stays lazy within one window of overshoot; the repair phase stays serial, since that is where the shared workdir and the atomic-replacement contract live.
- `--json FILE` writes a machine-readable report of the whole run (per-book status, before/after counts, applied flag, summary totals). `--manual-list FILE` writes the paths of every book that was not auto-repaired, one per line, ready for manual follow-up.
- `--apply` is required to write; the default is a dry run. `--backup DIR` mirrors originals before replacing; `--backup-inplace` writes `.epub.bak` beside each file. `--backup-keep N` bounds the rotation: at most N backup files per book (the author original `.epub.bak` is never deleted), so a years-long apply habit cannot grow backups without bound.
- `--install-to-calibre` installs the repaired EPUB through cquarry's write module: the book id resolves from `metadata.db` via one read-only path→id map per run (the directory-name regex is only a no-catalog fallback, and a guessed id is trusted only when `metadata.db` corroborates it: the book exists and the file is that book's catalogued EPUB), the file is placed atomically at the catalogued path, and the format row is re-registered in one transaction, so the stored size stays truthful and the book lands in `metadata_dirtied` (Calibre regenerates its sidecar .opf on next start). No external `calibredb` call is involved. A database failure degrades to the atomic in-place save with a warning; an uncatalogued stray file inside a book directory is saved in place and the catalog row is left untouched.
- `--all` automatically turns on all opt-in non-fatal fixes and lossy strips in a single run.
- Only the `.epub` is replaced. `metadata.opf`, `cover.jpg`, and `metadata.db` are left for Calibre's Quality Check sync to reconcile.
- A per-book progress line goes to stderr (stdout stays a clean report); `--quiet` suppresses it. A corrupt or unreadable book is reported and skipped, never aborting the sweep.
- Exit codes, per verb (for scripts and cron; `--help-json`'s `exit_contract` carries this machine-side):
  - `repair`: 0 when clean, probed, OR written (a probe writes nothing and exits 0; a PARTIAL repair writes its best output and exits 0; the `--json` record's status says so) and 1 on trouble (gate-rejected, epubcheck failure, unreadable input) or invocation problems (bad args, an existing output without `--force`); it never exits 2.
  - `library`, `run phase1`, and `run phase3`: 0 clean, 2 when any book was flagged, rejected, unreadable, errored, or partial, 1 for invocation problems.
  - `audit`: 0 clean, 1 when any book was flagged (a flag is the audit's trouble), 2 for its own usage errors, 3 when findings coexist with a failed `--json`/`--tag` write.
  - `audit --tag` and `library --apply --install-to-calibre` are lock-class doors: REFUSED with exit 1 while Calibre is running. Argparse-level misuse exits 2 everywhere before any validation runs.
- A `partial` book (improved but still unable to open) is reported for manual follow-up and fails the run with exit 2, in `library`, `run phase1`, and `run phase3` alike: a book that still needs a human is trouble.
- `repair` refuses to overwrite an existing output file unless `--force` is given.

## Observing a library

`bindery observe` is the whole-library census: one warm-JVM pass validates
every EPUB (counts and error codes together, clean books included), the
result is tabulated (severity histogram, dominant error-code classes, worst
offenders), and one self-describing `.observe` artifact carries the per-book
records so any later question can be answered without re-validating:

```sh
bindery observe ~/docs/Calibre\ Library --output ~/observe/base.observe
```

- Strictly read-only: the only file written is the artifact, and the writer
  refuses to land it inside the scanned root (the default output is
  `observe-<timestamp>.observe` in the current directory).
- The optional repair preview (default on, `--no-preview` skips) composes the
  shipped library runner in-process as a dry-run over the default flag set:
  it measures what `--apply` would write, per candidate, and never writes.
- `--against base.observe` carries a trend block in the new artifact: library
  deltas, error-mass delta, and per-code changes with `new_codes` and
  `resolved_codes`. Artifacts are versioned (`bindery-observe-v2`); a
  schema mismatch is a usage error, never a silently incomparable trend
  (v1 artifacts stay valid `--against` targets: the trend keys exist in
  both).
- Two counting scales, stated in the artifact's `counting` block:
  `codes.messages` (and the per-book `codes` rows) count aggregated
  epubcheck JSON message entries, one per distinct defect text; that is
  the scale the repair gate measures. `codes.occurrences` sums the
  per-message locations, the per-site scale the epubcheck text summary
  reports; one book can differ by orders of magnitude between them.
- A book the JVM cannot read arrives as a sentinel record instead of being
  dropped, and a run that comes back short of the input count refuses to
  write a partial census. The library and audit walks skip `.caltrash`.
- Exit codes: 0 artifact written, 1 invocation problem, 2 the census harness
  failed mid-run.

## Run slices

`bindery run` wraps the acquisition pathway's EPUB steps as verbs. They add no
repair classes of their own: they compose the shipped audit and library runners
in the documented order, and both speak `--json` for a calling agent.

**`run phase1 DIR`** is the pre-import vetting slice for a directory of loose
files: the audit battery (corruption sweep, content battery, monolithic) in one
decompression pass per book, then the gated repair sweep (epubcheck, watermark
detection, repairability) over the same directory. The verb is read-only until
`--apply-lossy` is passed, and that flag IS the recorded lossy-strip consent;
`--backup DIR` mirrors originals first (keep backups outside the vetted
directory). Consent questions never block the verb: a read-only run that finds
repairable books records an `apply_lossy` entry in the JSON's
`decisions_needed` instead of prompting, and `--non-interactive` declares that
contract for callers. Exit codes per the `library` contract: 0 clean, 1 usage,
2 when any book is flagged, rejected, unreadable, or failed epubcheck (a
consent question alone is not trouble).

```sh
bindery run phase1 ~/Downloads --json phase1-report.json               # read-only
bindery run phase1 ~/Downloads --apply-lossy --backup /tmp/phase1-backups
```

**`run phase3 --ids IDS`** (from the library directory) is the post-import
scoped repair sweep: exactly `library --id IDS --sweep --only all --apply --all
--install-to-calibre` plus a pre/post epubcheck summary. It mechanically
refuses an unscoped library-wide sweep with exit 2 (a full-library sweep is a
dedicated hours-long task, never a verb call), and books left partial or
unreadable surface as `decisions_needed` in the JSON.

```sh
cd ~/docs/Calibre\ Library && bindery run phase3 --ids 5071,5072 --json phase3-report.json
```

## Companion scripts

`scripts/` holds standalone, read-only utilities that are useful for EPUB maintenance but fall outside bindery-cli's repair contract (fixing what they find would be a content change, which bindery-cli makes only via the opt-in `--strip-pagination`):

- `fast_sweep.py`: compiles and drives `FastSweep.java`, a parallel JVM epubcheck sweep that pays JVM startup once instead of per book (a 7,000-book dry-run sweep drops from hours to minutes). `--mode=audit` emits the `fatals,errors,warnings,path` CSV that `bindery library --audit` reads, with the counts taken from the same aggregated JSON surface the gate measures (not the raw occurrence counters), so selection and gating always agree; `--mode=extract` emits per-book error codes for aggregation (`--summary` prints the per-code report). The epubcheck jar is located from the `epubcheck` launcher or `EPUBCHECK_JAR`; the harness compiles once (`--release 25`) and is cached on mtime.

The older `find_*.py` detection wedges and the `sweep.sh`/`FastSweepExtract.java` prototypes were removed: every detection they performed is now a shipped, tested flag (missing images are `--prune-missing-resources`, and the rest of the RSC-005/PKG-010 family shipped through Phases 6-12), and keeping two copies meant future behavior changes landed in only one of them. Git history preserves them.

## Development

```sh
./run_tests.sh        # ruff (CI's pin) + unittest suite
```

The suite builds its own synthetic EPUBs in temporary directories. `testing_facility/`
holds staged real-book copies for manual dry runs (gitignored); `test_facility/` holds
loose local books that stay on disk but out of git (untracked since v0.33.0,
forward-only, because 9 of the 10 are commercial and one untracked leak on a public
repo is one too many).

See [docs/repairs.md](docs/repairs.md) for the opt-in repair reference,
[docs/audits.md](docs/audits.md) for the audit analyzers,
[docs/plugin.md](docs/plugin.md) for the Calibre plugin,
[spec.md](spec.md) for the full contract, and [roadmap.md](roadmap.md) for what is planned.

## License

MIT. See [LICENSE](LICENSE).

## Support

If bindery-cli's useful to you and you'd like to chip in:

- liberapay · [liberapay.com/bdkl](https://liberapay.com/bdkl/)
- bitcoin
  ```
  bc1qkge6zr45tzqfwfmvma2ylumt6mg7wlwmhr05yv
  ```
