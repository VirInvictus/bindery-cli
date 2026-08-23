from pathlib import Path
import re

content = Path("patchnotes.md").read_text()
new_notes = """# Bindery Patch Notes

## v0.16.0 (2026-08-23)

---

### Core Upgrades

**Centralized Calibre DB Access:** Bindery has transitioned to the unified `cquarry` library for all read-only `metadata.db` accesses. The internal `bindery.audit` DB connection logic has been completely replaced with `cquarry.db.CalibreDB`. This inherits robust Calibre lock handling, database snapshotting, and ensures query logic stays perfectly synchronized with `CalibreQuarry` and `Hermitage`.

"""
content = re.sub(r"^# Bindery Patch Notes\n", new_notes, content)
Path("patchnotes.md").write_text(content)
