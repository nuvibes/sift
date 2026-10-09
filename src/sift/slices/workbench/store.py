# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading and writing the record of what was decided."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from sift.kernel.db import Connection, Database
from sift.kernel.ledger import Actor, Reversal, record_event
from sift.kernel.ledger import Object as LedgerObject
from sift.kernel.vocabulary import LEDGER_QUEUE, Subject

# Receipts and ledger events share one table; `_RECENT` is the receipts alone, newest first.
_RECENT = (
    "SELECT * FROM workbench_decisions WHERE queue <> ?"
    " ORDER BY decided_at DESC, id DESC LIMIT ? OFFSET ?"
)
_COUNT = "SELECT COUNT(*) AS total FROM workbench_decisions WHERE queue <> ?"
_ONE = "SELECT * FROM workbench_decisions WHERE id = ?"

# `reversed_at IS NULL` makes a second undo press a no-op rather than a second reversal.
_MARK_REVERSED = (
    "UPDATE workbench_decisions SET reversed_at = ? WHERE id = ? AND reversed_at IS NULL"
)


@dataclass(frozen=True, slots=True)
class Decision:
    """One decision as it was recorded."""

    id: str
    queue: str
    user_id: str | None
    title: str
    detail: str
    payload: str
    decided_at: int
    reversed_at: int | None


def _decision(row: object) -> Decision:
    mapping = dict(row)  # type: ignore[call-overload]
    return Decision(
        id=str(mapping["id"]),
        queue=str(mapping["queue"]),
        user_id=None if mapping["user_id"] is None else str(mapping["user_id"]),
        title=str(mapping["title"]),
        detail=str(mapping["detail"]),
        payload=str(mapping["payload"]),
        decided_at=int(mapping["decided_at"]),
        reversed_at=None if mapping["reversed_at"] is None else int(mapping["reversed_at"]),
    )


class Store:
    """The decision record. One per application."""

    def __init__(self, database: Database) -> None:
        self._db = database

    @property
    def database(self) -> Database:
        return self._db

    @staticmethod
    def _now() -> int:
        return int(time.time())

    async def record_on(
        self,
        connection: Connection,
        *,
        queue: str,
        user_id: str | None,
        title: str,
        detail: str,
        payload: str,
        subjects: Sequence[Subject] = (),
        verb: str = "decided",
        object: LedgerObject | None = None,
        via: str | None = None,
    ) -> str:
        """Write down what a decision did on the caller's connection, in the same transaction."""
        return await record_event(
            connection,
            actor=Actor.user(user_id) if user_id else Actor.sift(via),
            verb=verb,
            subject=subjects,
            object=object,
            payload=payload,
            receipt=Reversal(queue=queue, title=title, detail=detail),
        )

    async def recent(self, *, limit: int, offset: int) -> tuple[list[Decision], int]:
        """Every receipt, newest first, unfolded, and how many there are. See `_RECENT`."""
        rows = await self._db.fetch_all(_RECENT, (LEDGER_QUEUE, limit, offset))
        totals = await self._db.fetch_all(_COUNT, (LEDGER_QUEUE,))
        total = int(totals[0]["total"]) if totals else 0
        return [_decision(row) for row in rows], total

    async def decision(self, decision_id: str) -> Decision | None:
        row = await self._db.fetch_one(_ONE, (decision_id,))
        return None if row is None else _decision(row)

    async def mark_reversed(self, decision_id: str) -> bool:
        """Claim a decision for reversal before its rows go back; False if somebody already has."""
        async with self._db.write() as connection:
            cursor = await connection.execute(_MARK_REVERSED, (self._now(), decision_id))
            return bool(cursor.rowcount)

    async def unmark_reversed(self, decision_id: str) -> None:
        """Give the claim back, when the reversal turned out to do nothing."""
        async with self._db.write() as connection:
            await connection.execute(
                "UPDATE workbench_decisions SET reversed_at = NULL WHERE id = ?", (decision_id,)
            )
