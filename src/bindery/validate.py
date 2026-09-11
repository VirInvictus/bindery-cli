"""epubcheck wrapper and the acceptance gate.

epubcheck is the external oracle. A repair is only worth keeping if it strictly
reduces problems and never introduces new ones. If epubcheck is not installed, the
gate degrades safely: validation is skipped and callers must decide whether to trust
the repair without it (the CLI requires --no-validate to do so).
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import re
import select
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_SUMMARY_RE = re.compile(
    r"Messages:\s*(\d+)\s+fatals?\s*/\s*(\d+)\s+errors?\s*/\s*(\d+)\s+warnings?"
)


@dataclass(frozen=True)
class CheckResult:
    fatals: int
    errors: int
    warnings: int

    def __str__(self) -> str:
        return f"{self.fatals}f/{self.errors}e/{self.warnings}w"


def epubcheck_available() -> bool:
    return shutil.which("epubcheck") is not None


def _english_locale_env() -> dict[str, str]:
    """The subprocess env with the JVM pinned to English (roadmap 5.2).

    epubcheck localizes its human-readable summary, which broke the regex
    fallback on non-English locales. Appended rather than assigned so a
    user's existing JAVA_TOOL_OPTIONS (heap flags etc.) survive.
    """
    env = dict(os.environ)
    opts = env.get("JAVA_TOOL_OPTIONS", "")
    env["JAVA_TOOL_OPTIONS"] = f"{opts} -Duser.language=en -Duser.country=US".strip()
    return env


def _counts_from_json(stdout: str) -> CheckResult | None:
    """Parse counts from epubcheck's locale-independent `--json -` output.

    epubcheck 5.x puts the totals under "checker" (nFatal/nError/nWarning,
    verified against 5.3.0). Anything unexpected returns None so the caller
    can fall back to the summary-line regex.
    """
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    checker = data.get("checker") if isinstance(data, dict) else None
    if not isinstance(checker, dict):
        return None
    try:
        return CheckResult(
            int(checker["nFatal"]), int(checker["nError"]), int(checker["nWarning"])
        )
    except KeyError, TypeError, ValueError:
        return None


_DAEMON_JAVA = """
import java.io.File;
import java.io.StringWriter;
import java.io.PrintWriter;
import java.util.Scanner;
import com.adobe.epubcheck.api.EpubCheck;
import com.adobe.epubcheck.reporting.CheckingReport;

public class FastDaemon {
    // Answers with the counts from the checker block of the JSON document
    // that CheckingReport.generate() itself serializes: the exact JSON the
    // epubcheck CLI's --json mode carries. Counting must match the
    // subprocess oracle by construction, never by imitation: the human
    // summary counts message occurrences while the JSON checker block
    // counts aggregated messages, and the gate is calibrated on the latter.
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
        Scanner scanner = new Scanner(System.in);
        while (scanner.hasNextLine()) {
            String path = scanner.nextLine();
            if (path.trim().isEmpty()) continue;
            File epub = new File(path);
            if (!epub.exists()) {
                System.out.println("-1,-1,-1");
                continue;
            }
            try {
                StringWriter json = new StringWriter();
                CheckingReport report = new CheckingReport(new PrintWriter(json),
                        epub.getName());
                report.initialize();
                new EpubCheck(epub, report).doValidate();
                report.generate();
                int f = field(json.toString(), "nFatal");
                int e = field(json.toString(), "nError");
                int w = field(json.toString(), "nWarning");
                if (f < 0 || e < 0 || w < 0) {
                    System.out.println("-1,-1,-1");
                } else {
                    System.out.println(f + "," + e + "," + w);
                }
            } catch (Exception e) {
                System.out.println("-1,-1,-1");
            }
        }
    }
}
"""


class _EpubcheckDaemon:
    """One warm epubcheck JVM driven over a stdin/stdout pipe.

    Any failure (a dead process, a wedged roundtrip, a timeout) tears the
    daemon down so a later call can start a fresh one; a failed start is
    remembered and never retried, so a broken toolchain cannot cost one
    javac compile per book. The caller falls back to the subprocess oracle
    whenever `check` returns None.
    """

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._failed = False
        self.succeeded = False
        self.workdir = None

    def _start(self):
        epubcheck_bin = shutil.which("epubcheck")
        if not epubcheck_bin:
            return False

        try:
            with open(epubcheck_bin) as f:
                script = f.read()
            m = re.search(
                r"java\s+(?:-[^ ]+\s+)*-jar\s+[\"'\\]*([^\s\"'\\]+\.jar)", script
            )
            if not m:
                return False
            jar_path = os.path.expandvars(m.group(1))
            if not os.path.exists(jar_path):
                return False

            self.workdir = tempfile.mkdtemp(prefix="bindery-daemon-")
            java_file = os.path.join(self.workdir, "FastDaemon.java")
            with open(java_file, "w") as f:
                f.write(_DAEMON_JAVA)

            self._proc = subprocess.Popen(
                # JEP 330 single-file source launcher: the JVM compiles the
                # daemon in memory with its own compiler, so there is no
                # javac step and no javac/java version skew (which kept the
                # daemon dead on machines where javac is newer than java).
                ["java", "-cp", f".:{jar_path}", java_file],
                cwd=self.workdir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            atexit.register(self.stop)
            return True
        except Exception:
            # Leave nothing behind: a failed start must not leak the
            # tempdir, and the failure is final for this instance.
            self.stop()
            return False

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def check(self, path: Path, timeout: int = 300) -> CheckResult | None:
        if self._failed:
            return None
        with self._lock:
            if self._proc is None and not self._start():
                self._failed = True
                return None
            try:
                self._proc.stdin.write(str(path.resolve()).encode() + b"\n")
                self._proc.stdin.flush()
                res = _readline_timeout(self._proc.stdout, timeout)
                if not res:
                    # EOF: the daemon is gone. Tear down so the failure is
                    # clean, and mark it final: a daemon that died before it
                    # ever answered will only die again, and restarting it
                    # would cost one JVM spawn per book.
                    self.stop()
                    self._failed = True
                    return None
                if res == "-1,-1,-1":
                    # The daemon's per-book error sentinel: counts unparseable
                    # for this book only; the daemon itself is fine.
                    return None
                f, e, w = map(int, res.split(","))
                self.succeeded = True
                return CheckResult(f, e, w)
            except Exception:
                # Wedged or dead (timeout included): tear down and mark it
                # final rather than failing (or respawning) for the life of
                # the process. The caller falls back to the subprocess
                # oracle, which is slower but never wrong.
                self.stop()
                self._failed = True
                return None

    def stop(self):
        if self._proc is not None:
            if self._proc.stdin:
                try:
                    self._proc.stdin.close()
                except OSError:
                    pass
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self.workdir is not None:
            shutil.rmtree(self.workdir, ignore_errors=True)
            self.workdir = None


def _readline_timeout(fh, timeout: int) -> str:
    """One line from a binary pipe, or TimeoutError after `timeout` seconds.

    A blocking readline on the daemon pipe is what made a JVM hang hang the
    whole sweep forever; select bounds every wait.
    """
    deadline = time.monotonic() + timeout
    fd = fh.fileno()
    buf = b""
    while b"\n" not in buf:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"daemon roundtrip exceeded {timeout}s")
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            raise TimeoutError(f"daemon roundtrip exceeded {timeout}s")
        chunk = os.read(fd, 4096)
        if not chunk:
            raise RuntimeError("daemon closed its stdout")
        buf += chunk
    return buf.decode(errors="replace").strip()


class _DaemonPool:
    """A bounded pool of warm daemons.

    Serial runs use one daemon (byte-for-byte the old behavior); with
    `--workers N`, checks run on up to N warm JVMs, so parallelism survives
    the oracle instead of serializing behind a single pipe. A worker that
    finds the pool busy falls back to the subprocess oracle, which is
    slower but never wrong.
    """

    def __init__(self, max_size: int = 1):
        self._max = max(0, max_size)
        self._idle: queue.Queue[_EpubcheckDaemon] = queue.Queue()
        self._live = 0
        self._exhausted = False
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()

    def set_size(self, max_size: int) -> None:
        # 0 disables the daemon oracle entirely (every check falls back to
        # the subprocess path); also re-arms a pool that had exhausted
        # itself against a missing toolchain.
        self._max = max(0, max_size)
        self._exhausted = False

    def check(self, path: Path, timeout: int = 300) -> CheckResult | None:
        if self._exhausted:
            return None
        daemon = self._checkout()
        if daemon is None:
            return None
        try:
            return daemon.check(path, timeout)
        finally:
            if daemon.alive():
                self._idle.put(daemon)
            else:
                with self._lock:
                    self._live -= 1
                    # A daemon that died without ever answering means the
                    # toolchain itself is broken (javac/java skew, missing
                    # jar): stop trying, so the sweep degrades to pure
                    # subprocess checks instead of respawning a JVM per book.
                    if not daemon.succeeded:
                        self._exhausted = True

    def _checkout(self) -> _EpubcheckDaemon | None:
        if self._max == 0:
            return None
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            if self._live < self._max:
                self._live += 1
            else:
                return None  # pool busy: caller falls back to subprocesses
        # Only one daemon may be starting at a time: N workers hitting a cold
        # pool must not stampede into N concurrent javac compiles. Whoever
        # finds a start already in flight falls back to subprocesses for this
        # one book.
        if not self._start_lock.acquire(blocking=False):
            with self._lock:
                self._live -= 1
            return None
        try:
            daemon = _EpubcheckDaemon()
            if daemon._proc is None and not daemon._start():
                with self._lock:
                    self._live -= 1
                    self._exhausted = True  # no toolchain: stop trying per book
                return None
            return daemon
        finally:
            self._start_lock.release()


_daemon_pool = _DaemonPool()


def set_daemon_pool_size(n: int) -> None:
    """Bound the daemon pool to the sweep's worker count (default 1: exactly
    the historical single-daemon behavior)."""
    _daemon_pool.set_size(n)


# Backwards-compatible alias for the historical single-daemon entry point.
_daemon = _daemon_pool


def run_epubcheck(path: Path, timeout: int = 300) -> CheckResult | None:
    """Run epubcheck and return parsed counts, or None if it could not be parsed.

    Counts come from `--json -` (locale-independent) first; the English summary-line
    regex stays as the fallback for epubchecks too old for `--json`, kept meaningful
    by the JVM locale pin in the env. A warm daemon answers first when one is
    available (the pool grows to the sweep's worker count, bounded by
    set_daemon_pool_size); the roundtrip itself is bounded by `timeout`, and None
    anywhere means the caller falls back to (or reports) failure.
    """
    res = _daemon_pool.check(path, timeout)
    if res is not None:
        return res
    try:
        out = subprocess.run(
            ["epubcheck", str(path), "--json", "-"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_english_locale_env(),
            check=False,
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return None
    result = _counts_from_json(out.stdout)
    if result is not None:
        return result
    m = _SUMMARY_RE.search(out.stdout + out.stderr)
    if not m:
        return None
    return CheckResult(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def gate(before: CheckResult, after: CheckResult) -> str:
    """Classify a repair: 'accept', 'partial', 'reject', or 'noop'.

    The metric depends on whether the book started with fatals, because a fatal parse
    error halts epubcheck on that file and hides every downstream schema error. So:

    - Started WITH fatals: success is fewer fatals. A rising error count is just those
      latent errors becoming visible once the file parses (the book now opens), not a
      regression. 'accept' if all fatals cleared, 'partial' if merely reduced.
    - Started with NO fatals (pure error/NCX-001 cleanup): nothing was masking errors,
      so an error increase is a real regression. Require a strict error decrease.

    Introducing net-new fatals is always a 'reject'.
    """
    if after.fatals > before.fatals:
        return "reject"
    if before.fatals > 0:
        if after.fatals == 0:
            return "accept"
        return "partial" if after.fatals < before.fatals else "noop"
    if after.errors > before.errors:
        return "reject"
    return "accept" if after.errors < before.errors else "noop"


def no_worse(before: CheckResult, after: CheckResult) -> bool:
    """The acceptance bar for a lossy content repair (page-number stripping), whose
    benefit epubcheck cannot see. Unlike `gate`, it does not demand a measured
    improvement; it only forbids a regression: no net-new fatals, and no new errors
    unless fatals were already masking them. Mirrors oceanstrip's 'no more fatals or
    errors than the original' bar."""
    if after.fatals > before.fatals:
        return False
    if before.fatals == 0 and after.errors > before.errors:
        return False
    return True
