# SPDX-License-Identifier: AGPL-3.0-or-later
"""The groups of faces nobody has named yet, and the proposals that a group may be somebody."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce, telling
from sift.kernel.db import Connection, Row, in_clause
from sift.kernel.ids import new_id
from sift.slices.faces import recognize, tracking
from sift.slices.faces.models import (
    Attribution,
    PileStatus,
    Vector,
)
from sift.slices.faces.store_pictures import (
    PicturesStore,
)
from sift.slices.faces.store_records import (
    _PILES_PER_READ,
    _STAMP_PILE,
    PileProposal,
    StoredTrack,
    _track,
    now_ms,
)

#: A proposal's own columns, in the order `Store._carry_proposals` writes them back.
_PROPOSAL_COLUMNS = (
    "pile_id",
    "person_id",
    "reason",
    "folder_id",
    "files",
    "of_files",
    "state",
    "created_at",
    "updated_at",
)


class PilesStore(PicturesStore):
    """The groups of faces nobody has named yet, and the proposals on them."""

    # --- piles -------------------------------------------------------------------------------

    async def piles(
        self, status: PileStatus | None = None, *, limit: int | None = None, offset: int = 0
    ) -> list[Row]:
        """Piles, largest first, paged, ordered by size then id so a page is stable.

        **Largest first is the working order**: a named pile gives the person a reference, and the
        next pass folds smaller piles of the same face into her with nobody asked
        (`UnidentifiedQueue.advice` says so in words).
        """
        if status is None:
            if limit is None:
                return await self._db.fetch_all(
                    "SELECT * FROM face_piles ORDER BY size DESC, id", ()
                )
            return await self._db.fetch_all(
                "SELECT * FROM face_piles ORDER BY size DESC, id LIMIT ? OFFSET ?", (limit, offset)
            )
        if limit is None:
            return await self._db.fetch_all(
                "SELECT * FROM face_piles WHERE status = ? ORDER BY size DESC, id", (status.value,)
            )
        return await self._db.fetch_all(
            "SELECT * FROM face_piles WHERE status = ? ORDER BY size DESC, id LIMIT ? OFFSET ?",
            (status.value, limit, offset),
        )

    async def replace_piles(
        self,
        groups: Sequence[tuple[Vector, Sequence[str]]],
        *,
        kept: Mapping[int, str] | None = None,
    ) -> list[str]:
        """Write the piles, leaving the ones a person made exactly as they are.

        Re-grouping is something Sift does; setting a pile aside is something a person did, and the
        second must survive the first. So the tracks in an ignored pile are left alone and are not
        offered up for regrouping, or every re-group would resurrect what was ignored.

        A pile somebody built by merging or splitting is the same claim in a different place, and
        gets the same protection. It is open rather than ignored, so without the `by_hand` test the
        delete below would take it: a merge undone by the next grouping pass, silently, with the
        faces scattered back to wherever the arithmetic puts them.

        `kept` says which groups keep an existing pile's identity (position in `groups` -> pile
        id): that pile is rewritten in place rather than deleted and remade, so a screen showing
        it goes on showing it. Every open pile the arithmetic made and nothing kept is deleted.
        Hands back one id per group, in order.

        **A proposal goes with its faces.** A group proposed as somebody (`face_pile_proposals`)
        whose pile is deleted here is carried to the new pile that holds more than half of the
        faces it had, in the state it was in, so a question already on the board stays there and
        an answer already given is still remembered. A group the arithmetic split or scattered has
        no such pile and its proposal goes; the folder pass makes it again for whichever group now
        leads the folder, since a new pile moves the folder's stamp (`FaceEvidence.stamps`).
        """
        stamp = now_ms()
        kept = dict(kept or {})
        made: list[str] = []
        async with self._db.write() as connection:
            leaving = await self._proposals_leaving(connection, list(kept.values()))
            if kept:
                sql, params = in_clause(
                    "DELETE FROM face_piles WHERE status = 'open' AND by_hand = 0 "
                    "AND id NOT IN (?*)",
                    list(kept.values()),
                )
                await connection.execute(sql, params)
            else:
                await connection.execute(
                    "DELETE FROM face_piles WHERE status = ? AND by_hand = 0", ("open",)
                )
            for position, (centroid, track_ids) in enumerate(groups):
                pile_id = kept.get(position)
                if pile_id is None:
                    pile_id = new_id()
                    await connection.execute(
                        "INSERT INTO face_piles (id, status, centroid, size, created_at, "
                        "updated_at) VALUES (?, 'open', ?, ?, ?, ?)",
                        (pile_id, recognize.pack(centroid), len(track_ids), stamp, stamp),
                    )
                else:
                    await connection.execute(
                        "UPDATE face_piles SET centroid = ?, size = ?, updated_at = ? WHERE id = ?",
                        (recognize.pack(centroid), len(track_ids), stamp, pile_id),
                    )
                made.append(pile_id)
                sql, params = in_clause(
                    "UPDATE face_tracks SET pile_id = ? WHERE id IN (?*)", list(track_ids)
                )
                await connection.execute(sql, (pile_id, *params))
                await connection.execute(_STAMP_PILE, (pile_id,))
            await self._carry_proposals(connection, leaving, groups, made)
            # Once per grouping, which runs when a batch of scanning has settled, never per file:
            # Faces > Groups on every admin's tabs draws the new groups.
            announce(EVERY_ADMIN, About.LIBRARY)
        return made

    @staticmethod
    async def _proposals_leaving(
        connection: Connection, keeping: Sequence[str]
    ) -> list[tuple[dict[str, Any], set[str]]]:
        """The proposals on the piles a rebuild is about to delete, each with its pile's faces.

        Read on the rebuild's own connection before the delete, which takes the rows with it (the
        key cascades) and leaves nothing to carry.
        """
        sql, params = in_clause(
            "SELECT pp.*, t.id AS track_id FROM face_pile_proposals AS pp "
            "JOIN face_piles AS p ON p.id = pp.pile_id "
            "JOIN face_tracks AS t ON t.pile_id = pp.pile_id "
            "WHERE p.status = 'open' AND p.by_hand = 0 AND pp.pile_id NOT IN (?*)",
            list(keeping) or [""],
        )
        found: dict[tuple[str, str], tuple[dict[str, Any], set[str]]] = {}
        for row in await connection.execute_fetchall(sql, params):
            key = (str(row["pile_id"]), str(row["person_id"]))
            if key not in found:
                found[key] = (
                    {name: row[name] for name in _PROPOSAL_COLUMNS},
                    set(),
                )
            found[key][1].add(str(row["track_id"]))
        return list(found.values())

    @staticmethod
    async def _carry_proposals(
        connection: Connection,
        leaving: Sequence[tuple[dict[str, Any], set[str]]],
        groups: Sequence[tuple[Vector, Sequence[str]]],
        made: Sequence[str],
    ) -> None:
        """Put each proposal back on the pile that now holds more than half of its faces."""
        if not leaving:
            return
        pile_of = {
            track_id: made[position]
            for position, (_, track_ids) in enumerate(groups)
            for track_id in track_ids
        }
        for proposal, faces in leaving:
            landed: dict[str, int] = {}
            for track_id in faces:
                pile_id = pile_of.get(track_id)
                if pile_id is not None:
                    landed[pile_id] = landed.get(pile_id, 0) + 1
            if not landed:
                continue
            pile_id, held = max(landed.items(), key=lambda one: (one[1], one[0]))
            if held * 2 <= len(faces):
                continue
            await connection.execute(
                "INSERT INTO face_pile_proposals (pile_id, person_id, reason, folder_id, files, "
                "of_files, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (pile_id, person_id) DO NOTHING",
                (pile_id, *(proposal[name] for name in _PROPOSAL_COLUMNS[1:])),
            )

    async def open_piles_for_grouping(self) -> list[tuple[str, Vector]]:
        """The piles the arithmetic made and may change: open, and not built by hand. Each with
        its middle, which is what a new face is compared against."""
        rows = await self._db.fetch_all(
            "SELECT id, centroid FROM face_piles WHERE status = 'open' AND by_hand = 0 ORDER BY id",
            (),
        )
        return [(str(row["id"]), recognize.unpack(bytes(row["centroid"]))) for row in rows]

    async def turned_away(self, line: float) -> set[str]:
        """The appearances Sift may ask about and never names by itself: every one still open (no
        person on it, or a question standing) whose clearest face is turned past `line`.

        Chosen by the clearest face, exactly as `unattributed` and `asked` choose the description
        they hand back, so the face judged turned here is the face compared there. A face stored
        before angles were (a null) is not turned: it cleared the bar of its day.
        """
        rows = await self._db.sweep_all(
            "SELECT t.id AS track_id FROM face_tracks AS t "
            "JOIN face_detections AS d ON d.id = ("
            "  SELECT best.id FROM face_detections AS best "
            "   WHERE best.track_id = t.id "
            "   ORDER BY best.quality DESC, best.id LIMIT 1"
            ") "
            "WHERE (t.person_id IS NULL OR t.attribution = ?) AND d.frontality < ? "
            "ORDER BY t.id",
            (Attribution.SUGGESTED.value, line),
            what="turned faces",
        )
        return {str(row["track_id"]) for row in rows}

    async def unpiled(self, recognizer: str) -> list[tuple[str, str, Vector]]:
        """Every appearance nobody has been attached to that sits in no pile: what an
        incremental grouping has to place. The same one-row-per-appearance read as
        `unattributed`, narrowed to faces whose pile is null or names a pile that has gone."""
        rows = await self._db.sweep_all(
            "SELECT t.id AS track_id, t.asset_id AS asset_id, d.embedding AS embedding "
            "FROM face_tracks AS t "
            "JOIN face_scans AS s ON s.asset_id = t.asset_id AND s.recognizer = ? "
            "JOIN face_detections AS d ON d.id = ("
            "  SELECT best.id FROM face_detections AS best "
            "   WHERE best.track_id = t.id "
            "   ORDER BY best.quality DESC, best.id LIMIT 1"
            ") "
            "WHERE t.person_id IS NULL AND (t.pile_id IS NULL "
            "  OR NOT EXISTS (SELECT 1 FROM face_piles AS p WHERE p.id = t.pile_id)) "
            "ORDER BY t.id",
            (recognizer,),
            what="faces in no pile",
        )
        return [
            (
                str(row["track_id"]),
                str(row["asset_id"]),
                recognize.unpack(bytes(row["embedding"])),
            )
            for row in rows
        ]

    async def add_to_piles(self, joined: Mapping[str, Sequence[str]]) -> None:
        """Put faces into the piles they belong to (pile id -> track ids), and bring each
        touched pile's middle and count up to date from what it now holds."""
        if not joined:
            return
        stamp = now_ms()
        async with self._db.write() as connection:
            for pile_id, track_ids in joined.items():
                if not track_ids:
                    continue
                sql, params = in_clause(
                    "UPDATE face_tracks SET pile_id = ? WHERE id IN (?*)", list(track_ids)
                )
                await connection.execute(sql, (pile_id, *params))
                rows = list(
                    await connection.execute_fetchall(
                        "SELECT d.embedding AS embedding FROM face_tracks AS t "
                        "JOIN face_detections AS d ON d.id = ("
                        "  SELECT best.id FROM face_detections AS best "
                        "   WHERE best.track_id = t.id "
                        "   ORDER BY best.quality DESC, best.id LIMIT 1"
                        ") WHERE t.pile_id = ? AND t.person_id IS NULL",
                        (pile_id,),
                    )
                )
                if not rows:
                    continue
                middle = tracking.centroid(
                    [recognize.unpack(bytes(row["embedding"])) for row in rows]
                )
                await connection.execute(
                    "UPDATE face_piles SET centroid = ?, size = ?, updated_at = ? WHERE id = ?",
                    (recognize.pack(middle), len(rows), stamp, pile_id),
                )
                await connection.execute(_STAMP_PILE, (pile_id,))
            # Once per grouping, as `replace_piles` says: the groups grew on Faces > Groups.
            announce(EVERY_ADMIN, About.LIBRARY)

    async def add_piles(self, groups: Sequence[tuple[Vector, Sequence[str]]]) -> list[str]:
        """New open piles beside the ones that exist. What an incremental grouping makes of the
        faces that joined nothing."""
        stamp = now_ms()
        made: list[str] = []
        async with self._db.write() as connection:
            for centroid, track_ids in groups:
                pile_id = new_id()
                made.append(pile_id)
                await connection.execute(
                    "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at) "
                    "VALUES (?, 'open', ?, ?, ?, ?)",
                    (pile_id, recognize.pack(centroid), len(track_ids), stamp, stamp),
                )
                sql, params = in_clause(
                    "UPDATE face_tracks SET pile_id = ? WHERE id IN (?*)", list(track_ids)
                )
                await connection.execute(sql, (pile_id, *params))
                await connection.execute(_STAMP_PILE, (pile_id,))
            # Once per grouping, as `replace_piles` says: new groups on Faces > Groups.
            announce(EVERY_ADMIN, About.LIBRARY)
        return made

    async def ignored_track_ids(self) -> set[str]:
        rows = await self._db.sweep_all(
            "SELECT t.id AS id FROM face_tracks AS t JOIN face_piles AS p ON p.id = t.pile_id "
            "WHERE p.status = 'ignored'",
            (),
            what="faces set aside",
        )
        return {str(row["id"]) for row in rows}

    async def set_pile_status(self, pile_id: str, status: PileStatus) -> bool:
        # A group set aside or brought back moves on Faces > Groups on every admin's other tabs.
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            await connection.execute(
                "UPDATE face_piles SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, now_ms(), pile_id),
            )
        row = await self._db.fetch_one("SELECT id FROM face_piles WHERE id = ?", (pile_id,))
        return row is not None

    async def pile_tracks(
        self, pile_id: str, *, limit: int | None = None, offset: int = 0
    ) -> list[StoredTrack]:
        """The faces in one pile that nobody has been attached to yet.

        Attributed faces are left out, and that is what makes naming part of a pile behave the way
        somebody expects: the ones just named leave, the rest stay a pile, and a pile whose faces
        have all been claimed empties out instead of sitting there asking a question that has been
        answered. Without the filter the same faces keep being offered until something unrelated
        triggers a regrouping.
        """
        if limit is None:
            return [
                _track(row)
                for row in await self._db.fetch_all(
                    "SELECT * FROM face_tracks WHERE pile_id = ? AND person_id IS NULL "
                    "ORDER BY quality DESC, id",
                    (pile_id,),
                )
            ]
        return [
            _track(row)
            for row in await self._db.fetch_all(
                "SELECT * FROM face_tracks WHERE pile_id = ? AND person_id IS NULL "
                "ORDER BY quality DESC, id LIMIT ? OFFSET ?",
                (pile_id, limit, offset),
            )
        ]

    async def tracks_in_piles(self, pile_ids: Sequence[str]) -> dict[str, list[StoredTrack]]:
        """The unclaimed faces of several piles in one go, keyed by pile, in each pile's own order.

        The batched `pile_tracks`, by the same rule about attributed faces, so a card never counts
        faces the pile screen does not show. Every asked-for pile is present, empty when it has
        nothing left.
        """
        found: dict[str, list[StoredTrack]] = {one: [] for one in dict.fromkeys(pile_ids)}
        if not found:
            return found
        for start in range(0, len(found), _PILES_PER_READ):
            chunk = list(found)[start : start + _PILES_PER_READ]
            query, params = in_clause(
                "SELECT * FROM face_tracks WHERE pile_id IN (?*) AND person_id IS NULL "
                "ORDER BY quality DESC, id",
                chunk,
            )
            for row in await self._db.fetch_all(query, params):
                found[str(row["pile_id"])].append(_track(row))
        return found

    async def open_pile_middles(
        self, recognizer: str, *, at_least: int
    ) -> list[tuple[str, Vector, int]]:
        """Every open group described by `recognizer` holding at least `at_least` unnamed faces,
        with its middle and how many unnamed faces it holds. What a whole group is compared with a
        person by (`FaceService.groups_that_may_be`).

        **The stored middle, not one worked out again here.** It is the unit-length mean of the
        pile's faces, written whenever a face joins (`add_to_piles`, `replace_piles`), and agrees
        with the mean of its unnamed faces to three places. Recomputing it would read every face of
        every group on every read of the review list.

        Only groups one model described, which is what the pile's `recognizer` stamp says
        (`_STAMP_PILE`: none when its faces disagree): a middle is compared with a gallery the
        same model described, or it means nothing. Set-aside groups are not asked about: somebody
        dismissed them, and the rule `evidence.py` states for proposals holds here too.
        """
        rows = await self._db.fetch_all(
            "SELECT p.id AS id, p.centroid AS centroid, COUNT(t.id) AS unnamed "
            "  FROM face_piles AS p "
            "  JOIN face_tracks AS t ON t.pile_id = p.id AND t.person_id IS NULL "
            " WHERE p.status = 'open' AND p.recognizer = ? "
            " GROUP BY p.id HAVING COUNT(t.id) >= ? ORDER BY p.id",
            (recognizer, max(1, at_least)),
        )
        return [
            (str(row["id"]), recognize.unpack(bytes(row["centroid"])), int(row["unnamed"]))
            for row in rows
        ]

    async def refusals_in_piles(self, pile_ids: Sequence[str]) -> dict[tuple[str, str], int]:
        """How many of each group's unnamed faces have been refused as each person, keyed
        `(pile, person)`. A pair with none is absent.

        What says a group has already been answered "not her" face by face and, because a
        refusal is kept per face, still says it after the group is rebuilt under a new id.
        """
        found: dict[tuple[str, str], int] = {}
        wanted = list(dict.fromkeys(pile_ids))
        for start in range(0, len(wanted), _PILES_PER_READ):
            query, params = in_clause(
                "SELECT t.pile_id AS pile_id, r.person_id AS person_id, COUNT(*) AS refused "
                "  FROM face_tracks AS t JOIN face_rejections AS r ON r.track_id = t.id "
                " WHERE t.pile_id IN (?*) AND t.person_id IS NULL "
                " GROUP BY t.pile_id, r.person_id",
                wanted[start : start + _PILES_PER_READ],
            )
            for row in await self._db.fetch_all(query, params):
                found[(str(row["pile_id"]), str(row["person_id"]))] = int(row["refused"])
        return found

    async def refused_tracks(self, pile_ids: Sequence[str], person_id: str) -> set[str]:
        """The unnamed faces in these groups somebody has refused as this person, by track id.

        What keeps a No: naming a group from its card offers the rest of the group as her
        questions, and a face already answered "not her" must not be asked about her again.
        """
        found: set[str] = set()
        wanted = list(dict.fromkeys(pile_ids))
        for start in range(0, len(wanted), _PILES_PER_READ):
            query, params = in_clause(
                "SELECT t.id AS id FROM face_tracks AS t"
                "  JOIN face_rejections AS r ON r.track_id = t.id AND r.person_id = ?"
                " WHERE t.pile_id IN (?*) AND t.person_id IS NULL",
                wanted[start : start + _PILES_PER_READ],
            )
            for row in await self._db.fetch_all(query, [person_id, *params]):
                found.add(str(row["id"]))
        return found

    async def proposed_piles(self) -> list[str]:
        """Every open group with a proposal still standing for anybody, by id.

        Read-only over the folder reader's table (`propose_pile` writes it): the review list asks
        which groups a folder has proposed, then `pile_proposals` says for whom and why.
        """
        rows = await self._db.fetch_all(
            "SELECT DISTINCT pp.pile_id AS pile_id FROM face_pile_proposals AS pp "
            "  JOIN face_piles AS p ON p.id = pp.pile_id AND p.status = 'open' "
            " WHERE pp.state = 'pending' ORDER BY pp.pile_id",
            (),
        )
        return [str(row["pile_id"]) for row in rows]

    async def pile_statuses(self, pile_ids: Sequence[str]) -> dict[str, PileStatus]:
        """Which of these piles are open and which were set aside, in one read.

        A face on a file knows the pile it belongs to and not what has happened to that pile, and
        the two lead to different screens. Batched rather than asked per face: this is read by the
        strip on the item detail, which opens every time somebody opens anything, and a question
        asked once per face there would slow every screen that opens one.

        A pile id that names nothing is absent rather than guessed at. Grouping rebuilds piles, so
        a track can hold the id of one that has since been replaced.
        """
        wanted = [one for one in dict.fromkeys(pile_ids) if one]
        found: dict[str, PileStatus] = {}
        if not wanted:
            return found
        for start in range(0, len(wanted), _PILES_PER_READ):
            chunk = wanted[start : start + _PILES_PER_READ]
            query, params = in_clause("SELECT id, status FROM face_piles WHERE id IN (?*)", chunk)
            for row in await self._db.fetch_all(query, params):
                found[str(row["id"])] = PileStatus(str(row["status"]))
        return found

    async def count_pile_tracks(self, pile_id: str) -> int:
        """How many unclaimed faces a pile holds, before anybody's visibility is applied."""
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM face_tracks WHERE pile_id = ? AND person_id IS NULL",
            (pile_id,),
        )
        return 0 if row is None else int(row["n"])

    async def pile_of(self, pile_id: str) -> Row | None:
        return await self._db.fetch_one("SELECT * FROM face_piles WHERE id = ?", (pile_id,))

    # --- groups proposed as somebody, for a reason found outside this feature -----------------
    #
    # See `schema._CREATE_PILE_PROPOSALS`. The folder reader proposes, the review list reads, and
    # whoever answers the card settles. Nothing here decides anything about a face.

    async def propose_pile(
        self,
        pile_id: str,
        person_id: str,
        *,
        reason: str,
        folder_id: str,
        files: int,
        of_files: int,
    ) -> bool:
        """Keep "this group may be this person". True when a proposal is now standing.

        **Once answered, never re-made.** A proposal somebody refused or accepted is left exactly as
        it is, so the pass (which runs whenever the folder's faces move) cannot put a question
        back in front of somebody who has answered it. Only a `pending` row takes the new counts.

        **And never made at all for a group somebody has already said is not this person**, even
        once: a face in the group refused as them is the same answer given one face at a time, and
        a group rebuilt under a new id after that refusal is still those faces. Checked in the
        statement rather than read first, so a refusal landing between the two cannot slip past.

        Only an OPEN group is proposed. A group set aside was dismissed, and work that comes back
        after being dismissed is worse than work never offered: the rule `evidence.py` states.

        The folder's earlier proposal for this person on a DIFFERENT group is withdrawn in the same
        transaction while it is still pending: a folder has one main face, so a folder that now
        names another group has stopped naming the first.
        """
        stamp = now_ms()
        async with self._db.write() as connection:
            await connection.execute(
                "DELETE FROM face_pile_proposals WHERE folder_id = ? AND person_id = ? "
                "AND pile_id != ? AND state = 'pending'",
                (folder_id, person_id, pile_id),
            )
            await connection.execute(
                "INSERT INTO face_pile_proposals (pile_id, person_id, reason, folder_id, files, "
                "of_files, state, created_at, updated_at) "
                "SELECT ?, ?, ?, ?, ?, ?, 'pending', ?, ? "
                " WHERE EXISTS (SELECT 1 FROM face_piles WHERE id = ? AND status = 'open') "
                "   AND NOT EXISTS (SELECT 1 FROM face_tracks AS t "
                "                     JOIN face_rejections AS r ON r.track_id = t.id "
                "                    WHERE t.pile_id = ? AND r.person_id = ?) "
                "ON CONFLICT (pile_id, person_id) DO UPDATE SET "
                "  reason = excluded.reason, folder_id = excluded.folder_id, "
                "  files = excluded.files, of_files = excluded.of_files, "
                "  updated_at = excluded.updated_at "
                "WHERE face_pile_proposals.state = 'pending'",
                (
                    pile_id,
                    person_id,
                    reason,
                    folder_id,
                    files,
                    of_files,
                    stamp,
                    stamp,
                    pile_id,
                    pile_id,
                    person_id,
                ),
            )
            row = await (
                await connection.execute(
                    "SELECT state FROM face_pile_proposals WHERE pile_id = ? AND person_id = ?",
                    (pile_id, person_id),
                )
            ).fetchone()
        return row is not None and str(row["state"]) == "pending"

    async def withdraw_pile_proposals(self, folder_id: str, person_id: str) -> int:
        """Take back what this folder still has pending for this person. Returns how many.

        What the pass says when the folder no longer has a main group at all. An answered proposal
        is kept, for the reason `propose_pile` keeps one.
        """
        async with self._db.write() as connection:
            rows = await connection.execute_fetchall(
                "DELETE FROM face_pile_proposals WHERE folder_id = ? AND person_id = ? "
                "AND state = 'pending' RETURNING pile_id",
                (folder_id, person_id),
            )
        return len(list(rows))

    async def settle_pile_proposal(self, pile_id: str, person_id: str, *, state: str) -> bool:
        """Record the answer to one proposal: `accepted` or `refused`, or `pending` again, which
        is the undo of a refusal and nothing else. False when no row was in the state it leaves.

        The answer is written here and nothing else is: naming the group or refusing its faces is
        the card's own press, through the paths every other naming takes. This is only what stops
        the pass asking again and, taken back, what lets it ask once more.
        """
        if state not in ("accepted", "refused", "pending"):
            raise ValueError(f"a proposal is answered accepted or refused, not {state!r}")
        leaving = "refused" if state == "pending" else "pending"
        async with self._db.write() as connection:
            cursor = await connection.execute(
                "UPDATE face_pile_proposals SET state = ?, updated_at = ? "
                "WHERE pile_id = ? AND person_id = ? AND state = ?",
                (state, now_ms(), pile_id, person_id, leaving),
            )
            return bool(cursor.rowcount)

    async def reopen_accepted_proposal(self, pile_id: str, person_id: str) -> bool:
        """Put a proposal a Yes accepted back to asking. True when one moved.

        The undo of an acceptance, and only that: `settle_pile_proposal` refuses accepted to
        pending so that no pass or press can put an answered question back, and the one caller
        here is an Undo whose receipt names the proposal its press accepted. `pending` is the only
        state an acceptance leaves (see that method), so this is the state before the press. A
        group the press emptied has gone, and its proposal with it (the key cascades); nothing is
        made again for it here.
        """
        async with self._db.write() as connection:
            cursor = await connection.execute(
                "UPDATE face_pile_proposals SET state = 'pending', updated_at = ? "
                "WHERE pile_id = ? AND person_id = ? AND state = 'accepted'",
                (now_ms(), pile_id, person_id),
            )
            return bool(cursor.rowcount)

    async def pile_proposals(self, pile_ids: Sequence[str]) -> list[PileProposal]:
        """The proposals still standing for these groups, in the order they were first made.

        Pending only, and only for a group still open: a group set aside after it was proposed has
        been dismissed since, and its proposal went with it.
        """
        if not pile_ids:
            return []
        sql, params = in_clause(
            "SELECT pp.pile_id, pp.person_id, pp.reason, pp.folder_id, pp.files, pp.of_files "
            "  FROM face_pile_proposals AS pp "
            "  JOIN face_piles AS p ON p.id = pp.pile_id AND p.status = 'open' "
            " WHERE pp.state = 'pending' AND pp.pile_id IN (?*) "
            "\n -- ordered by the clock: a proposal has no id of its own; its key is the group"
            "\n -- and the person it names"
            "\n ORDER BY pp.created_at, pp.pile_id, pp.person_id",
            list(dict.fromkeys(pile_ids)),
        )
        rows = await self._db.fetch_all(sql, tuple(params))
        return [
            PileProposal(
                pile_id=str(row["pile_id"]),
                person_id=str(row["person_id"]),
                reason=str(row["reason"]),
                folder_id=str(row["folder_id"]),
                files=int(row["files"]),
                of_files=int(row["of_files"]),
            )
            for row in rows
        ]

    async def unnamed_files_of_pile(self, pile_id: str) -> list[str]:
        """The files one group's unnamed faces are in: what a proposal's own sentence counts."""
        rows = await self._db.fetch_all(
            "SELECT DISTINCT asset_id FROM face_tracks WHERE pile_id = ? AND person_id IS NULL",
            (pile_id,),
        )
        return [str(row["asset_id"]) for row in rows]
