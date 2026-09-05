#!/usr/bin/env python3
"""Compile and drive the FastSweep JVM harness (Phase 12 benchmarking).

epubcheck costs ~3 s per book from a Python subprocess loop (~6 h for a
7,000-book library); scripts/FastSweep.java pays JVM startup once and
saturates every core, turning a full-library sweep into minutes. This wrapper
is the bindery-cli side of that harness: it locates the epubcheck jar,
compiles the harness once (--release 25, cached on mtime), feeds it a
directory or a path list, and optionally aggregates extract-mode output into
an error-code report (the testing_facility/top500candidates/REPORT.md
generator).

Stdlib only; a benchmarking tool, not part of the repair contract.

Examples:
  python scripts/fast_sweep.py --mode=extract testing_facility/top500candidates
  python scripts/fast_sweep.py --mode=audit ~/docs/Calibre\\ Library --out audit.csv
  find "$HOME/docs/Calibre Library" -name '*.epub' |
      python scripts/fast_sweep.py --from-file - --mode=extract --summary
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
HARNESS = SCRIPT_DIR / "FastSweep.java"

# Same shape bindery.validate uses for its daemon: the epubcheck launcher is a
# shell script whose java line names the real jar.
_JAR_RE = re.compile(r"""java\s+(?:-[^ ]+\s+)*-jar\s+["'\\]*([^\s"'\\]+\.jar)""")


def find_jar(explicit: str | None) -> Path:
    if explicit:
        jar = Path(explicit).expanduser()
    elif env := os.environ.get("EPUBCHECK_JAR"):
        jar = Path(env).expanduser()
    elif launcher := shutil.which("epubcheck"):
        m = _JAR_RE.search(Path(launcher).read_text())
        if not m:
            sys.exit(
                f"error: cannot find a jar path in the epubcheck launcher {launcher}"
            )
        jar = Path(os.path.expandvars(m.group(1))).expanduser()
    else:
        sys.exit("error: epubcheck not found; install it or pass --jar / EPUBCHECK_JAR")
    if not jar.is_file():
        sys.exit(f"error: no such epubcheck jar: {jar}")
    return jar


def classpath(jar: Path) -> str:
    # epubcheck.jar resolves its own lib/ dependencies when launched via -jar,
    # but a straight classpath invocation needs them spelled out.
    return f"{SCRIPT_DIR}:{jar}:{jar.parent / 'lib' / '*'}"


def ensure_compiled(jar: Path) -> None:
    cls = SCRIPT_DIR / "FastSweep.class"
    if cls.exists() and cls.stat().st_mtime > HARNESS.stat().st_mtime:
        return
    subprocess.run(
        ["javac", "--release", "25", "-cp", classpath(jar), str(HARNESS)],
        check=True,
    )


def collect_paths(args: argparse.Namespace) -> list[str]:
    if args.from_file:
        if args.from_file == "-":
            return [p.strip() for p in sys.stdin if p.strip()]
        text = Path(args.from_file).read_text()
        return [p.strip() for p in text.splitlines() if p.strip()]
    if not args.root.is_dir():
        sys.exit(f"error: not a directory: {args.root}")
    return sorted(str(p) for p in args.root.rglob("*") if p.suffix.lower() == ".epub")


def run(jar: Path, paths: list[str], mode: str) -> list[str]:
    cmd = ["java", "-cp", classpath(jar), "FastSweep", f"--mode={mode}"]
    t0 = time.monotonic()
    proc = subprocess.run(
        cmd, input="\n".join(paths) + "\n", capture_output=True, text=True, check=True
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    elapsed = time.monotonic() - t0
    print(
        f"[fast_sweep] {len(paths)} books in {elapsed:.1f}s "
        f"({len(paths) / elapsed:.1f} books/s), {len(lines)} line(s) out",
        file=sys.stderr,
    )
    missing = len(paths) - len(lines)
    if mode == "audit" and missing > 0:
        # audit must account for every book: a missing row would leave that book
        # a candidate forever on the bindery side.
        print(
            f"warning: {missing} book(s) produced no audit row (unreadable or "
            "missing); they stay candidates in a --audit run",
            file=sys.stderr,
        )
    return lines


def summarize(lines: list[str]) -> None:
    """The REPORT.md aggregation: per-code occurrences and distinct books."""
    occurrences: Counter[str] = Counter()
    books: Counter[str] = Counter()
    for line in lines:
        if " ||| " not in line:
            continue
        _, codes = line.split(" ||| ", 1)
        seen = set(codes.split(","))
        for code in seen:
            books[code] += 1
        occurrences.update(c for c in codes.split(",") if c)
    print(f"\n{len(lines)} book(s) with findings; top error codes:")
    for code, n in occurrences.most_common(30):
        print(f"{code}: {n} occurrence(s) in {books[code]} book(s)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "root", nargs="?", type=Path, help="directory to scan for .epub files"
    )
    ap.add_argument("--mode", choices=("audit", "extract"), default="audit")
    ap.add_argument("--from-file", help="read EPUB paths from this file ('-' = stdin)")
    ap.add_argument(
        "--jar",
        help="epubcheck.jar path (default: EPUBCHECK_JAR or the epubcheck launcher)",
    )
    ap.add_argument("--out", type=Path, help="also write the raw output to this file")
    ap.add_argument(
        "--summary",
        action="store_true",
        help="aggregate extract-mode output into a per-code report",
    )
    args = ap.parse_args()
    if not args.from_file and args.root is None:
        ap.error("a directory or --from-file is required")

    jar = find_jar(args.jar)
    ensure_compiled(jar)
    paths = collect_paths(args)
    print(f"[fast_sweep] {len(paths)} book(s), mode={args.mode}", file=sys.stderr)
    lines = run(jar, paths, args.mode)

    for line in lines:
        print(line)
    if args.out:
        args.out.write_text("\n".join(lines) + "\n")
    if args.summary:
        if args.mode != "extract":
            ap.error("--summary works with --mode=extract")
        summarize(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
