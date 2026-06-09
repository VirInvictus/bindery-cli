"""Bindery: repair EPUBs (well-formedness fixes), epubcheck-gated.

Single source of truth for the version. pyproject.toml must match VERSION below.
"""

VERSION = "0.2.0"
__version__ = VERSION

from .epub import RepairReport, repair_epub  # noqa: E402

__all__ = ["VERSION", "__version__", "RepairReport", "repair_epub"]
