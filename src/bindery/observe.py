"""The read-only whole-library census: one versioned .observe artifact per run.

`bindery observe` validates every EPUB under a tree in one warm-JVM pass
(epubcheck counts and error codes together), tabulates the census (severity
histogram, dominant error-code classes, worst offenders), optionally composes
the gated repair preview (what `bindery library --apply` would write: a
dry-run over the default flag set), and writes one self-describing artifact
for trend diffing with `--against`. Strictly read-only: the only file it
writes is the artifact itself, and the writer refuses to land it inside the
scanned root (the keep-root-clean ruling; Calibre scans the library root and
stray files there are a session regression).

The census harness embeds its Java like validate.py's FastDaemon does (JEP
330 single-file launcher, no javac, compiled in memory at start): the wheel
ships the package, not scripts/, and scripts/fast_sweep.py is a benchmarking
tool outside the repair contract. The jar-locating regex is deliberately the
same shape as fast_sweep.py's; the duplication is the price of keeping
scripts/ unshipped. Counting matches the subprocess oracle by construction,
never by imitation: the live report getters count message occurrences while
the JSON document generate() serializes (what epubcheck --json emits and what
the gate is calibrated on) counts aggregated messages, so the harness scans
that same document, exactly as FastSweep and FastDaemon do.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import __version__

SCHEMA = "bindery-observe-v1"

# The per-book output line: counts, then the error-code profile, then the
# canonical path. The sentinel counts (-1,-1,-1) mark a book the JVM could
# not read: a census must never lose a book the way the scripts/ audit and
# extract modes' silence would (their omission is why fast_sweep.py prints a
# missing-rows warning; the observe harness closes that gap instead).
OBSERVE_SENTINEL = "-1,-1,-1"

_OBSERVE_JAVA = """\
import java.io.File;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.util.ArrayList;
import java.util.List;
import java.util.Scanner;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import com.adobe.epubcheck.api.EpubCheck;
import com.adobe.epubcheck.reporting.CheckingReport;

public class ObserveSweep {
    private static final Pattern CODE = Pattern.compile("([A-Z]{3}-\\\\d{3})");

    static int field(String json, String name) {
        int i = json.indexOf(name);
        while (i != -1) {
            int j = i + name.length();
            while (j < json.length() && (json.charAt(j) == ' '
                    || json.charAt(j) == ':' || json.charAt(j) == '"')) {
                j++;
            }
            if (j < json.length() && Character.isDigit(json.charAt(j))) {
                int k = j;
                while (k < json.length() && Character.isDigit(json.charAt(k))) {
                    k++;
                }
                return Integer.parseInt(json.substring(j, k));
            }
            i = json.indexOf(name, i + 1);
        }
        return -1;
    }

    public static void main(String[] args) throws Exception {
        List<String> paths = new ArrayList<>();
        try (Scanner scanner = new Scanner(System.in)) {
            while (scanner.hasNextLine()) {
                String p = scanner.nextLine().trim();
                if (!p.isEmpty()) paths.add(p);
            }
        }
        paths.parallelStream().forEach(path -> {
            File epub = new File(path);
            String line;
            if (!epub.exists()) {
                line = "-1,-1,-1 ||| ||| " + epub.getAbsolutePath();
            } else {
                try {
                    StringWriter sw = new StringWriter();
                    CheckingReport report = new CheckingReport(new PrintWriter(sw),
                            epub.getName());
                    report.initialize();
                    new EpubCheck(epub, report).doValidate();
                    report.generate();
                    String json = sw.toString();
                    int f = field(json, "nFatal");
                    int e = field(json, "nError");
                    int w = field(json, "nWarning");
                    if (f < 0 || e < 0 || w < 0) {
                        line = "-1,-1,-1 ||| ||| " + epub.getCanonicalPath();
                    } else {
                        List<String> codes = new ArrayList<>();
                        Matcher m = CODE.matcher(json);
                        while (m.find()) {
                            codes.add(m.group(1));
                        }
                        line = f + "," + e + "," + w + " ||| "
                                + String.join(",", codes) + " ||| "
                                + epub.getCanonicalPath();
                    }
                } catch (Exception ex) {
                    line = "-1,-1,-1 ||| ||| " + epub.getAbsolutePath();
                }
            }
            synchronized (System.out) {
                System.out.println(line);
            }
        });
    }
}
"""

# Same shape fast_sweep.py uses: the epubcheck launcher is a shell script
# whose java line names the real jar.
_JAR_RE = re.compile(r"""java\s+(?:-[^ ]+\s+)*-jar\s+["'\\]*([^\s"'\\]+\.jar)""")


@dataclass(frozen=True)
class CensusRecord:
    path: str
    fatals: int
    errors: int
    warnings: int
    codes: tuple[str, ...]
    unreadable: bool


def _find_epubcheck_jar() -> Path | None:
    if env := os.environ.get("EPUBCHECK_JAR"):
        jar = Path(env).expanduser()
        return jar if jar.is_file() else None
    launcher = shutil.which("epubcheck")
    if not launcher:
        return None
    m = _JAR_RE.search(Path(launcher).read_text())
    if not m:
        return None
    jar = Path(os.path.expandvars(m.group(1))).expanduser()
    return jar if jar.is_file() else None


def parse_census_line(line: str) -> CensusRecord | None:
    """One harness output line into a record, or None on a shape it cannot own.

    `f,e,w ||| CODE,CODE,... ||| path`; the sentinel counts mark unreadable.
    maxsplit keeps a path that itself contains the separator intact (the
    scripts/ harness carries the same assumption; here the cost of being
    wrong is a None the caller reports, not a lost book).
    """
    parts = line.split(" ||| ", 2)
    if len(parts) != 3:
        return None
    counts, codes_text, path = parts
    f, e, w = (int(x) for x in counts.split(","))
    unreadable = (f, e, w) == (-1, -1, -1)
    codes = tuple(c for c in codes_text.split(",") if c)
    return CensusRecord(
        path=path,
        fatals=max(f, 0),
        errors=max(e, 0),
        warnings=max(w, 0),
        codes=codes,
        unreadable=unreadable,
    )


def run_census(paths: list[str], timeout_s: int = 3600) -> list[CensusRecord]:
    """One warm JVM, one validation per book, counts and codes in one pass.

    The JVM writes its harness to a tempdir and compiles it in memory (JEP
    330, the FastDaemon shape), so there is no javac step and no javac/java
    version skew. Every input path must come back as a line: a book the JVM
    cannot read arrives as the sentinel record, and a book that produced no
    line at all is reported after the fact rather than dropped.
    """
    jar = _find_epubcheck_jar()
    if jar is None:
        raise RuntimeError("cannot locate the epubcheck jar")
    records: list[CensusRecord] = []
    with tempfile.TemporaryDirectory(prefix="bindery-observe-") as td:
        java_file = Path(td) / "ObserveSweep.java"
        java_file.write_text(_OBSERVE_JAVA)
        cp = f".:{jar}:{jar.parent / 'lib' / '*'}"
        proc = subprocess.Popen(
            ["java", "-cp", cp, str(java_file)],
            cwd=td,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write("\n".join(paths) + "\n")
        proc.stdin.close()
        try:
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                rec = parse_census_line(line)
                if rec is not None:
                    records.append(rec)
        finally:
            proc.stdout.close()
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    answered = {r.path for r in records}
    missing = [
        p for p in paths if p not in answered and str(Path(p).resolve()) not in answered
    ]
    for p in missing:
        records.append(
            CensusRecord(
                path=str(Path(p).resolve()),
                fatals=0,
                errors=0,
                warnings=0,
                codes=(),
                unreadable=True,
            )
        )
    return records


def _severity_bucket(errors: int) -> str:
    """The error-count bucket for the severity histogram."""
    if errors <= 2:
        return str(errors)
    if errors <= 5:
        return "3-5"
    if errors <= 20:
        return "6-20"
    if errors <= 100:
        return "21-100"
    return ">100"


def tabulate(records: list[CensusRecord], root: Path) -> dict:
    """The census aggregates: totals, severity histogram, code profile,
    worst offenders. Paths are reported relative to the root so an artifact
    stays portable across a library move."""
    total = len(records)
    unreadable = [r for r in records if r.unreadable]
    clean = [
        r
        for r in records
        if not r.unreadable and not (r.fatals or r.errors or r.warnings)
    ]
    with_findings = [
        r for r in records if not r.unreadable and (r.fatals or r.errors or r.warnings)
    ]

    severity: dict[str, int] = {
        "fatal_books": sum(1 for r in with_findings if r.fatals > 0),
        "warnings_only": sum(
            1 for r in with_findings if r.fatals == 0 and r.errors == 0
        ),
        "unreadable": len(unreadable),
        "error_mass": sum(r.errors for r in records),
    }
    buckets: Counter[str] = Counter()
    for r in with_findings:
        if r.errors > 0:
            buckets[_severity_bucket(r.errors)] += 1
    severity["error_books_by_count"] = dict(
        sorted(
            buckets.items(),
            key=lambda kv: (len(kv[0]) > 3, kv[0]),  # numeric 0-2 first, then text
        )
    )

    occurrences: Counter[str] = Counter()
    books: Counter[str] = Counter()
    for r in records:
        for c in set(r.codes):
            books[c] += 1
        occurrences.update(r.codes)
    codes = {
        "occurrences": dict(occurrences.most_common()),
        "books": {code: books[code] for code, _ in occurrences.most_common()},
    }

    worst = [
        {
            "path": os.path.relpath(r.path, root),
            "fatals": r.fatals,
            "errors": r.errors,
            "warnings": r.warnings,
            "codes": sorted(set(r.codes)),
        }
        for r in sorted(
            with_findings,
            key=lambda r: (r.errors + r.fatals, r.path),
            reverse=True,
        )[:25]
    ]

    return {
        "library": {
            "epubs_total": total,
            "clean": len(clean),
            "with_findings": len(with_findings),
            "unreadable": len(unreadable),
        },
        "severity": severity,
        "codes": codes,
        "worst": worst,
    }


def build_trend(previous: dict, current: dict) -> dict:
    """The delta block for --against: library totals, then per-code changes.

    Both sides must carry the same schema (the caller checks): a trend block
    across schema versions would compare vocabularies that no longer agree.
    """
    plib, clib = previous["library"], current["library"]
    delta: dict = {
        "against": {
            "generated": previous.get("generated"),
            "root": previous.get("root"),
        },
        "epubs_total_delta": clib["epubs_total"] - plib["epubs_total"],
        "clean_delta": clib["clean"] - plib["clean"],
        "with_findings_delta": clib["with_findings"] - plib["with_findings"],
        "unreadable_delta": clib["unreadable"] - plib["unreadable"],
        "error_mass_delta": (
            current["severity"]["error_mass"] - previous["severity"]["error_mass"]
        ),
    }
    pbooks = previous["codes"]["books"]
    cbooks = current["codes"]["books"]
    changes: dict[str, dict] = {}
    for code in sorted(set(pbooks) | set(cbooks)):
        before, after = pbooks.get(code, 0), cbooks.get(code, 0)
        if before == after:
            continue
        changes[code] = {"books_before": before, "books_after": after}
    delta["code_changes"] = changes
    delta["new_codes"] = sorted(c for c, v in changes.items() if v["books_before"] == 0)
    delta["resolved_codes"] = sorted(
        c for c, v in changes.items() if v["books_after"] == 0
    )
    return delta


def _epubcheck_version() -> str | None:
    try:
        out = subprocess.run(
            ["epubcheck", "--version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (out.stdout + out.stderr).strip().splitlines()
    return text[0].strip() if text else None


def _preview_payload(root: Path, workers: int) -> tuple[dict, int]:
    """The gated repair preview: the shipped library runner, dry-run, default
    flag set, in-process (the run phase1 pattern). No --apply, no backup, no
    --install-to-calibre: the preview measures, it never writes."""
    from .cli import _repair_sweep

    argv = ["library", str(root), "--sweep", "--quiet", f"--workers={workers}"]
    return _repair_sweep(argv)


def _resolve_output_path(root: Path, output: str | None) -> Path:
    """The artifact destination: explicit --output, or a timestamped default
    in the current directory. The keep-root-clean guard refuses to land the
    artifact inside the scanned root: Calibre scans that root, and a stray
    file there is exactly the litter the library rules exist to prevent."""
    if output:
        out = Path(output).expanduser()
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out = Path.cwd() / f"observe-{stamp}.observe"
    out = Path(os.path.abspath(out))
    if os.path.commonpath([str(out), str(root)]) == str(root) and str(out) != str(root):
        raise ValueError(
            f"refusing to write the .observe artifact inside the scanned "
            f"root ({root}); pass --output pointing outside it"
        )
    return out


def run_observe(args) -> int:
    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 1
    paths = sorted(
        str(p)
        for p in root.rglob("*.epub", case_sensitive=False)
        if p.is_file() and ".caltrash" not in p.parts
    )
    if not paths:
        print(f"error: no .epub files under {root}", file=sys.stderr)
        return 1
    if args.limit:
        paths = paths[: args.limit]
    if shutil.which("java") is None:
        print("error: java not found (the census harness needs a JVM)", file=sys.stderr)
        return 1

    try:
        output = _resolve_output_path(root, args.output)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    previous = None
    if args.against:
        try:
            previous = json.loads(Path(args.against).expanduser().read_text())
        except (OSError, json.JSONDecodeError) as e:
            print(
                f"error: cannot read --against artifact {args.against}: {e}",
                file=sys.stderr,
            )
            return 1
        if not isinstance(previous, dict) or previous.get("schema") != SCHEMA:
            print(
                f"error: --against artifact {args.against} is not a {SCHEMA} document",
                file=sys.stderr,
            )
            return 1

    started = time.monotonic()
    print(f"[observe] census: {len(paths)} book(s) under {root}", file=sys.stderr)
    try:
        records = run_census(paths)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    census_s = time.monotonic() - started
    answered = len(records)
    if answered < len(paths):
        # The harness answers for every input path; anything short is a
        # harness failure and the artifact would understate the library.
        print(
            f"error: the census harness returned {answered} line(s) for "
            f"{len(paths)} book(s); refusing to write a partial census",
            file=sys.stderr,
        )
        return 2

    tab = tabulate(records, root)
    artifact: dict = {
        "schema": SCHEMA,
        "generated": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "tool": {"bindery": __version__, "epubcheck": _epubcheck_version()},
        "root": str(root),
        "census_seconds": round(census_s, 1),
        **tab,
    }

    if not getattr(args, "no_preview", False):
        print(
            "[observe] repair preview: dry-run sweep over the default flag set",
            file=sys.stderr,
        )
        payload, _rc = _preview_payload(root, getattr(args, "workers", 1) or 1)
        artifact["preview"] = {
            "mode": payload.get("mode"),
            "candidates": payload.get("candidates"),
            "summary": payload.get("summary"),
            "books": [
                {
                    "path": os.path.relpath(b["path"], root),
                    "status": b["status"],
                    "before": b.get("before"),
                    "after": b.get("after"),
                    "fixes": b.get("fixes"),
                }
                for b in payload.get("books", [])
            ],
        }

    if previous is not None:
        artifact["trend"] = build_trend(previous, artifact)

    output.write_text(json.dumps(artifact, indent=2) + "\n")

    lib = artifact["library"]
    print(
        f"\nbooks: {lib['epubs_total']}  clean: {lib['clean']}  "
        f"with findings: {lib['with_findings']}  unreadable: {lib['unreadable']}"
    )
    top = list(artifact["codes"]["occurrences"].items())[:10]
    if top:
        print("top error codes:")
        for code, n in top:
            print(
                f"  {code}: {n} occurrence(s) in "
                f"{artifact['codes']['books'][code]} book(s)"
            )
    print(f"artifact: {output}")
    return 0
