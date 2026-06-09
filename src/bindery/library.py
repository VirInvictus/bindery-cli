"""Calibre-library-aware helpers: find books and replace a format file in place.

Replacement is atomic and surgical. It writes the repaired EPUB to a temporary file
in the same directory, fsyncs it, then os.replace()s it over the original so the path
and filename Calibre expects never change and no half-written file is ever visible.
Only the .epub is touched; metadata.opf, cover.jpg, and metadata.db are left alone for
Calibre's Quality Check sync to reconcile. An optional backup is taken first.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def iter_epubs(root: Path):
    """Yield every .epub under a Calibre library tree, sorted."""
    yield from sorted(p for p in root.rglob("*.epub") if p.is_file())


def backup_path(epub: Path, backup_dir: Path | None) -> Path:
    """Where the backup of `epub` should go."""
    if backup_dir is None:
        return epub.with_suffix(epub.suffix + ".bak")
    # Mirror Author/Title (id)/file.epub under backup_dir to avoid name collisions.
    return backup_dir / epub.parent.name / epub.name


def make_backup(epub: Path, backup_dir: Path | None) -> Path:
    dst = backup_path(epub, backup_dir)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(epub, dst)
    return dst


def atomic_replace(target: Path, new_file: Path) -> None:
    """Replace `target` with the contents of `new_file`, atomically and in place.

    `new_file` is copied into the target's directory first so the final os.replace is a
    same-filesystem rename (atomic). File mode of the original is preserved.
    """
    mode = target.stat().st_mode
    tmp = target.with_name(target.name + ".bindery.tmp")
    shutil.copyfile(new_file, tmp)
    os.chmod(tmp, mode)
    with open(tmp, "rb") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, target)
