import re
from pathlib import Path

readme_file = Path('README.md')
text = readme_file.read_text()

# Add to feature list
new_feature = """- **strip-pagination** (`--strip-pagination`): remove baked-in print page numbers and running headers.
- **strip-broken-tags** (`--strip-broken-tags`): remove leaked HTML closing tags missing their open brackets that render as raw text."""
text = text.replace('- **strip-pagination** (`--strip-pagination`): remove baked-in print page numbers and running headers.', new_feature)

# Add install-to-calibre
new_lib = """  --apply               atomically replace accepted books in place (default: dry run)
  --install-to-calibre  with --apply, use calibredb to natively replace the format in the Calibre database
  --all                 enable all individual fix flags"""
text = text.replace('  --apply               atomically replace accepted books in place (default: dry run)', new_lib)

readme_file.write_text(text)
