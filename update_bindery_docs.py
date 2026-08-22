import re
from pathlib import Path

# 1. Bump __init__.py
init_file = Path('src/bindery/__init__.py')
init_text = init_file.read_text()
init_text = init_text.replace('VERSION = "0.10.2"', 'VERSION = "0.11.0"')
init_file.write_text(init_text)

# 2. Bump pyproject.toml
toml_file = Path('pyproject.toml')
toml_text = toml_file.read_text()
toml_text = toml_text.replace('version = "0.10.2"', 'version = "0.11.0"')
toml_file.write_text(toml_text)

# 3. Update patchnotes.md
notes_file = Path('patchnotes.md')
notes_text = notes_file.read_text()
new_notes = """## v0.11.0 (2026-08-22)

### Features
- **Broken Tags Repair:** Added `--strip-broken-tags` lossy repair to identify and remove leaked HTML closing tags (e.g. `</p>` rendering as raw text). The repair strips the rogue text and verifies via `validate.no_worse` that `epubcheck` scores do not regress.
- **CalibreDB Integration:** Added `--install-to-calibre` flag to `--apply`. Instead of standard atomic filesystem replacement, Bindery can now natively update the format in the Calibre database (`calibredb add_format --replace`). It seamlessly preserves all database metadata, custom columns, and reading progress. Automatically falls back to atomic filesystem replace if a Calibre ID cannot be extracted from the path.
- **Run All Fixes:** Added `--all` flag to `library` and `repair` commands to instantly opt-in to all individual fix flags.

"""
notes_text = notes_text.replace('# Bindery Patch Notes\n\n', '# Bindery Patch Notes\n\n' + new_notes)
notes_file.write_text(notes_text)

