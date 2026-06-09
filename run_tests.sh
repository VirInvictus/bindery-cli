#!/bin/sh
# Run the stdlib unittest suite. html5lib (for the --reserialize tests) is supplied via
# `uv run --with` when available; otherwise those tests skip themselves.
set -e
cd "$(dirname "$0")"
if command -v uv >/dev/null 2>&1; then
    PYTHONPATH=src uv run --with html5lib python3 -m unittest discover -s tests -v
else
    PYTHONPATH=src python3 -m unittest discover -s tests -v
fi
