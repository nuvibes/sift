# SPDX-License-Identifier: AGPL-3.0-or-later
"""Face pictures nothing points at any more: those of removed files, covers of gone faces, and
what built up before passes removed what they replace. Counted by what the tables name, since a
reference crop cannot be rebuilt."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sift.kernel.log import get_logger
from sift.kernel.ml.store import LIBRARY_MODELS
from sift.kernel.tidy import (
    Leftovers,
    Resources,
    in_thread_files,
    register_tidying,
    remove_files,
    size_of,
)
from sift.slices.faces.store import Store

log = get_logger(__name__)

# Every table that names a picture on disk, in one list: missing one would delete pictures in use.
_NAMED_PICTURES = (
    "SELECT crop_path FROM face_detections",
    "SELECT crop_path FROM face_references WHERE crop_path IS NOT NULL",
    "SELECT crop_path FROM pack_entry_faces WHERE crop_path IS NOT NULL",
)

# Covers have no table: one is reachable while its face exists.
_LIVE_TRACKS = "SELECT id FROM face_tracks"


def _one_spelling(value: str) -> str:
    """A stored picture path with either separator, as Windows rows may hold backslashes."""
    return value.replace("\\", "/")


class LeftoverFacePictures:
    """Cropped faces and covers on disk that no row names."""

    name = "leftover-face-pictures"
    costly = True
    title = "Face pictures from earlier scans"
    noun = "picture"
    nouns = "pictures"
    detail = (
        "Cropped faces left behind when a file was scanned again or removed from your "
        "library. No screen shows these pictures any more. The faces you can see are "
        "untouched, and so is every reference picture."
    )

    def __init__(self, resources: Resources) -> None:
        self._db = resources.database
        self._store = Store(resources.database, data_dir=resources.settings.data_dir)
        # Models once lived in the faces folder and may linger; no row names them, so skip them.
        self._models = self._store.root / LIBRARY_MODELS

    async def _orphans(self) -> list[Path]:
        named: set[str] = set()
        live_covers: set[Path] = set()
        # One lane block for all four reads, so they describe one moment before anything is deleted.
        async with self._db.sweep("leftover face pictures") as connection:
            for query in _NAMED_PICTURES:
                for row in await connection.execute_fetchall(query, ()):
                    named.add(_one_spelling(str(row["crop_path"])))
            live_covers = {
                self._store.cover_path(str(row["id"]))
                for row in await connection.execute_fetchall(_LIVE_TRACKS, ())
            }

        root = self._store.root
        orphans: list[Path] = []
        for path in await in_thread_files(root):
            if self._models in path.parents:
                continue
            if path in live_covers:
                continue
            if _one_spelling(path.relative_to(root).as_posix()) in named:
                continue
            orphans.append(path)
        return orphans

    async def survey(self) -> Leftovers:
        orphans = await self._orphans()
        return Leftovers(
            name=self.name,
            title=self.title,
            detail=self.detail,
            noun=self.noun,
            nouns=self.nouns,
            count=len(orphans),
            frees_bytes=await asyncio.to_thread(size_of, orphans),
        )

    async def run(self) -> int:
        removed = await asyncio.to_thread(remove_files, await self._orphans())
        log.info("faces.tidy.leftover_pictures", removed=removed)
        return removed


def register() -> None:
    register_tidying(LeftoverFacePictures.name, LeftoverFacePictures)
