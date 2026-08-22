"""epubcheck wrapper and the acceptance gate.

epubcheck is the external oracle. A repair is only worth keeping if it strictly
reduces problems and never introduces new ones. If epubcheck is not installed, the
gate degrades safely: validation is skipped and callers must decide whether to trust
the repair without it (the CLI requires --no-validate to do so).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
import java.io.PrintWriter;
import java.util.Scanner;
import com.adobe.epubcheck.api.EpubCheck;
import com.adobe.epubcheck.reporting.CheckingReport;

public class FastDaemon {
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
                PrintWriter out = new PrintWriter(new java.io.OutputStream() {
                    public void write(int b) {}
                });
                CheckingReport report = new CheckingReport(out, epub.getName());
                EpubCheck check = new EpubCheck(epub, report);
                check.doValidate();
                System.out.println(report.getFatalErrorCount() + "," + report.getErrorCount() + "," + report.getWarningCount());
            } catch (Exception e) {
                System.out.println("-1,-1,-1");
            }
        }
    }
}
"""


class _EpubcheckDaemon:
    def __init__(self):
        self._proc = None
        self._lock = __import__("threading").Lock()

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

            self.workdir = __import__("tempfile").mkdtemp(prefix="bindery-daemon-")
            java_file = os.path.join(self.workdir, "FastDaemon.java")
            with open(java_file, "w") as f:
                f.write(_DAEMON_JAVA)

            subprocess.run(
                ["javac", "--release", "25", "-cp", jar_path, java_file],
                check=True,
                capture_output=True,
            )

            self._proc = subprocess.Popen(
                ["java", "-cp", f".:{jar_path}", "FastDaemon"],
                cwd=self.workdir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            __import__("atexit").register(self.stop)
            return True
        except Exception:
            return False

    def check(self, path: Path) -> CheckResult | None:
        with self._lock:
            if self._proc is None:
                if not self._start():
                    return None
            try:
                self._proc.stdin.write(str(path.resolve()) + "\n")
                self._proc.stdin.flush()
                res = self._proc.stdout.readline().strip()
                if not res or res == "-1,-1,-1":
                    return None
                f, e, w = map(int, res.split(","))
                return CheckResult(f, e, w)
            except Exception:
                return None

    def stop(self):
        if self._proc:
            self._proc.terminate()
            self._proc = None
        if hasattr(self, "workdir") and os.path.exists(self.workdir):
            shutil.rmtree(self.workdir, ignore_errors=True)


_daemon = _EpubcheckDaemon()


def run_epubcheck(path: Path, timeout: int = 300) -> CheckResult | None:
    res = _daemon.check(path)
    if res is not None:
        return res

    """Run epubcheck and return parsed counts, or None if it could not be parsed.

    Counts come from `--json -` (locale-independent) first; the English
    summary-line regex stays as the fallback for epubchecks too old for
    `--json`, kept meaningful by the JVM locale pin in the env.
    """
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
