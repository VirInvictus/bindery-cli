# Bindery specification

The contract. Read this before changing semantics.

## Scope

Bindery repairs well-formedness and a few specific validity defects in EPUBs. It is
deliberately narrow: every transform is deterministic and semantics-preserving, and
the result is only kept when epubcheck confirms it improved. Deterministic is meant at
the byte level: repairing the same book twice, in separate processes, produces
identical archives. Bindery does not reflow,
restyle, re-compress, or restructure content, and it does not attempt to fix arbitrary
schema (RSC-005) violations, which are usually harmless to readers and not safely
mechanizable.

The deliberate exceptions to "semantics-preserving" come in two kinds, both strictly
opt-in. The six **structural repairs** (`--fix-empty-body`, `--fix-missing-title`,
`--fix-id-colons`, `--unwrap-block-in-inline`, `--strip-invalid-value`,
`--unwrap-illegal-tags`) alter markup structure or fabricate minimal content; the three
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
fix_named_entities, plus **dtb:uid sync**, **fix_ncx_playorder** (sequentially rewrites duplicate playOrder attributes)  to the OPF unique identifier (NCX-001).
With `--fix-ids`, ids in the NCX that are not valid XML names (digit-led, as when a
converter stamps navPoint ids from UUIDs; colon-bearing) are renamed with the same
`id_` scheme as OPF manifest ids. NCX ids are internal to the NCX (nothing in the
OPF or content documents references them), so the rename needs no cross-file
bookkeeping.

The OPF is located via `META-INF/container.xml` (falling back to the first `.opf`
in the archive) and is left untouched, to keep Calibre's embedded metadata pristine.

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

Six repairs go past well-formedness and therefore require their own flag; none is ever
part of the default pipeline:

- **`--fix-empty-body`**: `&nbsp;` inside a strictly empty `<body></body>` ("body
  incomplete"). Adds visible content, hence opt-in like `--add-img-alt`.
- **`--fix-missing-title`**: inject `<title>Unknown</title>` when the head has no usable
  title.
- **`--fix-id-colons`**: illegal colons in `id="X:Y"` and their matching `#X:Y` fragment
  references become `_`; word-bounded, so external URLs are untouched.
- **`--unwrap-block-in-inline`**: drop a `<span>` that illegally wraps a
  `<div>/<p>/<blockquote>`, keeping the block element and its text.
- **`--strip-invalid-value`**: remove misplaced `value="..."` attributes from non-form
  elements.
- **`--unwrap-illegal-tags`**: delete `<st> <sentence> <o> <w> <pagebreak>` tags outright,
  inner text preserved. Its CSS precondition is enforced by the library itself:
  `transforms.css_protected_tags` scans every stylesheet entry in the book (nested at-rules
  included) and `style_block_tags` each document's inline `<style>` blocks for these names
  used as *element selectors* (`w { }`, `pagebreak.new:after {}`; `.st`/`#w` class/id
  selectors do not protect), and protected names are skipped for the whole book — styled
  formatting can never be silently destroyed.

All six are evaluated by the normal `gate`: unlike the lossy strips, their benefit is
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
clean non-UTF-8 file cannot be silently mangled. A `RepairReport` records
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
  interrupt, preserving roman chapter and front-matter numbering. Year-range values
  (1500-2099) are never page numbers.
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

### calibredb replacement (`--install-to-calibre`)
Optionally, Bindery can replace the format natively inside Calibre using `calibredb add_format` (replacement is add_format's default behavior; the flag does not exist). This natively manages the database change (updating sizes, retaining metadata, keeping custom columns intact). It automatically falls back to atomic filesystem replacement if a valid Calibre ID cannot be extracted from the candidate's filesystem path.

## Audit subcommand (read-only)

`bindery audit {content,pagenumbers,emptytext,ocr,monolithic,all} [PATH] [--max-doc-chars N]
[--tag TAG] [--id IDs]` (v0.15.0, `audit.py`; `--tag` since v0.18.0; `monolithic` since v0.21.0; `--id` since v0.19.0, comma-lists in v0.23.0) inspects
EPUB body text for flaws epubcheck cannot see: non-English script blocks, baked-in page-number
layers (sliding-window density heuristics), empty or thin books, systemic OCR damage, and
single oversized content documents (one spine doc at or above 300k characters — readers refuse
to render them even though the book totals normally), damaged archives (every archive entry
is fully read for CRC + decompression, reporting CORRUPT rather than EMPTY), and spine integrity
issues. Manifest/NCX references to absent files are classified as either `convention` (ToC is bloated but
present documents form a consecutive chapter span) or `fragment` (the span itself is broken).

In library mode, EPUB files are resolved through `cquarry.db.CalibreDB.get_format_path()` — the
storage-layout logic is not duplicated here. The scan itself still writes nothing. The opt-in
`--tag TAG` pass applies `TAG` to flagged books via `cquarry.write.WritableCalibreDB`, the
separate trigger-safe write module (it registers Calibre's `title_sort`/`uuid4` SQL functions,
bumps `books.last_modified`, and cleans link tables before tag deletion). THIN emptytext
advisories stay untagged; already-tagged books are skipped; a missing file is a scan error, not a
silent skip.

## Out of scope (non-goals)

- Fixing RSC-005 schema/content-model violations in bulk.
- Repairing genuinely mangled structure (unclosed non-void elements, corrupted tag
  names, embedded VML/SVG). These are detected as `partial`/`nochange` and reported.
- Editing human-facing dc: metadata or creating content.
