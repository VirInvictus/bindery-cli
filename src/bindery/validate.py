"""epubcheck wrapper and the acceptance gate.

epubcheck is the external oracle. A repair is only worth keeping if it strictly
reduces problems and never introduces new ones. If epubcheck is not installed, the
gate degrades safely: validation is skipped and callers must decide whether to trust
the repair without it (the CLI requires --no-validate to do so).
"""

from __future__ import annotations

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


def run_epubcheck(path: Path, timeout: int = 300) -> CheckResult | None:
    """Run epubcheck and return parsed counts, or None if it could not be parsed."""
    try:
        out = subprocess.run(
            ["epubcheck", str(path)], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return None
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
