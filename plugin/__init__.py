"""Bindery Repair: repair EPUBs on import with bindery-cli's gate-safe pass.

A Calibre FileTypePlugin running bindery-cli's always-on core pass (the five
well-formedness transforms plus the NCX pipeline) on *.epub files as they are
imported. The structural repairs and the lossy strips stay CLI-only: their
acceptance IS the epubcheck gate, which cannot run inside Calibre (no jar, no
JVM, and seconds-per-book latency), so their flags are never enabled here and
nothing runs ungated, ever.

The on_import contract: run(path) returns the path to a repaired copy built
with the plugin's own temporary_file(), and Calibre imports that instead. The
original on disk is never touched and nothing writes to metadata.db. No
exception ever escapes run(): a raising plugin cannot abort an import (Calibre
falls back to the original silently), so every failure returns the original
path and logs one line. The plugin is byte-idempotent: an already-clean book
yields zero fixes and the original path, which db.add_format's
run_hooks=True re-entry requires.

Configuration is the plain site_customization string, parsed as JSON::

    {"log": true, "log_path": "/path/bindery_repair.log",
     "max_size_mb": 150, "epubcheck_path": null}

log_path defaults to <calibre config dir>/bindery_repair.log. The size cap
refuses absurd files rather than stalling an import; 150MB is the recorded
default. epubcheck_path opts into the experimental on-PATH validation mode:
the repaired copy is re-measured with the epubcheck binary and refused unless
it is no worse than the original (the CLI's no_worse bar). It defaults to
off, keeping the import fast; the default pass is safe without a gate.

The vendored bindery modules ride in this zip as submodules of the plugin
package (their relative imports resolve unchanged), so the zip's module bytes
are identical to the repo's src/bindery/ sources; scripts/build_plugin.py
generates the zip at release and the suite's drift test pins the equality.
"""

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from collections import namedtuple
from pathlib import Path

__PLUGIN_VERSION__ = (0, 0, 0)  # substituted by scripts/build_plugin.py

# The vendored bindery modules load as submodules of this plugin package
# (calibre_plugins.bindery_repair.epub and siblings), so their own relative
# imports resolve unchanged.
from calibre.customize import FileTypePlugin

from . import epub as _bindery_epub

DEFAULT_MAX_SIZE_MB = 150
_EPUB_SUFFIXES = (".epub",)
# the slice of validate.CheckResult the experimental mode needs
_EcCounts = namedtuple("_EcCounts", "fatals errors warnings")


class BinderyRepair(FileTypePlugin):
    name = "Bindery Repair"
    description = (
        "Repair well-formedness defects in EPUB files as they are imported, "
        "running bindery-cli's epubcheck-safe default pass"
    )
    author = "Brandon LaRocque"
    version = __PLUGIN_VERSION__
    minimum_calibre_version = (2, 0, 0)
    supported_platforms = ["linux"]
    type = "File type"
    file_types = {"epub"}
    on_import = True
    priority = 1

    # ---- run() never raises: the contract is the original path on trouble

    def run(self, path_to_ebook):
        try:
            return self._run(path_to_ebook)
        except Exception as e:
            # the last-resort sink re-reads the config, whose parsing is
            # itself exception-safe; run() must never raise
            self._log(
                f"errored: {path_to_ebook}: {type(e).__name__}: {e}",
                self._resolve_log_path(self._config()),
            )
            return path_to_ebook

    def _run(self, path_to_ebook: str) -> str:
        src = Path(path_to_ebook)
        if src.suffix.lower() not in _EPUB_SUFFIXES:
            return path_to_ebook
        cfg = self._config()
        log_path = self._resolve_log_path(cfg)
        try:
            size_mb = src.stat().st_size / (1024 * 1024)
        except OSError as e:
            self._log(f"errored: {src}: {e}", log_path)
            return path_to_ebook
        cap = cfg.get("max_size_mb", DEFAULT_MAX_SIZE_MB)
        if isinstance(cap, (int, float)) and cap > 0 and size_mb > cap:
            self._log(
                f"refused: {src.name}: {size_mb:.0f}MB over the {cap:g}MB cap",
                log_path,
            )
            return path_to_ebook

        with self.temporary_file(".epub") as tf:
            dst = Path(tf.name)
            try:
                report = _bindery_epub.repair_epub(src, dst)
            except Exception as e:
                self._log(
                    f"errored: {src.name}: repair failed: {type(e).__name__}: {e}",
                    log_path,
                )
                return path_to_ebook
            if not report:
                # zero fixes: hand back the original byte-for-byte (the
                # add_format(run_hooks=True) re-entry depends on this)
                dst.unlink(missing_ok=True)
                self._log(f"no fixes: {src.name}", log_path)
                return path_to_ebook
            with zipfile.ZipFile(dst) as z:
                damaged = z.testzip()
            if damaged is not None:
                dst.unlink(missing_ok=True)
                self._log(
                    f"refused: {src.name}: rewritten archive failed testzip() "
                    f"at {damaged}",
                    log_path,
                )
                return path_to_ebook
            if "epubcheck_path" in cfg and cfg["epubcheck_path"]:
                verdict = self._epubcheck_no_worse(src, dst, cfg["epubcheck_path"])
                if verdict is False:
                    dst.unlink(missing_ok=True)
                    self._log(
                        f"refused: {src.name}: epubcheck measured the repair "
                        "no better or worse; keeping the original",
                        log_path,
                    )
                    return path_to_ebook
            detail = ", ".join(f"{k}={v}" for k, v in sorted(report.fixes.items()))
            if report.ncx_uid_synced:
                detail = (detail + ", " if detail else "") + "ncx_uid_synced"
            self._log(f"fixed: {src.name}: {detail}", log_path)
            return str(dst)

    # ---- configuration and logging

    def _config(self) -> dict:
        raw = getattr(self, "site_customization", None)
        if not raw:
            return {}
        try:
            cfg = json.loads(raw)
        except ValueError:
            # a broken config lands one line in the default log and the run
            # proceeds on the defaults; an import must never be lost to config
            self._log(
                "config: site_customization is not valid JSON; using defaults",
                self._default_log_path(),
            )
            return {}
        return cfg if isinstance(cfg, dict) else {}

    def _resolve_log_path(self, cfg: dict) -> str | None:
        if not cfg.get("log", True):
            return None
        path = cfg.get("log_path") or self._default_log_path()
        return path if isinstance(path, str) else None

    def _default_log_path(self) -> str | None:
        try:
            from calibre.constants import config_dir

            return os.path.join(config_dir, "bindery_repair.log")
        except Exception:
            return None

    def _log(self, line: str, log_path: str | None) -> None:
        """One line per book, appended; logging must never break an import."""
        if not log_path:
            return
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line.rstrip() + "\n")
        except OSError:
            pass

    # ---- experimental epubcheck-on-PATH validation (default off)

    def _epubcheck_no_worse(self, src: Path, dst: Path, epubcheck: str) -> bool | None:
        """Measure both files with the epubcheck binary; True when the repair
        is no worse (no net-new fatals, no new errors unless fatals masked
        them), False when it regressed, None when the binary cannot answer
        (an unanswered check never refuses the repair; the pass is safe)."""
        before = self._epubcheck_counts(src, epubcheck)
        after = self._epubcheck_counts(dst, epubcheck)
        if before is None or after is None:
            return None
        if after.fatals > before.fatals:
            return False
        if before.fatals == 0 and after.errors > before.errors:
            return False
        return True

    def _epubcheck_counts(self, path: Path, epubcheck: str):
        try:
            proc = subprocess.run(
                [epubcheck, "--json", "-", str(path)],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except OSError, subprocess.TimeoutExpired:
            return None
        try:
            data = json.loads(proc.stdout)
            checker = data["checker"]
            return _EcCounts(
                int(checker["nFatal"]),
                int(checker["nError"]),
                int(checker["nWarning"]),
            )
        except json.JSONDecodeError, KeyError, TypeError, ValueError:
            return None

    # ---- plugin housekeeping

    def customization_help(self, gui=False):
        return (
            'Enter a JSON object: {"log": true, "log_path": ..., '
            '"max_size_mb": 150, "epubcheck_path": null}. '
            "epubcheck_path enables the experimental post-repair validation "
            "with the epubcheck binary (slow; off by default)."
        )
