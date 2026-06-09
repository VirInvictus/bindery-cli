<div align="center">
  <img src="logo.svg" width="96" height="96" alt="Bindery logo"/>
  <h1>Bindery</h1>
  <p>Repair broken EPUBs with safe, deterministic fixes, validated by epubcheck, with optional in-place replacement in a Calibre library.</p>
</div>

## What it fixes

Bindery makes intentionally-broken markup well-formed again. It does not rewrite or reflow content; it only applies a small set of deterministic, semantics-preserving fixes that real-world EPUBs (especially Calibre conversions) trip over:

- **Unclosed void elements** (`<link>`, `<br>`, `<img>`, ...) get self-closed.
- **Undeclared named entities** (`&nbsp;`, `&deg;`, `&eacute;`, ...) become numeric character references that every XML parser understands.
- **Bare `&`** (common in `toc.ncx`) is escaped to `&amp;`.
- **Junk before the XML prolog** (BOM, stray bytes) is stripped.
- **Duplicate `xmlns`** on the root `<html>` is collapsed to one.
- **NCX-001**: `toc.ncx` `dtb:uid` is synced to the OPF unique identifier.
- **mimetype** is rewritten first and stored, fixing the common ordering defect.

Two opt-in fixes go further:

- **`--fix-ids`**: rewrite manifest item ids that are not valid XML names (start with a digit, contain a colon) and update their spine references. Touches the OPF, so it is off by default; the dc: metadata is never altered.
- **`--reserialize`**: rebuild content documents that are still malformed by re-parsing them with html5lib and re-emitting XHTML, closing unclosed `<p>`/`<div>`/`<span>`/`<blockquote>` that the regex transforms cannot. Runs only on documents that are not already well-formed, so good files are untouched.

Office VML and broken inline SVG can survive even `--reserialize` and are left for manual repair.

## The safety contract

Every repair is gated by [epubcheck](https://github.com/w3c/epubcheck). The acceptance rule is two-mode, because a fatal parse error makes epubcheck stop reading a file and hides every downstream error:

- If a book **had fatals**, success means **fewer fatals**. The error count may rise as previously-hidden schema warnings become visible once the file parses; that is the book going from "won't open" to "opens with nits," not a regression.
- If a book had **no fatals**, an error increase is a real regression, so a strict error decrease is required.

Introducing a net-new fatal is always rejected. Originals are never modified except by an explicit, atomic in-place replace (see below), and even then only after the gate accepts the result.

## Install

Python 3.14+, plus epubcheck on `PATH` for the gate. The core is stdlib-only; the one
dependency, `html5lib`, is needed only for `--reserialize` and is imported lazily.

```sh
uv tool install /path/to/Bindery
# or from a checkout:
PYTHONPATH=src python3 -m bindery --help          # all modes except --reserialize
PYTHONPATH=src uv run --with html5lib python3 -m bindery --help   # incl. --reserialize
```

## Usage

Repair one book to a new file (gated; writes only if it is an improvement):

```sh
bindery repair broken.epub                 # -> "broken (repaired).epub"
bindery repair broken.epub fixed.epub
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
- `--audit CSV` (the `fatals,errors,warnings,path` format produced by an epubcheck sweep) skips clean books so a run is fast.
- `--apply` is required to write; the default is a dry run. `--backup DIR` mirrors originals before replacing; `--backup-inplace` writes `.epub.bak` beside each file.
- Only the `.epub` is replaced. `metadata.opf`, `cover.jpg`, and `metadata.db` are left for Calibre's Quality Check sync to reconcile.

## Development

```sh
./run_tests.sh        # stdlib unittest suite
```

See [spec.md](spec.md) for the full contract and [roadmap.md](roadmap.md) for what is planned.

## License

MIT. See [LICENSE](LICENSE).
