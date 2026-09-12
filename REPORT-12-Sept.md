# REPORT-12-Sept: the repair-landscape research (bindery-cli vs real-world EPUB damage)

Research date: 2026-09-10/11. Method: a read-only research pass mapping
the full taxonomy of real-world EPUB damage — by producer (Calibre
conversions, Word/Office export, InDesign, legacy OCR, Adobe Digital
Editions, KindleUnpack, self-pub aggregators, Windows case-manglers) —
against this repo's shipped repair surface, the Phase 14/15 backlogs,
and the Bug Reports. References include the upstream clone's format
readers. Full evidence lives in the agent's findings; this report is
the distilled verdict and every actionable gap with its routing.

## Verdict

Bindery handles roughly **60-65% of real-world damage classes
end-to-end** and is near-complete for its founding niche: markup
well-formedness (the conversion-fatals class), NCX basics,
zip/mimetype container hygiene, dead-reference pruning, watermark and
page-number furniture, and archive/spine diagnosis (corruption, DRM
skip, ToC-bloat classification). Phase 14's hardening and all five Bug
Reports are shipped through v0.35.0; Phase 15 (the completeness
analyzer) is approved.

The missing ~35% is not more markup surgery. It is **package-level
repairs** — container.xml, manifest media-types, spine attribute
cleanup, encryption.xml/obfuscation state, zip-level dedupe, href
case/IRI mismatches — and the finding that matters most: several of
those classes are **gateway defects**. Epubcheck stays fatal while
they persist, so no shipped repair can ever be gate-accepted on those
books. They are locked out of the pipeline entirely.

## A. The damage-taxonomy matrix and the repair gaps

1. **Missing/corrupt `META-INF/container.xml`** (any producer):
   epubcheck fatal; the OPF is unfindable. The locator falls back to
   the first `.opf` but never writes the file. Deterministic fix:
   generate the standard container.xml at the located OPF (upstream
   calibre's `initialize_container` is the model). Effort S; HIGH
   value — a gateway repair that unlocks an entire class of books.
2. **Stale font-obfuscation state** (publisher EPUB3 fonts; IDPF
   `2008/embedding` and Adobe `pdf/enc#RC`): post-KindleUnpack/strip
   tools decrypt fonts in place but leave `encryption.xml` declaring
   obfuscation; epubcheck errors and the audit implies DRM where there
   is none. Fix: detect via font magic bytes at offset 0 vs the
   CipherReference; prune stale entries; drop encryption.xml when only
   obfuscation entries remain and the fonts verify. Never
   un-obfuscate. Also gives the audit a real OBFUSCATED-vs-DRM
   distinction. Effort M; needs the magic-byte detection validated on
   real samples first.
3. **Wrong/missing manifest `media-type`** (aggregators, ADE): fonts,
   images, and media declared with wrong types. Fix: sniff magic bytes
   or extension map; attribute-only. Effort S.
4. **href case/backslash/IRI mismatches** (KindleUnpack, Windows
   producers): `Images/Cover.JPG` vs `images/cover.jpg`, backslash
   separators, `%XX` variants — the file IS present, so
   `--prune-missing-resources` cannot touch the RSC-007/PKG-010 it
   causes. Fix: case-insensitive namemap when exactly one match;
   backslash to slash. Effort M.
5. **Dangling reference edges after pruning** (any producer):
   `spine@toc`, `item@media-overlay`, `item@fallback`, and EPUB2 cover
   meta pointing at items the prune pass deleted. Today's prune checks
   only `spine_ids` — it can manufacture the regression the gate then
   rejects. Fix: extend prune to rewrite those edges (the rewrite
   machinery exists in fix_manifest_ids). Effort S/M.
6. **Duplicate zip entries** (any producer): last-wins shadowing; the
   audit counts `dup_entries`, the rewrite copies both. Fix: drop
   shadowed duplicates on rewrite. Effort S.
7. **Cover wiring** (EPUB2 producers): dangling `<meta name="cover">`,
   missing EPUB3 `properties="cover-image"`, wrong cover media-type —
   calibre and booksellers extract no cover. Narrow, finding-driven
   repair when the cover is identifiable from the existing guide/meta;
   audit-only otherwise. Effort M.
8. **Broken CSS from converters**: `url()` to absent assets, malformed
   selectors. Repair is fiddly; ship the audit-only detector first and
   let prevalence data decide. Effort M (repair).
9. **Human-facing metadata validity** (self-pub/Word): non-ISO
   `dc:date`, invalid `dc:language`, missing EPUB3
   `dcterms:modified`. Explicit non-goal today; `dcterms:modified`
   also collides with byte-determinism. Needs a written charter
   ruling: carve-out with a determinism-safe timestamp policy, or
   route to cquarry/CalibreQuarry. Effort M + a decision.
10. **EPUB3 dual-ToC drift** (nav vs NCX): detection is a
    deterministic structural diff feeding `decisions_needed`; full
    sync is ToC synthesis and stays out of charter permanently.
    Effort L if repair is ever attempted; audit-only recommended.
11. **No-repair classes, correctly routed**: OCR garbage, truncated
    re-sources, absent font families (audit + re-source); real DRM
    (clean ENCRYPTED skip — shipped); DB-level format rows
    (cquarry's, done); catalog completeness and human-facing metadata
    quality (CalibreQuarry's audits).

## B. Research-before-boxing

1. **FastSweep prevalence study** — run `fast_sweep.py --mode=extract`
   aggregation over the full library targeting the new classes
   (container.xml presence/validity, encryption.xml algorithms,
   media-type warnings, case-mismatch RSC-007s). The harness exists;
   it sizes every box above with real counts before any is built.
2. **The metadata carve-out ruling** (gap 9) — Brandon decides;
   the audit records both options.
3. **The obfuscation magic-byte table** (gap 2) — validate IDPF and
   Adobe font prefixes against real samples before the repair ships.

## C. Routing notes

Package-structure repairs (items 1-6) fit the existing "structural
opt-ins + gate" model without reopening the semantics-preserving
contract. Items 7-10 each need an explicit scope decision first.
Cross-repo: DB-level size/format-row mismatches route to cquarry
(set_format, shipped); catalog completeness, duplicate detection, and
human-facing metadata quality route to CalibreQuarry's audits; OCR,
truncation, monolithic, and missing-font content stay audit-and-
re-source permanently.

## D. Phase 16 routing

Items 1-6 (the ungated package-structure repairs) are boxed in
roadmap.md **Phase 16** with the FastSweep prevalence study leading.
Items 7-10 are recorded as decision-gated boxes. Phase 15 (approved)
runs first if its fixture work is not yet done.
