#!/bin/sh
# Run the stdlib unittest suite.
set -e
cd "$(dirname "$0")"
PYTHONPATH=src python3 -m unittest discover -s tests -v
