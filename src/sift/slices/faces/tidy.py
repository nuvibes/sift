# SPDX-License-Identifier: AGPL-3.0-or-later
"""Face pictures nothing points at any more.

Every scan of a file replaces what the previous scan of it found. The rows go in one transaction;
the pictures those rows named are on disk, and a pass removes the pictures it replaces. This
tidying is for what built up before that, and for the two cases a pass cannot reach: pictures
belonging to a file that has since been removed from the library, and covers cut for faces that
no longer exist. Unreachable pictures are otherwise invisible: nothing asks for them.

Everything here is rebuildable. A detected crop costs one decode of the file it came from and a
cover costs the same, so removing one wrongly costs a moment rather than a picture. A **reference**
crop is not rebuildable (it is the only copy of a face somebody chose), which is why the survey
below counts by what the tables name rather than by which directory a file sits in.
"""

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

# Every table that names a picture on disk. Missing one here would offer to delete pictures that
# are still in use, so it is written as one list and read as one query rather than three places
# each remembering to include the third.
_NAMED_PICTURES = (
    "SELECT crop_path FROM face_detections",
    "SELECT crop_path FROM face_references WHERE crop_path IS NOT NULL",
    "SELECT crop_path FROM pack_entry_faces WHERE crop_path IS NOT NULL",
)

# Covers are not stored in a table at all: one is cut on demand and named after the face it belongs
# to, so the file itself is the record. What makes a cover reachable is that its face still exists.
_LIVE_TRACKS = "SELECT id FROM face_tracks"


def _one_spelling(value: str) -> str:
    """A stored picture path, however the machine that wrote it spelled a separator.

    Rows are written with slashes, but older rows written on Windows can hold backslashes, so a
    library moved between machines can hold both. This sweep decides what nothing names any more
    and deletes it, and a spelling it does not recognize would read as exactly that.

    Tolerated on the way in rather than repaired in place: the pictures are rebuildable and a
    migration to rewrite them would be a write over somebody's library to fix a comparison.
    """
    return value.replace("\\", "/")


class LeftoverFacePictures:
    """Cropped faces and covers on disk that no row names."""

    name = "leftover-face-pictures"
    #: Reads the whole faces directory, a file per face ever detected.
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
        # Models once lived INSIDE the faces folder, and the one-time move into the device's store
        # can leave some there: a move that failed is tried again at the next start, and a file the
        # store holds a different copy of is left rather than chosen between by deleting. No row
        # names them, so the walk below would offer them up as leftovers: hundreds of megabytes that
        # read as space freed and may be the only copy. They are not pictures; they are stepped over.
        self._models = self._store.root / LIBRARY_MODELS

    async def _orphans(self) -> list[Path]:
        named: set[str] = set()
        live_covers: set[Path] = set()
        # One lane block for all four, because this pass is deciding what to DELETE and the
        # four answers have to describe the same moment. Read separately, a face added
        # between two of them is a picture nothing appears to name.
        #
        # Deliberately whole-library, all four: what they are compared against is the set of
        # files actually on disk, which is not a question SQLite can be asked.
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
