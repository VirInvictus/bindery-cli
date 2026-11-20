# bindery-cli roadmap

*Phase numbers are historical identifiers, not an order: the campaign added phases
non-monotonically and two distinct efforts both carry "Phase 4". Cross-repo notes
cite them, so the ledger keeps the identifiers.*

**Minimized 2026-09-29 (the cquarry precedent).** Phases 1-16, the supplementary
audit section, the bug-report waves, and every audit block are shipped; their full
text is preserved in git at the tree of commit 157afc9 (the last full roadmap) and
the release-by-release record is `patchnotes.md`. This file keeps the ledger, the
open work, the records that live nowhere else, and the parity dispositions.

Standing rules: `spec.md` is the contract (deterministic, semantics-preserving,
epubcheck-gated repairs; opt-in lossy lane; dry-run default; atomic replacement).
The plugin vendors the core and never gates (the epubcheck latency ruling).
`tests/test_version.py` pins the version carriers.

## Completed phases (ledger)

| Phase | Scope | Shipped |
|---|---|---|
| 1 | Deterministic repair engine: transforms, NCX-001, mimetype repair, the two-mode epubcheck gate, `repair`/`library` modes, atomic in-place replacement with backups, dry-run default | v0.1.0 |
| 2 | The long tail: unclosed non-void elements (`--reserialize`), unbound namespaces (`--strip-bad-attrs`), digit-led/colon ids (`--fix-ids`), JSON reports + `--manual-list`, `--sweep` re-audit integration | v0.2.0-v0.7.0 |
| 4 (lossy) | `--strip-pagination`: the fenced lossy lane's first member (confident interrupts, character conservation, `no_worse`) | v0.5.0 |
| 5 | Audit fixes and hardening: the partial-classification bug, per-book exception isolation, id-anchor preservation, html5lib as an extra, the epubcheck locale/JSON hardening (5.2; cited by tests/test_validate.py), UX batch (progress, warnings, exit codes, `--limit`), mimetype/unknown-entity/NCX-ids/`--add-img-alt` additions, the void-end-tag spec gap | v0.6.0-v0.10.0 |
| 3 | Calibre plugin ("Bindery Repair", `FileTypePlugin` `on_import`): the decided shape (see Records kept), scoped vendor slice, byte-idempotence, per-release zip attachment | v0.37.0 |
| Suppl. | Library-audit schemas: `--fix-id-colons`, `--fix-empty-body`, `--fix-missing-title`, `--unwrap-block-in-inline`, `fix_ncx_playorder`, `--strip-invalid-value` (all opt-in since v0.17.0; `--add-img-alt` deliberately never defaulted) | v0.14.0 |
| 4 (cquarry) | cquarry integration: `get_format_path()` resolution, the `--tag` write path, `audit --id` single-book mode | v0.18.0-v0.19.0 |
| 6 | Code sweep: the content-loss fix, `--replace` crash, dead method, java pin removal, regex bounds, opt-in decoupling, CSS-aware unwrapping | v0.16.3-v0.19.0 |
| 7 | `monolithic` analyzer + the version-sync pin (`tests/test_version.py`) | v0.21.0 / v0.19.2 |
| 8 | `library --id` batch scoping, spine-integrity classification (convention vs fragment), `audit --id` comma lists; `--workers` made real in v0.30.0, the daemon pool in v0.35.0 | v0.23.0 |
| 9 | Archive integrity: the CRC sweep with its own CORRUPT verdict (never mislabeled EMPTY), the unreadable sub-reason split | v0.22.0 |
| 10 | OPF/NCX edges: `--fix-page-map`; the RSC-005 EPUB3 scrubs (`--strip-epub3-attrs`, `--downgrade-epub3-tags`), ruled Option B | v0.25.0-v0.27.0 |
| 11 | install-to-calibre on the native cquarry write module (`install_format`/`set_format`; the calibredb subprocess retired) | v0.24.0 |
| 12 | Top-error repairs: `--prune-missing-resources`, `--strip-broken-anchors`, the HTM-025 half, the FastSweep harness; bulk RSC-005 rejected (see Records kept) | v0.28.0 |
| 13 | Machine-readable audit (`audit --json`) + the `run phase1`/`run phase3` acquisition slices + the non-interactive contract | v0.29.0 |
| 14 | Hardening from the 2026-09-08 sweep: anchored/quote-aware regexes, `fix_id_colons` honesty + NCX follow, CDATA/comment protection everywhere, strict UTF-8, `--reserialize` html-root scoping, the watermark/roman/install/EPUB3-gate bug fixes, audit ToC accounting inside the open zip, the "unify on trouble" exit ruling, backup rotation + out-of-tree refusal, the daemon pool, docs reconciliation, the test-suite cleanups | v0.32.0-v0.35.0 |
| 15 | `audit completeness` spot-check analyzer (trailing-ToC, prose sampling), fixtures from the Redwall run | v0.36.0 |
| 16 | Package-structure repairs: the prevalence study, `--fix-container`, the OBFUSCATED vs DRM verdict split, prune edge completion, `--fix-media-types`, `--fix-cover` (the ruled hybrid), the boxed classes below | v0.38.0-v0.39.0 |
| Waves 15 + blitz | The plugin PEP-758 HIGH + the 3.11-3.14 interpreter matrix, analyzer robustness, the per-matcher census (possessive bounding; cited by tests/test_matcher_hardening.py), stdlib XML hardening, FastDaemon v2 (reconcile-not-retire), `--strip-stub-docs`, `bindery doctor` + `repair --json`, `--encode-url-spaces` entry renames, the cover + tocdrift analyzers, the privacy/GitHub/docs/comment/hygiene batches, requires-python 3.12 with markers, the em-dash sweep | v0.40.0-v0.44.0 |
| 0.45 | Structured fix records (`fixes`, `ncx_uid_synced`, `watermark_refusals`) in every JSON payload; CalibreQuarry consumes them with the 0.45.0 floor | v0.45.0 |
| 0.46 | RSC-016 `--fix-comment-double-hyphen` (the last open repair class) and the RepairFlags dataclass | v0.46.0 |
| 0.47 | The intake-wave batch (five open issues): the stub classifier's body-pool + image-carrier exemption, the NCName-complete `--fix-ids`, and `--fix-svg-dup-ids`, `--fix-cdata-terminator`, `--fix-misnested-inline`, `--fix-stray-close`, `--fix-unterminated-attr` | v0.47.0 |
| 0.48 | The AI-probe batch: the enforced closed-Calibre gate on both metadata.db doors (`audit --tag`, `library --apply --install-to-calibre`), the stated contracts (exit codes, no-prompt, NCX-001, --id scoping, json envelope keys), and `--help-json` | v0.48.0 |
| 0.49 | The probe form (issue #7): single-file repair without an output path runs the gate and writes nothing; writing needs an explicit DST | v0.49.0 |
| 0.50 | The contamination analyzer (issue #8): foreign-title template stamps, fan-bundle ad signatures, review-paste front matter; advisory by contract, archive-wide corpus (`Book.extra_docs`, capped), and the `_dir_dispatch` map that fixes the v0.44.0 cover/tocdrift ocr misroute in directory/single mode | v0.50.0 |
| 0.51 | The unbound-prefix repair (issue #9): `--fix-unbound-prefix` binds the Calibre embed scar's undeclared attribute prefix inline (evidence-based URI, scope-tracked, refuse over guess), plus the masked-ncx-gain rule that stops a real dtb:uid sync reading as no measurable gain under a fatal package | v0.51.0 |
| 0.52 | The spine guard (issue #10): the manifest prune reads the layout its hrefs are written in and derives its spine protection from the text it prunes (the raw-space wave's spine collapse), and `process_book` vetoes any candidate whose spine (itemref -> item -> archive) dangles more than the source's, above the counts and every promotion | v0.52.0 |
| 0.53 | `bindery observe` (the observatory): the whole-library census in one warm-JVM pass (counts and error codes together, sentinel-backed so no book is dropped), tabulation (severity histogram, code profile, worst offenders), the gated repair preview composed in-process, the versioned `.observe` artifact with `--against` trend diffing and the keep-root-clean output guard; the library and audit walks skip `.caltrash` | v0.53.0-v0.53.1 |
| 0.54 | The surgery fixes: `fix_ncx_playorder` is contract-gated (epubcheck's playOrder contract: same-target sharing, distinct-target distinctness, contiguous 1..K across navTarget/pageTarget; the old unconditional 1..N renumbering rewrote valid TOCs into violations, 211 of 579 firings gate-rejected vs 17 real fixes) and `_PROTECTED_RE` protects the Calibre `/*<![CDATA[*/ ... /*]]>*/` wrapper as one unit (the bare CDATA span exposed the trailing `*/` to strip_prolog_junk's amputation, 111 phantom-fix rejections); verified by the 2026-10-24 error-code surgery (OBSERVE-RESEARCH.md) | v0.54.0 |
| 0.55 | The issue-batch release (issues #11-#16, the 2026-10-24 surgery's follow-ups): `--strip-epub3-attrs` extended with `role` + `aria-hidden` (the census killed the DOCTYPE-gate concern: epubcheck is profile-driven; #11), `--fix-duplicate-ids` for content docs (#14), `--declare-missing-items` for RSC-008 (#16, with the prune's RSC-001 boundary documented), the size-scaled epubcheck timeout with one retry and the distinct `OracleTimeout` (#15), observe schema v2 (`codes.messages` rename, true `codes.occurrences`, the `counting` block, v1 `--against` accepted; #13), the #12 content-model ruling (measured, not repaired: the wrap changes layout), and the #17 census lanes (RSC-012/OPF-029 dispositions on existing flags; OPF-030/CSS-007/RSC-004 recorded leave-measured with recipes) | v0.55.0 |
| 0.56 | The boxed recipes, built same-day on the follow-up call: `--fix-unique-identifier` (the OPF-030 recipe: wire the package's named id onto the first dc:identifier, refuses every ambiguous shape, and predicts the post-wiring uid so the NCX sync the broken wiring was masking fires in the same run -- eagle 2e -> 1e accept) and the CSS-007 font half inside `--fix-media-types` (font/* blessed on both package versions, magic confirmed, .eot never touched; accepted under no_worse because the gain sits on the INFO axis -- Brave New World 8 fonts normalized, CSS-007 cleared) | v0.56.0 |
| 0.57 | The embed scar's element half and the end of the silent equal (issue #22): `--fix-unbound-prefix` now binds an unbound ELEMENT prefix inline (the 9.7.0 writer self-declares xmlns:dc on every dc: element but one; rule 2 for a literal dc: element resolves to the DC namespace itself; Hackers 1f/1e -> 0f/1e with both binds in one run, and the half-repaired intermediate that read equal now repairs), and the masked-axis reclassification generalizes from the v0.51.0 NCX sync to every fired fix: a gate noop on a book that started and stayed fatal reads partial (the manual list) instead of a silent equal; the specimen's residual ncx:-prefixed dtb:uid gap shipped as the v0.57.1 patch (both matchers read a prefixed `<ncx:meta>` carrier; Hackers fully clean 0f/0e/0w) | v0.57.0-v0.57.1 |
| 0.58 | The structural dry run's four rejects (issues #18-#21), each bisected to the root on its specimens: the private-ZipInfo write contract (issue #18: writestr stamps the info it is handed with the DESTINATION offsets, one shared info repointed zin's own lookups into the output file -- all 57 "unreadable" books, the "possible zip bomb" included, were this one bug; the peek also catches BadZipFile per the issue), bracket-encoded renamed references plus the start-tag/PI anchor on both reference rewriters (issue #19: raw brackets are RSC-020 path-illegal -- IJ 108e -> 22e -- and the unanchored regex corrupted display text that teaches HTML -- Hacking's two RSC-016 fatals), the entity-decoding href resolver and prefix-tolerant OPF matchers (issue #20: `&amp;` hrefs vs raw-`&` entries made prune and declare swap the same items on the D&D four, and `<opf:item>` blindness duplicated whole manifests on the Sony three), and the three #21 mechanisms (guide-fragment strip, the post-anchor playOrder re-run, the comment-blind duplicate-id rename) | v0.58.0 |
| 0.59 | The dangling-NCX prune (issue #23): `--prune-dangling-ncx-targets` removes navPoint/pageTarget elements whose `<content src>` names a document the archive never contained (RSC-007 the manifest prune cannot reach: no item exists for a file the OPF never declared), node-exact over a balanced stack scanner (dead subtree drops as one span, dead parent spliced above live children, scheme/bare-fragment/comment-hidden/contentless refused, unbalanced walk aborts), with the contract-gated playOrder pass re-run in the same branch -- the hand-surgery-plus-second-run rescue is one gate (fixture 0f/1e/0w -> 0f/0e/0w: one navPoint pruned, 389 playOrder values re-sequenced) | v0.59.0 |
| 0.60 | The oracle budget scales on decimal MB and archive entry count (issue #24): the Summa Theologica (10,440,245 bytes = 9.96 binary MiB, 3,831 entries, 3,808 spine docs, 16,456 playOrder findings) is entry-count-bound, not bytes-bound -- measured 384s source and 445s candidate against the flat 300s its bytes computed (the binary 10 MiB divisor ate the size chunk; the book the docs call 10.4 MB never earned one) -- so every sweep double-timed-out and misfiled it as `(epubcheck timeout)`; the size term counts decimal MB now, the entry term adds +60s per started 500 entries above 1,000 (same 1800s cap), the book computes 720s, and a non-zip path falls back to size-only. epubcheck does police the playOrder contract (2 of the source's 6 RSC-005 error entries): default-pass candidate measures 0f/6e -> 0f/4e (accept), scoped `--all` candidate 0f/0e/0w | v0.60.0 |
| 0.61.1 | The hex-ref href crash and the sweep that survives it: `_NUMERIC_REF_RE` captured the hex ref's `x` into the digits group and `int('x2013', 16)` killed the first whole-library `--all` dry run (2026-11-03 observe lane; 36 hex-ref hrefs across 5 books, every one an external URL the decode reaches before the scheme check discards it; dormant since v0.58.0 because the resolver is opt-in-path-only); the fix strips the prefix before the int, and the sweep's repair loop gains the crash guard -- an unexpected per-book exception is that book's `error` verdict (`crash (Type): ...; not applied`), counted in the existing errors tally, instead of a run-killing traceback (the single-file verb keeps its traceback) | v0.61.1 |
| 0.62 | The observatory batch (the 2026-11-03 lane's answers): three predicate repairs -- the playOrder contract goes base-free (epubcheck polices sharing/distinctness/gaps, never the base; 107 no-op renumberings silenced), `fix_pagelist_class` inserts the collision-checked id beside the class (class-only made epubcheck demand the id it enabled; the 215-rejection poison), `fix_missing_title` fills the self-closed `<title/>` (the paired-fill branch was dead code; the 70-rejection poison) -- and four new flags for the never-triaged census codes: `--fix-package-version` (OPF-001; literal 1.0 only), `--fix-doctype` (HTM-004; present-but-forbidden declarations only, after the firing census killed insertion), `--prune-invalid-guide-refs` (OPF-032; declared-but-not-a-content-doc targets), `--fix-item-properties` (OPF-014; EPUB3-only, scripted/svg/remote-resources from a whole-book census) | v0.62.0 |
| 0.62.1 | `--fix-double-colon-tags`: the pseudo-tag escape for the last library fatal -- an XML name carries at most one colon, so a `::`-named pseudo-tag is never markup (fatal RSC-016; the Book of Debugging's C++ samples), and escaping both ends restores the author's text; nested templates escape inner-first, real markup is unreachable by construction; also closes the spec.md drift (the four v0.62.0 flags had not joined the inventory) | v0.62.1 |
| 0.62.2 | The review batch (three-reviewer pass over the v0.62.x range): the item-properties insertion branch emitted malformed XML on the self-closed shape (attribute after the solidus; no test drove the branch -- every fixture pre-carried properties), now appends before the solidus with an ET-parsed pin; the missing-title empty-fill is head-scoped (an empty SVG `<title>` is visible text); the double-colon tail is bounded at 120 chars (the escaped-bracket runaway); the unverified on\* handler disjunct left the census; `_scan_doctype_span` refuses unterminated declarations; the guide-prune docstring matches the code; and the documentation inventories caught up (README's seven missing flags, spec.md's four behavioral bullets + body counts + the safe-opt-in section, AGENTS.md's inventory) | v0.62.2 |
| 0.63 | The harvest census (the honest dig into the remaining 14,109): `--strip-presentational-attrs` (the largest attribute family; rendering-caveat approved), `--encode-ref-brackets` (the issue #19 mechanism applied to brackets without spaces; the 59-book RSC-020 lane), `--fix-id-colons` broadened to every NCName failure (digit-led scraped ids; the apostrophe charset blind spot closed), and the audit battery's `shell` analyzer (the Wandering Inn detector: spine-missing fraction at or over 50% reads SHELL, re-source never repair) | v0.63.0 |
| 0.63.1 | Two single-flag live-verification catches: `--encode-ref-brackets` alone was a silent no-op (`present` only filled under the other flags' guard -- The Given Day's 59 NCX bracket srcs encoded, zero raw left, once the guard included the flag), and the audit CLI's `--only` choices lacked `shell` | v0.63.1 |
| 1.0 | The milestone: the contract is the promise (spec.md stability section — additive flag surface, stable gate/records, oracle-evidenced predicate refinements), the shell-% denominator fix, the README status line. The observatory's final cycle: preview 0/0/0, clean 4,126/7,156, mass 11,455, ZERO fatals, 2,055 books repaired across the session's waves. Open items carry as 1.x additive work: RSC-011's spine-write policy call, the header-pseudo-tag someday-flag, the observatory cadence | v1.0.0 |
| 0.64 | `--prune-dead-spine`: the shell repair, shell-gated at the 50% signature (drops dead manifest items + spine itemrefs, coordinates the NCX navPoint prune and an UNCONDITIONAL playOrder re-sequence to 1..K after epubcheck 5.3 flagged the non-1 base on the post-prune shape -- the base-free clause's minimal matrix missed the shell shape); proven at 742e -> 5e on the facility copy | v0.64.0 |
| 1.1 | The delimiter-damage repair (issue #26): `--encode-ref-delims` (structural repair #34; the two-gate existence-proven encode for raw #/? filename damage: the reference must not resolve as written, the encoded spelling must match an archive entry; entities and empty-path anchors refused), the shell census's delim-recoverable exclusion (output layout AND raw layout; the Elminster shape can never cross the 50% signature), the missing-file mask acceptance bar keyed on `refs_delim_encoded`, and the audit shell verdict on the genuine fraction (`spine_missing_delim`; a fully-recoverable book reads ADVISORY, never SHELL); plus the two latent gaps the flag surfaced (the brackets OPF dispatch, the stylesheet non-UTF-8 guard). Fixture: probe accept 0f/3e -> 0f/4e, 36 encoded, no amputation, dangles 36 -> 0 | v1.1.0 |
| 1.1.1 | The audit batch (issues #28-#33, filed from the 2026-11-16 deep codebase audit, each live-replicated before the fix; no CLI surface change): the NCX fragment half follows the full `fix_id_colons` NCName predicate and rename and both fragment passes carry the tempered-quote idiom (digit-led and apostrophe-bearing ToC/href fragments no longer dangle or diverge; escaped fragments -- percent-escapes and entity references -- refused outright per the pre-commit review, and the matchers take the whitespace+namespace anchor), the three anchor cleaners percent-decode fragments before the existence check (links to non-ASCII ids survive; xml:id stays in the anchor index via `_ANCHOR_ID_RE`), `data-id`/`xml:id`/`data-class` leave the id and class matchers (the `fix_id_colons` lookbehind generalized), the EPUB3 downgrade never rewrites comments or CDATA, a self-closing paragraph never welds into a pagination block, and the manifest declarer finds prefixed and whitespace close tags | v1.1.1 |
| 1.2 | The CSS dead-font drop (issue #27): `--strip-dead-font-faces` (structural repair #35) removes every `@font-face` block in a stylesheet whose url() references ALL miss the archive -- the ebook-convert azw3->epub placeholder class (`url(styles/XXXXXXXXXXXXXXXX)`, one RSC-007 per dead reference, 149/316/159/450 errors across the four found cookbooks; the manifest prune has no item to answer and `--all` never converges because the references live in CSS url()). Rendering-neutral by construction (the font never loaded; the fallback is the rendered reality), one live url() keeps the block, scheme/query/fragment/raw-space/local()-only references and nested-brace blocks are never guessed at (the query guard and the comment-span intersection rule are pre-commit-review catches), resolution walks the shared resolver against the pre-rename layout (the pass runs first in the CSS branch), and XML comments/CDATA plus plain CSS comments are never rewritten. Facility verification: scoped-flag gate runs 0f/6e -> 0f/3e and 0f/5e -> 0f/2e, both ACCEPT, zero residual RSC-007 (the residuals are other flags' classes: epub:type, colon ids) | v1.2.0 |
| 1.2.1 | The latent-lookbehind cleanup (issue #34, filed from the v1.1.1 pre-commit review): the #30 `data-*` guard generalized to every remaining `\b<attr>=` matcher -- `fix_manifest_ids`' five edge matchers (`fallback=`, `media-overlay=`, `spine toc=`, both cover `meta content=` forms), the `_DTB_UID_RE`/`_DTB_UID_RE_REV` uid-sync pair, `_COVER_META_RE`'s lookahead (`data-name="cover"` is not the cover convention), pagination's `_ID_ANCHOR_RE`/`_OPEN_ID_RE` (anchored on `(?:\s)(?:xml:)?id=`: `data-id` no longer over-protects a number block from the strip, `xml:id` stays a protected anchor), plus the review pass's three completions so the sweep is literally total: `_PACKAGE_VERSION_ONE_RE` (a `data-version="1.0"` was rewritten), the guide cover-statement scan (`data-type="cover"` drove cover-meta repointing -- a real misfire), and `fix_pagelist_class`'s satisfaction checks (plain `id`/`class` only, what epubcheck actually demands). `_EDGE_ATTR_RES`/`_COVER_CONTENT_RE` were already whitespace-anchored | v1.2.1 |
| 1.2.2 | The half-spine veto (issue #35, the Flux specimen, live in the 2026-11-17 mega-batch). Root cause: the `--prune-dead-spine` shell census read its hrefs' existence against the RENAMED output layout and consulted the raw source layout only for the #26 delim variant, so when `--encode-url-spaces` co-fires every raw-space href reads dead -- Flux measured a 97% shell signature (31 of 32) on a book whose files were all present, and the census fed the amputating prune (31 of 32 spine items applied away; the issue's off-by-one clue -- 30 refs rewritten -- was the prune amputating the documents the rewrite pass would have covered). The census now consults `present_src` for the plain href (the v0.52.0 lesson, census edition): Flux repairs cleanly, 31 renamed / 61 rewritten / 0 pruned. The gate arm beside the #10 veto: the dangle-count check is blind to a prune (pruning removes the dangling declarations with the rest -- Flux measured 0f/38e/31w -> 0f/30e/0w), so a `dead_spine_items_pruned` candidate resolving less than half its source's resolved count rejects (`(spine halved)`), resolved-vs-resolved per the issue's Elminster-rule intent: the genuine shell keeps v0.64.0's contract (only never-resolving positions dropped, same resolution both sides, no veto), "less than half" is strict, both promotions rank below it, and the probe is fail-open (`spine_resolution` total/resolved/dangles; `spine_dangles` delegates) | v1.2.2 |

## Open work

- [ ] **The 2.0 program: the library updater** (planned and decided 2026-11-20; see
      the 2.0 program section below). Native, pilot-first EPUB 2/1.x to EPUB 3
      migration lane under a recorded charter amendment; groundwork (2.0-A) runs
      first; annotations and the 3.4-rules gating layer are parked; the
      azw3/mobi box below is absorbed as a parked lane.
- [ ] **Percent-encode illegal URL characters in references**: the RSC-020 class
      beyond spaces. Opened by issue #10's My Enemy My Ally fixture: once
      `--encode-url-spaces` clears the spaces, the fan conversion's brackets and
      commas re-report as 28 individual RSC-020s (previously folded into one
      aggregated RSC-005), so the candidate measures as a regression and the
      gate refuses a book that is otherwise repaired. Needs the same
      entry-and-reference coordination as the space renames, with the
      decode-before-lookup epubcheck rule verified per character class.
      *v0.63.0 narrowed this: `--encode-ref-brackets` ships the bracket
      slice (the decode-before-lookup rule verified for `%5B`/`%5D`); the
      comma and remaining classes stay open.*
      *(Narrowed v0.58.0: renamed references now percent-encode `[`/`]`
      (issue #19), so a book whose bracket-named entries carry spaces
      repairs cleanly. The no-rename shape remains: entries with brackets
      but no spaces never enter the rename map, and the flag stays a
      no-op on such books.)*
- [ ] **Kindle-container conversion support (azw3/mobi -> epub): a possible
      x.0.0 release candidate** (Brandon, 2026-11-13). The acquisition
      pipeline keeps meeting Kindle-container sources (the 2026-11-13
      cookbook batch imported four azw3s that had to go through
      ebook-convert by hand, then through bindery for the converter's own
      artifacts - see issue #27: ebook-convert emits dead @font-face rules
      in page_styles(.1).css for publisher fonts whose payloads it drops,
      RSC-007 from CSS url(), unconverged by --all). The candidate shape:
      KF8/PalmDOC container parsing, HTML/OPF/NCX extraction into bindery's
      existing repair-and-gate pipeline (so converted books get the same
      epubcheck-gated deterministic treatment instead of a second tool's
      artifact classes), metadata passthrough, and the open question of
      font extraction - the sources carry TrueType-signature payloads, but
      whether publisher fonts are recoverable from KF8 (and whether they
      are obfuscated) needs a container parser before the answer is known.
      The dead-font-face repair (issue #27) ships independently and first;
      it is useful against any ebook-convert output, not just ours. Entry
      conditions: a real batch need beyond the four-book proof (the
      acquisition tempo keeps bringing Kindle containers), a KF8 parser
      survey (existing stdlib-parseable state vs new code), and the spec
      question of whether conversion is a bindery verb or a companion tool
      (bindery's charter is repair, not format conversion - this is the
      biggest scope question the project has faced).
      *(Absorbed 2026-11-20 as the parked mobi/azw3 lane of the 2.0 program
      below; entry conditions unchanged, no work scheduled by 2.0.)*
- [ ] **Obfuscation-aware encryption.xml repair**: prune stale obfuscation entries
      when font magic bytes prove the fonts are decrypted in place; drop
      encryption.xml when only obfuscation entries remain and fonts verify. NEVER
      un-obfuscate. *(BOXED with real counts, 2026-09-12: 424/424 readable
      obfuscation fonts genuinely scrambled, zero stale entries in-library; the
      precondition is validated and recognition carries both Adobe URI spellings.
      The audit half SHIPPED v0.38.0 as the OBFUSCATED verdict. Opens only if
      acquisition ever brings a decrypted-in-place book.)*
- [ ] **href case/backslash resolution**: case-insensitive namemap when exactly one
      zip entry matches; backslash to slash. Kills the present-file
      RSC-007/PKG-010 slice prune cannot touch. *(BOXED with real counts,
      2026-09-12: exactly 1 case-mismatch href in 1 book and 0 backslash hrefs
      across the library; reopen if acquisition changes the mix.)*
- [ ] **Duplicate zip-entry dedupe on rewrite**: drop shadowed duplicates (audit
      already counts `dup_entries`). *(BOXED with real counts, 2026-09-12: 0 books
      with duplicate entries in-library.)*
- [ ] **CSS url() pruning** as a `--prune-missing-resources` scope extension:
      stays closed until a named epubcheck finding demands it (the 2026-09-12
      prevalence read found selector damage, not url() candidates).
- [x] **Candidate transform: `--` inside XML comments (RSC-016 fatal)**. A
      `fix_comment_double_hyphen` transform (comment nodes only, `--` -> en-dash,
      byte-counted) would make the always-on core able to parse such books at all;
      until then the book parses in lenient readers but never passes epubcheck.
      Low frequency (one book in the classics wave); the fixture reference is in
      git history. *(SHIPPED v0.46.0 as the opt-in `--fix-comment-double-hyphen`
      structural repair: comment bodies only in content documents, the NCX, and
      the OPF; the `-->` terminators stay intact; normal `gate`. Not a core
      transform: it edits comment content, which everything else protects.)*
- [x] **RepairFlags dataclass** for the triplicated ~25-kwarg
      process_book/repair_epub signature. Deferred with reason: pure churn on that
      surface immediately after the v0.44 batch touched exactly those signatures;
      do it as a standalone lane with the full suite and the plugin byte-compat
      job green at every step, not as a rider. (The roman/arabic half of its parent
      box resolved to UNIFY at pagination.py, v0.44.0.) *(SHIPPED v0.46.0 as its
      own commit, exactly by that method: dataclass in epub.py, repair_epub, then
      process_book, then `cli._flags_from_args` collapsing the per-verb kwarg
      blocks, suite green at every step; the plugin's bare repair_epub call and
      byte-compat unchanged.)*
- [ ] **MobileRead listing for the Bindery Repair plugin** (Brandon's manual
      step): post the listing with name/identity, the release-attached zip,
      minimum Calibre 7.0, Linux platform note. Posting is outward-facing under
      his name.
- [ ] **README screenshot regeneration** (Brandon's call pending): rebuild
      docs/screenshots/library-sweep.png against a synthetic testing_facility
      library (invented titles, real dry-run output); the current PNG shows 16
      real rows. Asked 2026-09-15, unanswered.
- [x] **The 2026-10-02 closing sweep** (the four-lane verification's doc
      findings against the v0.46.0 tree): the safety contract's lossy
      enumeration names all four modes, the core-compat em-dash is gone,
      ci.yml's module comment covers all eleven stack-free modules, and the
      plugin's `customization_help` names `max_log_mb`. *(SHIPPED v0.46.1:
      text plus the one plugin string; no surface change.)*

## The 2.0 program: the library updater (planned 2026-11-20)

Brandon's idea: rebuild every EPUB in the library to a current, clean state, "clean
first, then convert, then clean again", with the house method (census first, then one
surgical gated flag per repeated defect class) and a manual remainder. Researched
2026-11-20 (assay idea-mode grade plus the latro landscape report at
`~/docs/reports/bindery-cli-epub34/2026-11-20-epub-3.4-migration-landscape.md`, all
keyless sources, the two streams cross-confirming) and decided the same day. The
research reshaped the goal before any code:

- **There is no `version="3.4"` package.** EPUB 3.4 mandates `version="3.0"` verbatim
  ("each new 3.X specification is a continuation of the EPUB 3 format"); writing 3.4
  is a spec violation epubcheck rejects (verified locally: OPF-001 on the installed
  5.3.0, and the version-string constants are identical in 5.4.0 source). The real
  migration is EPUB 2.0.1/1.x to EPUB 3. The target spec's MUST list is small: the
  version attr at 3.0, exactly one `dcterms:modified` (UTC, `YYYY-MM-DDThh:mm:ssZ`),
  and a mandatory nav document with exactly one toc nav (`properties="nav"`).
- **3.4 invalidates nothing conforming in 3.3.** Every substantive change since 3.3
  moves features to outdated or deprecated classes, both still conforming; a
  3.3-conforming book already is a 3.4-conforming book. The 3.4 layer is advisory
  (epubcheck 5.4's OBS_001 usage class), not obligation.
- **The installed oracle gates the real migration today.** epubcheck 5.3.0 validates
  `version="3.0"` under "EPUB version 3.3 rules", and the migration introduces no
  3.4-only features, so 5.3.0 suffices; epubcheck 5.4.0 (2026-09-15) adds the 3.4
  rules mode. Any 5.x oracle rejects 1.x version strings outright, so the 10 OEBPS
  1.2 books are ungateable until their packages upgrade.
- **Census (read-only, 2026-11-20, full library):** 7,387 epubs: 5,593 EPUB 2.0
  (including 30 prefixed `<opf:package>` spellings), 1,784 EPUB 3.0, 10 OEBPS 1.2,
  zero unreadable archives. Migration surface: ~5,600 full migrations plus 10
  special cases; the 3.0 shelf is already conformant.
- **KOReader (the household reader):** crengine classifies any package version whose
  first character is "3" as EPUB 3 and prefers the nav TOC over the NCX when a nav
  exists; the NCX stays legal in 3.4 (legacy/outdated). Posture: correct nav, NCX
  kept.
- **EPUB Annotations 1.0 is a Working Draft** (latest 2026-10-15) with no
  package-document wiring at all (embedded sets are `META-INF/annotations.json`) and
  no consumer or writer found anywhere; KOReader keeps highlights in sidecar
  `metadata.lua` files. Scaffolding today is bytes no reader reads.
- **Prior art:** DAISY Pipeline 2's EPUB Upgrader (XProc; the same transform
  inventory, validation off by default), ePub3-itizer (Sigil-bound), calibre's
  epub-to-epub (lossy full re-serialization defaulting to EPUB 2, the source of the
  converter artifact classes bindery already repairs). Nothing found combines
  in-place operation, determinism, content conservation, and an epubcheck gate: the
  niche bindery would occupy is unoccupied.
- **Assay idea-mode verdict: not worth it as stated** (draft-spec timing, the
  annotations category error, charter collisions), with kill conditions; the
  decisions below answer them.

**Decisions recorded 2026-11-20 (Brandon):**

1. **Vehicle: native lane, pilot-first.** bindery grows the gated upgrade lane
   itself, under a deliberate charter amendment, with a 50-100 book pilot batch
   before any library wave. (Assay kill-condition 3 answered up front: the charter
   amendment is recorded here, and the fidelity axis, 2.0-C, is specified before any
   converter code.)
2. **Annotations: parked** until Annotations 1.0 reaches Recommendation AND a reader
   the household runs consumes it. Only the presence-detector ships now.
3. **Groundwork: this week** (decision-independent; phase 2.0-A).

**Scope boundary (the parity ruling, reconciled).** "Conversion stays out,
permanently" declines the Calibre conversion ENGINE lane (cross-format input by
orchestration; CalibreQuarry territory) and ebook-polish's content-improving
actions. The 2.0 lane is neither: it is EPUB-to-EPUB version migration by bindery's
own deterministic transforms, gated by the same oracle. The charter amendment, to
land with the first flag:

- The upgrade lane is a first-class surface (an `upgrade` verb plus flags),
  additive to the 1.x contract (the stability contract explicitly allows new
  opt-in flags); "2.0" is the milestone where the updater is declared stable, as
  1.0 was a contract statement rather than a feature claim.
- **Derived-navigation exception:** nav-from-NCX and landmarks-from-guide are
  translation of navigation data the book already carries, never invention. The
  ToC-synthesis non-goal stands unchanged (tocdrift stays detector-only).
- Visible-text conservation remains absolute; the conversion gate (2.0-C) is the
  acceptance axis.

### 2.0-A Groundwork (this week, decision-independent)

epubcheck 5.4.0 installed side-by-side in `~/.local/opt` (the wrapper stays 5.3.0
default until its baseline re-run; changing the oracle means re-running the library
dry run, the standing rule); a sample-library observe under the 3.4-rules mode to
measure what actually fires (OBS_001 outdated classes, deprecation reports) instead
of assuming; the version census promoted into a first-class audit analyzer; the
annotations presence scan (`META-INF/annotations.json`, payload sniff). Output: a
counts report that finalizes the flag inventory below.

### 2.0-B The migration flags (EPUB 2.0.1 to EPUB 3)

Each flag gated like every repair, built on existing precedents
(`--fix-package-version` does the literal 1.0 to 2.0 bump today;
`--fix-unique-identifier` wires the uid; the NCX pipeline already synchronizes
dtb:uid; `--fix-item-properties` computes EPUB 3 properties from a whole-book
census; `--fix-container` set the constant-epoch determinism rule):

- `--upgrade-package`: version attr to 3.0, exactly one `dcterms:modified`
  (constant-epoch, byte-idempotent), uid wiring reuse. Content docs untouched.
- `--nav-from-ncx`: the mandatory nav document, derived by translating the NCX
  navMap (the NCX tokenizer already exists), `properties="nav"` wired through the
  properties census; refusals where the NCX is unusable land the book on the manual
  list.
- `--landmarks-from-guide`: derived from the guide's own references. The guide, the
  OPF 2 meta elements, and the NCX all STAY (legacy-legal in 3.4); their removal is
  separate opt-in work, never bundled with the upgrade.
- Optional forward moves, each its own flag grown from the pilot census: cover meta
  to `properties="cover-image"`, `opf:` attribute translations, OEBPS 1.2-era
  media-type normalization (the `--fix-media-types` precedent).

NCX policy: keep. KOReader prefers the nav when present but resolves spine@toc to
the NCX; EPUB 2-only tools elsewhere keep their data; never-break-userspace.

### 2.0-C The conversion gate (specified before the verb ships)

A third acceptance bar beside `gate` and `no_worse`. Accept iff: post-conversion
fatals == 0 (still-fatal lands partial, the manual list); visible-text conservation
across the spine (the audit battery's own primitives); spine resolution preserved
(both veto arms apply unchanged); byte-idempotent re-run. Errors and warnings MAY
rise: the new ruleset legitimately introduces new check classes, so the conversion
bar measures fatals, content, and spine, never the error axis. The masked-axis
reclassification (a noop on a still-fatal book reads partial) applies.

### 2.0-D The `upgrade` verb

Single-file plus library modes; per-book pipeline clean (the default pass, its own
gate) then convert (the migration flags, the conversion gate) then clean again (the
default pass over the new profile's classes); one atomic write, one backup ring
entry; dry-run default; `--install-to-calibre`; additive per-book/per-stage record
vocabulary (CalibreQuarry's mirror floors move when it consumes). The plugin never
runs upgrades (the latency ruling, unchanged). Acquisition-pipeline wiring (`run`
verb slices) comes after the library wave.

### 2.0-E The pilot

50-100 books, stratified (clean EPUB 2, damaged EPUB 2, 3.0 controls): dry-run,
census what the conversion broke or left, build the missing surgical flag, re-run.
No library wave until the pilot's accept rate is measured. THE WAVE ops template
(the v0.61 lane: 1,170 books in one night, backups plus metadata.db) is the rollout
shape; the learning loop is the wave cadence.

### 2.0-F The wave and the manual remainder

Batched rollout with backups and before/after observe. The remainder lands on the
manual list: the 10 OEBPS 1.2 books (any 5.x oracle rejects their version strings
outright, so they need the package upgrade before the pipeline can even gate them; a
dedicated flag only if the pilot census shows a repeated shape), still-fatal
conversions, and the shell books (detected, re-source, unchanged).

### Parked lanes

- **Annotations 1.0 scaffolding:** opens at Recommendation plus a consumer reader
  the household runs (nothing reads it today; KOReader uses sidecars). The
  presence-detector ships with 2.0-A.
- **3.4-rules gating layer:** opens when the default oracle flips to 5.4.0 after its
  baseline re-run; until then the migration produces 3.3-conformant packages that
  are already 3.4-conforming.
- **mobi/azw3 input:** the 2026-11-13 open box below, unchanged (entry conditions
  stand; absorbed here for the record, no work scheduled by 2.0).

## The repair domain in the ecosystem parity program (recorded 2026-09-29)

Brandon opened the ecosystem Calibre-parity program on 2026-09-29 (cquarry roadmap.md,
"The parity program"; parity counts native coverage plus orchestration of Calibre's own
headless tools, and excludes process-bound and declined surface with recorded reasons).
bindery-cli's lane is the repair/acceptance domain, where this repo is already past
parity with Calibre's own tooling: ebook-polish has no acceptance oracle, no determinism
contract, and no atomic library replacement; bindery has all three, plus native
metadata.db registration and the Calibre plugin. Dispositions recorded under the program:

- **Conversion stays out, permanently** (spec.md:10-14): Calibre's conversion engine
  (~45 input / ~20 output formats) is orchestration-lane territory (CalibreQuarry
  `run convert`); bindery never grows one, and no parity claim depends on it.
- **The ebook-polish content-improving actions bindery lacks stay declined**:
  embed/subset fonts, jacket, smarten punctuation, remove-unused-css, compress-images,
  upgrade-book, download-external-resources, the --opf rewrite, --cover. They are
  content-improving rather than repair, and dc: metadata editing is out of charter
  (spec.md:597). Recorded here so the parity ledger's bindery row names them declined,
  not missing.
- **The `data`-row waiver note**: bindery has updated `data` rows through
  `WritableCalibreDB.set_format` since v0.24.0 (the sanctioned install path);
  cquarry's conditional write-back waiver was reworded 2026-09-29 to name what it
  actually guards: a bindery-side book-metadata write-back convenience, still declined.
- **Parity-relevant accepted gaps**: RSC-016 (`--` inside XML comments) SHIPPED
  v0.46.0 as `--fix-comment-double-hyphen` (the candidate box above), so no open
  repair class blocks epubcheck acceptance on prevalence anymore; the
  prevalence-gated classes (encryption.xml, href case/backslash,
  zip dedupe, CSS url()) reopen only on acquisition mix. The RepairFlags refactor shipped
  v0.46.0 (the ticked box above).
- **Brandon-parked items are unchanged**: the MobileRead plugin listing and the README
  screenshot regeneration stay his; the program does not schedule them.

## Records kept (decisions and counts that live only here)

- **The plugin shape decision** (2026-08-09, researched against the Calibre source;
  corrected 2026-09-05; shipped v0.37.0): `FileTypePlugin` with `on_import`:
  `run(path)` returns a `temporary_file()` replacement before the file lands, so the
  original on disk is never touched and metadata.db is never written. A raising plugin
  cannot abort an import and cannot lose the file, but the failure is silent, so the
  plugin never raises and keeps its own log under the Calibre config dir.
  `supported_platforms = ['linux']` (the loader rejects an empty declaration). The hook
  also fires on `add_format`, so the plugin is byte-idempotent. The vendor slice
  (transforms/epub/pagination/watermark/reserialize + a plugin `__init__`) is
  generated at release from the tagged tree with a byte-equality drift test. Active
  set = the gate-safe default pass only: every structural repair and the four
  lossy strips stay CLI-only, because epubcheck cannot gate inside Calibre (no jar/JVM
  and seconds-per-book latency). Config via `site_customization` JSON (`log`,
  `log_path`, `max_size_mb`, recorded default 150MB); `publish.yml` attaches
  `BinderyRepair-v<VERSION>.zip` to each release.
- **The metadata-validity carve-out ruling** (Brandon, 2026-09-12): route to
  cquarry/CalibreQuarry; bindery's side is closed and dc: metadata stays a non-goal.
  The routed half (51 OPF-085 invalid-UUID warnings plus the thin date/language tail)
  is CalibreQuarry's lane (recorded in its roadmap; its 3.41/3.42 audit rows found the
  classes clean at the DB level, the warnings file-side).
- **Prevalence baselines** (2026-09-12 study over 5,228 books; 2026-09-17 refresh):
  RSC-005 164,302 -> 13,782 occurrences; RSC-007 14,156 in 600 books; RSC-020 9,953
  in 337; PKG-010 8,237 in 284 (population flat, the rename class stayed live, hence
  v0.44.0); container.xml valid 5,228/5,228; case-mismatch hrefs 1 in 1 book;
  backslash hrefs 0; duplicate zip entries 0 books; encryption.xml in 86 books (426
  Adobe + 3 IDPF obfuscation entries; 424/424 readable obfuscated fonts genuinely
  scrambled at offset 0; the wild Adobe URI is `http://ns.adobe.com/pdf/enc#RC`, and
  recognition carries both spellings); NO real-DRM algorithm anywhere in the library.
- **The exit-code contracts:** argparse-level misuse exits 2 before the tool's
  validation; the tool's own usage errors exit 1; trouble (rejected + errors +
  unreadable + partial books) exits 2 in all three layers by the 2026-09-10 "unify on
  trouble" ruling (option A of the recorded pair).
- **The daemon decision** (2026-09-10): RECONCILE, not retire. The counting divergence
  ran deeper than the daemon (the human summary counts message occurrences, epubcheck's
  JSON counts aggregated messages, and the gate has always measured the aggregated
  scale), so FastDaemon v2 reads the JSON epubcheck itself serializes and measures the
  subprocess oracle's numbers exactly; the launch is Java's single-file source launcher
  (no javac, no version skew); warm throughput ~0.27s/book vs ~4.2s/book subprocess.
  Shipped v0.35.0.
- **requires-python ~3.12** (decision 63, executed v0.40.0): every cquarry/vir-tui on
  PyPI declares >=3.14, so the VirInvictus pins carry
  `; python_version >= '3.14'` markers (3.14 installs byte-identical); 3.12/3.13
  installs run the stack-free repair core; the CLI dispatch guard turns a missing
  stack into one stderr line + exit 2; CI byte-compiles the plugin zip under
  3.11/3.12/3.13/3.14; `minimum_calibre_version` is (7, 0, 0).
- **Tag policy** (decided 2026-09-11, recorded 2026-09-12): v0.35.0 is the anchor;
  every release from v0.36.0 on is tagged at its release commit under auto-tag
  promotion. The nine untagged 0.18.0-0.26.0 entries are exempted by decision of
  record (no backfill tags; patchnotes and PyPI carry them). Executed 2026-09-12: the
  v0.28.0 tag re-pointed at its first green commit with its title line restored, the
  v0.33.0 tag message corrected verbatim, and the 10 test_facility EPUBs stripped from
  all history via git filter-repo (main + 14 tags force-pushed; PyPI untouched).
- **Privacy record:** top500candidates/REPORT.md is untracked forward-only (Brandon's
  ruling, no history strip); the file stays on disk as the fast_sweep `--summary`
  target. Reopen condition: the history-strip recipe (rewrite 16 release tags,
  publish.yml disabled around the push) if the reachable copy is ever judged
  unacceptable (also in project.done).
- **The matcher census** (v0.42.0; cited by tests/test_matcher_hardening.py): six
  greedy exponential-class sites possessive-bounded (`_START_TAG_RE`, `_COVER_META_RE`,
  `_GUIDE_REF_RE`, the prune spine matcher, `_ITEM_TAG_RE`, `_SPINE_ITEM_RE`); five
  lazy quadratic-class sites took the disjoint `[^>"']` catch-all (`_VOID_RE`, the
  transforms `<img>` matcher, epub's link/a/img matchers); everything else audited
  safe by construction. Never a third grammar copy: pagination.py is the canonical
  roman/number home (it vendors into the plugin; audit imports from it), and any
  future divergence is a decision at pagination.
- **Non-goals kept with reasons:** ToC synthesis is out of the repair charter
  permanently (tocdrift is detector-only); conversion is out permanently (the parity
  section above); dc: metadata editing is out (spec); bulk RSC-005 rejected (164,302
  instances; narrowly bounded sub-cases stay opt-in under their own flags); no
  spine-item manifest pruning (missing spine documents mean corrupt archives or broken
  fragments); no text deletion in anchor unwrapping (character conservation is
  absolute); no auto-split of monolithic docs and no auto-trim of bloated ToCs (flag
  and re-source); no repair of corrupt archive entries (re-source, never repair); no
  PDF/DJVU work (never this repo's charter); `--add-img-alt` stays opt-in
  (`alt=""` asserts "decorative" to screen readers); defusedxml stays declined (the
  stdlib `_safe_xml_parse` delivers the entity protection and the vendored-plugin cost
  was the real objection; the full-parser swap is Brandon-gated on a real 3.12/3.13
  stranger audience); the plugin never gates on epubcheck (the latency ruling); no
  manifest format ownership (CalibreQuarry owns `acquisition-manifest` and the
  orchestration).
