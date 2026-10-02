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

## Open work

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
- [ ] **RepairFlags dataclass** for the triplicated ~25-kwarg
      process_book/repair_epub signature. Deferred with reason: pure churn on that
      surface immediately after the v0.44 batch touched exactly those signatures;
      do it as a standalone lane with the full suite and the plugin byte-compat
      job green at every step, not as a rider. (The roman/arabic half of its parent
      box resolved to UNIFY at pagination.py, v0.44.0.)
- [ ] **MobileRead listing for the Bindery Repair plugin** (Brandon's manual
      step): post the listing with name/identity, the release-attached zip,
      minimum Calibre 7.0, Linux platform note. Posting is outward-facing under
      his name.
- [ ] **README screenshot regeneration** (Brandon's call pending): rebuild
      docs/screenshots/library-sweep.png against a synthetic testing_facility
      library (invented titles, real dry-run output); the current PNG shows 16
      real rows. Asked 2026-09-15, unanswered.

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
  (spec.md:583). Recorded here so the parity ledger's bindery row names them declined,
  not missing.
- **The `data`-row waiver note**: bindery has updated `data` rows through
  `WritableCalibreDB.set_format` since v0.24.0 (the sanctioned install path);
  cquarry's conditional write-back waiver was reworded 2026-09-29 to name what it
  actually guards: a bindery-side book-metadata write-back convenience, still declined.
- **Parity-relevant accepted gaps**: RSC-016 (`--` inside XML comments) SHIPPED
  v0.46.0 as `--fix-comment-double-hyphen` (the candidate box above), so no open
  repair class blocks epubcheck acceptance on prevalence anymore; the
  prevalence-gated classes (encryption.xml, href case/backslash,
  zip dedupe, CSS url()) reopen only on acquisition mix. The RepairFlags refactor stays
  deferred with its recorded reason.
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
  set = the gate-safe default pass only: every structural repair and the three
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
