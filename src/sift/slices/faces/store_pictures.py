# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the face pictures live on the disk: the base every part of the store stands on."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from pathlib import Path

from sift.kernel.db import Database


def _write_pictures(written: Sequence[tuple[Path, bytes]]) -> None:
    """Put a pass's face pictures on the disk, the whole list in one hop to a thread."""
    for path, picture in written:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(picture)


def _remove_pictures(paths: Iterable[Path]) -> None:
    """Delete pictures, tolerating ones already gone, the whole list in one hop to a thread."""
    for path in paths:
        path.unlink(missing_ok=True)


def _read_pictures(paths: Sequence[Path]) -> list[bytes]:
    """The bytes of every picture that is still there, skipping the ones that are not."""
    return [path.read_bytes() for path in paths if path.is_file()]


def _digest(crop: bytes) -> str:
    from sift.slices.faces.crop import digest

    return digest(crop)


class PicturesStore:
    """The database handle and the picture directories."""

    def __init__(self, database: Database, *, data_dir: Path) -> None:
        self._db = database
        self._root = data_dir / "faces"

    @property
    def database(self) -> Database:
        """For the one caller that has to write on the same connection as something else."""
        return self._db

    @property
    def root(self) -> Path:
        """Everything this feature writes to disk, under one directory."""
        return self._root

    @property
    def detected_root(self) -> Path:
        return self._root / "detected"

    @property
    def reference_root(self) -> Path:
        return self._root / "references"

    @property
    def cover_root(self) -> Path:
        """Display-sized covers, rebuilt on demand, apart from the crops a rescan replaces."""
        return self._root / "covers"

    def cover_path(self, track_id: str) -> Path:
        """Where one face's cover picture is written, fanned out like the crops."""
        return self.crop_path(self.cover_root, track_id)

    def crop_path(self, root: Path, crop_id: str) -> Path:
        """Where one face picture is written, fanned out so no directory holds a library."""
        return root / crop_id[:2] / f"{crop_id}.jpg"

    def relative(self, root: Path, crop_id: str) -> str:
        """A face picture's path as the row spells it: with slashes, compared by text."""
        return self.crop_path(root, crop_id).relative_to(self._root).as_posix()

    def resolve(self, stored: str) -> Path:
        """A stored picture path back to a real one, refused outside the face directory."""
        candidate = (self._root / stored).resolve()
        root = self._root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError("a stored face picture is not inside the face directory")
        return candidate

    def _resolve_all(self, stored: Iterable[str]) -> list[Path]:
        """`resolve` over a list in one hop to a thread, skipping rows that name nothing."""
        return [self.resolve(path) for path in stored if path]

    async def read_pictures(self, stored: Sequence[str]) -> list[bytes | None]:
        """The bytes behind each stored picture path, in order, None where one has gone."""

        def read() -> list[bytes | None]:
            out: list[bytes | None] = []
            for one in stored:
                path = self.resolve(one) if one else None
                out.append(path.read_bytes() if path is not None and path.is_file() else None)
            return out

        return await asyncio.to_thread(read)

    async def picture_bytes(self, stored: object) -> bytes | None:
        """The bytes behind a stored picture path, or nothing where it names none or has gone.

        The one way to read a face picture, off the event loop. Takes whatever a row column holds,
        so callers need not coerce.
        """
        if not stored:
            return None
        path = str(stored)
        held = await asyncio.to_thread(lambda: _read_pictures([self.resolve(path)]))
        return held[0] if held else None

    async def reference_picture(self, reference_id: str) -> bytes | None:
        """The stored picture behind one reference, or nothing where the reference is numbers
        only."""
        row = await self._db.fetch_one(
            "SELECT crop_path FROM face_references WHERE id = ?", (reference_id,)
        )
        return await self.picture_bytes(None if row is None else row["crop_path"])
