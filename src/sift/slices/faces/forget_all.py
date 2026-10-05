# SPDX-License-Identifier: AGPL-3.0-or-later
"""Delete face data: every face, reference and picture, a bounded batch per write.

There is one writer, and a sign-in queues behind whatever holds it. So each table goes a batch at a
time with the writer given back between batches, and a batch is sized by TIME rather than rows: one
row's cost differs many times over between tables and between a quiet machine and a busy one, so a
fixed row count that is quick on one is seconds on another. Each write is measured and the next
sized to fit `TURN_SECONDS`. The pictures go last, after every row naming them, with no write held
while the disk works. It runs as a job (`FACE_FORGET`), so Activity shows it.

The names this feature put on files go with it, and only those: a person attached by hand stays.
`face_removals` stays too: it says a picture is not a face at all, a fact about the file.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from sift.kernel.cache_stamp import bump_every_cache_stamp
from sift.kernel.changes import About, announce
from sift.kernel.db import Database
from sift.kernel.ledger import Actor, record_event
from sift.kernel.vocabulary import Subject
from sift.slices.faces.settings import ENABLED_KEY

#: How long one write may hold the writer, in seconds: a sign-in queued behind it waits about this.
TURN_SECONDS = 0.25

#: Rows the first write of each table takes, before anything about its cost is known.
FIRST_BATCH = 50

#: The most rows one write ever takes, however cheap they measure: a measurement is one sample.
MOST_BATCH = 5000

_UNNAMED = (
    "SELECT COUNT(DISTINCT asset_id) AS n FROM asset_people WHERE (asset_id, person_id) IN "
    "(SELECT asset_id, person_id FROM face_asset_people)"
)
_CLAIMS = "SELECT rowid, asset_id, person_id FROM face_asset_people LIMIT ?"
_TAKE_NAME = "DELETE FROM asset_people WHERE asset_id = ? AND person_id = ?"
_TAKE_CLAIM = "DELETE FROM face_asset_people WHERE rowid = ?"

# Literal statements, never a table name formatted in. Rows that reference others go first.
TABLES = (
    "DELETE FROM face_rejections WHERE rowid IN (SELECT rowid FROM face_rejections LIMIT ?)",
    "DELETE FROM face_rejected WHERE rowid IN (SELECT rowid FROM face_rejected LIMIT ?)",
    "DELETE FROM face_successors WHERE rowid IN (SELECT rowid FROM face_successors LIMIT ?)",
    "DELETE FROM face_confirmations WHERE rowid IN (SELECT rowid FROM face_confirmations LIMIT ?)",
    "DELETE FROM face_ignored WHERE rowid IN (SELECT rowid FROM face_ignored LIMIT ?)",
    "DELETE FROM face_grouping WHERE rowid IN (SELECT rowid FROM face_grouping LIMIT ?)",
    "DELETE FROM face_detections WHERE rowid IN (SELECT rowid FROM face_detections LIMIT ?)",
    "DELETE FROM face_tracks WHERE rowid IN (SELECT rowid FROM face_tracks LIMIT ?)",
    "DELETE FROM face_piles WHERE rowid IN (SELECT rowid FROM face_piles LIMIT ?)",
    "DELETE FROM face_references WHERE rowid IN (SELECT rowid FROM face_references LIMIT ?)",
    "DELETE FROM face_folder_left_out"
    " WHERE rowid IN (SELECT rowid FROM face_folder_left_out LIMIT ?)",
    "DELETE FROM face_packs WHERE rowid IN (SELECT rowid FROM face_packs LIMIT ?)",
    "DELETE FROM face_scans WHERE rowid IN (SELECT rowid FROM face_scans LIMIT ?)",
)

Touched = Callable[[Sequence[str]], Awaitable[None]]
Progress = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class Turns:
    """How many rows the next write takes, sized from what the last ones cost.

    It shrinks at once to what fits the turn and grows by doubling at most, so one cheap sample (a
    stretch of small rows, a moment the machine was idle) cannot buy a write many times too long.
    """

    def __init__(
        self,
        *,
        first: int = FIRST_BATCH,
        turn: float = TURN_SECONDS,
        most: int = MOST_BATCH,
    ) -> None:
        self.size = max(1, min(first, most))
        self._turn = turn
        self._most = most

    def took(self, rows: int, seconds: float) -> None:
        """A write of `rows` rows held the writer `seconds`."""
        if rows <= 0:
            return
        fits = self._most if seconds <= 0 else int(self._turn * rows / seconds)
        self.size = max(1, min(self._most, self.size * 2, fits))


async def forget_all(
    database: Database,
    *,
    actor: Actor,
    roots: Sequence[Path],
    touched: Touched | None = None,
    progress: Progress | None = None,
    turn: float = TURN_SECONDS,
    first: int = FIRST_BATCH,
    clock: Clock = time.perf_counter,
) -> int:
    """Delete it all. Returns how many files lost a name this feature put on them.

    `touched` hears the files each batch took a name off, for the search index; it writes too, so
    its time counts against the batch that named the files. The event goes in the last write, with
    the count read before the first takes the rows that say so.
    """
    row = await database.fetch_one(_UNNAMED, ())
    unnamed = int(row["n"]) if row is not None else 0
    steps = len(TABLES) + 2
    turns = Turns(first=first, turn=turn)
    while True:
        async with database.write() as connection:
            started = clock()
            rows = list(await connection.execute_fetchall(_CLAIMS, (turns.size,)))
            await connection.executemany(
                _TAKE_NAME, [(str(one["asset_id"]), str(one["person_id"])) for one in rows]
            )
            await connection.executemany(_TAKE_CLAIM, [(int(one["rowid"]),) for one in rows])
        held = clock() - started
        if not rows:
            break
        if touched is not None:
            started = clock()
            await touched(sorted({str(one["asset_id"]) for one in rows}))
            held = max(held, clock() - started)
        turns.took(len(rows), held)
        await asyncio.sleep(0)
    # The walls drop the names now rather than when every face table is empty.
    async with database.write() as connection:
        announce(await bump_every_cache_stamp(connection), About.LIBRARY)
    for done, statement in enumerate(TABLES, start=1):
        if progress is not None:
            await progress(done / steps)
        # Each table its own measure: a row of one costs nothing like a row of another.
        turns = Turns(first=first, turn=turn)
        while True:
            async with database.write() as connection:
                started = clock()
                cursor = await connection.execute(statement, (turns.size,))
            if not cursor.rowcount:
                break
            turns.took(cursor.rowcount, clock() - started)
            await asyncio.sleep(0)
    async with database.write() as connection:
        # The subject is the feature's switch: the act was done to the install, not to a file.
        await record_event(
            connection,
            actor=actor,
            verb="forgot",
            subject=Subject(kind="setting", id=ENABLED_KEY, name="Faces"),
            count=unnamed or None,
        )
        announce(await bump_every_cache_stamp(connection), About.LIBRARY)
    # Outside every write: no row names a picture any more, and the writer is free while the disk
    # works.
    for root in roots:
        await asyncio.to_thread(remove_tree, root)
    if progress is not None:
        await progress(1.0)
    return unnamed


def remove_tree(root: Path) -> None:
    """Every picture under `root`, and the folders holding them."""
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink(missing_ok=True)
        else:
            path.rmdir()
    root.rmdir()
