# The Calibre plugin (Bindery Repair)

Each release also ships **BinderyRepair-v<VERSION>.zip**, a Calibre plugin that
repairs EPUBs as they are imported: it is attached to the GitHub release beside
the PyPI wheel (install it via Calibre's Preferences, Plugins, "Load plugin
from file"). The plugin is the CLI's always-on core pass only: the five
well-formedness fixes, the NCX pipeline, and the mimetype fix. The structural
repairs and lossy
strips stay CLI-only, because their acceptance is the epubcheck gate and
epubcheck cannot run inside Calibre; nothing runs ungated in the plugin, ever.

Compatibility: the plugin declares a minimum Calibre of **7.0** and
`supported_platforms` is Linux. Both floors state what is actually verified, no more:
released Calibre embeds Python 3.11 through the whole 8.x series and 3.14 from
9.0 on, and CI byte-compiles the shipped plugin zip under Python 3.11, 3.12,
3.13 and 3.14, so the plugin parses on every current Calibre series; 7.x is
the oldest whose interpreter CI covers, 2.x-6.x embed interpreters nothing
verifies. The Calibre API surface the plugin uses is the long-stable
`FileTypePlugin` shape, exercised against test stubs only; the plugin is
developed and tested on Linux (v0.39.0 shipped the 3.14-only except syntax
and silently failed to load on every released Calibre, which is the failure
this floor and CI now guard against).

Behavior and guarantees:

- `run()` never raises and never touches the original file or `metadata.db`: a
  repaired copy is built in a persistent temp file and handed back for import;
  any trouble returns the original path and logs one line.
- Byte-idempotent: an already-clean book yields zero fixes and the original
  path, so re-adding a format re-runs the plugin harmlessly.
- The rewritten archive is `testzip()`-verified before it is handed back.
- One log line per book (fixed / no fixes / refused / errored), appended to
  `bindery_repair.log` in Calibre's config directory by default.
- Files over the size cap are refused (default 150MB) rather than stalling an
  import.

Configuration is the plugin's customization string, parsed as JSON:

```json
{"log": true, "log_path": "/path/bindery_repair.log", "max_size_mb": 150,
 "max_log_mb": 2, "epubcheck_path": null}
```

The log rotates at `max_log_mb` (default 2MB, one `.old` generation kept;
`0` disables rotation), so years of imports cannot grow it without bound.

`epubcheck_path` opts into the experimental on-PATH validation mode: the
repaired copy is re-measured with your epubcheck binary and refused unless it
is no worse than the original. It defaults to off; the default pass is safe
without a gate, and the mode costs two epubcheck runs per imported book.
