# Bindery specification

The contract. Read this before changing semantics.

## Scope

Bindery repairs well-formedness and a few specific validity defects in EPUBs. It is
deliberately narrow: every transform is deterministic and semantics-preserving, and
the result is only kept when epubcheck confirms it improved. Bindery does not reflow,
restyle, re-compress, or restructure content, and it does not attempt to fix arbitrary
schema (RSC-005) violations, which are usually harmless to readers and not safely
mechanizable.

The one deliberate exception to "semantics-preserving" is the **opt-in
`--strip-pagination` mode** (see "Page-number strip"). It removes content a converter
injected, not content the author wrote, so it is off by default and gated differently.
Everything else in this spec is the always-safe core.

## Transforms

Applied to content documents (`.xhtml`, `.html`, `.htm`, `.xml`), in order:

1. **strip_prolog_junk**: remove a BOM or stray characters before the first `<`.
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
fix_named_entities, plus **dtb:uid sync** to the OPF unique identifier (NCX-001).

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

## Archive rewrite

Entries are copied one at a time; `mimetype` is written first and `ZIP_STORED`. Its
content is the OCF constant `application/epub+zip` (exact bytes, no trailing newline):
a missing entry is added (`mimetype_added`) and wrong or whitespace-padded content is
normalized (`mimetype_normalized`), both counted in the report and gate-checked like
any other fix. Content documents and the NCX get the transforms above; every other
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

## Out of scope (non-goals)

- Fixing RSC-005 schema/content-model violations in bulk.
- Repairing genuinely mangled structure (unclosed non-void elements, corrupted tag
  names, embedded VML/SVG). These are detected as `partial`/`nochange` and reported.
- Editing metadata, the OPF, or the Calibre database.
