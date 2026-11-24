# AGENTS.md (bindery-cli)

Per-project guidance. Overrides the global file where they conflict.

This file is for human programmers and AI agents alike. It carries rules, decisions,
and constraints, stated in the present tense. How a rule came to be, and the specimen
behind it, is in `patchnotes.md` under the release that shipped it. The behavior each
rule protects is specified in `spec.md`.

## What this is

A focused EPUB repair and diagnostic tool: deterministic well-formedness fixes, gated
by epubcheck, with atomic in-place replacement in a Calibre library. It absorbed the
retired oceanstrip tool as the lossy `--strip-watermarks` flag; any instruction that
runs `python -m oceanstrip` is dead.

## Docs layout (decided 2026-11-24)

- **README.md** is all user-facing documentation: the manual a Calibre user reads. It
  is the only home for behavior prose aimed at users. No version numbers, issue
  numbers, or internal history in it.
- **docs/repairs.md** is generated from `_REPAIR_FLAG_TABLE` in `src/bindery/cli.py`
  by `scripts/render_repairs_doc.py`, and pinned to the live table by
  `tests/test_repairs_doc.py`. It is the same text `--help-repairs` prints. Never
  edit it by hand. Edit the table, then re-run the script, in any change that touches
  a flag's short or long text.
- **docs/audits.md** and **docs/plugin.md** are hand-written reference detail.
- **spec.md** is the contract: present-tense behavior, never stale. A behavior change
  that ships without its spec line is an incomplete change.
- **roadmap.md** is the plan plus the shipped ledger, collapsed every pass (the global
  rule). Its Open section is the only living part. A release is not a phase and earns
  no ledger row.
- **patchnotes.md** is the versioned record: one entry per release, tied one-to-one to
  its annotated tag (the tag message is the entry), so a published entry is frozen.
- **docs/research/** keeps dated research reports that other docs cite (evidence
  archives, not living documentation).
- **AGENTS.md** (this file) carries programmer-facing rules, decisions, and
  constraints only. Version narratives do not belong here either.
- **CONTRIBUTING.md** is the short contributor guide: setup, where the rules
  live, how to report a bug. It states no rule that spec.md and this file do
  not carry, so when a rule changes, those two change first.

When the code and a documented rule disagree, the documentation states what the code
does and the gap gets an issue. Never leave a rule in the docs that the code does not
implement.

## Hard constraints

- **Minimal dependencies.** Runtime deps are exactly `tqdm` (progress), `vir-tui`
  (shared TUI rendering, `>=2.5.0`) and `cquarry` (read-only Calibre `metadata.db`
  access, `>=1.19.0`). The last two carry `; python_version >= '3.14'` markers because
  every release they publish requires 3.14. The package floor is 3.12: a 3.12/3.13
  install runs the stack-free repair core, and the audit, library, and run surfaces
  degrade through `cli.py`'s dispatch guard instead of failing resolution. `html5lib`
  is the one approved heavy-parsing exception, imported lazily and used only by
  `--reserialize`. Tests use `unittest`. epubcheck is an external CLI expected on
  `PATH`. `--install-to-calibre` needs no external binary; it goes through cquarry's
  write module. Before adding any further Python package, stop and ask.
- **CLI surface contract.** bindery is the reference implementation of the workspace
  CLI machine-surface contract (`~/.gitrepos/CLAUDE.md`, Throughlines). Machine help
  carries exit contracts and gate annotations (`--help-json`). Errors are one clean
  line on stderr, never a traceback. Missing-input paths are usage-class errors on
  stderr. User-facing surfaces are additive-only after 1.0 (spec-stated).
- **Semantics-preserving by default; everything else behind a flag.** The always-on
  core is exactly five well-formedness fixes (prolog junk, duplicate `xmlns`, bare
  `&`, named entities, void self-closing), the NCX pipeline, and the mimetype fix.
  Every core fix must render identically to the author's intent: never add, remove,
  or reorder visible content. Everything else is opt-in (35 structural repairs, four
  lossy strips, five safe opt-ins, plus `--reserialize`; the inventory is `_REPAIR_FLAG_TABLE`
  and spec.md). A new fix never touches content without its own flag. If a candidate
  repair cannot be made deterministically safe, it does not belong here: report it
  for manual repair instead.
- **The gate is the safety contract.** Never apply a repair epubcheck has not
  accepted. Respect the two-mode logic in `validate.gate` (fatal-fixing tolerates
  error unmasking; error-cleanup does not). The lossy strips, `--fix-cover`,
  `--encode-url-spaces`, and `--fix-media-types` are accepted by `validate.no_worse`
  or its promotion, because their gain is invisible to epubcheck, so it only forbids
  a regression. `--encode-ref-delims` rides its own keyed bar. Changing any bar, or
  the veto, means re-running the library dry run.
- **Library writes are sacred.** Replacement stays atomic (a temp file in the same
  directory, then `os.replace`), touches only the `.epub`, preserves mode, and is
  dry-run by default. Never write to the library without `--apply`. Test every
  change on `/tmp` copies first.

## Working invariants

The rules that bite. Each states the rule and the one reason behind it.

### Text matching (transforms.py, epub.py)

- **Every greedy loop in a quote-aware tag matcher is possessive (`*+`).** The
  alternation (`"..." | '...' | [^>]`) overlaps, because `[^>]` matches quotes too,
  so a plain `*` re-partitions exponentially on a failing candidate. This is why `re`
  3.11+ is the floor. Lazy siblings use the disjoint catch-all `[^>"']`, so an
  unterminated quote fails to match rather than pairing across markup. Attribute
  values use the tempered-quote idiom `(["'])((?:(?!\1).)*)\1`.
  `tests/test_matcher_hardening.py` pins the census of every site; a new matcher
  joins it.
- **An attribute matcher must not match inside a longer name.** Use the
  `(?<![\w:.-])attr=` lookbehind or a whitespace anchor. `data-id`, `data-value`,
  `data-class`, `data-fallback`, `data-toc`, `data-content`, `data-name`,
  `data-version`, and `data-type` have each been mistaken for the real attribute.
- **Anchor id sets include `xml:id`.** `ids_by_doc` collects through
  `_ANCHOR_ID_RE`, and the id matchers (`_ITEM_ID_RE`, `_XML_ID_RE`,
  `_SVG_ID_ATTR_RE`) carry the `(?<![\w:.-])` lookbehind. epubcheck resolves
  fragments against `xml:id`, so dropping it from the anchor set would strip links
  the oracle never flagged. A `data-id` is not an id: counting one renamed real ids
  away from their fragments. OPF edge attributes (`fallback`, `media-overlay`,
  `spine toc`, the cover `meta content`) are never namespaced, so their matchers are
  unprefixed-only.
- **CSS comment spans are whole-comment intersections.** `_CSS_COMMENT_SPAN_RE`
  spans are intersected over the whole match. A `}` inside a comment truncates a
  flat-brace match, and a start-keyed drop would leave comment garbage in the
  stylesheet.
- **Rewrite only inside real start tags and processing instructions.** Comments,
  CDATA, and display text are never touched. A book that teaches HTML carries
  `href="..."` in its prose, and a bare attribute regex pairs the phantom quote with
  the next real tag. `transforms._outside_protected_map` is the wrapper and
  `_START_TAG_OR_PI_RE` the matcher.
- **CDATA and comments are never rewritten**, except comment bodies under the opt-in
  `--fix-comment-double-hyphen`. `_PROTECTED_RE` protects the Calibre
  `/*<![CDATA[*/ ... /*]]>*/` wrapper as one unit: a bare CDATA span ends inside the
  closing `/*]]>`, which exposes the trailing `*/` to the prolog strip.
- **Decode before you resolve.** `_resolve_href` decodes the five predefined XML
  entities and numeric character references, then splits the fragment. `_norm_fragment`
  decodes a fragment the same way before any anchor-cleaner existence check.
  Attribute text arrives escaped and archive entries are raw.
- **Package matchers accept a namespace prefix.** `<opf:item>`, `<opf:package>`, and
  `<ncx:meta>` are legal spellings, and a bare `<package` or `</manifest>` literal
  reads a prefixed file as absent. `_MANIFEST_CLOSE_RE` handles prefixed and
  whitespace close tags. `package_version()` reads a prefixed package element.
- **OPF and NCX passes run in a fixed order, and later passes read the text as it
  stands.** The manifest prune derives its spine protection from the text it prunes,
  never from a snapshot taken before `fix_manifest_ids` renames ids ahead of it. The
  anchor pass runs last, against an id snapshot that replicates each id-moving fix.
  `fix_ncx_playorder` is contract-gated and re-runs after any NCX structural change
  (the fragment strip, the dangling-target prune, the dead-spine prune). It compares
  values with quotes stripped, because regex groups carry the quote characters.
- **Existence checks consult the layout the text is written in.** With
  `--encode-url-spaces` active, a raw-space href whose file exists in the source is
  rename material, never prune material: the manifest prune and the dead-spine count
  read `present_src`, and the rename and encode passes keep the full sets. When
  `--strip-stub-docs` drops documents, the prune's existence set (`anchor_present`)
  excludes them, so `--prune-missing-resources` strips links into dropped stubs
  instead of leaving them dangling.
- **A scope walk aborts rather than guesses.** `strip_ncx_dangling_targets` is
  node-exact over a balanced stack scanner. An unbalanced walk returns its input
  unchanged, and a prune that would empty a container refuses, because an empty
  navMap is itself an RSC-005. `_scan_doctype_span` refuses an unterminated
  declaration.

### Lossy strips

- `--strip-pagination` needs four nets to pass on every edit: character
  conservation; `p`/`a` balance plus the all-tag delta net (`_tag_deltas` compares
  per-element deltas, not absolute counts); prose flow (`_in_prose_flow`,
  `_flat_run`); and the `no_worse` gate. A self-closing start tag never opens a
  block. Only `id` and `xml:id` are navigation anchors; `data-id` is not.
  `collect_internal_numbering` flags any document whose standalone-number values are
  25% or more shared with other documents, and the strip takes no numbers from a
  flagged document, because stanza and section numbering is text-shape-identical to
  page numbers. epub.py passes the per-document verdict through a `content_idx`
  counter aligned with the `htmls` list.
- `--strip-watermarks` deletes the outermost wrapper whose entire visible text is
  the stamp. An inline stamp link with no wrapper is deleted only under the
  fallback's two conditions: it is the only `<a>` in the match and its body is
  tag-free text of at most 120 normalized characters, or its visible text is exactly
  the watermark. Anything larger is refused, counted in
  `RepairReport.watermark_refusals`, and surfaced by `run phase1` as a
  `manual_watermark_repair` decision. The first condition is looser than it sounds
  (see #40 under Known limitations).
- `--strip-stub-docs` keys on identical body text across at least three documents
  and 30% of the spine, pooled from the `<body>` span. A page carrying an `<img>` or
  `<image>` is exempt whatever short text it holds. A book whose whole spine is the
  stub is refused: it is empty and needs a re-source. The drop cascades through
  archive entries, manifest, spine, NCX navPoints, nav toc entries, and the OPF
  guide references (`prune_guide_refs_to`, wired on all three prune cascades).

### The gate (validate.py, cli.py `process_book`)

- The spine-resolution veto rides above every verdict path, promotions included.
  Arm one: a candidate whose dangle count rises is `reject` with `(spine collapse)`.
  Arm two: a `dead_spine_items_pruned` candidate resolving less than half the
  documents the source resolved is `reject` with `(spine halved)`. The comparison is
  strict and counts documents that resolve on both sides. `spine_resolution` returns
  None on an unreadable package: the veto needs evidence and never fires on a probe
  failure.
- Masked-axis rule: a gate `noop` on a book that started and stayed fatal is
  reclassified `partial`, because epubcheck's per-file halt hides whatever the fix
  cleared below it. Application behavior is unchanged, and clean books are untouched.
- `refs_delim_encoded` promotes through the missing-file mask bar: net-new fatals
  reject, a still-fatal candidate stays partial, and a fatal-free candidate accepts
  whatever the unmasking did to the error count. The veto still overrides.
- The no-worse promotion keys on fired counters, not on flags. In a candidate where
  one of those fixes fired, every fix in the candidate is judged by the no-worse bar.
- `oracle_timeout_for` scales the budget on decimal MB and on archive entry count:
  a 300 s base, plus 60 s per started 10 MB above 10 MB, plus 60 s per started 500
  entries above 1,000, capped at 1,800 s. One subprocess retry follows a timeout.
  Only a double timeout raises `OracleTimeout`, which reads `(epubcheck timeout)` and
  keeps the book a candidate.

### Archive writing (epub.py)

- **Every `zout.writestr` gets a private `ZipInfo`** (a `copy.copy` of the source's,
  a fresh one on a rename). `writestr` stamps the info it is handed with the
  destination's offsets. A shared info repoints the open source archive's name
  lookups into the output file mid-loop, and one drifted probe reads whole books as
  unreadable. The media-type `peek` catches `BadZipFile` too.
- `mimetype` is written first and stored, with the constant-epoch timestamp and a
  fresh `ZipInfo`. Repairing a book twice must produce identical bytes.
- A document that is not valid UTF-8 is copied byte-for-byte and counted
  (`non_utf8_docs_skipped`), never decoded with replacement. The same guard covers
  the stylesheet branches that the reference encodes select.

### Audit (audit.py)

- Audit is read-only. The only write path is `--tag`, through
  `cquarry.write.WritableCalibreDB`, and it is lock-class (refused with exit 1 while
  Calibre runs). The scan loop reuses `tag` as its per-book display column, so the
  argument is captured as `audit_tag` at function entry. Do not collapse them.
- `run_directory` and `run_single` dispatch analyzers through `_dir_dispatch`, one
  entry per analyzer in `ALL`. `tests/test_contamination.py` pins the key set.
- Advisory analyzers (`completeness`, `cover`, `tocdrift`, `contamination`, `shell`)
  never flag a book and never move the exit code.
- Untrusted metadata XML goes through `_safe_xml_parse`: DOCTYPE and ENTITY
  declarations are refused before `xml.etree` sees them, and a refused metadata file
  yields the shell book. A single entry above `_MAX_ENTRY_BYTES` (32 MiB) is refused
  before decompression. `Book.extra_docs` retains non-spine html under a 64 MiB cap.
- `analyze_cover` resolves manifest hrefs against the OPF's directory
  (`Book.opf_path`), not the archive root.
- The console renderer loads through the module-level `_LazyUI` proxy. A plain
  `import vir_tui` at module level would make 3.12/3.13 installs unimportable, and
  `LOAD_GLOBAL` never consults a module `__getattr__`, so a proxy object is the lazy
  shape that works.

### CLI (cli.py)

- `_REPAIR_FLAG_TABLE` is the one flag inventory, with three renderings: the compact
  `--help` groups, the `--help-repairs` page, and `docs/repairs.md`. Long text is
  plain sentences with backticked code and blank lines between paragraphs. It is
  printed in a terminal and rendered as markdown, so it carries no em-dashes, no
  double-hyphen stand-ins, and no version or issue numbers. The preamble and the
  acceptance section live in the `_REPAIRS_DOC_*` and `_REPAIR_REFERENCE_PREAMBLE`
  constants beside it.
- **`RepairFlags` is immutable in a caller's hands.** The all-off instance is the
  default pass, and a bare `repair_epub(src, dst)` builds exactly that. The EPUB 2
  version gate swaps in a `dataclasses.replace` copy and never mutates the caller's
  instance (`tests/test_epub.py`, `TestVersionGateKeepsFlagsIntact`).
- `_flags_from_args` is the one place argparse names map onto `RepairFlags` fields
  (each `or --all`). The run verbs drive the shipped `run_library` through the real
  parser, so no flag can drift between a verb and the command it wraps.
- `--help-repairs` is a custom argparse Action, deliberately not `store_true`, so the
  wiring test's selection count ignores it. Its colors come from argparse's own
  `_colorize.can_colorize` detection, so the two help levels always agree. Colored
  output with codes stripped must equal plain output byte for byte.
- `_require_closed_calibre` (pgrep, fail-closed, exit 1) guards `audit --tag` and
  `library --apply --install-to-calibre`. `run phase3` reaches the second
  transitively.
- The single-file verb without an output path is a probe: the gate runs and nothing
  is written. Writing needs an explicit DST.
- `library --workers N` parallelizes only the sweep's candidate pass, in windows of
  N consumed in order so `--limit` stays lazy. The repair phase is never parallel:
  it owns the shared workdir and the atomic-replacement contract.
- `main()` turns a missing `vir_tui` or `cquarry` into one stderr line and exit 2.
  Anything else missing still raises.
- `_phase1_decisions` reads the structured record fields (`fixes`,
  `ncx_uid_synced`, `watermark_refusals`), never the rendered `summary`.
  CalibreQuarry's lossy-consent mirror reads the same fields, so the fix-breakdown
  keys are contract.
- `run phase3`'s summary sums real post-run states only: applied repairs at their
  after counts, unapplied books at their before counts. A refused candidate's
  projected after-state is its own labeled `rejected_projection` line, never summed
  into the totals.

### Library replacement (library.py)

- The book id comes from `metadata.db` through one lazy path-to-id map per run
  (`CalibreDB.format_path_index()`, re-normalized with `resolve().lower()`). The
  `(id)` directory-name regex is only the no-catalog fallback. Never reintroduce
  directory-name guessing as the primary source: a renamed directory would replace
  the wrong book. A guessed id drives a row update only when `_verify_guess` finds
  the book row, the file in the book's own directory, and the catalogued
  `data.name`.
- `make_backup` never overwrites the first backup, which is the only copy of the
  author original. Later ones rotate `.bak2`, `.bak3`, and so on. `--backup-keep N`
  (minimum 2) bounds the rotation and never deletes the original `.bak`.

### Plugin compatibility (plugin/, scripts/build_plugin.py)

- The vendored modules (`transforms.py`, `epub.py`, `pagination.py`,
  `watermark.py`, `reserialize.py`) are byte-identical to `src/bindery/`, and a
  drift test pins the equality. The zip root is a package, so their relative imports
  resolve unchanged.
- **Grammar floor: everything Calibre embeds must parse these modules.** Calibre 7.x
  and 8.x embed Python 3.11, and 9.0+ embeds 3.14. So: parenthesized `except (A, B):`
  tuples everywhere (PEP 758's bare form is a `SyntaxError` on 3.11); ruff's
  `target-version = "py312"` pin (inference from requires-python would rewrite the
  parens back off); the parse guard in `tests/test_version.py`; and CI's
  `plugin-compat` job byte-compiling the built zip under 3.11 to 3.14. Any new
  syntax in a vendored module clears that job before it ships.
- `minimum_calibre_version` is `(7, 0, 0)` and `supported_platforms` is
  `["linux"]`. Both state what is verified, no more.
- The plugin runs only the default pass, never raises, is byte-idempotent, and
  `testzip()`-verifies its output. Load it in tests through the
  `tests/test_plugin.py` stubs (the `calibre` namespace in `sys.modules`, a two-dir
  `__path__`), which is the shape of Calibre's zipplugin loader.

## Known limitations

Open defects where the code does less than its rule promises. The documentation
states the real behavior; remove a line here, and its "known limitation" sentence in
the flag table and spec, when the fix lands.

- **#39 `--fix-double-colon-tags`** can escape the next real tag. When the
  pseudo-tag's own closing bracket is entity-escaped, the match's tail runs to the
  next raw `>`. The result parses, so the gate can accept it.
- **#40 `--strip-watermarks`** deletes an inline stamp link whenever its body is
  tag-free and at most 120 characters, whatever the text says.
- **#41 `--fix-missing-title`** fills an empty SVG `<title>` in a document that has
  no `<head>` (the head scoping only applies when a head exists).
- **#42 `repair SRC DST`** writes the output for an `equal` result too. `run_repair`
  has no `equal` branch.

## Layout

| Path | Owns |
|---|---|
| `src/bindery/transforms.py` | Pure `str -> (str, int)` text transforms, the regex matchers, and the matcher hardening rules |
| `src/bindery/epub.py` | `RepairFlags`, `RepairReport`, the archive rewrite, the NCX pipeline, `spine_resolution`, and every opt-in repair's package-level plumbing |
| `src/bindery/pagination.py` | The page-number strip, and the canonical roman and number grammar (audit imports from it; it vendors into the plugin) |
| `src/bindery/watermark.py` | The watermark strip |
| `src/bindery/reserialize.py` | The html5lib rebuild |
| `src/bindery/validate.py` | The epubcheck wrapper, `gate`, `no_worse`, `oracle_timeout_for`, the daemon |
| `src/bindery/library.py` | The Calibre walk, `atomic_replace`, `make_backup`, `CalibreIdResolver`, `install_format` |
| `src/bindery/audit.py` | The ten read-only analyzers and the `_LazyUI` console proxy |
| `src/bindery/observe.py` | The whole-library census harness and the `.observe` artifact |
| `src/bindery/cli.py` | The parser, `_REPAIR_FLAG_TABLE`, the renderers, the exit contracts, `process_book`, the run verbs |
| `plugin/__init__.py`, `scripts/build_plugin.py` | The Bindery Repair Calibre plugin and its builder |
| `scripts/render_repairs_doc.py` | Regenerates `docs/repairs.md` |
| `scripts/fast_sweep.py`, `scripts/FastSweep.java` | The standalone parallel epubcheck sweep |

## Tests

`./run_tests.sh` runs ruff (CI's pin) and the `unittest` suite. The suite builds its
own synthetic EPUBs and needs no Calibre library.

| Module | Pins |
|---|---|
| `test_transforms.py` | Every text transform, the shared duplicate-id rename, the playOrder contract |
| `test_matcher_hardening.py` | The possessive and disjoint catch-all rules, with alarm-guarded spin shapes |
| `test_epub.py` | End-to-end repairs per flag, the prune cascades, the encodes, the declarer, `RepairFlags` caller immutability |
| `test_flags_wiring.py` | The flag inventory reflected against argparse dests and `_flags_from_args` |
| `test_flags_real_core.py` | Every flag driven from argv through the unmocked core (`--no-validate`) |
| `test_spine_guard.py` | The spine-resolution veto's arms and the keyed promotions |
| `test_validate.py` | `gate`, `no_worse`, the oracle budget, retry then `OracleTimeout` |
| `test_pagination.py`, `test_watermark.py`, `test_reserialize.py` | The lossy strips' nets and refusals; the html5lib rebuild |
| `test_library.py` | Atomic replace, backups, `CalibreIdResolver` |
| `test_cli.py` | The parser surface, the two help levels, the JSON records, exit codes |
| `test_help.py` | The closed-Calibre doors and `--help-json` |
| `test_observe.py` | The `.observe` artifact schema and trend blocks |
| `test_audit.py`, `test_contamination.py`, `test_cover_tocdrift.py`, `test_comment_hyphens.py`, `test_space_renames.py`, `test_xml_hardening.py` | The analyzers, the comment fix, the space renames, the XML hardening |
| `test_plugin.py` | The plugin build's byte-equality drift and its run behavior under Calibre stubs |
| `test_version.py` | The version carriers, the syntax-floor grammar guard, the newest patchnotes heading |
| `test_repairs_doc.py` | `docs/repairs.md` equals the live flag table |

CI runs three legs: `test` on 3.14 (the whole suite), `core-compat` on 3.12 and 3.13
(a hand-listed set of stack-free modules in `ci.yml`; a new stack-free test module
joins that list), and `plugin-compat` (the built zip byte-compiled under 3.11 to
3.14). The suite count goes into the patchnotes entry as `Suite: N tests.`,
written last, after the final count is known.

### Environment traps

- `uv run --python X` rebuilds `.venv` for interpreter X, and the version markers
  then gate `vir-tui` and `cquarry` out of it below 3.14. After an interpreter
  experiment, rebuild with `uv run --python 3.14` before running the suite.
- A plain `uv sync` strips the `reserialize` extra. Use `uv sync --extra
  reserialize`.
- `uv.lock`'s root version lags a bump until the next `uv run` or `uv lock`.
- A bare `git checkout -- <file>` in a tool call discards every uncommitted edit to
  that file, including your own from earlier in the session. Commit or stash work
  in progress before any command that resets a path.

### Adding a flag

1. Add the transform and its `RepairFlags` field, and thread it through
   `repair_epub`.
2. Add the `_REPAIR_FLAG_TABLE` row, then run
   `.venv/bin/python scripts/render_repairs_doc.py`.
3. Add the `_flags_from_args` line. The wiring test fails until you do.
4. Add the spec.md entry and a test in the module that owns the behavior, plus a
   row and fixture in `test_flags_real_core.py` when the flag changes bytes.
5. If the flag needs a gate promotion, add it beside the existing ones and pin it in
   `test_spine_guard.py`.

## Conventions

- Python style follows the global conventions: type hints,
  `from __future__ import annotations`, ruff for lint and format.
- `VERSION` lives in `src/bindery/__init__.py` and is mirrored in `pyproject.toml`.
  Bump both.
- No em-dashes in anything a reader sees rendered: README, spec, roadmap,
  patchnotes, tag messages, commit messages, PR text, issue replies. En-dashes in
  numeric ranges are correct and stay.

## Validation workflow

The library is real data. The loop is always: dry run on `/tmp` copies, inspect the
report, then apply with backups. `--backup DIR` is opt-in on `bindery library
--apply`, so compose it into every apply. epubcheck is the oracle. A repaired book
that still has fatals is `partial` and is left for manual work, never auto-applied.

## Release flow

Commit and push verified work (the standing push grant; force-pushes still need
approval), wait for CI to go green, then tag: an annotated `vX.Y.Z` at the release
commit, with that release's patchnotes entry verbatim as the message
(`--cleanup=verbatim`; the full tag procedure is in the global file). The tag drives
`publish.yml`: the PyPI push and the Bindery Repair plugin zip attached to the
GitHub release.

The patchnotes entry pattern is `## vX.Y.Z (YYYY-MM-DD)`, then one `###` headline
naming the change, then prose on what changed and why, ending with
`Suite: N tests.` Older entries that predate the pattern are frozen with their tags.

## Decisions

Recorded calls that constrain the work. The reasoning behind each is in the
patchnotes entry that shipped it.

- **Docs layout (2026-11-24).** The roles listed at the top of this file. One flag
  text is canonical (the CLI table), and the page and the terminal reference are
  renderings of it.
- **The plugin shape (2026-08-09, corrected 2026-09-05).** A `FileTypePlugin` with
  `on_import`. `run(path)` returns a `temporary_file()` replacement before the file
  lands, so the original on disk is never touched and `metadata.db` is never
  written. A raising plugin cannot abort an import and cannot lose the file, but the
  failure would be silent, so the plugin never raises and keeps its own log. The
  hook also fires on `add_format`, so the plugin is byte-idempotent. The active set
  is the default pass only, because epubcheck cannot gate inside Calibre (no JVM,
  seconds-per-book latency). This latency ruling also keeps the plugin off upgrades.
- **Exit-code contracts (2026-09-10, "unify on trouble").** Argparse-level misuse
  exits 2 before the tool's validation. The tool's own usage errors exit 1. Trouble
  (rejected, errored, unreadable, partial books) exits 2 in `library` and both run
  slices. `audit` inverts the repair shape (findings exit 1), and a failed
  `--json` or `--tag` write on top of findings exits 3. `repair` never exits 2.
- **The daemon: reconcile, not retire (2026-09-10).** epubcheck's human summary
  counts occurrences while its JSON counts aggregated messages, and the gate has
  always measured the aggregated scale. So the daemon reads the JSON that
  epubcheck's own `CheckingReport` serializes and matches the subprocess by
  construction. It launches through Java's single-file source launcher (no javac,
  no version skew).
- **Tag policy (2026-09-11).** Every release from v0.27.0 on is tagged at its
  release commit with the full patchnotes entry as the tag message. The 39 entries
  from v0.1.0 to v0.26.0 are untagged by decision (v0.16.3 and v0.17.0 excepted):
  no backfill tags. patchnotes and PyPI carry them.
- **requires-python 3.12 with markers.** Every `cquarry` and `vir-tui` release on
  PyPI declares 3.14, so the pins carry version markers. A 3.14 install is
  byte-identical, and 3.12/3.13 installs run the stack-free repair core with a
  one-line dispatch guard. `minimum_calibre_version` is `(7, 0, 0)`.
- **The metadata-validity carve-out (2026-09-12).** Invalid dc: metadata (OPF-085
  and the thin date/language tail) routes to cquarry and CalibreQuarry. bindery's
  side is closed, and dc: metadata stays a non-goal.
- **Privacy record.** `testing_facility/top500candidates/REPORT.md` is untracked,
  forward-only. The local book collections never enter git: `testing_facility/` is
  gitignored and `test_facility/` is untracked, because most of their contents are
  commercial books.
- **One number grammar.** `pagination.py` is the canonical home of the roman and
  number logic. It vendors into the plugin and audit imports from it. Never write a
  third copy.
- **Ecosystem parity dispositions (2026-09-29).** In the Calibre-parity program,
  bindery's lane is the repair and acceptance domain, where it is past parity
  (ebook-polish has no acceptance oracle, no determinism contract, and no atomic
  library replacement). Conversion between formats stays out permanently: the
  Calibre conversion engine is orchestration-lane territory (CalibreQuarry). The
  ebook-polish content-improving actions bindery lacks stay declined: embed and
  subset fonts, jacket, smarten punctuation, remove-unused-css, compress-images,
  upgrade-book, download-external-resources, the OPF rewrite, cover. A bindery-side
  book-metadata write-back is also declined. The planned EPUB 2 to EPUB 3 upgrade
  lane (roadmap.md, the 2.0 program) is neither of those: it is EPUB-to-EPUB
  migration by bindery's own gated transforms, and its charter amendment lands with
  its first flag.
- **Non-goals kept with reasons.** ToC synthesis is out of the repair charter
  permanently (`tocdrift` is detector-only). Bulk RSC-005 repair is rejected;
  narrowly bounded sub-cases stay opt-in under their own flags. No spine-item
  manifest pruning, because missing spine documents mean a corrupt archive or
  broken fragments. No text deletion in anchor unwrapping: character conservation is
  absolute. No auto-split of monolithic documents and no auto-trim of bloated ToCs
  (flag, and re-source). No repair of corrupt archive entries (re-source). No PDF or
  DJVU work. `--add-img-alt` stays opt-in, because `alt=""` asserts "decorative".
  `defusedxml` stays declined: `_safe_xml_parse` delivers the entity protection,
  and the vendored-plugin cost was the objection. The plugin never gates on
  epubcheck. bindery has no manifest format ownership (CalibreQuarry owns
  `acquisition-manifest` and the orchestration).
- **A documented caveat is kept, not hidden (2026-11-04).** `--strip-presentational-attrs`
  changes rendering in readers that honor presentational hints. The flag ships with
  that caveat stated in its reference text, by the maintainer's decision.
