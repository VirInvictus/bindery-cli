# CLAUDE.md (bindery-cli)

Per-project guidance. Overrides the global file where they conflict.

## What this is

A focused EPUB repair and diagnostic tool: deterministic well-formedness fixes, gated by epubcheck,
with atomic in-place replacement in a Calibre library. Absorbed the retired oceanstrip at
v0.12.0 (2026-08-22) as the lossy `--strip-watermarks` flag; the standalone repo is gone from
the workspace (2026-08-26), and any instruction that runs `python -m oceanstrip` is dead.
Born from the 2026 library audit (see the user memory `calibre-library-epubcheck-audit`).

## Hard constraints

- **Minimal Dependencies.** Runtime deps are exactly: `tqdm` (progress/output),
  `vir-tui` (shared TUI rendering) and `cquarry` (read-only Calibre `metadata.db` access,
  adopted in v0.16.0), pinned as PyPI ranges in pyproject.toml (`vir-tui>=2.5.0`,
  `cquarry>=1.19.0`); `uv.lock` records the resolved versions. Since v0.40.0 both
  VirInvictus pins carry `; python_version >= '3.14'` markers (every release they
  ship declares requires-python >=3.14) and the package floor is 3.12: a 3.12/3.13
  install runs the stack-free repair core, and the audit/library surfaces degrade
  through cli.py's dispatch guard instead of failing resolution. `html5lib` remains the one approved
  heavy-parsing exception (used only for the `--reserialize` fix, imported lazily so every
  other mode runs without it). Tests use the standard `unittest` framework. epubcheck is an
  external CLI dependency expected on PATH; `--install-to-calibre` needs no external
  binary, it re-registers the format through cquarry's write module (the calibredb
  subprocess was retired in v0.24.0). Before adding any further Python package, stop and ask.
- **Semantics-preserving transforms by default, everything else fenced behind a flag.**
  The always-on core is exactly five well-formedness fixes (prolog junk, duplicate
  `xmlns`, bare `&`, named entities, void self-closing), the NCX pipeline, and the
  mimetype fix (added/normalized/first-stored, epub.py's archive rewrite); every core
  fix must render identically to the author's intent: never add, remove, or reorder
  visible content. The canonical opt-in inventory lives in spec.md: fifteen structural
  repairs, three lossy strips, four safe opt-ins, plus `--reserialize`.
  * **Structural repairs** (`--fix-empty-body`, `--fix-missing-title`, `--fix-id-colons`,
    `--fix-page-map`, `--strip-epub3-attrs`, `--downgrade-epub3-tags`,
    `--unwrap-block-in-inline`, `--strip-invalid-value`, `--unwrap-illegal-tags`,
    `--prune-missing-resources`, `--strip-broken-anchors`, `--encode-url-spaces`,
    `--fix-container`, `--fix-media-types`, `--fix-cover`; transforms.py, threaded
    through epub.py). Since v0.44.0 `--encode-url-spaces` also RENAMES the
    archive entries whose names carry raw spaces to their underscore spellings
    and rewrites every reference (OPF, NCX, content, CSS `url()`); underscore,
    never percent-encoding (epubcheck decodes references before entry lookup,
    verified against 5.3), ambiguous renames refused per entry (`space_rename_map`),
    accepted under `no_worse` because PKG-010 sits on the warning axis the gate
    does not measure:
    they alter markup structure or fabricate minimal content. v0.14–v0.16 ran these unconditionally, which broke this rule;
    v0.17.0 restored it. `--unwrap-illegal-tags` additionally protects any illegal-tag
    name that an EPUB stylesheet styles as an element selector (`css_protected_tags`,
    book-wide, inline `<style>` blocks included). The two EPUB2-targeted fixes
    (`--strip-epub3-attrs`, `--downgrade-epub3-tags`) are additionally gated on the
    package version in the OPF (`package_version()`): inert on EPUB 3 books and when
    no version can be read, since their target defects only exist below EPUB 3; the
    attribute scrub is anchored to real start tags via
    `transforms.strip_attrs_in_start_tags` (prose mentioning the attributes and
    CDATA/comment content are preserved), and `strip_invalid_value` matches the
    attribute name with a `(?<![\w:.-])value` lookbehind so `data-value` survives.
  * **Lossy strips** (`--strip-pagination`, `--strip-broken-tags`,
    `--strip-watermarks`; pagination.py, watermark.py; `--strip-stub-docs`,
    epub.py's `detect_stub_docs`): remove only what a converter injected (page numbers,
    running headers, leaked tags, watermarks, repeated placeholder chapters), fenced behind
    character conservation, tag
    balance, and the epubcheck no-regression bar. The watermark anchored pass's
    whole-match fallback only fires when the match holds nothing but the stamp; a
    larger match (an unclosed stamp anchor that swallowed prose) is refused, counted
    in `RepairReport.watermark_refusals`, and surfaced as a `manual_watermark_repair`
    decision by `run phase1` on both the read-only and apply paths. `--strip-stub-docs`
    (2026-09-15) mirrors emptytext's placeholder signals: identical short text across
    >= 3 spine docs and >= 30% of the spine; the whole-spine-stubs book is refused
    (EMPTY, re-source), and the drop cascades to archive entries, manifest, spine,
    NCX navPoints, and nav toc entries.
  Do not let any NEW fix touch content without its own flag; if a candidate repair cannot
  be made deterministically safe, it does not belong here: report it for manual repair
  instead.
- **The gate is the safety contract.** Never apply a repair epubcheck has not accepted.
  Respect the two-mode logic in `validate.gate` (fatal-fixing tolerates error unmasking;
  error-cleanup does not). The lossy strips (`--strip-pagination`, `--strip-broken-tags`,
  `--strip-watermarks`) are accepted by `validate.no_worse` instead (their gain is
  invisible to epubcheck, so it only forbids a regression, never demands a measured
  improvement). The structural opt-ins go through the normal `gate`: their gain IS
  visible (they clear errors), so a run with no measurable improvement is a noop and
  nothing is applied. Changing either bar means re-running the library dry run.
- **Library writes are sacred.** Replacement must stay atomic (temp in same dir, then
  `os.replace`), touch only the `.epub`, preserve mode, and be dry-run by default.
  Calibre format installation goes through cquarry's write module (`--install-to-calibre`;
  the calibredb subprocess was retired in v0.24.0). Never write to the library without
  `--apply`. Test every change on `/tmp` copies first.

## Layout

- `src/bindery/transforms.py`: pure `str -> (str, int)` text transforms (including `strip_broken_tags`). Since v0.32.0: `strip_attrs_in_start_tags` anchors attribute edits on the quote-aware start-tag matcher, `_outside_protected` forwards arguments (context-carrying transforms can be decorated), `fix_id_colons` matches only the bare `id` attribute and only internal fragments (`fix_ncx_src_fragments` carries renames into the NCX), `fix_ncx_playorder` is anchored to `<navPoint>` start tags, and the css selector boundary covers namespaced and functional selector forms. HAZARD (the 0.41.0 spin): the quote-aware tag-prefix alternation (double-quoted | single-quoted | [^>]) OVERLAPS ([^>] matches quotes too), so a plain `*` re-partitions exponentially on failing candidates; every such greedy loop must carry the possessive `*+` (re 3.11+, the plugin's minimum Calibre). Since v0.42.0 the census is complete and pinned by tests/test_matcher_hardening.py: every greedy loop of this family is possessive (the three 0.41.1 rewrites plus _START_TAG_RE, _COVER_META_RE, _GUIDE_REF_RE, prune_dangling_edges' spine|item|itemref matcher, _ITEM_TAG_RE, _SPINE_ITEM_RE -- possessive is byte-identical on success because the loop is always followed by a literal `>` the [^>] branch cannot consume), and the five LAZY siblings (_VOID_RE, the transforms <img> matcher, and epub's link/a/img matchers) use the disjoint catch-all `[^>"']` instead: mutually exclusive branches cannot re-partition, and an unterminated quote inside a tag now fails to match rather than pairing across markup. NEW tag matchers: quote-aware possessive for greedy loops, the disjoint catch-all for lazy ones, and the tempered-quote idiom (["'])((?:(?!\1).)*)\1 for attribute values. Since v0.37.0: `fix_ncx_playorder` strips quotes before its already-correct comparison (regex groups carry quote characters; comparing quoted values against bare numbers counted phantom fixes on every sequential NCX and rewrote single-quoted correct attributes to double quotes).
- `src/bindery/pagination.py`: the opt-in lossy page-number strip (runhead detection, page-layer decision, block-centric removal/merge, safety nets).
- `src/bindery/watermark.py`: the opt-in lossy watermark strip (anchored and anchorless signature removal).
- `src/bindery/reserialize.py`: structural repair via `html5lib`.
- `src/bindery/audit.py`: read-only body-text audits (`content`, `pagenumbers`, `emptytext`, `ocr`, `monolithic` since v0.21.0 with `--max-doc-chars N`, and `completeness` since v0.36.0) behind the `audit` subcommand (v0.15.0). Since v0.18.0 library mode resolves EPUB paths through `cquarry.get_format_path()` and can apply a tag to flagged books via `cquarry.write.WritableCalibreDB` (only with explicit `--tag`; the only sanctioned write path). Since v0.19.0 `audit --id BOOK_ID` audits a single library book through cquarry's single-entity `get_book()` fetch (no library-wide cache; supports `--tag`; incompatible with the directory argument; comma lists since v0.23.0). Since v0.22.0 every archive entry is fully read (CRC + decompression) before analysis (a damaged entry reports its own CORRUPT verdict instead of feeding emptytext) and manifest/NCX references to absent files classify as `convention` (bloated ToC, consecutive span) or `fragment` (broken span). Hazard: the scan loop reuses `tag` as its per-book display column, so the `--tag` argument is captured as `audit_tag` at function entry; do not collapse them again. Since v0.29.0 `audit --json FILE` writes per-file analyzer verdicts in the `library --json` shape (directory, library, and single-book modes; with `--id`, exactly one id; emptytext omitted from a record when the archive verdict owns the body-text story). Since v0.44.0 two more advisory analyzers ride the single pass: `cover` (the EPUB3 `properties~="cover-image"` slice of the ruled hybrid: dangling EPUB2 meta, EPUB3 declaration, cover-file presence) and `tocdrift` (the NCX navMap vs EPUB3 nav toc structural diff, fragments included; audit-only, ToC synthesis out of charter permanently); the loader now exposes the parsed OPF, the NCX text, and the nav HTML on `Book`. Since v0.36.0 the completeness analyzer (Phase 15) reports the phase-1 spot-check per book: spine/prose-doc counts (prose = >= 400 visible chars), first/middle/last prose-doc opening/closing excerpts, trailing-ToC classification (`_trailing_toc`: short link lines dominate, no paragraph-length blocks), and the unreadable fraction. It is ADVISORY by contract (problem always False; ADVISORY on a trailing ToC or unreadable fraction >= 10%), never moves the exit code, and is skipped like emptytext when the archive verdict owns the book. Since v0.38.0 the archive verdict distinguishes font obfuscation from DRM (`OBFUSCATION_ALGOS`: IDPF 2008/embedding + both Adobe URIs; readable obfuscated entries are the benign OBFUSCATED advisory, an unreadable one is CORRUPT, and only non-obfuscation algorithms give ENCRYPTED). Since v0.43.0 untrusted metadata XML is parsed through `_safe_xml_parse` (DOCTYPE/ENTITY declarations refused before xml.etree sees them; the stdlib shape of defusedxml's entity protection) and single entries above `_MAX_ENTRY_BYTES` (32 MB) are refused before decompression; a refused metadata file returns the shell book. Since v0.40.0 the vir-tui console renderer loads through the module-level `_LazyUI` proxy (a plain `import vir_tui as ui` at module level would make 3.12/3.13 installs unimportable now that the stack is marker-gated; LOAD_GLOBAL never consults module `__getattr__`, so a proxy object is the lazy shape that works).
- `src/bindery/epub.py`: archive rewrite, NCX uid sync, RepairReport, mismatch detection, and the opt-in structural-repair plumbing (including the CSS precondition scan). Since v0.38.0/v0.39.0 (Phase 16): `generate_container` + the `--fix-container` gateway (insert after mimetype when missing, replace in-loop when stale; constant-epoch timestamp keeps bytes deterministic), `prune_dangling_edges` (prune_missing_manifest_items returns the pruned ids and the edges that pointed at them (spine@toc, media-overlay, fallback, EPUB2 cover meta) are rewritten so pruning cannot manufacture the regression the gate rejects), and `fix_manifest_media_types` (extension map + magic-byte confirmation via a `peek` callable over the open source zip; jpg/png/gif only), and `fix_cover_meta` (the ruling's deterministic half: dangling EPUB2 cover meta re-pointed from the guide's cover reference when exactly one manifest item carries that file, removed otherwise; accepted under no_worse like the lossy strips because cover wiring is invisible to epubcheck).
- `src/bindery/validate.py`: epubcheck wrapper, the `gate` (improvement) and `no_worse` (no-regression, for the lossy strips) acceptance bars.
- `src/bindery/library.py`: Calibre walk, atomic replace, backups, and native format installation. Since v0.19.0 `CalibreIdResolver` resolves the book id from `metadata.db` through cquarry (one lazy path→id map per run; since v0.20.0 the map comes from `CalibreDB.format_path_index()`, re-normalized with `resolve().lower()` for the resolver's symlinked-directory and case-insensitive matching); the `(id)` directory-name regex is only the no-catalog fallback. Since v0.24.0 `install_format()` places the repaired file atomically and updates the `data` row through `cquarry.write.WritableCalibreDB` (since v0.31.0 via `set_format`, cquarry 1.17's sanctioned remove+add in one transaction); the external `calibredb` CLI is gone. Since v0.32.0 a guessed id drives a row update only when metadata.db corroborates it (`_verify_guess`: books row exists, file in the book's own directory, catalogued `data.name` when a row exists); anything else saves in place and leaves the catalog untouched: never reintroduce directory-name guessing as the primary source: renamed/mismatched directories would replace the wrong book. Since 2026-09-15 `make_backup` takes the opt-in `--backup-keep N` ring: at most N backup files per book, the author original `.bak` never deleted, newest content at the highest name; default remains an unbounded rotation.
- `src/bindery/cli.py`: `repair` and `library` subcommands, including `--all` and `--install-to-calibre`; `repair --json` emits the library per-book record vocabulary (2026-09-15; since v0.45.0 every per-book record, the phase1 payload included, also carries the structured fix breakdown: the `fixes` dict plus `ncx_uid_synced`/`watermark_refusals` off the `Outcome`, and `_phase1_decisions` reads that data instead of substring-matching the rendered `summary`, which stays for human eyes; CalibreQuarry's lossy-consent mirror is the waiting consumer, floor `bindery>=0.45.0`); since v0.29.0 also the `run` verbs: `phase1` (pre-import vetting over loose files: the audit battery composed with one gated repair sweep; read-only until `--apply-lossy`, which IS the lossy-strip consent) and `phase3` (the scoped post-import apply step: `library --id --sweep --only all --apply --all --install-to-calibre` plus a pre/post summary; mechanically refuses unscoped sweeps with exit 2; since 2026-09-15 the summary sums applied/equal states only and carries rejected projections as `rejected_projection`). Plus `bindery doctor` (2026-09-15): the environment self-check that imports none of the VirInvictus stack, never raises, and always exits 0. Both run verbs take `--non-interactive` and surface open questions as `decisions_needed` in their JSON, never prompts. They drive the shipped `run_library` through the real parser, so no flag can drift between the verb and the subcommand it wraps. `library --workers N` (v0.30.0) parallelizes only the sweep's candidate pass, in windows of N consumed in order so `--limit` stays lazy; the repair phase is never parallel (shared workdir, atomic-replacement contract). Since v0.40.0 `main()` guards the VirInvictus stack: a `ModuleNotFoundError` naming `vir_tui`/`cquarry` (marker-gated to 3.14+ on the 3.12 floor) becomes one stderr line and trouble exit 2 instead of a traceback; anything else missing still raises.
- `plugin/__init__.py` + `scripts/build_plugin.py`: the Bindery Repair Calibre plugin (v0.37.0; identity `Bindery Repair` / `bindery_repair`, approved and built per the scoped spec in roadmap.md Phase 3). The builder vendors `transforms.py`, `epub.py`, `pagination.py`, `watermark.py`, `reserialize.py` byte-identically into the zip (the zip root is a package, so their relative imports resolve unchanged; the suite's drift test pins the equality) with the version tuple substituted from the single-source VERSION; `publish.yml` attaches `BinderyRepair-v<VERSION>.zip` to each release. The plugin runs ONLY the default pass (epubcheck cannot gate inside Calibre), never raises, is byte-idempotent (zero fixes returns the original path), testzip()-verifies its output, logs one line per book, and takes JSON config via `site_customization` (`log`, `log_path`, `max_size_mb` default 150, `epubcheck_path` experimental validation default OFF). Load it in tests through the `tests/test_plugin.py` stubs: the calibre namespace is installed in sys.modules and the package loads with a two-dir `__path__` (plugin dir first, then src/bindery) exactly like calibre's zipplugin loader shape. **Plugin/interpreter compatibility (v0.40.0):** v0.39.0 shipped PEP 758 unparenthesized except tuples, and the plugin died with a bare SyntaxError on every released Calibre (7.x and the whole 8.x series embed Python 3.11, 9.0+ embeds 3.14; from calibre's bypy sources.json, checked against the upstream clone). The core is now grammar-pinned: parenthesized except tuples everywhere, `ruff`'s `target-version = "py312"` pin (inference from requires-python would rewrite the parens back off), `tests/test_version.py`'s parse guard, and CI's `plugin-compat` job byte-compiling the built zip under 3.11/3.12/3.13/3.14. `minimum_calibre_version` is (7, 0, 0): the oldest series whose interpreter CI covers, replacing the hollow (2, 0, 0); `supported_platforms` stays `["linux"]` (developed and tested on Linux only). Any new syntax in the vendored modules must clear the guard before it ships.
- `tests/`: transforms, end-to-end repair, atomic replace, pagination, watermarks, the audit analyzers (content/pagenumbers/emptytext/ocr/monolithic/completeness, archive integrity, spine classification), the library sweep and `CalibreIdResolver`, validate, reserialize, CLI wiring, the version pin and the syntax-floor grammar guard, and the Calibre plugin (build drift + run behavior under calibre stubs).

## Conventions

- Type hints, `from __future__ import annotations`, ruff for lint and format.
- `./run_tests.sh` runs the suite in the project venv. Since the 3.12 floor, `uv run --python X` REBUILDS `.venv` for interpreter X, and the `python_version >= '3.14'` markers then gate vir-tui/cquarry OUT of it below 3.14 (the suite goes red with ModuleNotFoundError: cquarry). After interpreter experiments, rebuild with `uv run --python 3.14` before running the suite again.
- `VERSION` lives in `src/bindery/__init__.py`, mirrored in `pyproject.toml`. Bump both.
- Run tests with `./run_tests.sh`.

## Validation workflow

The library is real data. The loop is always: dry run on `/tmp` copies, inspect the
report, then apply with backups. epubcheck is the oracle; a repaired book that still has
fatals is `partial` and must be left for manual work, never auto-applied.
