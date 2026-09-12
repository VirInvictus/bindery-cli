# bindery-cli specification

The contract. Read this before changing semantics.

## Scope

bindery-cli repairs well-formedness and a few specific validity defects in EPUBs. It is
deliberately narrow: every transform is deterministic and semantics-preserving, and
the result is only kept when epubcheck confirms it improved. Deterministic is meant at
the byte level: repairing the same book twice, in separate processes, produces
identical archives. bindery-cli does not reflow,
restyle, re-compress, or restructure content, and it does not attempt to fix arbitrary
schema (RSC-005) violations, which are usually harmless to readers and not safely
mechanizable.

The deliberate exceptions to "semantics-preserving" come in two kinds, both strictly
opt-in. The fourteen **structural repairs** (`--fix-empty-body`, `--fix-missing-title`,
`--fix-id-colons`, `--fix-page-map`, `--strip-epub3-attrs`, `--downgrade-epub3-tags`,
`--unwrap-block-in-inline`, `--strip-invalid-value`, `--unwrap-illegal-tags`,
`--prune-missing-resources`, `--strip-broken-anchors`, `--encode-url-spaces`,
`--fix-container`, `--fix-media-types`, `--fix-cover`) alter
markup structure or fabricate minimal content; the three
**lossy modes** (`--strip-pagination`, `--strip-broken-tags`, `--strip-watermarks`) remove
content a converter injected rather than content the author wrote. The default pass runs
ONLY the transforms listed above and the NCX pipeline — nothing else. (v0.14–v0.16 briefly
ran the structural repairs unconditionally, which broke this contract; v0.17.0 restored
it.) The `--all` flag enables every opt-in transform (safe, structural, and lossy) for a
comprehensive repair pass.

## Transforms

Applied to content documents (`.xhtml`, `.html`, `.htm`, `.xml`), in order:

1. **strip_prolog_junk**: remove a BOM or stray characters before the first `<`.
   Leading *whitespace* counts as junk only when an XML declaration follows it, since
   a declaration must be the very first thing in the document; before a DOCTYPE or the
   root element it is legal prolog whitespace and is left in place. (Stripping it
   anyway counted a fix on an undamaged document, which forced the archive rewrite's
   lossy `decode("utf-8", "replace")` round-trip for no gain.)
2. **drop_duplicate_xmlns**: keep only the first `xmlns="..."` on the root `<html>`.
3. **escape_bare_amp**: replace a `&` that does not start a valid entity or character
   reference with `&amp;`.
4. **fix_named_entities**: replace any HTML named entity that XML does not predefine
   (everything but `amp`, `lt`, `gt`, `quot`, `apos`) with its numeric character
   reference; the few entities that expand to several codepoints become one
   reference per codepoint. Unknown names are left alone.
5. **self_close_void**: self-close void elements (`area base br col embed hr img input
   link meta param source track wbr`) that were left open. Orphaned **end tags** for
   void elements (`</br>`, `</col>`, ...) are removed outright: a void element can
   never legally carry an end tag, so the tag is always invalid and its removal cannot
   change what renders. Removed end tags are counted in the fix total.

Applied to the NCX sidecar (`.ncx`): strip_prolog_junk, escape_bare_amp,
fix_named_entities, plus **dtb:uid sync** (rewriting the NCX's `dtb:uid`
meta to match the OPF's unique identifier when they drift, NCX-001) and
**fix_ncx_playorder** (resequencing `playOrder` attributes on
`<navPoint>` start tags so they are strictly sequential; nav-label text
is never touched).
With `--fix-ids`, ids in the NCX that are not valid XML names (digit-led, as when a
converter stamps navPoint ids from UUIDs; colon-bearing) are renamed with the same
`id_` scheme as OPF manifest ids. NCX ids are internal to the NCX (nothing in the
OPF or content documents references them), so the rename needs no cross-file
bookkeeping.

The OPF is located via `META-INF/container.xml` (falling back to the first `.opf`
in the archive). The default pass leaves it untouched, to keep Calibre's embedded
metadata pristine; the opt-in `--fix-ids`, `--strip-epub3-attrs`, `--fix-page-map`,
`--prune-missing-resources`, and `--encode-url-spaces` each edit it when requested,
and the human-facing `dc:` metadata is never altered by any of them.

### Opt-in: escape unknown entities (`--escape-unknown-entities`)

An entity name that is neither XML-predefined nor in the HTML5 table stays a fatal
"entity not declared" (fix_named_entities deliberately leaves it). With this flag,
such references are escaped (`&foo;` -> `&amp;foo;`), which renders exactly as
browsers already render an unknown entity: the literal text `&foo;`. This is
**conditionally** semantics-preserving: rendering is identical except against a
document whose DOCTYPE internal subset *declares* the entity, so any document
carrying an internal subset (`<!DOCTYPE ... [`) is skipped wholesale. Off by
default, never a core transform; the normal gate applies, and CDATA sections and
comments are never rewritten.

### Opt-in: add missing img alt (`--add-img-alt`)

An `<img>` without the required `alt` attribute is an RSC-005 error on every
occurrence. With this flag, `alt=""` is added to such elements. Rendering is
unchanged (an empty alt draws nothing), but this is the one transform that ADDS
markup the author never wrote, and `alt=""` asserts "decorative" to a screen reader
where a missing alt did not; hence off by default, never a core transform. Existing
alt attributes (either quote style) are untouched and the fix is idempotent; the
normal gate applies.

### Opt-in: structural repairs

Fifteen repairs go past well-formedness and therefore require their own flag; none is ever
part of the default pipeline:

- **`--fix-empty-body`**: `&nbsp;` inside a strictly empty `<body></body>` ("body
  incomplete"). Adds visible content, hence opt-in like `--add-img-alt`.
- **`--fix-missing-title`**: inject `<title>Unknown</title>` when the head has no usable
  title.
- **`--fix-id-colons`**: illegal colons in `id="X:Y"` and their matching internal
  `#X:Y` fragment references become `_`. Only the bare `id` attribute is in scope
  (`data-id` values are arbitrary data and are never touched), the fragment of an
  external URL names a position in that other document and survives verbatim, and the
  NCX's `content src` fragments follow the rename (`fix_ncx_src_fragments`), so a ToC
  never dangles against the ids it references.
- **`--fix-ids`**: renames manifest `item` ids that are not valid XML names
  (digit-led, colon-bearing) with the deterministic `id_` scheme, and updates
  every reference to them: spine `idref` and `toc`, item `fallback` and
  `media-overlay`, and the EPUB 2 cover meta, in both quote styles. NCX ids are
  renamed by the same pass (`fix_ncx_ids`); colon-bearing fragments follow the
  rename via `--fix-id-colons`' NCX half. Href paths and filenames are untouched.
- **`--unwrap-block-in-inline`**: drop a `<span>` that illegally wraps a
  `<div>/<p>/<blockquote>`, keeping the block element and its text.
- **`--strip-invalid-value`**: remove misplaced `value="..."` attributes from non-form
  elements.
- **`--fix-page-map`**: normalize legacy page-map markup — drop the non-standard
  `page-map="..."` attribute from the OPF `<spine>` and add `class="pages"` to
  classless NCX `<pageList>` elements (epubcheck rejects both; older HarperCollins /
  Anna's Archive conversions carry them). A pageList that already carries a class is
  untouched.
- **`--strip-epub3-attrs`**: scrub the EPUB3-only attributes epubcheck rejects on an
  EPUB2 package — `page-progression-direction`, `epub:type`, `aria-label` (a fixed,
  documented set; extend only with a named epubcheck finding). Rendering is unchanged,
  and reader-legitimate lookalikes (`type`, the wider aria family) survive. The edit is
  anchored to real start tags and never touches CDATA sections or comments, so prose
  that merely mentions `epub:type="chapter"` is preserved.
- **`--downgrade-epub3-tags`**: downgrade EPUB3/HTML5 semantic elements to their EPUB2
  equivalents — `figure`/`section` to `div`, `figcaption` to `p` — keeping existing
  classes and appending the semantic name (`class="figure"`) as the styling hook.
  Names a stylesheet styles as an element selector are protected book-wide
  (`css_protected_tags`/`style_block_tags` are parameterized over the tag set), so
  styled formatting can never be silently destroyed; a protected book keeps its
  RSC-005 findings, which is the honest outcome.

Both EPUB2-targeted fixes are gated on the package version carried in the OPF: they
fire on EPUB 2 packages (major version 2 or 1) and are inert on EPUB 3 packages and
when no version can be read, where their target defects do not exist and firing them
would strip legal attributes and downgrade legal elements (the 2026-09-08 sweep found
every EPUB3 book in an `--all` sweep taking exactly that damage).
- **`--unwrap-illegal-tags`**: delete `<st> <sentence> <o> <w> <pagebreak>` tags outright,
  inner text preserved. Its CSS precondition is enforced by the library itself:
  `transforms.css_protected_tags` scans every stylesheet entry in the book (nested at-rules
  included) and `style_block_tags` each document's inline `<style>` blocks for these names
  used as *element selectors* (`w { }`, `pagebreak.new:after {}`; `.st`/`#w` class/id
  selectors do not protect), and protected names are skipped for the whole book — styled
  formatting can never be silently destroyed.
- **`--prune-missing-resources`**: remove references to files the archive does not
  contain (RSC-007/PKG-010): dead `<link>` elements, anchors' `href` to absent files
  (the anchor and its text stay), absent `<img>` sources (replaced by their escaped
  alt text when they carry one, dropped otherwise), and orphaned non-spine OPF manifest
  `<item>` declarations. Spine documents are never pruned: a missing spine document is
  a damaged fragment for the audit's spine-integrity check, never a silent drop.
- **`--strip-broken-anchors`**: strip `href` attributes that cannot resolve, keeping
  the anchor text byte-for-byte. A `#fragment` the target document does not define
  (RSC-020 "fragment identifier not defined", RSC-012 "points to the wrong element") is
  removed from the anchor; NCX `<content src="doc#frag"/>` falls back to the document
  target, keeping chapter navigation at document precision — the fragment is never
  re-pointed at a guessed sibling document, because a drifted id (Mobipocket `filepos`
  anchors after a converter re-split) may exist nowhere. A target wholly absent from
  the archive is left for the spine-integrity report. href values carrying a scheme no
  reader resolves (`kindle:embed:`, `file:`, ...) are stripped under the same flag; the
  resolvable set is fixed (`http`, `https`, `mailto`) and extends only with a named
  finding.
- **`--encode-url-spaces`**: percent-encode raw spaces in `src`/`href` attribute values
  across the package (OPF manifest, NCX `content src`, content documents). A URL with a
  literal space is not a valid URL (RSC-020 "not a valid URL") and unresolvable on
  strict readers; the encoded form denotes the same file and renders identically. Scope
  is fixed to the space character: extend only with a named epubcheck finding.

- **`--fix-container`**: generate `META-INF/container.xml` at the located OPF when the
container is missing, unparseable, or names a file the archive does not contain. This is the
gateway defect: epubcheck stays fatal while the OPF is unfindable, so no other repair can be
gate-accepted on such a book. The generated file is byte-deterministic (a fixed template plus
the OPF path; the entry's timestamp is the constant epoch), and a healthy container is never
touched.
- **`--fix-media-types`**: normalize wrong manifest `media-type` declarations (OPF-029: a
file's bytes do not match the declared type). The expected type comes from the extension, and
the rewrite fires only when the file's magic bytes at offset 0 confirm the extension (jpg,
jpeg, png, gif): a PNG renamed `.jpg` keeps its wrong-but-honest declaration rather than
gaining a worse one. Attribute-only, quote-style preserved; files absent from the archive
belong to `--prune-missing-resources`.

- **`--fix-cover`**: repair dangling EPUB2 cover wiring (the cover-wiring ruling's
deterministic half). A `<meta name="cover" content="X">` whose `X` names no manifest id is
re-pointed when the OPF guide's `<reference type="cover">` resolves to exactly one manifest
item (the producer's own statement; nothing is guessed), and removed when nothing identifies
the item. A meta whose id exists in the manifest is never touched here, even when the item's
file is absent: that class belongs to `--prune-missing-resources` and its edge completion.
The EPUB3 `properties="cover-image"` slice is audit-only by ruling. Cover wiring is invisible
to epubcheck, so cover-only repairs are accepted under the `no_worse` bar the lossy strips
use, with the `partial` rule intact.

All fifteen are evaluated by the normal `gate`: unlike the lossy strips, their benefit is
visible to epubcheck (they clear errors), so a run with no measurable improvement is a
noop and nothing is applied. CDATA sections and comments are never rewritten, as
everywhere else.

### Transform invariants

- **Semantics preserved.** A self-closed void element, a numeric character reference,
  and an escaped ampersand render identically to the author's intent. No visible text,
  attribute, or element is added or removed beyond making the markup parseable.
- **CDATA sections and comments are never rewritten.** Their content is literal and
  already legal XML; escaping a `&` or self-closing a `<br>` inside them would change
  the content (e.g. corrupt CDATA-wrapped CSS/JS).
- **Idempotent.** Re-running changes nothing once a document is well-formed.
- **Already-correct markup is untouched.** Self-closed void elements, predefined and
  numeric entities, and single `xmlns` declarations are left exactly as they are.

### Opt-in, lossy: broken tags strip (`--strip-broken-tags`)
Strips leaked HTML closing tags missing their open brackets (e.g. `</p>` rendering as raw text). 
Since this removes visible text from the reading experience, it is lossy by design and off by default. It is evaluated via the `no_worse` acceptance gate.

### Opt-in, lossy: watermark strip (`--strip-watermarks`)
Strips known producer and redistributor watermarks out of EPUBs (e.g. OceanofPDF.com, ABC Amber LIT Converter). The removal is a balanced-element surgery rather than regex slicing: it locates the stamp and deletes the outermost wrapper whose *entire visible text* is the watermark, ensuring prose that merely mentions the URL is preserved. Also drops known zero-byte marker files. Like other lossy operations, this is verified via `no_worse`.

When the stamp link sits inline (no clean wrapper), the bare `<a>` element may be
deleted only when the match demonstrably holds nothing but the stamp: a tag-free
body no longer than a stamp, or a body whose visible text is exactly the
watermark. An anchored match larger than that (an unclosed stamp `<a>` that
swallowed prose out to the next unrelated `</a>`) is refused: nothing is removed,
the refusal is counted (`watermark_refusals`) and reported for manual repair, and
`run phase1` surfaces it as a `manual_watermark_repair` decision.

## Archive rewrite

Entries are copied one at a time; `mimetype` is written first and `ZIP_STORED`. Its
content is the OCF constant `application/epub+zip` (exact bytes, no trailing newline):
a missing entry is added (`mimetype_added`) and wrong or whitespace-padded content is
normalized (`mimetype_normalized`), both counted in the report and gate-checked like
any other fix. The entry carries the source entry's timestamp, or 1980-01-01 when it
is being added, never the wall clock: repairing one book twice must produce identical
bytes, and this was the only entry not written from a source `ZipInfo`. It is built as
a fresh `ZipInfo` rather than the source one because OCF requires the mimetype entry to
carry no extra field. Content documents and the NCX get the transforms above; every other
entry is copied verbatim with its original compression. An eligible entry that no
transform changed is also copied byte-for-byte, never decoded and re-encoded, so a
clean non-UTF-8 file cannot be silently mangled. A document that is not valid UTF-8
at all (windows-1252, UTF-16) is never decoded-with-replacement and re-encoded: it is
copied byte-for-byte, counted in the report (`non_utf8_docs_skipped`), and left for
manual repair. The anchor pass (`--strip-broken-anchors`) runs after every other
content fix, against an id snapshot that replicates each id-moving fix ahead of it. A
`RepairReport` records
per-transform counts and whether the NCX uid was synced.

## The epubcheck gate

`gate(before, after)` classifies a repair:

- `reject` if `after.fatals > before.fatals` (a net-new fatal).
- If `before.fatals > 0`: `accept` when `after.fatals == 0`; `partial` when fatals were
  reduced but not eliminated; `noop` when unchanged. Error-count changes are ignored
  here, because fixing a fatal unmasks previously-hidden errors in the same file.
- If `before.fatals == 0`: `reject` if errors rose, `accept` if they strictly fell,
  else `noop`.

Only `accept` repairs are applied in place. `partial` repairs are reported for manual
follow-up and never auto-applied (the book still does not open). If epubcheck cannot
be run or its output cannot be parsed during a validated run, the book is an `error`:
the gate has not accepted anything, so nothing is applied or written. With
`--no-validate`, the gate is skipped and repairs are trusted on the RepairReport alone.

The oracle itself runs two ways: the `epubcheck --json -` subprocess, or a persistent
daemon (`_EpubcheckDaemon`, a bounded pool sized by `--workers`) that writes
`FastDaemon.java` at runtime and launches it through Java's single-file source
launcher (in-memory compilation by the running JVM: no javac, no version skew),
driving the warm JVM over a pipe. The daemon answers with the counts it reads from
the JSON that epubcheck's own `CheckingReport.generate()` serializes (the same
document `--json` produces), so both paths measure identically by construction.
(Epubcheck's JSON counts aggregated messages, not occurrences: a defect repeated
three times is one error. The gate has always measured that scale.) Every daemon
roundtrip is bounded by the caller's timeout, and any daemon failure tears it down
and falls back to the subprocess for good.

## Page-number strip (opt-in, lossy)

`--strip-pagination` removes print page numbers and running headers that a PDF/OCR
conversion baked into the body as literal paragraphs (so they reflow mid-sentence:
"where the hay cart 16 was taking him"). This is the one mode that removes visible
content; it is off by default and never runs unless requested.

Scope is `<p>` elements (where the defect is carried). For each book:

- **Running headers** are short `<p>` blocks repeated >= 8 times across the whole book
  (the title, an author byline, a download watermark); they are page furniture.
- A standalone `<p>` whose entire text is a bare number is a candidate. It is removed
  when it is **baked** (interrupts prose: a lowercase continuation after it, a word
  split across it where the previous block ends in a hyphen, an unfinished previous
  sentence, or it sits between two running headers) or, when the book has a confirmed
  **page-number layer**, when it is any arabic page number in the body.
- A **page-number layer** is confirmed only when BOTH hold: >= 20 standalone arabic
  numbers AND >= 3 confident interrupts. A chapter-numbered book has zero interrupts, so
  its chapter numbers are never touched. Roman numerals are removed only as a confident
  interrupt, preserving roman chapter and front-matter numbering; a roman counts as a
  number only when it is well-formed (explicit subtractive pairs) and under 100, so
  ordinary words built from roman letters (`mid`, `mix`, `civil`) are never read as
  pages. Year-range values (1500-2099) are never page numbers.
- **Merging:** only a confident interrupt (lowercase continuation or word split) rejoins
  the two surrounding prose paragraphs (a word split closes up its hyphen). Every other
  removal is delete-only, leaving the existing paragraph break.
- **Navigation targets survive.** An `id` anywhere in a removed block (a page-list or
  internal-link target) is preserved: `<a id=...>` anchors are hoisted into the merged
  paragraph, an id on the removed `<p>` itself becomes an empty anchor there, and a
  delete-only removal keeps an emptied `<p id=...></p>` shell instead of deleting
  outright. Single- and double-quoted ids are both recognized.

### Safety nets

Any failure aborts the edit and returns the document unchanged:

1. **Character conservation**: the visible text after the strip equals the text before
   minus exactly the removed numbers/headers (whitespace and hyphens normalized away),
   so not one character of prose can be lost or fabricated.
2. **Tag balance**: `<p>` and `<a>` remain balanced after splicing.
3. **The `no_worse` gate** (below) is the final oracle.

### Acceptance: `no_worse`

The strip's benefit is invisible to epubcheck (a baked page number is valid markup), so
the improvement-demanding `gate` does not apply. `no_worse(before, after)` accepts when
the result is no worse: no net-new fatals, and no new errors unless fatals were already
masking them. A net-new fatal or error is a `reject`. This mirrors oceanstrip's bar.

`no_worse` relaxes only the improvement demand, never the `partial` rule: a result that
still has fatals is classified `partial` even when it is no worse, so a still-broken
book can never be auto-applied through the lossy path.

## Library replacement

For a Calibre library (`Author/Title (id)/Title - Author.epub`):

- Work is done on a temporary copy; the original is read-only until the gate accepts.
- Replacement is **atomic**: the repaired bytes are written to a temp file in the same
  directory, fsynced, then `os.replace()`d over the original. The filename and path
  Calibre expects never change; no partial file is ever visible.
- The original file mode is preserved. Only the `.epub` is touched; `metadata.opf`,
  `cover.jpg`, and `metadata.db` are left for Calibre's Quality Check sync.
- Writing requires `--apply` (default is a dry run). A backup is taken first when
  `--backup DIR` or `--backup-inplace` is given.
- A book that cannot be read at all (not a zip, truncated, encrypted entries) is
  reported and counted as `unreadable`; it never aborts the rest of the sweep.
- The `library` exit code is 0 for a clean sweep, 1 for a usage error, and 2 when any
  book was rejected, unreadable, or failed epubcheck, so scripts can detect trouble.
  Argparse-level misuse (unknown flag, malformed argument) exits 2 before the tool's
  own validation runs; the tool's own usage validations exit 1. A `partial` book
  (improved but still fatal) is reported for manual follow-up and counts as trouble:
  `library`, `run phase1`, and `run phase3` all exit 2 on it (unified 2026-09-10).
- With `--sweep`, `--workers N` runs the candidate-selection epubcheck pass through N
  concurrent workers (default 1: serial, unchanged). Books are checked in windows of N
  consumed in input order, so the candidate set and the before-measurements are identical
  to the serial sweep; the repair phase is never parallel (the shared workdir and the
  atomic-replacement contract live there).

### Native format installation (`--install-to-calibre`)
Optionally, bindery-cli installs the repaired EPUB as the book's format through cquarry's write module (`WritableCalibreDB`): the file is placed atomically — an in-place replace over the catalogued file when one exists (same path, same `data.name`), or a fresh placement under the repaired file's stem otherwise — and the `data` row follows through `set_format` (cquarry 1.17's sanctioned remove+add in one transaction), keeping the size truthful and queuing the book in `metadata_dirtied` so Calibre regenerates its sidecar .opf. The external `calibredb` CLI is no longer used (the v0.23.1 `--replace` crash class is gone with it). It automatically falls back to atomic filesystem replacement if a valid Calibre ID cannot be extracted, and a database failure degrades to the in-place save with a warning rather than losing the repair.

The no-catalog fallback guesses the id from the `(N)` directory fragment. A guessed
id may drive a row update only when metadata.db corroborates it: the book row exists,
the file lives in that book's own directory, and (when an EPUB is catalogued) carries
its stored `data.name`. Anything else is a stray file inside a book directory; the
repair is saved in place and the row is left untouched (updating the row from a stray
wrote the stray's size over the catalogued entry, the 2026-09-08 stray-size incident),
and a stale `(N)` directory whose book no longer exists saves in place instead of
crashing the sweep.

## Audit subcommand (read-only)

`bindery audit {content,pagenumbers,emptytext,ocr,monolithic,completeness,all} [PATH] [--max-doc-chars N]
[--tag TAG] [--id IDs]` (v0.15.0, `audit.py`; `--tag` since v0.18.0; `monolithic` since v0.21.0, `--max-doc-chars N`; `--id` since v0.19.0, comma-lists in v0.23.0; `completeness` since v0.36.0) inspects
EPUB body text for flaws epubcheck cannot see: non-English script blocks, baked-in page-number
layers (sliding-window density heuristics), empty or thin books, systemic OCR damage, and
single oversized content documents (one spine doc at or above 300k characters — readers refuse
to render them even though the book totals normally), damaged archives (every archive entry
is fully read for CRC + decompression, reporting CORRUPT rather than EMPTY), and spine integrity
issues. Manifest/NCX references to absent files are classified as either `convention` (ToC is bloated but
present documents form a consecutive chapter span) or `fragment` (the span itself is broken).

The archive verdict distinguishes font obfuscation from DRM (Phase 16): entries
`encryption.xml` declares under a font-obfuscation algorithm (the IDPF `2008/embedding`
URI and both Adobe forms, including the `ns.adobe.com` URI real-world files carry) that
read fine are the OBFUSCATED advisory — publisher embedding, benign, never a failure; an
unreadable obfuscation entry is CORRUPT (a broken font, not a business model), and only
non-obfuscation algorithms give the ENCRYPTED verdict with its DRM skip advice.

The completeness analyzer is the phase-1 spot-check and is advisory by contract: it never
flags a book and never moves the exit code. Per book it reports the spine doc count, the
prose-doc count (spine docs carrying at least 400 visible characters), the opening and closing
120 characters of the first/middle/last prose doc, a trailing-ToC classification of the final
spine doc, and the fraction of spine docs that could not be read (corrupt entries plus
unresolved itemrefs). A trailing ToC (the final doc is dominated by short link lines, at
least half its lines carry links, and no block runs to paragraph length) is book furniture:
it is excluded from the prose sampling so the closing excerpt comes from the real back
matter, and the verdict becomes ADVISORY (as it does at an unreadable fraction of 10% or
more). Like emptytext it is skipped wholesale when the archive verdict owns the body-text
story.

In library mode, EPUB files are resolved through `cquarry.db.CalibreDB.get_format_path()` — the
storage-layout logic is not duplicated here. The scan itself still writes nothing. The opt-in
`--tag TAG` pass applies `TAG` to flagged books via `cquarry.write.WritableCalibreDB`, the
separate trigger-safe write module (it registers Calibre's `title_sort`/`uuid4` SQL functions,
bumps `books.last_modified`, and cleans link tables before tag deletion). THIN emptytext
advisories stay untagged; already-tagged books are skipped; a missing file is a scan error, not a
silent skip.

`--json FILE` (v0.29.0) writes the same verdicts machine-readably, in the `library --json`
shape: one record per file with a `status` (`clean`, `problem`, or `error`) and per-analyzer
verdicts (`problem`, `status`, `details`); the always-on archive/spine verdicts appear OK when
they were silent, emptytext is omitted when the archive verdict owns the book's body-text story,
and a scan error becomes its own record (`status: "error"`, an `error` message, no verdicts).
All three modes write it (directory, library, and single-book); `--json` with `--id` accepts
exactly one book id, since each single-book run writes the file wholesale.

## Run verbs (acquisition slices)

`bindery run phase1 DIR [--json FILE] [--apply-lossy] [--backup DIR] [--non-interactive]`
composes the shipped read-only audit battery (corruption sweep, content battery,
monolithic) with the gated repair sweep (epubcheck, watermark detection,
repairability) over one directory of loose files, in the phase-1 skill's
documented order. It introduces no new repair classes: every fix, gate, and
safety net is the shipped one. The verb is read-only until `--apply-lossy` is
passed, and that flag is the recorded lossy-strip consent; `--backup DIR`
mirrors originals before any replacement. Consent questions never block the
verb: they surface as `decisions_needed` entries in the JSON (never prompts),
and `--non-interactive` declares that contract for callers. Exit codes per the
library contract: 0 clean, 1 usage, 2 when any book is flagged, rejected,
unreadable, or failed epubcheck; a consent question alone is not trouble.

`bindery run phase3 --ids IDS [--json FILE] [--non-interactive]` (run from the
library directory) is exactly `library --id IDS --sweep --only all --apply
--all --install-to-calibre` plus a pre/post epubcheck summary over the swept
books. It is the post-import apply step by design: the batch was vetted in
phase 1, the epubcheck gate still governs every replacement, and the atomic
replacement contract is untouched. The scope refusal is mechanical: no `--ids`,
no sweep, exit 2 (a library-wide sweep is a dedicated hours-long task, never a
verb call). Books left partial or unreadable surface as `decisions_needed`.

## The Calibre plugin (Bindery Repair)

Each release vendors the repair core into a Calibre plugin zip,
`BinderyRepair-v<VERSION>.zip`, generated from the tagged tree by
`scripts/build_plugin.py` and attached to the GitHub release. The zip carries
`transforms.py`, `epub.py`, `pagination.py`, `watermark.py`, and
`reserialize.py` byte-identical to `src/bindery/` (the zip root is a package,
so their relative imports resolve unchanged; a suite drift test pins the
equality) plus the plugin entry `plugin/__init__.py` with the version tuple
substituted from the single-source `VERSION`.

Identity and shape: the plugin is `Bindery Repair` (import name
`bindery_repair`, via the `plugin-import-name-` marker), a
`FileTypePlugin` with `on_import = True`, `supported_platforms = ['linux']`
(the loader rejects an empty platform list, which also makes other OSes an
enforced non-goal).

The active fix set is exactly the CLI's default pass: the five
well-formedness transforms plus the NCX pipeline. All structural repairs and
the three lossy strips stay CLI-only: their acceptance IS the epubcheck gate,
which cannot run inside Calibre. Opt-in flags are never enabled by the
plugin; nothing runs ungated.

Behavior contract:

- `run(path)` never raises. It returns the path of a repaired copy built with
  the plugin's `temporary_file()` (Calibre imports that instead; the original
  on disk is untouched, `metadata.db` is never written, there is no
  `postimport` hook), or the original path on any trouble: non-`.epub`
  suffix, over the size cap, zero fixes, a failed `testzip()` verification of
  the rewritten archive, or an exception (each logged, never raised).
- Byte-idempotence: an already-clean book yields zero fixes and the original
  path, because the format-add path (`db.add_format(..., run_hooks=True)`)
  re-enters the plugin on repaired files.
- One log line per book (fixed / no fixes / refused / errored), appended
  under a lock-free best-effort policy: logging failures never break an
  import.
- Configuration is the `site_customization` string parsed as JSON: `log`
  (default true), `log_path` (default `<config_dir>/bindery_repair.log`),
  `max_size_mb` (default 150: refuse absurd files rather than stall an
  import), and `epubcheck_path` (default null). `epubcheck_path` enables the
  experimental on-PATH validation mode: the repaired copy is re-measured with
  the user's epubcheck binary under the `no_worse` bar and refused on a
  regression; an unanswered measurement never refuses. The mode is off by
  default (the latency ruling stands; the default pass is safe ungated).

## Out of scope (non-goals)

- Fixing RSC-005 schema/content-model violations *in bulk*: the scoped, opt-in
  RSC-005 repairs (`--strip-epub3-attrs`, `--downgrade-epub3-tags`, both gated on
  the package version) ship and stay, but an indiscriminate RSC-005 sweep remains
  out of scope.
- Repairing genuinely mangled structure remains opt-in and bounded:
  `--reserialize` (the lone html5lib dependency) re-parses malformed documents
  carrying an `<html>` root and re-emits XHTML, refusing non-HTML XML sidecars;
  corrupted tag names are covered by `--strip-bad-attrs` and
  `--unwrap-illegal-tags`. Wholesale structural rewrites without a root to anchor
  on stay out of scope, and anything the opt-ins cannot make deterministically
  safe is detected as `partial`/`nochange` and reported.
- Editing human-facing dc: metadata or creating content.
