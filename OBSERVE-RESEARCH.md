# Observe research: the error-code surgery (2026-10-24)

Working research file behind bindery observe's first library census
(`~/docs/Calibre Library.backups/observe/observe-2026-10-24-base.observe`).
Question: what exactly is triggering the library's four dominant epubcheck
code classes, and what exactly makes the gate reject the 313 repairs it
refused on the whole-library dry run? Specimen books live in
`testing_facility/error-surgery-2026-10-24/` (gitignored; never modified
copies of library books). This file is the living record; findings land here
as the investigation proceeds.

## The oracle's own definitions (epubcheck 5.3.0 MessageBundle.properties)

| Code | epubcheck's message | Library mass |
|---|---|---|
| RSC-005 | `Error while parsing file: %1$s` (the generic XML parse-error class; a zoo of distinct underlying defects) | 17,105 occurrences in 3,323 books |
| RSC-007 | `Referenced resource "%1$s" could not be found in the EPUB.` | 14,341 in 692 books (~20.7/book) |
| RSC-026 | `URL "%1$s" leaks outside the container (it is not a valid-relative-ocf-URL-with-fragment string)` | 12,607 in only 18 books (~700/book) |
| RSC-020 | `"%1$s" is not a valid URL (%2$s)` | 10,102 in 353 books |
| PKG-010 | `The file name "%1$s" contains spaces, which may create interoperability issues with older reading systems.` | 8,294 in 287 books (~29/book) |

Siblings in the top ten: RSC-001 (`File "%1$s" could not be found`, 1,546 in
39 books), HTM-004 (irregular DOCTYPE, in the Wandering Inn signature),
RSC-008 (referenced resource not declared in the OPF manifest), CSS-007,
RSC-004 (encrypted files), OPF-029/030.

## The structural fact that frames everything

The whole-library preview ran the DEFAULT flag set (the five always-on
well-formedness fixes plus the NCX pipeline and mimetype repair). Every fix
that addresses the four classes above is opt-in:

- RSC-007 -> `--prune-missing-resources` (drops references to absent files)
- PKG-010 -> `--encode-url-spaces` (renames the entries and rewrites every
  reference: OPF, NCX, content, CSS url())
- RSC-005 families -> spread across `--fix-ids`, `--strip-bad-attrs`,
  `--strip-epub3-attrs`, `--fix-misnested-inline`, `--fix-id-colons`,
  `--fix-unterminated-attr`, `--reserialize`, and friends
- RSC-026 / RSC-020 -> no dedicated repair today (candidate analysis below)

So the 3,156 `nochange` books are mostly books whose defects belong to
opt-in classes the default pass never attempts. That is by design, not a
failure. The 313 `reject`s are the interesting anomaly: there the DEFAULT
fixes fired and the gate refused because the error count went UP.

## The 313 rejects: first shape

From the 2026-10-24 dry run (`bindery-dryrun-2026-10-24.json`):

- fix mix: `fix_ncx_playorder`-only 199, `strip_prolog_junk`-only 73,
  `fix_named_entities`+`strip_prolog_junk` 29, mixes 12
- delta shapes: overwhelmingly +1 or +2 errors ((2,4) x50, (1,2) x42,
  (2,3) x35, (5,6) x29, (4,5) x25, (3,4) x19)

Working hypothesis (to be proven or killed by the message-diff surgery):
the repair un-masks latent errors. epubcheck's parser stops counting at a
malformed construct; when the default pass removes junk before the XML
prolog, normalizes entities, or renumbers the NCX, validation reaches
deeper into the file and surfaces errors that were always there. The gate
sees a count increase on a no-fatal book and refuses, which is correct
behavior for a `strict error decrease` bar, but the books' TRUE defect set
is unchanged. The question the message diffs answer: does the delta
consist of NEW message families (un-masking) or more of the same message
(the fix genuinely breaking something)?

## Surgery log

### RSC-026: SOLVED. The `/root/work/img` equation scar (wave 1, agent B)

Specimen: the three Feynman Lectures volumes (facility copies,
`testing_facility/error-surgery-2026-10-24/rsc026/`). On vol3, **100% of
RSC-026 occurrences (10,044) are `<img src="/root/work/img/<32-hex>.png">`**
tags sitting mid-sentence where inline equations render. Zero absolute
http(s) used internally, zero `file://`, zero `../` escapes, zero
percent-encoding.

Mechanism: an upstream conversion pipeline ran as root with a scratch
workdir `/root/work`, rendered every equation and figure to a PNG named by
its md5, and emitted root-absolute `src` references. On the build machine
those paths resolved, so the conversion "succeeded" and the HTML was zipped
without any packager ever rewriting or embedding the images (Calibre embeds
only images it can find relative to a source tree it knows about;
root-absolute refs into its own temp dir are invisible once that dir is
gone). `class="calibre10"` fingerprints the Calibre pass-through. The
3,499 distinct URLs are uniformly-random md5s (3,414 distinct 4-hex
prefixes), i.e. content hashes, not sequence numbers; every one of the 21
content docs is affected (top: 902, 801, 791 occurrences per file).

Two compounding facts:

1. **RSC-026 and RSC-007 are 1:1 twins for the same defect** on this book:
   the URL is invalid (leaks the container) AND the resource is missing.
   On vol3: RSC-026 10,044 text occurrences / RSC-007 10,106, of which
   10,044 are the same equation images. Together they are 99.2% of vol3's
   error mass. The remaining 62 RSC-007s are a separate small habit:
   hand-written cross-volume chapter links (`I_37.html`, `II_34.html`) that
   assume the other volumes' files live in the same package.
2. **No repair in bindery's inventory should touch this.**
   `--prune-missing-resources` would delete the references, which means
   deleting every equation in a physics text: content destruction, not
   repair. These books need re-sourcing (the census says so; the flag
   inventory has nothing honest to offer). The 18-book RSC-026 population
   (12,607 aggregated messages) is almost certainly all the same producer
   fingerprint (root-absolute md5 workdir images); confirm from the books
   array when the v0.53.1 baseline lands.

#### RSC-026 population revised (wave 2): the 18 carriers are NOT one producer

The fingerprint check across 6 of the 15 non-Feynman carriers (Dawn of
Everything, Embracing Defeat, Feet of Clay, all three Gotrek & Felix
omnibuses; specimens in the facility) kills the one-fingerprint
generalization: zero `/root/work` literals anywhere outside Feynman.
RSC-026 is epubcheck's generic "URL escapes the container" code, so it
aggregates unrelated defects:

- **Pipeline A (Feynman x3)**: the root-absolute md5 equation images. This
  is the entire mass (12,607 aggregated messages library-wide is
  essentially all Feynman).
- **Pipeline B (Calibre flat EPUB2 repacks)**: HTML flattened to zip root
  while refs kept pre-flatten `../` prefixes. Three sub-shapes: cover img
  `../Images/0000NN.jpg` (G&F family, byte-identical template, plus a case
  mismatch: the dir is lowercase `images/`), content img `../images/...`
  (Dawn of Everything), nav anchor `../Text/...#fileposNNN` (Feet of Clay).
  Tiny counts (1-9 per book). Notably REPAIRABLE in principle: the G&F and
  Dawn images exist in the archive under their flat names, so a re-point
  (basename exists elsewhere in the archive) would fix them; that is a
  candidate repair class, not an existing flag.
- **Pipeline C (Embracing Defeat)**: 9 garbage hrefs `../../../../l%20` —
  broken source-conversion cross-references; strip-broken-anchors class.

Also surfaced: Dawn of Everything carries ~13,490 RSC-005 text
occurrences, another heavyweight outside agent A's sample; the stratified
resample matters.

### MEASUREMENT FINDING: the census counts aggregated messages, not text-summary occurrences

Direct run on vol3: the epubcheck **text summary** says `20,307 errors`;
the epubcheck **`--json`** (and identically `CheckingReport.generate()`,
which the census harness and the gate both read) says **`nError: 7030`**.
Both are real; they are different units:

- JSON: one **message entry** per distinct defect (ID + message text),
  carrying a `locations` array with every occurrence site (plus
  `additionalLocations`). vol3: RSC-007 = 3,528 entries, RSC-026 = 3,499
  entries (exactly the 3,499 distinct equation URLs), RSC-005 = 3 entries
  (the `valign` family), total 7,030.
- Text summary: sums the **locations** (20,307 = every site individually).

Consequences:

1. The gate and the whole observe stack are internally consistent: before,
   after, the dry run, the preview, and the census all read
   `checker.nError`, the same aggregated scale. No accept/reject verdict
   changes.
2. The `.observe` artifact's `codes.occurrences` field is **mislabeled**:
   it counts aggregated message entries (distinct defect texts), not
   occurrences. Comparing it to a manual `epubcheck` text run will
   understate by the aggregation factor (vol3: 7,030 vs 20,307, 2.9x).
   The field should be renamed (`codes.messages`) and/or paired with a
   true occurrences count (sum of `locations` lengths), at the next schema
   revision. Until then: every number in this report and in the census
   that came from the JSON path is in aggregated-message units.
3. The library-wide census figures (RSC-005 17,105 etc.) are therefore
   distinct-defect-text counts. The true text-scale mass is higher
   (vol3 alone contributes ~13k more locations than entries).

### PKG-010: SOLVED. The mobi-rip split-chunk naming convention (wave 1, agent D)

400-book sample (seed 42): **17 of 400 books (4.25%) carry space-bearing
zip entries**, projecting ~300 library-wide against the census's 287 (the
census value sits inside the binomial band). 872 space entries in the
sample, median 20 per book, one 591-entry outlier (The Red and the Black).
Extensions: .html 641 / .htm 223 / .xhtml 6, i.e. almost entirely split
content documents.

The decisive measurement: **the OPF hrefs match the zip names exactly**.
In all 10 books checked, every space-bearing href resolves to an existing
entry (Stars My Destination 20/20/20, Red and the Black 591/591/591).
Nothing is broken internally; the producing tool wrote consistent names.
Verbatim names fingerprint the pipeline: `Alfred Bester - Tiger Tiger!
(v1.2)_split_018.htm`, `Salvatore, RA - Cleric Quintet 1 -
Canticle_split_012.htm`, `Steven Brust - Vlad Taltos 02 - Yendi v4.0
(BD)_split_000.htm` — a mobi/azw3-to-epub rip pipeline that chunks
oversized source HTML into `<Author> - <Title>_split_NNN.htm` named after
the book's own metadata, plus a smaller Sigil-style subfamily
(`OEBPS/Text/Half Title.xhtml`).

Disposition: `--encode-url-spaces` already exists for exactly this (entry
renames to underscore spellings, every reference rewritten, ambiguous
renames refused, accepted under no_worse). A structural-flag pass over the
~287-book population is the honest fix; the spaces are convention, not
loss.

### RSC-007: TWO populations, both legacy-EPUB2 scars (wave 1, agent D)

300-book sample: the xpgt hypothesis (H1) is **real but not the whole
story**.

1. **The xpgt cluster**: 45 of 300 books reference `xpgt` (~46 refs per
   referencing book), but 39 of them CARRY the template; only 6 of 300
   reference it without carrying it, projecting ~141 of the 692 RSC-007
   books. High mass though: 266 of 450 unresolved-reference occurrences in
   the sample (59%) are xpgt (a handful of books at ~40-53 dangling refs
   each, e.g. Global Crisis: 53, all `text/partNNNN.html ->
   page-template.xpgt`). The reference is one shape everywhere:
   `<link href="../Misc/page-template.xpgt" rel="stylesheet"
   type="application/vnd.adobe-page-template+xml"/>` repeated in every
   content doc, with converter-layout variants of the target path
   (`Misc/`, `../styles/`, `ops/styles/`, `ArrowofGod/styles/`,
   `page_template.xpgt`). This is the Adobe-era page template that a
   converter dropped from the archive while leaving the per-document
   links: **content-neutral loss** (the file never existed in the
   archive, so no styling was ever applied), and `--prune-missing-resources`
   is the honest repair for it: dropping dead `<link>` tags strictly
   decreases the error count, normal gate.
2. **The diffuse loss tail**: 50 of 300 books have at least one unresolved
   internal reference (ceiling projection ~1,175 books, ~10.5k
   occurrences; the counter did not skip `kindle:` URIs or fragment junk,
   so treat as an upper bound). Median 2 per book: stripped image
   sequences (`00012.jpg` x36 in one book), Calibre's own generated
   stylesheets gone missing (`page_styles.css` x12, `stylesheet.css` x12),
   missing covers (`cover.xhtml` x10), stray images/fonts, broken NCX
   targets, malformed hrefs (concatenation artifacts like
   `..._cop_r1.htmlop`). Case-by-case, not one flag.

Both populations are ~90-98% `package version="2.0"`: the damage was baked
in by the legacy conversion chain, and the modern EPUB3 portion of the
library is structurally clean. Control: How Music Works (9691) belongs to
neither class (zero space names, zero xpgt, zero missing hrefs), so the
scars are pipeline-specific, not library-wide.

Projected reconciliation of the census's 692 RSC-007 books: ~141 xpgt
books (high per-book mass) + the diffuse tail reproduces the total within
sampling error.

### RSC-005: a narrow zoo behind a huge count (wave 1, agent A)

20-book sample, epubcheck text output, reason texts normalized into
families. Total 129,312 RSC-005 **text occurrences** in 16 of 20 books, but
the sample is extremely skewed: four books carry 99.2% (Modern Japan
76,859, Against the Gods 31,068, Rest Is Noise 12,658, Theory of Poker
7,931). Sixteen of the 20 books are clean or near-clean on this code
(median ~28 per book). In aggregated-message terms (the census scale) this
sample's dominant families are exactly the ones that produce one entry per
distinct defect text.

| family | text occ | books | flag judgment |
|---|---|---|---|
| Duplicate id, 99.97% inside .svg files (`Duplicate "glNNNN"` gradient/filter ids, thousands per file) | 115,861 | 4 | `--fix-svg-dup-ids` (high confidence; built for this) |
| element not allowed here (content model; 97% is `span` directly in body under XHTML 1.1; also br/a/div/p/tbody; 2 OPF oddities: ns0:timestamp, ns1:title_sort) | 9,210 | 7 | `--reserialize` (medium; html5lib reparse makes body-level inline legal in HTML5). The OPF oddities: no flag |
| element incomplete (blockquote lacking a block child under XHTML 1.1) | 3,639 | 5 | `--reserialize` (same reasoning) |
| text not allowed here (bare text as body child) | 256 | 4 | `--reserialize` (medium-high) |
| id values with colons (`X:Y`) | 227 | 3 | `--fix-id-colons` (high; message names it verbatim) |
| attribute not allowed (deprecated: clear 51, charset 25, link 10, vlink 10, valign 5, border 1; OPF page-map 2; EPUB3 `properties` on an EPUB2 package 1) | 106 | 7 | `--strip-bad-attrs` (content docs), `--strip-epub3-attrs` (the properties hit) |
| invalid lang value | 5 | 1 | no honest flag (rewrite, not strip) |
| playOrder conflict (same target, two values) | 5 | 2 | no dedicated flag |
| pageList missing class; OPF missing nav property | 3 | 2 | no dedicated flag |

Takeaways: the RSC-005 mass is **duplicate SVG gradient ids in a handful
of conversion-heavy books**, and `--fix-svg-dup-ids` is the existing,
purpose-built repair. The XHTML 1.1 content-model families (span-in-body,
incomplete blockquote, bare body text) are the legacy-doctype shape that
`--reserialize` neutralizes by re-parsing as HTML5. Agent A's caveat: a
single heavily-SVG history book produced 60% of the sample volume, so the
library-wide family distribution is likely flatter; a stratified re-sample
off the v0.53.1 artifact's books array is queued as wave 2.

#### RSC-005 resample (wave 2): the typical book is epub3-semantics-on-legacy-doctype, not SVG

The 30-book stratified resample (Random(7), census pool minus the known
heavyweights) overturns the first sample's model:

- **The dominant family is EPUB3 semantics rejected by legacy DTDs**:
  "attribute not allowed" = 10,403 of 12,364 resample occurrences across 18
  books, led by `role` (6,645), `aria-label` (1,737), `epub:type` (1,453),
  `aria-hidden` (239). One converter habit: modern semantic attributes on
  HTML4/XHTML1 doctypes.
- The first sample's SVG duplicate-id family is **entirely absent** here
  (0 of 12,364): duplicate ids are a per-book quirk of a handful of
  SVG-annotated titles, contributing essentially nothing to the other
  ~3,316 RSC-005 books.
- Mass concentration again, different books: five long-prose books (Some
  Desperate Glory, House of Sky and Breath, Storming Heaven, The City and
  Its Uncertain Walls, The Tiger) carry 91.5% of resample occurrences;
  median 44; 13 of 30 books under 15. Zero of 30 books read clean, so the
  census's RSC-005 book set is real.
- New metadata-level families the first sample missed: `@refines` missing
  target id, invalid epub3 role value, href-not-a-URI,
  Property-not-refining-creator, navList/pageList missing required
  attribute. No flags.

**Flag gap (verified in source)**: `--strip-epub3-attrs`' fixed set is
exactly three attributes (`page-progression-direction`, `epub:type`,
`aria-label`). It covers aria-label and epub:type of the dominant family
but **not `role` or `aria-hidden`**, and its gate keys on the OPF package
version while epubcheck's rejection here is DOCTYPE-driven (an EPUB3
package carrying XHTML 1.1 doctypes would show the same findings yet be
skipped by the version gate). Extending the set and re-examining the gate
condition is the fix candidate for the family.

**Census-undercount confirmed systematically**: the artifact's per-book
error counts are unique-message-text counts; verified exactly on all 30
books, with undercount up to ~600x (one book: 5,946 occurrences collapse
to 10 census entries). Any repair prioritization must re-measure
occurrences directly rather than sort by the census field; the schema
revision (rename + a true occurrences count) is now load-bearing, not
cosmetic.

### THE 313 REJECTS: SOLVED. Two transform bugs, un-masking killed (wave 1, agent C)

The 12-book message-diff surgery (before/after epubcheck on repairs
regenerated through the same code path the gate measures) kills the
un-masking hypothesis outright: **zero books had a masking construct** (no
prolog errors, no fatals, no prior playOrder findings), and every +delta is
a violation the fix itself wrote into previously clean bytes, verified at
byte level at identical coordinates. The gate's refusals were all correct:
313 saves, not 313 misses.

**Bug 1: `fix_ncx_playorder` breaks contract-valid NCXs (199 of the 313
rejects).** The transform re-sequences `playOrder` across `<navPoint>` start
tags to document-order 1..N unconditionally. epubcheck's actual playOrder
contract: navPoints whose `<content src>` point at the same target must
SHARE one playOrder value; distinct targets need distinct values; no gaps,
and `pageTarget`/`navTarget` elements participate in the same sequence.
Real TOCs satisfy this with duplicate-target sharing (steel-breeze: two
navPoints at the same split_000.html, both `playOrder="1"`, zero findings
before, renumbered to 1/2 with a new finding at exactly those lines) and
with pageTargets interleaved (chronomancy: navPoints rewritten to 1,2,3
while the untouched `pageTarget ... playOrder="442"` at line 3034 now reads
as a sequence gap; mushroom-blues: 440 pageTargets, 0 -> 56 inconsistent
targets after the rewrite). Fix-mix evidence: 199 reject records carry
`fix_ncx_playorder` alone.

**Bug 2: `strip_prolog_junk` amputates the trailing `*/` of Calibre's CDATA
style wrappers (~110 of the 313).** `_PROTECTED_RE` matches
`<![CDATA[.*?]]>` non-greedily, so inside `/*<![CDATA[*/ ... /*]]>*/` the
protected span ends at the first `]]>` (inside the closing `/*]]>*/`),
leaving the trailing `*/` as the next outside segment. strip_prolog_junk
amputates everything before that segment's first `<`, eating `*/` plus
whitespace before `</style>` (the whitespace-only guard cannot catch `*`),
truncating the CSS comment, and epubcheck reports one CSS-008 "Premature
end of file" per broken file. Every fired instance in the sample is a
PHANTOM fix on a file with no prolog junk at all: fired counts equal
wrapper-file counts exactly (dark-star 18/18, sisyphus 6/6, calcutta 22/22,
moons-jupiter 21/21). At gate scale (aggregated entries) each book reads
+1.

**fix_named_entities: no breakage observed** (fired 3 times, all in one
book alongside the two buggy fixes; its mechanism cannot produce either
failure class; the same book's NCX-001 uid mismatch was genuinely repaired
by the uid sync, the one message-set loss in 12 books).

Consequences and scale note: both bugs sit in the DEFAULT pass (the NCX
pipeline is always-on; strip_prolog_junk is one of the five core
well-formedness fixes), so every library run attempts them; the gate is the
only thing that stood between the 199 books and a rewritten-but-worse NCX.
The aggregated-count scale also explains the tiny deltas: one bug hitting
22 files reads as exactly +1. Evidence preserved in
`testing_facility/error-surgery-2026-10-24/rejects/` (before/after epubcheck
outputs, probe records, and five specimen books); an independent
claim-verification pass is in flight before any code change is proposed.

Candidate repairs (to be scoped after verification): fix_ncx_playorder
should only fire when the NCX actually violates the playOrder contract, and
its renumbering must share values across same-target navPoints and include
pageTarget/navTarget in the sequence; strip_prolog_junk needs the
protection span to cover the wrapper closer (`/*]]>*/` as a unit) or an
orphan-`*/` guard before the whitespace test.

#### Independent verification: 4/4 claims confirmed, zero refuted

The claim-verifier re-proved both bugs from scratch with minimal synthetic
reproducers and re-derived every count from the dry-run JSON:

- **Claim 1 CONFIRMED**: a minimal EPUB whose NCX shares `playOrder="1"`
  across two navPoints on the same target validates clean; the transform
  in isolation fires 2 (a phantom fix on a valid file), splits the shared
  value to 1/2/3, and the repacked book fails with the exact claimed
  finding, twice. Exactly 199 reject records carry
  `{fix_ncx_playorder}` alone (deltas +1 x121, +2 x60, +3 x18); two of the
  313 rejects were 0f/0e fully clean books damaged by phantom fixes.
- **Claim 2 CONFIRMED**: isolation on a Calibre-wrapped inline style shows
  the removed bytes are exactly `*/` (the style close becomes
  `/*]]></style>`), the fired count is 1 on a file with no prolog junk,
  and the repacked book fails with CSS-008 "Premature end of file".
  111 reject records contain strip_prolog_junk (73 solo + 29 with
  fix_named_entities + 7 + 2 mixed).
- **Claim 3 CONFIRMED**: a 3-stylesheet demo shows 3 text occurrences
  collapsing to 1 JSON entry; vol3 reproduces 20,307 text vs 7,030 JSON
  exactly; the gate code reads `checker.nError` (validate.py).
- **Claim 4 CONFIRMED and strengthened**: fix_named_entities fired SOLO in
  93 records with zero rejections (14 accept at -1, 78 equal, 1 partial):
  when alone it repairs or is neutral, never breaks. All 34 mixed records
  are attributable to their companions.
- Verifier's aside (a third sequencing blind spot): a navPoint with no
  playOrder attribute neither consumes nor receives a sequence number.
- Bookkeeping note: the facility's rejects/ dir holds 6 of the 12 sampled
  books (the /tmp forensics set holds all 12); widen the facility set
  before any fix session that wants the full spread.

#### The benefit ratio that decides the fix design

Across the entire dry run, fix_ncx_playorder fired on 579 books:

| outcome | books |
|---|---|
| equal (fired, zero count change: a no-op rewrite) | 347 (60%) |
| reject with positive delta (actively made worse) | 211 (36%) |
| accept with negative delta (the legitimate use) | **17 (3%)** |
| partial / error | 4 |

The transform helps 3% of the books it fires on and harms 36%. The
contract-gating repair (fire only when the NCX actually violates the
playOrder contract) turns the 347 no-ops and 211 harms into no-ops and
preserves the 17 real fixes.

### Disposition probes: all three proposed flags CONFIRMED live (wave 2)

Each disposition was probed on a real library book (specimens in the
facility; gate records and before/after epubcheck transcripts preserved):

| class | book | flag | target delta | gate |
|---|---|---|---|---|
| RSC-005 svg dup ids | Making of Modern Japan | `--fix-svg-dup-ids` | 76,859 occurrences -> 0; **fully clean epubcheck** | accept, 104 svg entries touched, 71,711 ids fixed |
| RSC-007 xpgt dead links | Global Crisis | `--prune-missing-resources` | 53 RSC-007 -> 0; sole residual is an out-of-class RSC-020 mailto-space error present in the baseline | accept, dead_links_pruned=53 |
| PKG-010 spaces | Stars My Destination | `--encode-url-spaces` | 20 PKG-010 -> 0 AND 21 collateral RSC-020 -> 0; **fully clean epubcheck** | accept, 20 renames + 21 refs rewritten, zero rename-caused misses |

Two cross-cutting notes: the encode-url-spaces probe cleared the book's
entire RSC-020 population too (those were spaces-in-URL findings, the same
defect on the URL axis), so the two classes overlap on that shape; and
bindery's internal before-count for Modern Japan read 788 (aggregated
entries) against epubcheck's 76,859 (text locations) — the aggregation
scale again, consistent end to end.

### RSC-020: SOLVED. The PKG-010 twin plus a junk-link tail (wave 2)

15-book weighted sample off the artifact's books array (5
fingerprint-positive + 10 arbitrary; 178 raw lines, 127 unique URLs):

| family | raw lines | verdict |
|---|---|---|
| 1. space-in-URL, internal relative href, target exists | 133 (75%) | invalid-but-harmless naming; **the PKG-010 twin** |
| 2. escaped-markup/garbage href (`<i>` swallowed into an href, Windows path) | 20 | genuinely broken (missing internal targets) |
| 3. space-in-URL, external http(s) (line-wrap artifacts baked into hrefs) | 12 | genuinely broken addresses even after encoding |
| 4. %20 spliced mid-host | 7 | genuinely broken; encoding cannot fix (epubcheck percent-decodes then rejects the space) |
| 5. mailto-with-space | 2 | genuinely broken (unsendable) |
| 6. literal space in host | 1 | genuinely broken; still rejected if encoded |
| 7. illegal bracket in relative filename, target exists | 2 | harmless naming (needs %5B%5D or a rename, never a space flag) |
| 8. malformed host (empty domain label) | 1 | genuinely broken garbage |

Population overlap: **287 of the 353 RSC-020 books (81.3%) carry the
PKG-010 space-entry fingerprint**, and in every fingerprint-positive book
the counts are in strict three-way identity (PKG-010 count = zip
space-entry count = flagged internal space-URL count): one root cause, two
epubcheck layers. The `--encode-url-spaces` disposition therefore covers
family 1 wholesale (confirmed live on Stars My Destination, where all 21
RSC-020s cleared as collateral) and makes families 3 and 5 *parse* as
valid URLs while their targets stay dead (cosmetic repair at best). The
genuinely-broken families 2/4/6/8 live in the 66 fingerprint-negative
books, are near-zero user impact (junk links, mostly external), and have
no honest flag; leave them measured, not "repaired".

Counting-semantics proof from this lane: the artifact aggregates unique
flagged URLs per book (The Chill: 34 artifact vs 66 raw lines, each URL
flagged in both content.opf and toc.ncx), further confirming the
unique-text scale of every census number.

### --reserialize: FALSIFIED for the content-model family, and the bug bites again (wave 2)

The Dark Star probe (the span-in-body exemplar) came back with two
structural findings:

1. **The gate rejected the probe, and the regression is the prolog bug
   again**: the fixes dict carries only `strip_prolog_junk: 18`, the
   refusal is `0f/7e -> 0f/8e` (one aggregated CSS-008 entry), and a
   control run without `--reserialize` produces a byte-identical reject
   record. The core pass never let the opt-in stage run. The 18 CSS-008s
   are exactly the class bindery's own `--fix-cdata-terminator` exists to
   repair: the core pass manufactures work for an opt-in.
2. **Even gate-free, reserialize is inert here**: its html5lib path
   triggers only on XML ParseError ("runs only on documents that are not
   already well-formed"), and the content-model family is
   well-formed-but-schema-invalid. The no-validate hypothetical ran the
   rebuild trigger zero times; all 273 RSC-005 content-model occurrences
   survived; text preservation confirmed only that the strip damage lost
   no prose.

The medium-confidence research claim is falsified for this exemplar: the
span-in-body / incomplete-blockquote / bare-body-text families have **no
working repair today**. Addressing them would mean a new opt-in that wraps
loose block content (or an explicit second reserialize trigger for
well-formed docs with content-model errors, contradicting its current
"good files are untouched" contract). Until then they stay measured, not
repaired.

## Closing recommendations (2026-10-24 surgery)

1. **Fix the two core-pass bugs first** (both in the always-on pass, both
   gate-proven, independently verified with synthetic reproducers):
   - `fix_ncx_playorder`: fire only when the NCX actually violates the
     playOrder contract; share values across same-target navPoints;
     include pageTarget/navTarget in the sequence; handle navPoints with
     no playOrder attribute (the verifier's third blind spot). Benefit
     ratio today: 17 helped / 211 harmed / 347 no-ops out of 579 firings.
   - `strip_prolog_junk`: treat the Calibre wrapper `/*<![CDATA[*/ ...
     /*]]>*/` as one protected unit (or guard the orphan `*/` before the
     whitespace test). Today it phantom-fires on files with no prolog junk
     and manufactures CSS-008 x N.
   Re-run the whole-library dry run after the fix: the 313 rejects should
   collapse to near-zero, and the 347/equal no-op rewrites should stop
   firing.
2. **Then the validated structural pass** (one reviewed apply run, same
   backup pipeline as stage 2/2b): `--fix-svg-dup-ids` +
   `--prune-missing-resources` + `--encode-url-spaces`. All three are
   gate-proven to fully clear their classes on real specimens; the run
   also clears RSC-020 family 1 as collateral. Ordering matters: the
   structural pass must wait for the bug fixes because a book carrying
   both the fake-CDATA idiom and a target defect would be refused
   wholesale by the gate (the buggy core fix poisons the bundle).
3. **Extend `--strip-epub3-attrs`** (`role`, `aria-hidden`) and re-examine
   its package-version gate against the doctype-driven rejection, before
   any pass aimed at the dominant RSC-005 family. Check the target
   population's package versions first.
4. **Schema revision for observe (v2)**: rename `codes.occurrences` ->
   `codes.messages` (aggregated entries), add a true occurrences count
   (locations), state the scale in the artifact and docs. Now load-bearing:
   prioritization off per-book counts can mis-rank by ~600x.
5. **No-repair list (leave measured)**: the Feynman equation scar
   (re-source), the XHTML 1.1 content-model families (no working repair;
   reserialize falsified), RSC-020 junk-link families 2/4/6/8, the
   diffuse RSC-007 tail (case-by-case), the metadata-level RSC-005
   families. These are the honest residue; the observatory tracks them.
6. **Candidate new flag (low priority)**: flat-repack re-point (the
   Gotrek & Felix cover-image class: the target exists under its flat
   name; a basename-elsewhere re-point would fix 1-9-occurrence escapes).

Evidence: specimens and probe records under
`testing_facility/error-surgery-2026-10-24/` (rsc026/ rsc005/ rejects/
pkg010/), census artifacts under `~/docs/Calibre Library.backups/observe/`,
the dry-run JSON under the library's `.claude/`. All measurement was
read-only; the library was never written during this research.

## The un-researched top-ten lanes (2026-10-25 session, issues #16/#17)

Each lane: a message-family census off the baseline artifact's books array, a
mechanism verdict from live epubcheck 5.3.0 runs and zip reads, and a
disposition. All measurement read-only; specimen gates ran on /tmp copies.

### RSC-001 and RSC-008: the prune's coverage boundary (issue #16)

epubcheck 5.3.0 wordings, from real runs: RSC-001 `File "<name>" could not
be found.` (package-level, no line); RSC-008 `Referenced resource "<name>"
is not declared in the OPF manifest.` (at the referencing element).

- **RSC-001 (39 books / 1,546 entries)**: every sampled finding is rooted in
  an OPF `<item href>`; the flag's outcome splits exactly on the spine
  guard. Non-spine absent-file items (stray images, unspined docs) are
  cleared by the existing prune (25/25 sampled long-tail findings). Spine
  documents with absent files survive by design, and they ARE the census:
  738+736 of the 1,546 entries (95.3%) are the two Wandering Inn books
  (plus the facility's Light at the End, 8/8 spine), where the missing
  "file" exists under a different spelling (raw spaces vs underscores) --
  `--encode-url-spaces` territory, not prune territory. The itemref-with-no-
  `<item>` shape contributed 0 findings in 14 runs. A second finding rides
  along: both Wandering Inn OPFs are prefix-namespaced (`<opf:item>`), a
  shape `_ITEM_TAG_RE` never matches, so the prune could not see them even
  without the spine guard; every finding there is spine-protected anyway.
  **Disposition: coverage boundary documented; no code change.**
- **RSC-008 (55 books / 545 entries)**: zero coverage before v0.55.0,
  structurally: the prune's predicate is target-absent, and an undeclared
  resource is by definition present. Sample tally (452 findings across 7
  books): 441 content `<img src>` to present-but-undeclared files, 8
  content `<link href>` stylesheets (the one-corpse Calibre split-CSS
  shape), 2 inline `<style>` `@font-face url()` fonts, 1 OPF guide
  `<reference>` to an undeclared cover doc. The most dangerous shape (a
  spine doc missing from the manifest) does not occur.
  **Disposition: new `--declare-missing-items` flag (v0.55.0)**, the
  inverse move: add the manifest item (sorted `bindery-dN` ids, blessed
  extension-map media types, no itemref ever written). Live gate on the
  facility specimen: 3 items declared (the three split CSS files), 16e ->
  13e, RSC-008 fully cleared, accept; the RSC-001 carrier control
  correctly fired nothing; second run declares nothing.

### RSC-012: already covered except the OPF guide (issue #17 lane)

Verbatim (5.3.0): `Fragment identifier is not defined.` (ERROR) -- not the
"wrong element" wording strip_broken_anchors_doc's docstring guessed at;
the docstring is corrected in code separately if it ever matters. Census:
188 books, exactly 1 aggregated message each. Full-lane live sweep (all 188
carriers re-probed): 1,210 locations -- 934 content-doc `<a href>` (Mobipocket
filepos anchors, URL-encoded junk fragments), 234 NCX `<content src>`, 42
OPF guide `<reference href>` in 33 books. Every location resolved to a
target document that exists and lacks the fragment -- exactly the
strip-broken-anchors precondition (a missing target file is a different
code). **Disposition: existing flag.** `--strip-broken-anchors` clears the
content + NCX halves in principle (96.5% of locations; 155 books fully);
the 42 OPF-guide locations survive (no repair touches `<guide><reference>`
except fix_cover_meta's cover meta, and the guide ref is furniture). Live
probe on the facility's cold-days specimen (its one finding IS the guide
shape): the anchor flag correctly fires nothing.

### OPF-029: one family, mostly one flag (issue #17 lane)

Verbatim: `The file "<path>" does not appear to match the media type
<declared>, as specified in the OPF file.` (ERROR). Census: 17 books, 280
entries, all classified by declared type vs magic bytes: 216 locations are
jpg-stamped-image/png with JPEG bytes at offset 0 (`--fix-media-types`'
exact documented case; 13 books led by Tress 75 and Sunlit Man 68); 64
survive -- 56 .gif-declared-image/gif with JPEG bytes (Gateway 53,
Revelation 3: the magic check refuses, and renaming to .jpg is a
content-name change out of scope), 5 .png-declared-png with JPEG bytes,
3 jpg-declared-jpeg with PNG bytes (the nevernight case: the declaration
is honest; the fix deliberately keeps it). **Disposition: existing flag
for the 216; the 64 are leave-measured.** Live gate on the Tress copy:
75e -> 0e (fully clean), accept.

### OPF-030: the unique-identifier wiring scar (issue #17 lane)

Verbatim: `The unique-identifier "<id>" was not found.` (ERROR,
package-level). Census: 27 books, 1 entry each, all fatals=0 except one.
Families: 23 dangling-ref -- the package names an id (`bookid`, `uuid_id`,
`uid`, ISBN shapes) but NO dc:identifier carries any id attribute at all
(a converter dropped the id attributes wholesale); 2 attr-empty
(`unique-identifier=""`, rendered by epubcheck as "null"); 2 multi-OPF
merge leftovers (Either/Or, Book of Debugging: the defect sits in a stale
non-rootfile OPF the merge left behind). None is the v0.51 ns0 embed-scar.
**Disposition: leave measured.** The 23 are deterministically fixable in
principle (add the named id to the first dc:identifier; values untouched,
gate-visible), but the mass is 27 books at 1 error each with zero reader
impact, and the honest recipe would need the empty-attr and stray-OPF
refusals designed around it. The recipe is recorded here for a future
pass; boxed, not silently declined.

### CSS-007: unblessed font media types (issue #17 lane)

Verbatim (INFO severity, not an error): `Font-face reference "<url>"
refers to non-standard font type "<declared>".` Census: 114 books, 509
entries, population reconciled exactly against unblessed font-ext manifest
items + data-URI srcs. Mechanism (bytecode-extracted whitelists): epubcheck
checks the OPF-declared media type of every @font-face src against a
version-specific blessed set (EPUB2: `font/*`, `application/font*`,
`application/x-font*`, `application/vnd.ms-opentype`; EPUB3: an exact
eight-entry list). Sampled families: `text/plain` fonts (Huxley 8,
Cialdini 12), `application/xfont+otf` (Gottman 38), `application/octet-stream` woff2 + correct-typed EOT (Nakov 32 -- the EOT half is
unfixable at the manifest layer, its type is CORRECT), the
`application/application/x-font-ttf` typo (4), `.dat` fonts, `.ttc`
collections, `data:font/truetype` base64 srcs. bindery's
`--fix-media-types` is image-only by charter and touches 0/114 carriers.
**Disposition: candidate extension (a font half with version-aware
blessed targets and font magic bytes: TTF `00 00 01 00`/`true`, OTTO,
wOFF, wOF2, ttc), left measured this batch**: INFO mass, gate-invisible
(nWarning unaffected), version-dependent target types, and a quarter of
the family unfixable at the manifest layer. Boxed, revisitable.

### RSC-004: font obfuscation, not DRM (issue #17 lane)

Verbatim (INFO): `File "<path>" is encrypted, its content will not be
checked.` Census: 84 books, 427 entries == 427 EncryptedData blocks across
all 84 carriers (per-book counts match 84/84). Algorithms: 424 Adobe
`ns.adobe.com/pdf/enc#RC` + 3 IDPF `idpf.org/2008/embedding` (one book);
ZERO other algorithms -- no DRM anywhere in the lane. Encrypted entries
are fonts only (266 .otf, 137 .ttf, 23 .dat, 1 .ttc), all readable bytes
(obfuscation, not zip encryption): every sampled book is the audit
archive verdict's benign OBFUSCATED side, and `audit.py`'s
OBFUSCATION_ALGOS already covers both URIs. **Disposition: leave
measured**, cross-referenced: the repair inventory has nothing honest to
offer (de-obfuscating fonts to satisfy an INFO finding would break the
embedding rules the obfuscation exists for), and the observatory tracks
it.

### Lane addendum: what the runs changed in the code

Issue #11's gate question resolved by census: epubcheck's attribute
rejection is package-profile-driven, not DOCTYPE-driven (7 EPUB3 packages
carrying XHTML 1.1 doctypes: zero attribute findings; every role/
aria-hidden carrier is version="2.0"), so `package_version()` gating is
correct as built and only the set grew (`role`, `aria-hidden`).
