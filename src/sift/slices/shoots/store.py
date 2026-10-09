# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rows behind proposed shoots. Unscoped: the routes resolve pictures through access."""

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

#: An answered proposal is never found again, or a fresh grouping would hide behind its answer.
_WAITING_OF_PERSON = """
SELECT i.proposal_id AS proposal_id, i.asset_id AS asset_id
  FROM shoot_proposal_items i
  JOIN shoot_proposals p ON p.id = i.proposal_id
 WHERE p.person_id = ?
   AND NOT EXISTS (SELECT 1 FROM shoot_sets s WHERE s.proposal_id = p.id)
"""
_WAITING_PICTURES = """
SELECT i.proposal_id AS proposal_id, i.asset_id AS asset_id
  FROM shoot_proposal_items i
 WHERE NOT EXISTS (SELECT 1 FROM shoot_sets s WHERE s.proposal_id = i.proposal_id)
 ORDER BY i.proposal_id, i.position
"""
_RENAME_PROPOSAL = "UPDATE shoot_proposals SET name = ? WHERE id = ?"
_CLEAR_ITEMS = "DELETE FROM shoot_proposal_items WHERE proposal_id = ?"

#: When the floor rises, a creator below it is no longer looked at, so their cards would otherwise
#: stand for ever. The pictures go back in the pool.
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

#: Named by the creator's name as it is NOW, so a rename shows before the next pass. The
#: `shoot_sets` join drops an answered proposal without deleting it.
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

#: How many waiting proposals come before this one, in `_WAITING`'s order and conditions; no row
#: for one not waiting.
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
    "SELECT p.id AS id, p.person_id AS person_id, COALESCE(pe.name, p.name) AS name,"
    " p.found_at AS found_at FROM shoot_proposals p LEFT JOIN people pe ON pe.id = p.person_id"
    " WHERE p.id = ?",
)

_PICTURES_OF = point_read(
    "the pictures of one proposed shoot",
    "SELECT asset_id, named FROM shoot_proposal_items WHERE proposal_id = ? ORDER BY position",
)

_MARK_NAMED = "UPDATE shoot_proposal_items SET named = 1 WHERE proposal_id = ? AND asset_id = ?"
_UNMARK_NAMED = "UPDATE shoot_proposal_items SET named = 0 WHERE proposal_id = ? AND asset_id = ?"

_REFUSE = "INSERT INTO shoot_refusals (asset_id, created_at) VALUES (?, ?) ON CONFLICT DO NOTHING"
#: The refusals table is the durable memory; the proposal is only this pass's guess.
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
    #: Filled in only where a caller asked: the list draws a strip, not the whole shoot.
    asset_ids: tuple[str, ...] = field(default=())
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
        """Replace this creator's proposals in one transaction; one found again keeps its id."""
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
        """Drop every standing proposal holding fewer than `least` pictures. See `_UNDER_FLOOR`."""
        under = await self._db.fetch_all(_UNDER_FLOOR_COUNT, (least,))
        if not under:
            return 0
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            await connection.execute(_UNDER_FLOOR, (least,))
        return len(under)

    async def waiting_pictures(self) -> dict[str, list[str]]:
        """The pictures of every waiting proposal in shoot order, read once per pass."""
        rows = await self._db.fetch_all(_WAITING_PICTURES, ())
        held: dict[str, list[str]] = {}
        for row in rows:
            held.setdefault(str(row["proposal_id"]), []).append(str(row["asset_id"]))
        return held

    async def settle_filed(self, filed: dict[str, str]) -> int:
        """Mark these proposals answered by the Photo Set already holding them, with no receipt."""
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
        """Where one waiting proposal sits on the list from zero, or None. See `_POSITION`."""
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
        """Stop offering to name these pictures; the proposal stays."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            for asset_id in asset_ids:
                await connection.execute(_MARK_NAMED, (proposal_id, asset_id))

    @staticmethod
    async def unmark_named_on(
        connection: Connection, proposal_id: str, asset_ids: Sequence[str]
    ) -> None:
        """`mark_named` taken back on the undo's own transaction, so both land together."""
        for asset_id in asset_ids:
            await connection.execute(_UNMARK_NAMED, (proposal_id, asset_id))

    async def refuse(self, proposal_id: str, asset_ids: Sequence[str]) -> int:
        """Refuse these pictures and drop the proposal in one transaction: they are one answer."""
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
        """Link this proposal to its Photo Set on the caller's connection, beside the receipt."""
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
        """Drop the link of an undone Photo Set, so its proposal is a question again."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            cursor = await connection.execute(_FORGET_LINK, (photo_set_id,))
            return cursor.rowcount or 0
