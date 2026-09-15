"""bindery-cli: deterministic, epubcheck-gated EPUB repair and content auditing.

The repair side: an always-on core of five well-formedness fixes plus the NCX
pipeline and the mimetype fix, with the structural repairs, the three lossy
strips (page numbers, broken tags, watermarks), and the safe opt-ins behind
explicit flags. Around it: the read-only `audit` verb (six body-text
analyzers), the `library` sweep with atomic in-place replacement in a Calibre
library, the `run phase1`/`phase3` acquisition slices, and the Bindery Repair
Calibre plugin built from this core.

Single source of truth for the version. pyproject.toml must match VERSION below.
"""

VERSION = "0.41.0"
__version__ = VERSION

from .epub import RepairReport, repair_epub  # noqa: E402

__all__ = ["VERSION", "__version__", "RepairReport", "repair_epub"]
