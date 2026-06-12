# Bindery specification

The contract. Read this before changing semantics.

## Scope

Bindery repairs well-formedness and a few specific validity defects in EPUBs. It is
deliberately narrow: every transform is deterministic and semantics-preserving, and
the result is only kept when epubcheck confirms it improved. Bindery does not reflow,
restyle, re-compress, or restructure content, and it does not attempt to fix arbitrary
schema (RSC-005) violations, which are usually harmless to readers and not safely
mechanizable.

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
   link meta param source track wbr`) that were left open.

Applied to the NCX sidecar (`.ncx`): strip_prolog_junk, escape_bare_amp,
fix_named_entities, plus **dtb:uid sync** to the OPF unique identifier (NCX-001).

The OPF is located via `META-INF/container.xml` (falling back to the first `.opf`
in the archive) and is left untouched, to keep Calibre's embedded metadata pristine.

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

Entries are copied one at a time; `mimetype` is written first and `ZIP_STORED`. Content
documents and the NCX get the transforms above; every other entry is copied verbatim
with its original compression. An eligible entry that no transform changed is also
copied byte-for-byte, never decoded and re-encoded, so a clean non-UTF-8 file cannot
be silently mangled. A `RepairReport` records per-transform counts and whether the
NCX uid was synced.

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

## Out of scope (non-goals)

- Fixing RSC-005 schema/content-model violations in bulk.
- Repairing genuinely mangled structure (unclosed non-void elements, corrupted tag
  names, embedded VML/SVG). These are detected as `partial`/`nochange` and reported.
- Editing metadata, the OPF, or the Calibre database.
