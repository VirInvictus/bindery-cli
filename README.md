<div align="center">
  <img src="logo.svg" width="96" height="96" alt="Bindery logo"/>
  <h1>Bindery</h1>
  <p>Repair broken EPUBs with safe, deterministic fixes, validated by epubcheck, with optional in-place replacement in a Calibre library.</p>
</div>

## What it fixes

Bindery makes accidentally broken markup well-formed again. It does not rewrite or reflow content; it only applies a small set of deterministic, semantics-preserving fixes that real-world EPUBs (especially Calibre conversions) trip over:

- **Unclosed void elements** (`<link>`, `<br>`, `<img>`, ...) get self-closed.
- **Undeclared named entities** (`&nbsp;`, `&deg;`, `&eacute;`, ...) become numeric character references that every XML parser understands.
- **Bare `&`** (common in `toc.ncx`) is escaped to `&amp;`.
- **Junk before the XML prolog** (BOM, stray bytes) is stripped.
- **Duplicate `xmlns`** on the root `<html>` is collapsed to one.
- **NCX-001**: `toc.ncx` `dtb:uid` is synced to the OPF unique identifier.
- **mimetype** is rewritten first and stored, fixing the common ordering defect; a missing entry is added and wrong or whitespace-padded content is normalized to the OCF constant.

Five opt-in fixes go further:

- **`--fix-ids`**: rewrite ids that are not valid XML names (start with a digit, contain a colon) in the OPF manifest, updating every reference to them (spine, fallback, media-overlay, the EPUB 2 cover meta), and in the NCX (where old conversions stamp navPoint ids from UUIDs, one error per id). Touches the OPF, so it is off by default; the dc: metadata is never altered.
- **`--add-img-alt`**: add `alt=""` to `<img>` elements missing the required attribute. Renders identically, but it is the one fix that adds markup the author never wrote, and an empty alt tells a screen reader the image is decorative; hence opt-in.
- **`--reserialize`**: rebuild content documents that are still malformed by re-parsing them with html5lib and re-emitting XHTML, closing unclosed `<p>`/`<div>`/`<span>`/`<blockquote>` that the regex transforms cannot. Runs only on documents that are not already well-formed, so good files are untouched.
- **`--strip-bad-attrs`**: drop attributes that are invalid XML (a name starting with a digit, or a namespaced name whose prefix is never declared, like Office VML `v:shapes`). Surgical and a no-op on well-formed files.
- **`--escape-unknown-entities`**: escape entity names that are not in the HTML5 table (`&foo;` becomes `&amp;foo;`), which renders exactly as browsers already render an unknown entity: the literal text. Documents whose DOCTYPE carries an internal subset are skipped wholesale, since a subset can declare custom entities.

One opt-in fix is **lossy** and stands apart from the semantics-preserving rest:

- **`--strip-pagination`**: remove print page numbers and running headers that a PDF/OCR conversion baked into the body text as literal paragraphs (so they reflow into the middle of a sentence: "where the hay cart **16** was taking him"). It removes only that injected furniture, never the author's prose: where a number split a sentence it rejoins the two paragraphs (closing up a word split like `compli-` / `mentary`), and it preserves roman chapter numbers, page-list nav anchors, and years. A book is only treated as paginated when it has both a dense run of arabic numbers and several confident mid-sentence interrupts, so a merely chapter-numbered book is left alone. Three safety nets guard every edit (character conservation, tag balance, and an epubcheck no-regression check); any failure leaves the document untouched. Because its benefit is invisible to epubcheck, it is accepted when the result is *no worse* rather than measurably better.

## The safety contract

Every repair is gated by [epubcheck](https://github.com/w3c/epubcheck). The acceptance rule is two-mode, because a fatal parse error makes epubcheck stop reading a file and hides every downstream error:

- If a book **had fatals**, success means **fewer fatals**. The error count may rise as previously-hidden schema warnings become visible once the file parses; that is the book going from "won't open" to "opens with nits," not a regression.
- If a book had **no fatals**, an error increase is a real regression, so a strict error decrease is required.

Introducing a net-new fatal is always rejected. If epubcheck itself fails to run (crash, timeout, unparsable output), the book is reported as an error and never applied; only an explicit `--no-validate` skips the gate. Originals are never modified except by an explicit, atomic in-place replace (see below), and even then only after the gate accepts the result.

The lossy `--strip-pagination` mode is the exception to "must improve": removing a baked-in page number leaves epubcheck counts unchanged (the number was valid markup), so that mode is accepted when the result is **no worse** (no net-new fatals or errors), the same bar oceanstrip uses, on top of its own character-conservation and tag-balance checks.

## Install

Python 3.14+, plus epubcheck on `PATH` for the gate. The core is stdlib-only;
`html5lib` is an optional extra, needed only for `--reserialize`.

```sh
uv tool install /path/to/Bindery                    # stdlib core
uv tool install "bindery[reserialize] @ /path/to/Bindery"   # incl. --reserialize
# or from a checkout:
PYTHONPATH=src python3 -m bindery --help          # all modes except --reserialize
PYTHONPATH=src uv run --with html5lib python3 -m bindery --help   # incl. --reserialize
```

## Usage

Repair one book to a new file (gated; writes only if it is an improvement):

```sh
bindery repair broken.epub                 # -> "broken (repaired).epub"
bindery repair broken.epub fixed.epub
bindery repair scanned.epub --strip-pagination   # also remove baked-in page numbers
```

Scan a Calibre library and see what would be fixed, writing nothing:

```sh
bindery library ~/docs/Calibre\ Library --only fatals --audit epub_audit.csv
```

Apply accepted repairs in place, atomically, with backups:

```sh
bindery library ~/docs/Calibre\ Library --only fatals --apply --backup ~/bindery-backups
```

- `--only {fatals,ncx,all}` restricts the candidate set. `ncx` targets NCX-001 mismatches (detected without epubcheck); `fatals` needs `--audit`.
- `--audit CSV` (the `fatals,errors,warnings,path` format produced by an epubcheck sweep) skips clean books so a run is fast. Paths are resolved on both sides, and a CSV that matches nothing triggers a loud warning instead of silently selecting zero books.
- `--sweep` replaces the CSV step entirely: it runs a live epubcheck sweep for candidate selection and reuses each result as that book's before-measurement, so no book is checked twice. Combine with `--only fatals` for a self-contained "find and fix the broken books" run.
- `--json FILE` writes a machine-readable report of the whole run (per-book status, before/after counts, applied flag, summary totals). `--manual-list FILE` writes the paths of every book that was not auto-repaired, one per line, ready for manual follow-up.
- `--apply` is required to write; the default is a dry run. `--backup DIR` mirrors originals before replacing; `--backup-inplace` writes `.epub.bak` beside each file.
- Only the `.epub` is replaced. `metadata.opf`, `cover.jpg`, and `metadata.db` are left for Calibre's Quality Check sync to reconcile.
- A per-book progress line goes to stderr (stdout stays a clean report); `--quiet` suppresses it. A corrupt or unreadable book is reported and skipped, never aborting the sweep.
- Exit codes: 0 for a clean sweep, 1 for a usage error, 2 when any book was rejected, unreadable, or failed epubcheck (for scripts and cron).
- `repair` refuses to overwrite an existing output file unless `--force` is given.

## Companion scripts

`scripts/` holds standalone, read-only utilities that are useful for EPUB maintenance but fall outside Bindery's repair contract (fixing what they find would be a content change, which Bindery makes only via the opt-in `--strip-pagination`):

- `find_missing_images.py`: scans a library tree and reports every book whose `<img>` tags point at files that do not exist inside the archive (a common defect in converted EPUBs). Reads the archives in place; nothing is unpacked or written. The library path is set at the bottom of the script.

## Development

```sh
./run_tests.sh        # stdlib unittest suite
```

See [spec.md](spec.md) for the full contract and [roadmap.md](roadmap.md) for what is planned.

## License

MIT. See [LICENSE](LICENSE).

## Support

If Bindery's useful to you and you'd like to chip in:

```
bc1qkge6zr45tzqfwfmvma2ylumt6mg7wlwmhr05yv
```
