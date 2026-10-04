# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the end of a file takes out of the face references: the file's name, and nothing else.

A reference picture is a face somebody's confirmation taught Sift, and it deliberately OUTLIVES the
appearance it was cut from: that is the whole point of filing one (see schema v24). So the end of
a file must not delete it, and `face_references.asset_id` carries no foreign key for exactly that
reason: `ON DELETE CASCADE` would throw away somebody's learned pictures, and `ON DELETE SET NULL`
would be the right shape but cannot be added to an existing column without rebuilding the table.

What must end with the file is the CLAIM. `asset_id` says which file the reference came from, so a
rescan can hand it to the appearance that replaces the one it came from
(`FaceStore.claim_references_in`). Once the file is gone there is nothing to rescan and the id names
nothing, so it is set to NULL, the same answer a reference from a pack or a folder has always
had, because none of those ever came from a file in this library.

`track_id` is left as it is, and that is deliberate rather than tidy-minded. A NULL `track_id`
reads as "orphaned by a rescan" to the matching that re-pins a reference by description to
another of the person's appearances. Nulling it here would hand a picture from a deleted file to
some other file at the next match. The appearance it names was
removed with the file (`face_tracks` cascades), so the id matches nothing and marks nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

from sift.kernel.db import Database, in_clause
from sift.kernel.forgetting import register_forgetting


class ForgetReferenceFiles:
    """Clearing a deleted file's id off the reference pictures filed from it."""

    name = "face-references"

    tables = ("face_references",)

    def __init__(self, database: Database) -> None:
        self._database = database

    async def forget(self, asset_ids: Sequence[str]) -> int:
        # The registry hands over ids whose asset row is already committed away; that contract
        # is the whole guard, as it is for every other forgetting here.
        sql, values = in_clause(
            "UPDATE face_references SET asset_id = NULL WHERE asset_id IN (?*) RETURNING id",
            list(asset_ids),
        )
        async with self._database.write() as connection:
            rows = list(await connection.execute_fetchall(sql, tuple(values)))
        return len(rows)


register_forgetting(ForgetReferenceFiles.name, ForgetReferenceFiles.tables, ForgetReferenceFiles)
