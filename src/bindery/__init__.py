"""Bindery: repair EPUBs (well-formedness fixes, plus an opt-in lossy page-number
strip), epubcheck-gated.

Single source of truth for the version. pyproject.toml must match VERSION below.
"""

VERSION = "0.8.0"
__version__ = VERSION

from .epub import RepairReport, repair_epub  # noqa: E402

__all__ = ["VERSION", "__version__", "RepairReport", "repair_epub"]
