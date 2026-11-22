# Auditing reference

`bindery audit` inspects EPUB body text for non-schema flaws that epubcheck
cannot catch. It extracts and analyzes the visible text to detect content
issues, producing reports that feed a repair sweep or a manual re-source. This
page carries the analyzer detail; the command examples live in the
[README's Auditing section](../README.md#auditing).

## Completeness spot-check

The `completeness` audit answers the pre-import question "does this book reach its end?" It reports, per book: the prose-document count against the spine (docs with 400+ visible characters), the opening and closing text of the first/middle/last prose doc, a trailing-ToC classification of the final spine doc (a link-dense chapter list after the real back matter), and the fraction of unreadable docs. A trailing ToC is reported as an advisory, never a failure: the closing excerpt always comes from the real back matter, so a human can confirm the book ends where the author ended it.

## Contamination scan

The `contamination` audit is advisory too: fan-bundle cosmetics are the judge-and-import class. Three sweeps over the archive's content documents (spine plus the non-spine html a broken manifest can strand): another series' name where the book's own identity belongs, judged against the package's own metadata (the head-`<title>` template stamps a sloppy fan bundle leaves on every doc, a volume marker with named words in front of it, a repeated foreign running header); known fan-bundle ad signatures (jnovels, kokoro connect, zerobooks, light-novel download plugs); and pasted review-site text in the first few content docs (helpful counts, "was this review helpful", "out of 5 stars", verified purchase). Every finding is a report a human judges, never a failure.

## Machine-readable reports

`--json FILE` writes the run's verdicts in the same shape `library --json` uses: one record per book with a `status` (`clean`/`problem`/`error`) and per-analyzer verdicts (`problem`, `status`, `details`), plus the always-on archive/spine verdicts (OK when they were silent). This is the contract downstream consumers of the pre-import vetting slice read.

```sh
bindery audit all ~/Downloads/epubs --json vetting-report.json
```

With `--id`, `--json` accepts exactly one book id (each single-book run writes the file wholesale).

## Thresholds

`--min-chars N` and `--thin-chars N` are the EMPTY and THIN body-text thresholds (defaults 2000 and 20000); `--min-chars` above `--thin-chars` is a usage error. `--max-doc-chars N` is the monolithic-document flag threshold (default 300,000). All three belong to the audit parser only (`bindery library --min-chars …` is an unknown-flag error).

## Spine-integrity reporting

Both `audit` and `library` reports now classify manifest/NCX references that point to absent files. A `convention` verdict means the ToC is bloated but the present documents form a consecutive chapter span (e.g., the Wandering Inn official-build pattern, safe). A `fragment` verdict means the span itself is broken.

## Archive integrity

Every audit fully reads each archive entry (CRC + decompression), so a damaged download is reported CORRUPT, with the first broken entry named, instead of being mislabeled EMPTY by `emptytext`. `library --sweep` splits its `unreadable` bucket into `not_a_zip` / `truncated` / `encrypted` / `corrupt_entry`, so the right disease is visible without leaving the sweep.

## Tagging flagged books (opt-in; the write is refused while Calibre runs)

By default an audit writes nothing. With `--tag TAG` (library mode only), every book the audit
flags is tagged in `metadata.db` through cquarry's trigger-safe write module, useful for piping
flagged books back into Calibre views:

```sh
cd ~/docs/Calibre\ Library && bindery audit content --tag "Audit Flagged"
```

The audit itself stays read-only; only the final tagging pass writes, it skips books that already
carry the tag, and THIN emptytext advisories are never tagged. The write is REFUSED with exit 1
while Calibre is running (a fail-closed pgrep guard: close Calibre and re-run). Tagged books are recorded in Calibre's `metadata_dirtied` queue, so the
desktop app regenerates their sidecar `.opf`s (and re-pushes metadata to wireless readers) on its
next startup: no manual resync needed.
