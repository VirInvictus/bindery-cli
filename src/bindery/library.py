"""Calibre-library-aware helpers: find books and install a repaired format.

File placement is atomic and surgical: the repaired EPUB is written to a temporary
file in the target's directory, fsynced, then os.replace()d over the original so the
path and filename Calibre expects never change and no half-written file is ever
visible. With a resolved book id the ``data`` row follows through cquarry's write
module (OPF-resync queue included); without one, only the .epub is touched and
metadata.opf, cover.jpg, and metadata.db are left alone for Calibre's Quality Check
sync to reconcile. An optional backup is taken first.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
from pathlib import Path


def iter_epubs(root: Path):
    """Yield every .epub under a Calibre library tree, sorted. Case-insensitive on the
    suffix: Calibre emits lowercase, but a hand-added Book.EPUB should not be invisible."""
    yield from sorted(
        p for p in root.rglob("*.epub", case_sensitive=False) if p.is_file()
    )


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
    same-filesystem rename (atomic). File mode of the original is preserved. On any
    failure the temp file is removed, so a half-written .bindery.tmp never lingers in
    the library.
    """
    # A fresh format's destination does not exist yet; a replacement
    # inherits the original's mode.
    mode = target.stat().st_mode if target.exists() else 0o644
    tmp = target.with_name(target.name + ".bindery.tmp")
    try:
        shutil.copyfile(new_file, tmp)
        os.chmod(tmp, mode)
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    # fsync the directory too, so the rename itself survives a crash (either the old
    # or the new file is durable; never a missing or partial one).
    dfd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


class CalibreIdResolver:
    """Resolve Calibre book ids from ``metadata.db`` via cquarry — no guessing.

    Builds a lazy, one-shot map of EPUB path -> book id using cquarry's own
    layout logic (:meth:`CalibreDB.get_format_path`), so the path truth lives
    in exactly one place and a hand-renamed ``Author/Title (id)/`` directory
    can never cause the wrong book to be replaced. Returns ``None`` (caller
    falls back to the directory-name heuristic) when metadata.db is missing,
    unreadable, or the file is not a catalogued EPUB.
    """

    def __init__(self, library_root: Path) -> None:
        self._root = Path(library_root)
        self._paths: dict[str, int] | None = None

    def _load(self) -> None:
        if self._paths is not None:
            return
        self._paths = {}
        db_path = self._root / "metadata.db"
        if not db_path.is_file():
            return
        try:
            from cquarry.db import CalibreDB

            db = CalibreDB(str(db_path))
        except Exception:
            return
        try:
            # cquarry 1.8's format_path_index() is this map, built canonically
            # in one data⋈books query (the per-book get_format_path loop this
            # used to run is the same construction, N queries over). Keys are
            # re-normalized the resolver's historical way — resolve() for
            # symlinked library dirs, .lower() for case-insensitive matching.
            index = db.format_path_index()
            for path, bid in index.items():
                if not path.upper().endswith(".EPUB"):
                    continue  # the resolver maps EPUBs only
                self._paths[str(Path(path).resolve()).lower()] = bid
        finally:
            db.close()

    def id_for(self, epub: Path) -> int | None:
        """The catalogued book id for `epub`, or None if not in metadata.db."""
        self._load()
        return self._paths.get(str(Path(epub).resolve()).lower())

    @property
    def db_path(self) -> Path:
        """The metadata.db this resolver reads its id map from."""
        return self._root / "metadata.db"


def guess_calibre_id(epub: Path) -> str | None:
    """Legacy fallback: pull the id out of the ``(123)`` directory fragment.

    Only used when cquarry cannot resolve the id (no metadata.db, or the file
    is not catalogued) — the heuristic breaks on renamed directories, which is
    exactly why the resolver above is preferred.
    """
    match = re.search(r"\((\d+)\)/[^/]+\.epub$", str(epub.absolute()))
    return match.group(1) if match else None


def install_format(
    target: Path, new_file: Path, resolver: CalibreIdResolver | None = None
) -> None:
    """Install a repaired EPUB as a book's format through cquarry's write module.

    The book id comes from cquarry's metadata.db view when a resolver is given
    (accurate even for hand-renamed directories); the legacy ``(id)``
    directory-name guess is the fallback, and without any id the repaired
    file is saved atomically in place instead.

    With a book id, the repaired file is placed in the book's directory —
    an atomic replace over the catalogued file when one exists (same path,
    same ``data.name``; Calibre's layout never changes) — and the ``data``
    row follows through ``WritableCalibreDB``: ``remove_format`` +
    ``add_format`` in one ``batch()`` when the format exists (``add_format``
    refuses duplicates by design), a fresh ``add_format`` otherwise. The
    row's size stays truthful and the book lands in ``metadata_dirtied``, so
    Calibre regenerates its sidecar .opf. Files are the caller's
    responsibility in cquarry; they are placed here, atomically, before the
    row is written, and a failed row update degrades to the in-place save
    with a warning rather than losing the repair.

    This used to shell out to ``calibredb add_format``; the native path drops
    the external CLI dependency and the flag-shape crash class with it (the
    2026-08-31 ``--replace`` incident).
    """
    calibre_id: str | None = None
    if resolver is not None:
        bid = resolver.id_for(target)
        if bid is not None:
            calibre_id = str(bid)
    if calibre_id is None:
        calibre_id = guess_calibre_id(target)
    if calibre_id is None:
        import sys

        print(
            f"WARNING: Could not resolve the Calibre id for {target.name} "
            "(not in metadata.db, no (id) directory). Saving in place instead.",
            file=sys.stderr,
        )
        atomic_replace(target, new_file)
        return

    if resolver is not None:
        db_path = resolver.db_path
    else:
        # The legacy guess comes from a path inside a library tree whose root
        # only calibredb knew (its default library / CALIBRE_DBPATH). Mirror
        # that contract; with neither, do not guess where to write.
        env = os.environ.get("CALIBRE_DBPATH")
        db_path = Path(env) / "metadata.db" if env else None
    if db_path is None or not db_path.is_file():
        import sys

        print(
            f"WARNING: No metadata.db found for book {calibre_id} (no resolver "
            "library, CALIBRE_DBPATH unset or missing). Saving in place instead.",
            file=sys.stderr,
        )
        atomic_replace(target, new_file)
        return

    from cquarry.write import WritableCalibreDB

    placed = False
    try:
        with WritableCalibreDB(str(db_path)) as wdb:
            row = wdb.conn.execute(
                "SELECT name FROM data WHERE book = ? AND upper(format) = 'EPUB'",
                (int(calibre_id),),
            ).fetchone()
            if row is not None:
                atomic_replace(target, new_file)
                placed = True
                with wdb.batch():
                    wdb.remove_format(int(calibre_id), "EPUB")
                    wdb.add_format(
                        int(calibre_id), "EPUB", row["name"], new_file.stat().st_size
                    )
            else:
                book = wdb.conn.execute(
                    "SELECT path FROM books WHERE id = ?", (int(calibre_id),)
                ).fetchone()
                dest_dir = Path(db_path).parent / book["path"]
                name = new_file.stem
                dest_dir.mkdir(parents=True, exist_ok=True)
                atomic_replace(dest_dir / f"{name}.epub", new_file)
                placed = True
                with wdb.batch():
                    wdb.add_format(
                        int(calibre_id), "EPUB", name, new_file.stat().st_size
                    )
    except (ValueError, sqlite3.Error) as e:
        import sys

        if not placed:
            # The repair must never be lost to a database problem: save it in
            # place (same path the library already knows) and say what happened.
            atomic_replace(target, new_file)
            placed = True
        print(
            f"WARNING: the repaired file for book {calibre_id} was saved, but the "
            f"database row could not be updated ({e}); Calibre may show a stale "
            "size until its next metadata refresh.",
            file=sys.stderr,
        )
