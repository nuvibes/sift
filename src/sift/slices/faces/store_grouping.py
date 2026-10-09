# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a person did to the groups by hand (set aside, ignored, moved, named), remembered by
the faces' descriptions so a rescan puts it back."""

from __future__ import annotations

import time
from collections.abc import Sequence

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce
from sift.kernel.db import in_clause
from sift.kernel.ids import new_id
from sift.slices.faces import recognize, tracking
from sift.slices.faces.models import (
    Vector,
)
from sift.slices.faces.store_pictures import (
    PicturesStore,
)
from sift.slices.faces.store_records import (
    _STAMP_PILE,
    now_ms,
)

#: How long one write of `drop_empty_piles` keeps the writer, how many faces one statement in it
#: takes off a pile and how many the write takes at most. Each face taken off moves its file's
#: counts, settled as the write commits, and a pile of thousands of named faces in one statement
#: would hold every other write for its whole length.
_DROP_BLOCK_SECONDS = 0.05
_FACES_PER_STEP = 200
_FACES_PER_WRITE = 400

#: How many piles one write of the count correction reads.
_SIZES_PER_WRITE = 2000

#: Piles with no unclaimed face left, read off the writer and asked again pile by pile under it.
_EMPTIED_PILES = (
    "SELECT id FROM face_piles WHERE id NOT IN "
    "(SELECT DISTINCT pile_id FROM face_tracks WHERE pile_id IS NOT NULL "
    "AND person_id IS NULL)"
)
_UNCLAIMED_ON = "SELECT 1 FROM face_tracks WHERE pile_id = ? AND person_id IS NULL LIMIT 1"
_TAKE_OFF = (
    "UPDATE face_tracks SET pile_id = NULL "
    "WHERE id IN (SELECT id FROM face_tracks WHERE pile_id = ? LIMIT ?)"
)
_DROP_PILE = "DELETE FROM face_piles WHERE id = ? RETURNING id"

#: The last pile of the next window of `_SIZES_PER_WRITE`, by id.
_SIZES_WINDOW_END = (
    "SELECT MAX(id) AS last FROM (SELECT id FROM face_piles WHERE id > ? ORDER BY id LIMIT ?)"
)
_CORRECT_SIZES = (
    "UPDATE face_piles SET size = "
    "(SELECT COUNT(*) FROM face_tracks "
    "  WHERE pile_id = face_piles.id AND person_id IS NULL) "
    "WHERE id > ? AND id <= ? AND size <> "
    "(SELECT COUNT(*) FROM face_tracks "
    "  WHERE pile_id = face_piles.id AND person_id IS NULL)"
)


class ByHandStore(PicturesStore):
    """What a person did to the groups by hand, remembered past a rescan."""

    async def set_aside(self, track_ids: Sequence[str]) -> str | None:
        """Move some faces into a pile of their own and mark it ignored. Returns the new pile's id.

        A split rather than a per-face flag, so part of a pile set aside works exactly as a whole
        one does: listed under Discarded, out of every regrouping, brought back face for face. The
        faces left behind stay where they were: answering for a stranger answers for nobody else.
        """
        if not track_ids:
            return None
        stamp = now_ms()
        pile_id = new_id()
        async with self._db.write() as connection:
            rows = list(
                await connection.execute_fetchall(
                    *in_clause(
                        "SELECT d.embedding AS embedding FROM face_tracks AS t "
                        "JOIN face_detections AS d ON d.track_id = t.id WHERE t.id IN (?*)",
                        list(track_ids),
                    )
                )
            )
            if not rows:
                return None
            middle = tracking.centroid([recognize.unpack(bytes(row["embedding"])) for row in rows])
            await connection.execute(
                "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at) "
                "VALUES (?, 'ignored', ?, ?, ?, ?)",
                (pile_id, recognize.pack(middle), len(track_ids), stamp, stamp),
            )
            sql, params = in_clause(
                "UPDATE face_tracks SET pile_id = ? WHERE id IN (?*)", list(track_ids)
            )
            await connection.execute(sql, (pile_id, *params))
            await connection.execute(_STAMP_PILE, (pile_id,))
            # These faces leave their group on Faces > Groups on every admin's other tabs.
            announce(EVERY_ADMIN, About.LIBRARY)
        return pile_id

    async def remember_ignored(self, pile_id: str) -> int:
        """Write down what a pile holds, so that setting it aside outlives the rows it sits on.

        One description per appearance (its best), keyed to its file as a removal is, since two
        people can look alike within any useful threshold. Rewrites rather than appends, so the
        pile comes back as it is now.
        """
        stamp = now_ms()
        async with self._db.write() as connection:
            rows = list(
                await connection.execute_fetchall(
                    "SELECT t.id AS track_id, t.asset_id AS asset_id, d.embedding AS embedding "
                    "FROM face_tracks AS t JOIN face_detections AS d ON d.track_id = t.id "
                    "WHERE t.pile_id = ? ORDER BY t.id, d.quality DESC, d.id",
                    (pile_id,),
                )
            )
            centroid = await connection.execute_fetchall(
                "SELECT centroid FROM face_piles WHERE id = ?", (pile_id,)
            )
            middle = list(centroid)
            if not rows or not middle:
                return 0
            await connection.execute("DELETE FROM face_ignored WHERE pile_id = ?", (pile_id,))
            seen: set[str] = set()
            written = 0
            for row in rows:
                track_id = str(row["track_id"])
                if track_id in seen:
                    continue
                seen.add(track_id)
                await connection.execute(
                    "INSERT INTO face_ignored (id, asset_id, pile_id, embedding, centroid, "
                    "recognizer, created_at) VALUES (?, ?, ?, ?, ?, "
                    "(SELECT s.recognizer FROM face_scans AS s WHERE s.asset_id = ?), ?)",
                    (
                        new_id(),
                        str(row["asset_id"]),
                        pile_id,
                        bytes(row["embedding"]),
                        bytes(middle[0]["centroid"]),
                        str(row["asset_id"]),
                        stamp,
                    ),
                )
                written += 1
        return written

    async def move_tracks(self, track_ids: Sequence[str], pile_id: str | None) -> str | None:
        """Put these faces in a pile: an existing one, or a new one when `pile_id` is None.

        Merging and splitting are one operation with two targets, because that is what they are.
        Moving some faces into another group merges part of one into it; moving them nowhere in
        particular makes a group of exactly them, which is a split. Writing them as two would be
        two code paths for one write.

        The destination is marked as built by hand, and so is a new one. That is the whole of what
        stops the next grouping pass throwing it away. See `replace_piles`.

        A move takes a face out of whatever pile it was in, which can empty that pile; the tidy-up
        is the caller's, in the same step as remembering the decision.
        """
        if not track_ids:
            return None
        stamp = now_ms()
        async with self._db.write() as connection:
            target = pile_id
            if target is None:
                target = new_id()
                # The middle of a hand-made pile is the middle of what was put in it. It is what a
                # later face is compared against when this pile has to be rebuilt after a rescan.
                sql, params = in_clause(
                    "SELECT d.embedding AS embedding FROM face_detections AS d "
                    "WHERE d.track_id IN (?*)",
                    list(track_ids),
                )
                rows = list(await connection.execute_fetchall(sql, tuple(params)))
                if not rows:
                    return None
                middle = tracking.centroid(
                    [recognize.unpack(bytes(row["embedding"])) for row in rows]
                )
                await connection.execute(
                    "INSERT INTO face_piles (id, status, centroid, size, by_hand, created_at, "
                    "updated_at) VALUES (?, 'open', ?, ?, 1, ?, ?)",
                    (target, recognize.pack(middle), len(track_ids), stamp, stamp),
                )
            else:
                changed = await connection.execute(
                    "UPDATE face_piles SET by_hand = 1, updated_at = ? WHERE id = ?",
                    (stamp, target),
                )
                if changed.rowcount == 0:
                    return None
            sql, params = in_clause(
                "UPDATE face_tracks SET pile_id = ? WHERE id IN (?*)", list(track_ids)
            )
            await connection.execute(sql, (target, *params))
            await connection.execute(
                "UPDATE face_piles SET size = "
                "(SELECT COUNT(*) FROM face_tracks WHERE pile_id = ?) WHERE id = ?",
                (target, target),
            )
            await connection.execute(_STAMP_PILE, (target,))
            # These faces change groups on Faces > Groups on every admin's other tabs.
            announce(EVERY_ADMIN, About.LIBRARY)
        return target

    async def remember_grouping(self, pile_id: str) -> int:
        """Write down what a hand-made pile holds, so the grouping outlives the rows it sits on.

        The same shape as `remember_ignored`, for the same reason: a rescan deletes every track a
        file had, so a pile somebody built by merging or splitting would be left claiming faces
        that no longer exist while the new ones came back among the clustering's own piles.

        Keyed to the file, because that is the claim being made. "These particular appearances, in
        this file, belong together" is what somebody said; applying it library-wide would silently
        drag somebody else's face into the pile on a resemblance nobody was asked about.

        Rewrites rather than appends, so a pile moved into twice is remembered as it is now.
        """
        stamp = now_ms()
        async with self._db.write() as connection:
            rows = list(
                await connection.execute_fetchall(
                    "SELECT t.id AS track_id, t.asset_id AS asset_id, d.embedding AS embedding "
                    "FROM face_tracks AS t JOIN face_detections AS d ON d.track_id = t.id "
                    "WHERE t.pile_id = ? ORDER BY t.id, d.quality DESC, d.id",
                    (pile_id,),
                )
            )
            centroid = list(
                await connection.execute_fetchall(
                    "SELECT centroid FROM face_piles WHERE id = ?", (pile_id,)
                )
            )
            if not rows or not centroid:
                return 0
            await connection.execute("DELETE FROM face_grouping WHERE pile_id = ?", (pile_id,))
            seen: set[str] = set()
            written = 0
            for row in rows:
                track_id = str(row["track_id"])
                if track_id in seen:
                    continue
                seen.add(track_id)
                await connection.execute(
                    "INSERT INTO face_grouping (id, asset_id, pile_id, embedding, centroid, "
                    "recognizer, created_at) VALUES (?, ?, ?, ?, ?, "
                    "(SELECT s.recognizer FROM face_scans AS s WHERE s.asset_id = ?), ?)",
                    (
                        new_id(),
                        str(row["asset_id"]),
                        pile_id,
                        bytes(row["embedding"]),
                        bytes(centroid[0]["centroid"]),
                        str(row["asset_id"]),
                        stamp,
                    ),
                )
                written += 1
        return written

    async def forget_grouping(self, track_ids: Sequence[str]) -> int:
        """Forget the hand-made grouping of these particular faces.

        What moving a face somewhere else has to do as well as moving it. Left behind, the old
        memory would put it back in the pile it was moved out of at the next scan of that file:
        the same trap restoring an ignored pile has, and the reason both are cleared rather than
        only flipped.

        Matched on the description rather than on the track id, because the id is what a rescan
        destroys and the description is what survives it.
        """
        if not track_ids:
            return 0
        async with self._db.write() as connection:
            sql, params = in_clause(
                "SELECT DISTINCT d.embedding AS embedding, t.asset_id AS asset_id "
                "FROM face_tracks AS t JOIN face_detections AS d ON d.track_id = t.id "
                "WHERE t.id IN (?*)",
                list(track_ids),
            )
            rows = list(await connection.execute_fetchall(sql, tuple(params)))
            gone = 0
            for row in rows:
                removed = list(
                    await connection.execute_fetchall(
                        "DELETE FROM face_grouping WHERE asset_id = ? AND embedding = ? "
                        "RETURNING id",
                        (str(row["asset_id"]), bytes(row["embedding"])),
                    )
                )
                gone += len(removed)
        return gone

    async def grouped_for(self, asset_id: str) -> list[tuple[str, Vector, bytes]]:
        """What was grouped by hand in one file: the pile, the description, the pile's middle.

        Narrowed to descriptions the model that just described this file produced. See
        `confirmations_for` for why that is a correctness condition rather than a tidy-up.
        """
        rows = await self._db.fetch_all(
            "SELECT pile_id, embedding, centroid FROM face_grouping "
            "WHERE asset_id = ? AND recognizer = (SELECT s.recognizer FROM face_scans AS s WHERE s.asset_id = ?)",
            (asset_id, asset_id),
        )
        return [
            (str(row["pile_id"]), recognize.unpack(bytes(row["embedding"])), bytes(row["centroid"]))
            for row in rows
        ]

    async def group_again(self, pile_id: str, track_ids: Sequence[str], centroid: bytes) -> None:
        """Put freshly found faces back into the hand-made pile they were moved into.

        The pile row may or may not still exist: re-grouping leaves a hand-made pile alone, but a
        rescan deletes the tracks that were in it and the empty row is then tidied away. So this
        recreates it when it has gone and reuses it when it has not, under the SAME id, which is
        what makes faces put together by hand come back together rather than as a heap.
        """
        if not track_ids:
            return
        stamp = now_ms()
        async with self._db.write() as connection:
            await connection.execute(
                "INSERT INTO face_piles (id, status, centroid, size, by_hand, created_at, "
                "updated_at) VALUES (?, 'open', ?, ?, 1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET by_hand = 1, updated_at = excluded.updated_at",
                (pile_id, centroid, len(track_ids), stamp, stamp),
            )
            sql, params = in_clause(
                "UPDATE face_tracks SET pile_id = ? WHERE id IN (?*)", list(track_ids)
            )
            await connection.execute(sql, (pile_id, *params))
            await connection.execute(
                "UPDATE face_piles SET size = "
                "(SELECT COUNT(*) FROM face_tracks WHERE pile_id = ?) WHERE id = ?",
                (pile_id, pile_id),
            )
            await connection.execute(_STAMP_PILE, (pile_id,))

    async def by_hand_track_ids(self) -> set[str]:
        """The faces sitting in a pile somebody built, which re-grouping must not touch.

        The same exclusion an ignored pile gets, for the same reason: re-clustering them would
        scatter a decision a person made back into whatever the arithmetic thinks.
        """
        rows = await self._db.sweep_all(
            "SELECT t.id AS id FROM face_tracks AS t JOIN face_piles AS p ON p.id = t.pile_id "
            "WHERE p.by_hand = 1",
            (),
            what="groups made by hand",
        )
        return {str(row["id"]) for row in rows}

    async def forget_ignored(self, pile_id: str) -> int:
        """Stop setting these faces aside. What restoring a pile has to do as well as flipping it.

        Without this, bringing a pile back would work until the next scan of those files and then
        quietly set them aside again, which is worse than not having the memory at all, because
        the person did the one thing the screen offers and it came undone later.
        """
        async with self._db.write() as connection:
            rows = list(
                await connection.execute_fetchall(
                    "DELETE FROM face_ignored WHERE pile_id = ? RETURNING id", (pile_id,)
                )
            )
        return len(rows)

    async def remember_confirmation(self, track_id: str, person_id: str) -> None:
        """Write down that a person named this face, against the face's own description.

        The third decision of the same shape, and the one with a fallback. Confirming also files the
        face as one of that person's reference photos, and references survive a rescan, so the NAME
        comes back by itself. What would not come back is the fact that somebody decided it: every
        "you said so" would become "Sift recognized them" after a sweep, and anything between the
        two confidence thresholds a question that had already been answered.

        Rewritten rather than appended, so naming a face, taking it off and naming it again leaves
        one row rather than three.
        """
        async with self._db.write() as connection:
            rows = list(
                await connection.execute_fetchall(
                    "SELECT t.asset_id AS asset_id, d.embedding AS embedding FROM face_tracks AS t "
                    "JOIN face_detections AS d ON d.track_id = t.id "
                    "WHERE t.id = ? ORDER BY d.quality DESC, d.id LIMIT 1",
                    (track_id,),
                )
            )
            if not rows:
                return
            asset_id = str(rows[0]["asset_id"])
            await connection.execute(
                "DELETE FROM face_confirmations WHERE asset_id = ? AND person_id = ? "
                "AND embedding = ?",
                (asset_id, person_id, bytes(rows[0]["embedding"])),
            )
            await connection.execute(
                "INSERT INTO face_confirmations "
                "(id, asset_id, person_id, embedding, recognizer, created_at) "
                "VALUES (?, ?, ?, ?, "
                "(SELECT s.recognizer FROM face_scans AS s WHERE s.asset_id = ?), ?)",
                (
                    new_id(),
                    asset_id,
                    person_id,
                    bytes(rows[0]["embedding"]),
                    asset_id,
                    now_ms(),
                ),
            )

    async def forget_confirmation(self, track_id: str, person_id: str) -> int:
        """Stop re-applying a name. What taking one off has to do as well as clearing the row.

        Without it, a name taken off would come back at the next scan of that file, which is worse
        than never having remembered it, because the person did the one thing the screen offers and
        it came undone later with nothing to say why. The same trap restoring an ignored pile has.
        """
        async with self._db.write() as connection:
            rows = list(
                await connection.execute_fetchall(
                    "SELECT t.asset_id AS asset_id, d.embedding AS embedding FROM face_tracks AS t "
                    "JOIN face_detections AS d ON d.track_id = t.id "
                    "WHERE t.id = ? ORDER BY d.quality DESC, d.id LIMIT 1",
                    (track_id,),
                )
            )
            if not rows:
                return 0
            gone = list(
                await connection.execute_fetchall(
                    "DELETE FROM face_confirmations WHERE asset_id = ? AND person_id = ? "
                    "AND embedding = ? RETURNING id",
                    (str(rows[0]["asset_id"]), person_id, bytes(rows[0]["embedding"])),
                )
            )
        return len(gone)

    async def confirmations_for(self, asset_id: str) -> list[tuple[str, Vector]]:
        """Who was named in this file, by description: the person, and the face they were named on.

        **Only descriptions the model that just described this file produced**, and that is a
        correctness condition. What the caller does with these is compare them against the faces a
        pass has just found, by how closely the numbers agree, and numbers from two different
        models are not near or far from each other, they are in different spaces. Two models of the
        same width make that comparison SUCCEED rather than fail, so the fault is a name put back on
        the wrong face with nothing anywhere saying why.

        The model in force is read off this file's own scan row, which the pass rewrote a moment
        ago, so this asks "was this decision made on numbers comparable with the ones just found".
        A decision whose model is unknown (a row whose file had no scan row when the column was
        added) satisfies nothing and is left out, which is the safe direction: it comes back the
        first time the file is measured again by a model that stamps it.
        """
        rows = await self._db.fetch_all(
            "SELECT person_id, embedding FROM face_confirmations "
            "WHERE asset_id = ? AND recognizer = (SELECT s.recognizer FROM face_scans AS s WHERE s.asset_id = ?)",
            (asset_id, asset_id),
        )
        return [(str(row["person_id"]), recognize.unpack(bytes(row["embedding"]))) for row in rows]

    async def ignored_for(self, asset_id: str) -> list[tuple[str, Vector, bytes]]:
        """What was set aside in one file: the pile it belonged to, its description, its middle.

        Narrowed to descriptions the model that just described this file produced. See
        `confirmations_for` for why that is a correctness condition rather than a tidy-up.
        """
        rows = await self._db.fetch_all(
            "SELECT pile_id, embedding, centroid FROM face_ignored "
            "WHERE asset_id = ? AND recognizer = (SELECT s.recognizer FROM face_scans AS s WHERE s.asset_id = ?)",
            (asset_id, asset_id),
        )
        return [
            (str(row["pile_id"]), recognize.unpack(bytes(row["embedding"])), bytes(row["centroid"]))
            for row in rows
        ]

    async def set_aside_again(
        self, pile_id: str, track_ids: Sequence[str], centroid: bytes
    ) -> None:
        """Put freshly found faces back into the pile they were set aside in.

        The pile row may or may not still exist. An ignored pile is left alone by regrouping, but a
        rescan deletes the tracks that were in it and the empty row is then tidied away, so this
        recreates it when it has gone and reuses it when it has not, under the SAME id, which is
        what makes faces set aside together come back together rather than as a heap.
        """
        if not track_ids:
            return
        stamp = now_ms()
        async with self._db.write() as connection:
            await connection.execute(
                "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at) "
                "VALUES (?, 'ignored', ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status = 'ignored', updated_at = excluded.updated_at",
                (pile_id, centroid, len(track_ids), stamp, stamp),
            )
            sql, params = in_clause(
                "UPDATE face_tracks SET pile_id = ? WHERE id IN (?*)", list(track_ids)
            )
            await connection.execute(sql, (pile_id, *params))
            await connection.execute(
                "UPDATE face_piles SET size = "
                "(SELECT COUNT(*) FROM face_tracks WHERE pile_id = ?) WHERE id = ?",
                (pile_id, pile_id),
            )
            await connection.execute(_STAMP_PILE, (pile_id,))

    async def drop_empty_piles(self) -> int:
        """Remove piles with no unclaimed faces left in them, and correct the counts on the rest.

        A pile every one of whose faces has been named is a question that has been answered, and
        leaving the row behind means a count that never comes down and a page of empty cards.

        The counts are put right in the same step. `size` is written when a pile is made and not
        touched by naming or removing a face, so without this one face named of five leaves a row
        claiming five. The group screens recount per viewer and never read the column, so the drift
        would go unnoticed and be believed by the next thing to read it.

        The named faces still on an emptied pile come off it a few at a time, as deleting the pile
        would take them off anyway, each write ending after `_DROP_BLOCK_SECONDS`. A pile that gains
        an unclaimed face meanwhile is kept.
        """
        emptied = await self._db.sweep_all(_EMPTIED_PILES, what="groups left empty")
        waiting = [str(row["id"]) for row in emptied]
        dropped = 0
        while waiting:
            async with self._db.write() as connection:
                began, moved = time.monotonic(), 0
                while (
                    waiting
                    and moved < _FACES_PER_WRITE
                    and time.monotonic() - began < _DROP_BLOCK_SECONDS
                ):
                    pile_id = waiting[0]
                    if await connection.execute_fetchall(_UNCLAIMED_ON, (pile_id,)):
                        waiting.pop(0)
                        continue
                    taken = await connection.execute(_TAKE_OFF, (pile_id, _FACES_PER_STEP))
                    if taken.rowcount > 0:
                        moved += taken.rowcount
                        continue
                    dropped += len(list(await connection.execute_fetchall(_DROP_PILE, (pile_id,))))
                    waiting.pop(0)
        after = ""
        while True:
            async with self._db.write() as connection:
                rows = list(
                    await connection.execute_fetchall(_SIZES_WINDOW_END, (after, _SIZES_PER_WRITE))
                )
                last = rows[0]["last"] if rows else None
                if last is None:
                    break
                await connection.execute(_CORRECT_SIZES, (after, last))
            after = str(last)
        return dropped
