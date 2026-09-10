#!/bin/sh
# Run the stdlib unittest suite. html5lib (for the --reserialize tests) is supplied via
# `uv run --with` when available; without uv those tests skip themselves. The ruff steps
# run the same pins CI runs, so the local loop fails on the same things CI fails on.
set -e
cd "$(dirname "$0")"
if command -v uvx >/dev/null 2>&1; then
    uvx ruff@0.16.2 check .
    uvx ruff@0.16.2 format --check .
else
    echo "note: uvx not found; skipping the ruff steps that CI runs" >&2
fi
if command -v uv >/dev/null 2>&1; then
    PYTHONPATH=src uv run --with html5lib python3 -m unittest discover -s tests -v
else
    echo "note: uv not found; html5lib-dependent tests will skip themselves" >&2
    PYTHONPATH=src python3 -m unittest discover -s tests -v
fi
