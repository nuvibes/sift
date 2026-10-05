# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pictures a folder import left out, by file, so its report can say which."""

from __future__ import annotations

from collections.abc import Sequence

from sift.kernel.db import Database

_CLEAR_OTHERS = "DELETE FROM face_folder_left_out WHERE job_id != ?"
_HELD = "SELECT COUNT(*) AS n FROM face_folder_left_out WHERE job_id = ?"
_KEEP = "INSERT OR IGNORE INTO face_folder_left_out (job_id, file, reason) VALUES (?, ?, ?)"
_OF = "SELECT file, reason FROM face_folder_left_out WHERE job_id = ? ORDER BY reason, file"


class LeftOutStore:
    """`face_folder_left_out`: one import's pictures at a time."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def begin(self, job_id: str) -> None:
        """Clear every other import's rows; this one's stay, for a run read again after a pause."""
        await self._db.execute(_CLEAR_OTHERS, (job_id,))

    async def keep(self, job_id: str, rows: Sequence[tuple[str, str]], *, most: int) -> None:
        """Keep `(file, reason)` rows for this import, never past `most` in all."""
        if not rows:
            return
        async with self._db.write() as connection:
            (held,) = await connection.execute_fetchall(_HELD, (job_id,))
            room = most - int(held["n"])
            await connection.executemany(_KEEP, [(job_id, *row) for row in rows[: max(room, 0)]])

    async def of(self, job_id: str) -> list[tuple[str, str]]:
        """This import's `(file, reason)` rows, by reason and then file."""
        rows = await self._db.fetch_all(_OF, (job_id,))
        return [(str(row["file"]), str(row["reason"])) for row in rows]
