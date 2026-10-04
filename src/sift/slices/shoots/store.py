# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rows behind proposed shoots. Unscoped, like every store here.

Nothing in this file decides who may be told about anything. The routes resolve the pictures of a
proposal through the access layer before any of it reaches a screen, which is what stops a row
written by a pass that runs for nobody from being shown to somebody it was never meant for.

**The pass replaces rather than appends.** `replace_for` clears one creator's proposals and writes
the new ones in a single transaction, so a card never reads half a pass: a proposal whose
pictures have been filed since is gone, and the ones found this time arrive together. A proposal
the pass finds again, the same pictures, keeps its id and its place on the list.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Connection, Database, in_clause, point_read
from sift.kernel.ids import new_id

#: One creator's proposals, except the ones this pass found again.
_CLEAR_FOR_PERSON = (
    "DELETE FROM shoot_proposals WHERE person_id = ? AND id NOT IN (SELECT value FROM json_each(?))"
)

#: The pictures of every proposal of one creator still waiting on an answer. An answered one is
#: never found again: its link is what keeps it off the list, and reusing its id for a grouping
#: found afresh would hide that grouping behind the old answer.
_WAITING_OF_PERSON = """
SELECT i.proposal_id AS proposal_id, i.asset_id AS asset_id
  FROM shoot_proposal_items i
  JOIN shoot_proposals p ON p.id = i.proposal_id
 WHERE p.person_id = ?
   AND NOT EXISTS (SELECT 1 FROM shoot_sets s WHERE s.proposal_id = p.id)
"""
#: The pictures of every proposal still waiting on an answer, whoever it is of. For the pass's
#: reading of what is still standing: see `Store.waiting_pictures`.
_WAITING_PICTURES = """
SELECT i.proposal_id AS proposal_id, i.asset_id AS asset_id
  FROM shoot_proposal_items i
 WHERE NOT EXISTS (SELECT 1 FROM shoot_sets s WHERE s.proposal_id = i.proposal_id)
 ORDER BY i.proposal_id, i.position
"""
_RENAME_PROPOSAL = "UPDATE shoot_proposals SET name = ? WHERE id = ?"
_CLEAR_ITEMS = "DELETE FROM shoot_proposal_items WHERE proposal_id = ?"

#: Every standing proposal holding fewer pictures than the floor.
#:
#: When the floor rises, a proposal written under the old one does NOT go away on its own: a pass
#: replaces a creator's proposals (`_CLEAR_FOR_PERSON`), and a creator whose loose pictures no
#: longer reach the floor is not looked at by the pass at all, so their cards would stand on the
#: queue for ever. The cascade on `shoot_proposal_items` takes the pictures with it. Nothing is
#: refused and nothing is deleted from the library: the pictures go back in the pool, and a group
#: that does reach the floor is proposed again.
_UNDER_FLOOR_COUNT = """
SELECT proposal_id FROM shoot_proposal_items GROUP BY proposal_id HAVING COUNT(*) < ?
"""
_UNDER_FLOOR = """
DELETE FROM shoot_proposals WHERE id IN (
  SELECT proposal_id FROM shoot_proposal_items GROUP BY proposal_id HAVING COUNT(*) < ?
)
"""
_INSERT_PROPOSAL = "INSERT INTO shoot_proposals (id, person_id, name, found_at) VALUES (?, ?, ?, ?)"
_INSERT_ITEM = (
    "INSERT INTO shoot_proposal_items (proposal_id, asset_id, position, named) VALUES (?, ?, ?, ?)"
)

#: A proposal that has NOT been turned into anything, newest first, with how many pictures it holds.
#:
#: Named by its creator's name as it is NOW, read through `person_id`. The name the pass wrote
#: (`shoot_proposals.name`) is the creator's name at the time of that pass, and a card reading it
#: would go on saying the old name after a rename until the next pass happened to run. It stays the
#: fallback for a row whose person the join cannot find, which the cascade makes a row that is
#: already gone.
#:
#: The join against `shoot_sets` is what takes an answered proposal off the list without deleting
#: it: the link row is the record of what a press did, and a pass that re-ran would otherwise offer
#: the same shoot again beside the Photo Set it already became.
_WAITING = """
SELECT p.id AS id, p.person_id AS person_id, COALESCE(pe.name, p.name) AS name,
       p.found_at AS found_at, COUNT(i.asset_id) AS pictures
  FROM shoot_proposals p
  JOIN shoot_proposal_items i ON i.proposal_id = p.id
  LEFT JOIN people pe ON pe.id = p.person_id
 WHERE NOT EXISTS (SELECT 1 FROM shoot_sets s WHERE s.proposal_id = p.id)
 GROUP BY p.id, p.person_id, COALESCE(pe.name, p.name), p.found_at
 ORDER BY p.found_at DESC, p.id DESC
 LIMIT ? OFFSET ?
"""

_WAITING_TOTAL = """
SELECT COUNT(*) AS total FROM shoot_proposals p
 WHERE EXISTS (SELECT 1 FROM shoot_proposal_items i WHERE i.proposal_id = p.id)
   AND NOT EXISTS (SELECT 1 FROM shoot_sets s WHERE s.proposal_id = p.id)
"""

#: Where one waiting proposal sits in `_WAITING`'s order, counting from zero: how many waiting
#: proposals come before it. No row at all for a proposal that is not waiting (answered, cleared
#: by a later pass, or never there), so the caller has one reading of "not on the list".
#:
#: The same two conditions as `_WAITING_TOTAL` on both sides, and the same order as `_WAITING`
#: (newest first, the id breaking a tie), because a position only means anything in the list it
#: was counted in.
_POSITION = """
SELECT (
  SELECT COUNT(*) FROM shoot_proposals p
   WHERE EXISTS (SELECT 1 FROM shoot_proposal_items i WHERE i.proposal_id = p.id)
     AND NOT EXISTS (SELECT 1 FROM shoot_sets s WHERE s.proposal_id = p.id)
     AND (p.found_at > t.found_at OR (p.found_at = t.found_at AND p.id > t.id))
) AS at
  FROM shoot_proposals t
 WHERE t.id = ?
   AND EXISTS (SELECT 1 FROM shoot_proposal_items i WHERE i.proposal_id = t.id)
   AND NOT EXISTS (SELECT 1 FROM shoot_sets s WHERE s.proposal_id = t.id)
"""

_ONE = point_read(
    "one proposed shoot by its own id",
    # The creator's name as it is now, for the reason `_WAITING` reads it: `make()` names the
    # Photo Set and its receipt after it.
    "SELECT p.id AS id, p.person_id AS person_id, COALESCE(pe.name, p.name) AS name,"
    " p.found_at AS found_at FROM shoot_proposals p LEFT JOIN people pe ON pe.id = p.person_id"
    " WHERE p.id = ?",
)

_PICTURES_OF = point_read(
    "the pictures of one proposed shoot",
    "SELECT asset_id, named FROM shoot_proposal_items WHERE proposal_id = ? ORDER BY position",
)

_MARK_NAMED = "UPDATE shoot_proposal_items SET named = 1 WHERE proposal_id = ? AND asset_id = ?"
#: And back, when the naming is taken back: the card offers to name those pictures again.
_UNMARK_NAMED = "UPDATE shoot_proposal_items SET named = 0 WHERE proposal_id = ? AND asset_id = ?"

_REFUSE = "INSERT INTO shoot_refusals (asset_id, created_at) VALUES (?, ?) ON CONFLICT DO NOTHING"
#: And the proposal itself goes with the refusal. The refusals table is the durable memory: the
#: proposal is only this pass's guess at how those pictures group, and leaving it behind would be a
#: card still asking a question that has been answered until the next pass happened to run.
_FORGET_PROPOSAL = "DELETE FROM shoot_proposals WHERE id = ?"
_REFUSED_AMONG = "SELECT asset_id FROM shoot_refusals WHERE asset_id IN (?*)"

_LINK = (
    "INSERT INTO shoot_sets (proposal_id, photo_set_id, decision_id, made_at)"
    " VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING"
)
_MADE_FROM = point_read(
    "what one proposed shoot was turned into",
    "SELECT proposal_id, photo_set_id, decision_id, made_at FROM shoot_sets WHERE proposal_id = ?",
)
_FORGET_LINK = "DELETE FROM shoot_sets WHERE photo_set_id = ?"


@dataclass(frozen=True, slots=True)
class Shoot:
    """One grouping the pass found: the pictures in order, and which of them carry nobody."""

    asset_ids: tuple[str, ...]
    unnamed: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Proposal:
    """One proposed shoot as the table holds it."""

    id: str
    person_id: str
    name: str
    found_at: int
    pictures: int = 0
    #: Every picture of the shoot, in the order the sitting runs. Filled in only where a caller
    #: asked for them: the page that lists proposals draws a strip and not the whole shoot.
    asset_ids: tuple[str, ...] = field(default=())
    #: The ones carrying nobody at all, which is what "Name the rest" would be about.
    unnamed_ids: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class Made:
    """What one proposal was turned into: the Photo Set, and the receipt that did it."""

    proposal_id: str
    photo_set_id: str
    decision_id: str | None
    made_at: int


class Store:
    """Reads and writes for proposed shoots. Takes no view of who is asking."""

    def __init__(self, database: Database, *, clock: Callable[[], float] = time.time) -> None:
        self._db = database
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    async def replace_for(self, person_id: str, name: str, groups: Sequence[Shoot]) -> list[str]:
        """This creator's proposals, as the pass has just found them. Returns their ids, one per
        group and in the order of the groups, so a pass that files them itself can answer each one.

        One transaction, so the card never reads a creator halfway through a pass. What was there
        before and was not found again goes. See the schema's note on why a proposal is replaced
        rather than kept.

        Told, because the Shoots page and the board's card both LIST what this writes: a pass
        that finished while somebody had the page open would otherwise leave them looking at the
        proposals from before it until they reloaded.

        A group holding exactly the pictures of a proposal still waiting is that proposal, found
        again: it keeps its id and when it was found, so a pass over an unchanged library (every
        start runs one) rewrites nothing a screen or a link holds. Its pictures are written again,
        because their order and which of them carry nobody are this pass's answer.
        """
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            held: dict[str, set[str]] = {}
            for row in await connection.execute_fetchall(_WAITING_OF_PERSON, (person_id,)):
                held.setdefault(str(row["proposal_id"]), set()).add(str(row["asset_id"]))
            standing = {frozenset(pictures): proposal for proposal, pictures in held.items()}
            # Popped, so one standing proposal is found again by one group at most.
            again = [standing.pop(frozenset(group.asset_ids), None) for group in groups]
            found_again = [one for one in again if one is not None]
            await connection.execute(_CLEAR_FOR_PERSON, (person_id, json.dumps(found_again)))
            now = self._now()
            written: list[str] = []
            for group, same in zip(groups, again, strict=True):
                if same is None:
                    proposal_id = new_id()
                    await connection.execute(_INSERT_PROPOSAL, (proposal_id, person_id, name, now))
                else:
                    proposal_id = same
                    await connection.execute(_RENAME_PROPOSAL, (name, proposal_id))
                    await connection.execute(_CLEAR_ITEMS, (proposal_id,))
                for position, asset_id in enumerate(group.asset_ids):
                    named = 0 if asset_id in group.unnamed else 1
                    await connection.execute(_INSERT_ITEM, (proposal_id, asset_id, position, named))
                written.append(proposal_id)
        return written

    async def dissolve_under(self, least: int) -> int:
        """Drop every standing proposal holding fewer than `least` pictures. See `_UNDER_FLOOR`.

        Told, because a card leaving the queue is a change to a screen somebody may have open:
        the same reason `replace_for` above tells.

        The count is what was there to drop, read before the delete: SQLite's own `changes()` would
        answer the same number, and asking for it separately is a second statement about a table
        this one has already read.
        """
        under = await self._db.fetch_all(_UNDER_FLOOR_COUNT, (least,))
        if not under:
            return 0
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            await connection.execute(_UNDER_FLOOR, (least,))
        return len(under)

    async def waiting_pictures(self) -> dict[str, list[str]]:
        """The pictures of every proposal still waiting, by proposal, in the order the shoot runs.

        Every waiting proposal rather than a page, because the question asked of them is about each
        one (are any of its pictures in a Photo Set already) and a page would leave the rest
        standing. It is read once per pass, in the background, and it grows with the cards standing
        on the Shoots page rather than with the library.
        """
        rows = await self._db.fetch_all(_WAITING_PICTURES, ())
        held: dict[str, list[str]] = {}
        for row in rows:
            held.setdefault(str(row["proposal_id"]), []).append(str(row["asset_id"]))
        return held

    async def settle_filed(self, filed: dict[str, str]) -> int:
        """Mark these proposals answered by the Photo Set their pictures are already in.

        `filed` is proposal id to Photo Set id. The mark is the link a pressed yes writes, with no
        receipt behind it (`decision_id` is empty), because nobody decided anything here: the
        pictures were grouped somewhere else. So the card leaves the list exactly as an answered
        one does, and an undo of that Photo Set (`forget_link`) puts the question back, which is
        right: its pictures are loose again.

        Told, like every write here: the Shoots page and the board's card both list proposals.
        """
        if not filed:
            return 0
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            now = self._now()
            for proposal_id, photo_set_id in filed.items():
                await connection.execute(_LINK, (proposal_id, photo_set_id, None, now))
        return len(filed)

    async def waiting(self, *, limit: int, offset: int = 0) -> tuple[list[Proposal], int]:
        """A page of proposals nobody has answered, newest first, and how many there are."""
        rows = await self._db.fetch_all(_WAITING, (limit, offset))
        (total,) = await self._db.fetch_all(_WAITING_TOTAL, ())
        return (
            [
                Proposal(
                    id=str(row["id"]),
                    person_id=str(row["person_id"]),
                    name=str(row["name"]),
                    found_at=int(row["found_at"]),
                    pictures=int(row["pictures"]),
                )
                for row in rows
            ],
            int(total["total"]),
        )

    async def position_of(self, proposal_id: str) -> int | None:
        """Where one waiting proposal sits on the list, counting from zero, or None when it is not
        on the list at all. See `_POSITION`."""
        row = await self._db.fetch_one(_POSITION, (proposal_id,))
        return None if row is None else int(row["at"])

    async def one(self, proposal_id: str) -> Proposal | None:
        """One proposal and its pictures, or nothing."""
        row = await self._db.fetch_one(_ONE, (proposal_id,))
        if row is None:
            return None
        pictures = await self._db.fetch_all(_PICTURES_OF, (proposal_id,))
        found = tuple(str(one["asset_id"]) for one in pictures)
        return Proposal(
            id=str(row["id"]),
            person_id=str(row["person_id"]),
            name=str(row["name"]),
            found_at=int(row["found_at"]),
            pictures=len(found),
            asset_ids=found,
            unnamed_ids=tuple(str(one["asset_id"]) for one in pictures if not one["named"]),
        )

    async def pictures_of(self, proposal_id: str) -> list[str]:
        rows = await self._db.fetch_all(_PICTURES_OF, (proposal_id,))
        return [str(row["asset_id"]) for row in rows]

    async def mark_named(self, proposal_id: str, asset_ids: Sequence[str]) -> None:
        """These pictures of the shoot carry the creator now, so the card stops offering to name
        them. The proposal itself stays: naming is not an answer to whether this is a shoot.

        Told, like every write here: the card loses its Name the rest button, and a second tab that
        was not told would go on offering a press that now names nothing.
        """
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            for asset_id in asset_ids:
                await connection.execute(_MARK_NAMED, (proposal_id, asset_id))

    @staticmethod
    async def unmark_named_on(
        connection: Connection, proposal_id: str, asset_ids: Sequence[str]
    ) -> None:
        """`mark_named` taken back, on the caller's connection: the undo of a naming.

        On the undo's own transaction, so the creator coming off the files and the card offering to
        name them again land together. Without it the pictures would carry nobody while the card
        said every one of them already carried somebody, so the naming could never be offered again.
        A proposal that has gone since (made into a Photo Set, or refused) matches nothing.
        """
        for asset_id in asset_ids:
            await connection.execute(_UNMARK_NAMED, (proposal_id, asset_id))

    async def refuse(self, proposal_id: str, asset_ids: Sequence[str]) -> int:
        """Remember that these pictures are not a shoot, and take the question off the board.

        Both in one transaction, because they are one answer: the refusal is what stops the
        pictures being offered again in any grouping, and dropping the proposal is what stops the
        card asking a question somebody has already answered. Either without the other is a
        half-answer that looks exactly like the feature not working.

        Through `telling`, so another tab drawing the Shoots page re-reads rather than going on
        showing a card that is no longer there. `EVERY_ADMIN` because the page and the board card
        are admin-only, which is the same audience every library-shape write here announces to.
        """
        refused = 0
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            now = self._now()
            for asset_id in asset_ids:
                cursor = await connection.execute(_REFUSE, (asset_id, now))
                refused += cursor.rowcount or 0
            await connection.execute(_FORGET_PROPOSAL, (proposal_id,))
        return refused

    async def refused_among(self, asset_ids: Sequence[str]) -> set[str]:
        """Which of these pictures have already been turned down."""
        if not asset_ids:
            return set()
        query, params = in_clause(_REFUSED_AMONG, asset_ids)
        rows = await self._db.fetch_all(query, params)
        return {str(row["asset_id"]) for row in rows}

    async def link_on(
        self,
        connection: Connection,
        *,
        proposal_id: str,
        photo_set_id: str,
        decision_id: str | None,
    ) -> None:
        """Write down that this proposal became this Photo Set, on the caller's connection.

        On the caller's connection for the reason the receipt is written on it: a link that can be
        missing for a set that exists is a set nothing can take back, and one present for a set that
        does not is an undo that fails.
        """
        await connection.execute(_LINK, (proposal_id, photo_set_id, decision_id, self._now()))

    async def made_from(self, proposal_id: str) -> Made | None:
        row = await self._db.fetch_one(_MADE_FROM, (proposal_id,))
        if row is None:
            return None
        return Made(
            proposal_id=str(row["proposal_id"]),
            photo_set_id=str(row["photo_set_id"]),
            decision_id=None if row["decision_id"] is None else str(row["decision_id"]),
            made_at=int(row["made_at"]),
        )

    async def forget_link(self, photo_set_id: str) -> int:
        """Drop the link for a Photo Set that has just been taken back. Returns how many rows went.

        The proposal itself stays, unanswered, which is right: taking the decision back means the
        shoot is a question again, and the Shoots page has to be told, or the card comes back only
        for whoever pressed Undo.
        """
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            cursor = await connection.execute(_FORGET_LINK, (photo_set_id,))
            return cursor.rowcount or 0
