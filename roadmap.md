# bindery-cli roadmap

> Collapsed 2026-11-24, the first application here of the every-pass cleanup rule:
> shipped work lives in the ledger below, in `patchnotes.md`, and in the git tags.
> The full per-phase prose this replaced is one commit back in git history. "Open
> work" is the only living part of this file. New work starts there as a dated item,
> ships, and collapses back into the ledger in the same pass.

Phase numbers in the ledger are historical identifiers, not an order: the campaign
added phases non-monotonically, and two distinct efforts both carry "Phase 4".
Cross-repo notes cite them, so the ledger keeps the identifiers.

Standing rules: `spec.md` is the contract (deterministic, semantics-preserving,
epubcheck-gated repairs; an opt-in lossy lane; dry-run default; atomic replacement).
The plugin vendors the core and never gates (the epubcheck latency ruling).
`tests/test_version.py` pins the version carriers.

## Shipped

| Batch | Shipped | What it gave the tool |
|---|---|---|
| Phases 1, 2, 4 (lossy), 5 | v0.1.0-v0.10.0 | The repair engine and its gate, `repair` and `library`, atomic in-place replacement with backups, the long tail (`--reserialize`, `--strip-bad-attrs`, `--fix-ids`), JSON reports and `--sweep`, the first lossy strip, the first audit hardening |
| Supplementary audit schemas | v0.14.0 | `--fix-id-colons`, `--fix-empty-body`, `--fix-missing-title`, `--unwrap-block-in-inline`, `fix_ncx_playorder`, `--strip-invalid-value` |
| Phases 6, 7, 8, 9, and the cquarry half of 4 | v0.16.3-v0.23.0 | The code sweep, cquarry integration (format paths, the `--tag` write path, `audit --id`), the `monolithic` analyzer, `library --id`, spine-integrity and archive-integrity verdicts |
| Phases 10, 11, 12 | v0.22.0-v0.28.0 | `--fix-page-map`, the EPUB 3 scrubs on EPUB 2 books, native install-to-calibre, the top-error repairs |
| Phase 3 | v0.37.0 | The Bindery Repair Calibre plugin (its decided shape is in AGENTS.md) |
| Phases 13, 14, 15, 16 | v0.29.0-v0.39.0 | `audit --json`, the `run phase1` and `run phase3` slices, the 2026-09-08 hardening sweep, the completeness analyzer, the package-structure repairs (`--fix-container`, `--fix-media-types`, `--fix-cover`) |
| Waves 15 and the blitz | v0.40.0-v0.44.0 | The interpreter matrix and grammar pin, matcher hardening, the epubcheck daemon pool, `--strip-stub-docs`, `bindery doctor`, `repair --json`, the space-entry renames, the cover and tocdrift analyzers |
| Records and the intake wave | v0.45.0-v0.49.0 | The structured fix record, `RepairFlags`, `--fix-comment-double-hyphen`, five structural repairs from the intake wave, the closed-Calibre doors, `--help-json`, the probe form |
| Issue batches | v0.50.0-v0.57.1 | The contamination analyzer, `--fix-unbound-prefix`, the spine guard, `bindery observe`, the contract-gated playOrder, `--fix-duplicate-ids`, `--declare-missing-items`, `--fix-unique-identifier`, the masked-axis rule |
| The observatory lane | v0.58.0-v0.63.1 | The private-ZipInfo write fix and three more dry-run repairs, the dangling-NCX prune, the oracle budget's entry term, `--fix-double-colon-tags`, `--strip-presentational-attrs`, `--encode-ref-brackets`, the `shell` analyzer |
| The 1.0 milestone | v1.0.0, v0.64.0 | The stability contract in spec.md, and the shell repair `--prune-dead-spine` |
| 1.1 | v1.1.0, v1.1.1 | `--encode-ref-delims`; the fragment-sync and lookbehind audit batch |
| 1.2 | v1.2.0-v1.2.8 | `--strip-dead-font-faces`; the half-spine veto; the epubcheck 5.4 census fix; the cover analyzer's OPF-relative resolution; the pagination strip's prose-flow and internal-numbering guards; prune cascades that no longer manufacture rejected errors |

Open defects are tracked as GitHub issues (#39 to #42), each stated as a known
limitation in the documentation until it is fixed.

## Open work

- **The 2.0 program: the library updater** (planned 2026-11-20; the plan is the next
  section). Status: 2.0-A is partly done. epubcheck 5.4.0 is the default oracle and
  its baseline re-run is complete. Remaining in 2.0-A: the version census as a
  first-class audit analyzer, and the annotations presence scan.
- **NCX targets in the undeclared-resource census** (found 2026-11-20 from the 5.4
  observe sampling). The RSC-008 remainder is 20 books and 133 messages. The shape
  is content documents the NCX references and the OPF never declared (Courtship
  Rite: `split_001` in the NCX src, absent from the manifest, present in the
  archive). `collect_undeclared_resources` scans content img and link targets,
  stylesheet and inline `url()`, and OPF guide refs; it does not scan NCX
  `<content src>` targets, so `--declare-missing-items` never sees this class.
  Extending the census with them (same blessed media types, same no-itemref rule)
  closes it.
- **Dangling-itemref pairing for OPF-049** (found 2026-11-20): 11 books and 148
  messages of spine `<itemref idref>` pointing at manifest ids that do not exist
  (Cursor's Fury: a numbered `_v2.0NN` id series whose tail is missing). The
  declarer deliberately leaves an itemref with no manifest item unrepaired, because
  which file it meant is underdetermined. That refusal can be refined: the spine
  position is already committed by the itemref, so when the undeclared-file census
  finds files whose count or name pattern provably pairs with the dangling idrefs,
  declaring the item under the referenced id answers exactly what the itemref meant.
  Spine position never changes; the veto and the gate guard the outcome.
- **Dead-font magic predicate** (found 2026-11-20). The CSS-007 census splits 47
  books three ways. 28 are an apply gap: real fonts whose manifest declares
  `text/plain`, which `--fix-media-types` clears. 11 hit files that are not fonts at
  all (magic `FONT`, classic Mac OS font resources that no reader loads; the same
  rendering-neutrality argument as the dead-font drop applies). 6 mixed books are
  correctly refused by the every-url rule. The new work is a `--strip-dead-font-faces`
  extension: a block whose every live `url()` resolves to a file that fails the
  font-magic check (`00 01 00 00`, `OTTO`, `true`, `ttcf`, `wOFF`, `wOF2`) drops.
  The 2 remote-only books and the KaTeX `.eot` shape (1 book) stay measured.
- **Illegal URL characters in entry names** (the RSC-020 class beyond spaces).
  `--encode-url-spaces` renames space-bearing entries. `--encode-ref-brackets`
  encodes bracket references without renaming the entries. Measured 2026-11-20 under
  the 5.4 oracle: 44 books and 520 messages remain, dominated by entries whose names
  carry brackets but no spaces (the Eisenhorn Omnibus fan conversion), which never
  enter the space-driven rename map. Next slice: generalize the rename-map derivation
  from spaces to the illegal-character classes, reusing the bracket encode rule and
  the ambiguity refusals, with the decode-before-lookup rule verified per character
  class (commas first).
- **MobileRead listing for the Bindery Repair plugin** (the maintainer's manual
  step): the listing with name and identity, the release-attached zip, minimum
  Calibre 7.0, and the Linux platform note. Posting is outward-facing under his
  name.

Parked, each with the measurement that parked it (all from the 2026-09-12 prevalence
study over 5,228 books, refreshed 2026-09-17). Each reopens only if acquisition
changes the mix:

- **Obfuscation-aware `encryption.xml` repair**: prune stale obfuscation entries
  when font magic proves the fonts were decrypted in place, and drop
  `encryption.xml` when only obfuscation entries remain. Never un-obfuscate. Parked
  because 424 of 424 readable obfuscated fonts were genuinely scrambled and the
  library held zero stale entries. The audit half shipped as the OBFUSCATED
  verdict.
- **href case and backslash resolution** (a case-insensitive name map when exactly
  one entry matches; backslash to slash). Parked at exactly 1 case-mismatch href in
  1 book and 0 backslash hrefs.
- **Duplicate zip-entry dedupe on rewrite.** Parked at 0 books with duplicate
  entries.
- **CSS `url()` pruning** as a `--prune-missing-resources` extension. Closed until a
  named epubcheck finding demands it: the prevalence read found selector damage, not
  `url()` candidates.

## The 2.0 program: the library updater (planned 2026-11-20)

The idea: rebuild every EPUB in the library to a current, clean state, "clean first,
then convert, then clean again", with the house method (census first, then one
surgical gated flag per repeated defect class) and a manual remainder. Researched
2026-11-20 (an assay idea-mode grade plus a landscape report at
`~/docs/reports/bindery-cli-epub34/2026-11-20-epub-3.4-migration-landscape.md`, all
keyless sources, the two streams cross-confirming) and decided the same day. The
research reshaped the goal before any code:

- **There is no `version="3.4"` package.** EPUB 3.4 mandates `version="3.0"` verbatim
  ("each new 3.X specification is a continuation of the EPUB 3 format"); writing 3.4
  is a spec violation epubcheck rejects (verified locally on 5.3.0, and the
  version-string constants are identical in 5.4.0 source). The real migration is
  EPUB 2.0.1 or 1.x to EPUB 3. The target spec's MUST list is small: the version
  attribute at 3.0, exactly one `dcterms:modified` (UTC, `YYYY-MM-DDThh:mm:ssZ`),
  and a mandatory nav document with exactly one toc nav (`properties="nav"`).
- **3.4 invalidates nothing conforming in 3.3.** Every substantive change since 3.3
  moves features to outdated or deprecated classes, both still conforming; a
  3.3-conforming book already is a 3.4-conforming book. The 3.4 layer is advisory
  (epubcheck 5.4's OBS_001 usage class), not obligation.
- **The installed oracle gates the real migration today.** epubcheck 5.4.0 is the
  default oracle (5.3.0 kept as rollback, since 2026-11-20). It validates
  `version="3.0"` packages under the 3.3 rules and adds the 3.4 rules mode. The
  migration introduces no 3.4-only features. Any 5.x oracle rejects 1.x version
  strings outright, so the 10 OEBPS 1.2 books are ungateable until their packages
  upgrade.
- **Census (read-only, 2026-11-20, full library):** 7,387 EPUBs: 5,593 EPUB 2.0
  (including 30 prefixed `<opf:package>` spellings), 1,784 EPUB 3.0, 10 OEBPS 1.2,
  zero unreadable archives. Migration surface: about 5,600 full migrations plus 10
  special cases. The 3.0 shelf is already conformant.
- **KOReader (the household reader):** its engine classifies any package version
  whose first character is "3" as EPUB 3 and prefers the nav table of contents over
  the NCX when a nav exists. The NCX stays legal in 3.4 (legacy, outdated).
  Posture: a correct nav, NCX kept.
- **EPUB Annotations 1.0 is a Working Draft** (latest 2026-10-15) with no
  package-document wiring at all (embedded sets are `META-INF/annotations.json`) and
  no consumer or writer found anywhere. KOReader keeps highlights in sidecar
  `metadata.lua` files. Scaffolding today is bytes no reader reads.
- **Prior art:** DAISY Pipeline 2's EPUB Upgrader (XProc; the same transform
  inventory, validation off by default), ePub3-itizer (Sigil-bound), calibre's
  epub-to-epub (a lossy full re-serialization defaulting to EPUB 2, the source of
  the converter artifact classes bindery already repairs). Nothing found combines
  in-place operation, determinism, content conservation, and an epubcheck gate: the
  niche bindery would occupy is unoccupied.
- **Assay idea-mode verdict: not worth it as stated** (draft-spec timing, the
  annotations category error, charter collisions), with kill conditions. The
  decisions below answer them.

**Decisions recorded 2026-11-20:**

1. **Vehicle: native lane, pilot-first.** bindery grows the gated upgrade lane
   itself, under a deliberate charter amendment, with a 50 to 100 book pilot batch
   before any library wave. (Assay kill-condition 3 is answered up front: the
   charter amendment is recorded here, and the fidelity axis, 2.0-C, is specified
   before any converter code.)
2. **Annotations: parked** until Annotations 1.0 reaches Recommendation and a reader
   the household runs consumes it. Only the presence detector ships now.
3. **Groundwork first** (decision-independent; phase 2.0-A).

**Scope boundary (the parity ruling, reconciled).** "Conversion stays out,
permanently" declines the Calibre conversion engine lane (cross-format input by
orchestration; CalibreQuarry territory) and ebook-polish's content-improving actions.
The 2.0 lane is neither: it is EPUB-to-EPUB version migration by bindery's own
deterministic transforms, gated by the same oracle. The charter amendment lands with
the first flag:

- The upgrade lane is a first-class surface (an `upgrade` verb plus flags), additive
  to the 1.x contract (the stability contract explicitly allows new opt-in flags).
  "2.0" is the milestone where the updater is declared stable, as 1.0 was a contract
  statement rather than a feature claim.
- **Derived-navigation exception:** nav-from-NCX and landmarks-from-guide are a
  translation of navigation data the book already carries, never invention. The
  ToC-synthesis non-goal stands unchanged (tocdrift stays detector-only).
- Visible-text conservation remains absolute. The conversion gate (2.0-C) is the
  acceptance axis.

### 2.0-A Groundwork (decision-independent)

A sample-library observe under the 3.4 rules mode, to measure what actually fires
(OBS_001 outdated classes, deprecation reports) instead of assuming; the version
census promoted into a first-class audit analyzer; the annotations presence scan
(`META-INF/annotations.json`, payload sniff). Output: a counts report that finalizes
the flag inventory below.

Done 2026-11-20: epubcheck 5.4.0 installed as the default oracle (5.3.0 kept as
rollback), `update_epubcheck` in the update suite, and the baseline re-run
(`observe-2026-11-20-epubcheck540.observe`, against the 2026-11-09 artifact). OBS_001
never fired library-wide. The census-cap fix shipped as v1.2.3 from the same lane.
Remaining: the version-census analyzer and the annotations presence scan.

### 2.0-B The migration flags (EPUB 2.0.1 to EPUB 3)

Each flag gated like every repair, built on existing precedents (`--fix-package-version`
does the literal 1.0 to 2.0 bump today; `--fix-unique-identifier` wires the uid; the
NCX pipeline already synchronizes dtb:uid; `--fix-item-properties` computes EPUB 3
properties from a whole-book scan; `--fix-container` set the constant-epoch
determinism rule):

- `--upgrade-package`: version attribute to 3.0, exactly one `dcterms:modified`
  (constant-epoch, byte-idempotent), uid wiring reuse. Content documents untouched.
- `--nav-from-ncx`: the mandatory nav document, derived by translating the NCX
  navMap (the NCX tokenizer already exists), with `properties="nav"` wired through
  the properties scan. Refusals where the NCX is unusable land the book on the
  manual list.
- `--landmarks-from-guide`: derived from the guide's own references. The guide, the
  OPF 2 meta elements, and the NCX all stay (legacy-legal in 3.4); their removal is
  separate opt-in work, never bundled with the upgrade.
- Optional forward moves, each its own flag grown from the pilot census: cover meta
  to `properties="cover-image"`, `opf:` attribute translations, OEBPS 1.2-era
  media-type normalization (the `--fix-media-types` precedent).

NCX policy: keep. KOReader prefers the nav when present but resolves the spine's
toc target to the NCX, and EPUB 2-only tools elsewhere keep their data.

### 2.0-C The conversion gate (specified before the verb ships)

A third acceptance bar beside `gate` and `no_worse`. Accept iff: post-conversion
fatals equal zero (a still-fatal book lands partial, on the manual list); visible
text is conserved across the spine (the audit battery's own primitives); spine
resolution is preserved (both veto arms apply unchanged); and a re-run is
byte-idempotent. Errors and warnings may rise, because the new ruleset legitimately
introduces new check classes, so the conversion bar measures fatals, content, and
spine, never the error axis. The masked-axis reclassification applies (a noop on a
still-fatal book reads partial).

### 2.0-D The `upgrade` verb

Single-file plus library modes. Per book: clean (the default pass, its own gate),
then convert (the migration flags, the conversion gate), then clean again (the
default pass over the new profile's classes). One atomic write and one backup ring
entry. Dry-run default, `--install-to-calibre`, and additive per-book and per-stage
record vocabulary (CalibreQuarry's mirror floors move when it consumes them). The
plugin never runs upgrades (the latency ruling, unchanged). Acquisition-pipeline
wiring (the `run` verb slices) comes after the library wave.

### 2.0-E The pilot

50 to 100 books, stratified (clean EPUB 2, damaged EPUB 2, 3.0 controls): dry run,
census what the conversion broke or left, build the missing surgical flag, re-run.
No library wave until the pilot's accept rate is measured. THE WAVE ops template
(the 2026-11-03 lane: 1,170 books in one night, backups plus `metadata.db`) is the
rollout shape, and the learning loop is the wave cadence.

### 2.0-F The wave and the manual remainder

Batched rollout with backups and before-and-after observe. The remainder lands on
the manual list: the 10 OEBPS 1.2 books (any 5.x oracle rejects their version
strings outright, so they need the package upgrade before the pipeline can gate
them; a dedicated flag only if the pilot census shows a repeated shape), the
still-fatal conversions, and the shell books (detected, re-source, unchanged).

### Parked lanes

- **Annotations 1.0 scaffolding:** opens at Recommendation plus a consumer reader
  the household runs (nothing reads it today; KOReader uses sidecars). The
  presence detector ships with 2.0-A.
- **The 3.4 rules gating layer:** the default oracle is 5.4.0 and its baseline
  re-run showed OBS_001 never firing library-wide, so there is nothing to gate on
  yet. The migration produces 3.3-conformant packages that are already
  3.4-conforming.
- **mobi and azw3 input** (opened 2026-11-13, absorbed here 2026-11-20 as a
  possible x.0.0 candidate; no work scheduled by 2.0). The acquisition pipeline
  keeps meeting Kindle-container sources: the 2026-11-13 cookbook batch imported
  four azw3 files that had to go through ebook-convert by hand and then through
  bindery for the converter's own artifacts. The candidate shape is KF8 and PalmDOC
  container parsing, extraction of the HTML, OPF, and NCX into bindery's existing
  repair-and-gate pipeline, metadata passthrough, and the open question of font
  extraction (the sources carry TrueType-signature payloads; whether publisher
  fonts are recoverable from KF8, and whether they are obfuscated, needs a
  container parser before the answer is known). The dead-font-face repair that
  this need surfaced shipped independently as `--strip-dead-font-faces`. Entry
  conditions: a real batch need beyond the four-book proof, a KF8 parser survey,
  and the spec question of whether conversion is a bindery verb or a companion
  tool (bindery's charter is repair, not format conversion, which makes this the
  biggest scope question the project has faced).
