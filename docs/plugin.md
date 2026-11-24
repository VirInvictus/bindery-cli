# The Calibre plugin (Bindery Repair)

Each release also ships **BinderyRepair-v<VERSION>.zip**, a Calibre plugin that
repairs EPUBs as they are imported. It is attached to the GitHub release beside
the PyPI wheel; install it through Calibre's Preferences, Plugins, "Load plugin
from file".

The plugin runs the CLI's default pass only: the five well-formedness fixes, the
NCX pipeline, and the mimetype fix. The structural repairs and the lossy strips
stay on the command line, because their acceptance is the epubcheck gate and
epubcheck cannot run inside Calibre. So no opt-in repair ever runs in the
plugin. The default pass is the semantics-preserving set, and it is safe
without the gate.

Compatibility: the plugin declares a minimum Calibre of **7.0**, and
`supported_platforms` is Linux. Both floors state what is verified. Released
Calibre embeds Python 3.11 through the 8.x series and 3.14 from 9.0 on. CI
byte-compiles the shipped plugin zip under Python 3.11, 3.12, 3.13, and 3.14,
so the plugin parses on every current Calibre series. CI covers 7.x upward;
nothing verifies 2.x through 6.x, whose embedded interpreters no test runs on.
The Calibre API the plugin uses is the long-stable `FileTypePlugin` shape,
exercised against test stubs only. The plugin is developed and tested on Linux.

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
