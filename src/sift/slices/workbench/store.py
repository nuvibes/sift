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

# `workbench_decisions` holds two kinds of row, because the ledger is built on it: a RECEIPT, which
# is a judgement somebody took on a queue and can take back, and an EVENT, which is a thing that
# happened and carries no title, no detail and no queue that could undo it. This store writes the
# receipts and takes them back; the record a person reads is the feed's
# (`kernel/access/history_events.py`), whose Decisions narrowing is the receipts alone.
#
# `_RECENT` is the same rows unfolded and unpictured: every receipt, newest first, by the same
# predicate the Decisions narrowing reads (`queue <> ledger`). It is what a test asserting that a
# receipt was written reads, so a test and the screen agree on what a receipt is. Bound as `?` rather
# than typed as `'ledger'`, so the word the ledger writes and the word this leaves out cannot drift
# apart: there is one spelling of it, in `kernel/vocabulary.py`.
_RECENT = (
    "SELECT * FROM workbench_decisions WHERE queue <> ?"
    " ORDER BY decided_at DESC, id DESC LIMIT ? OFFSET ?"
)
_COUNT = "SELECT COUNT(*) AS total FROM workbench_decisions WHERE queue <> ?"
_ONE = "SELECT * FROM workbench_decisions WHERE id = ?"

# Only a decision that has not already been reversed. The `reversed_at IS NULL` is what makes two
# undo presses on one decision idempotent rather than a second reversal of rows that are already
# back, and two presses is what a slow request produces, every time.
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
        """Write down what a decision did, on the caller's own connection.

        On the caller's connection deliberately, so the receipt lands in the same transaction as
        the writes it describes. Written separately it could be missing for a decision that
        happened, or present for one that did not, and a record that is only usually right is
        worse than no record, because it is the thing somebody reaches for when they already know
        something has gone wrong.

        `subjects` is what the decision was ABOUT, and it goes down in the same transaction for
        exactly the same reason. Empty is a real answer and not a caller that forgot: two of the
        decisions this application writes are about a file that was never imported, so there is no
        row anywhere for them to name.

        **It writes through `kernel/ledger.record_event` and has no insert of its own.** The table
        is the ledger, and a table with two whole-row writers is a table where one of them gains a
        column and the other does not. There is one writer.

        A receipt whose caller says nothing is an event whose verb is `decided`, which is the
        honest word for it: WHICH judgement was taken lives in the queue's own payload, and the
        payload is opaque to this slice by design. An area that writes receipts says what it
        actually did by handing its own verb and object through here; where it does not, `decided`
        is what it means rather than a word somebody guessed on its behalf.

        `via` is WHICH TASK decided, where nobody did (`user_id` None): the pass or task word the
        ledger's door asks of every act Sift takes (`kernel.ledger.SIFT_WITHOUT_A_TASK`), so a
        receipt never says "Sift" and nothing else. Ignored where a user decided: then the user is
        who acted, whatever pass laid the card out.
        """
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
        """Claim a decision for reversal. False if somebody else already has it.

        Claimed BEFORE the rows are put back rather than after, so two presses cannot both proceed
        to reverse. The alternative (reverse, then mark) lets a second press through while the
        first is still working, and what it then reverses is whatever the first press has not got
        to yet.
        """
        async with self._db.write() as connection:
            cursor = await connection.execute(_MARK_REVERSED, (self._now(), decision_id))
            return bool(cursor.rowcount)

    async def unmark_reversed(self, decision_id: str) -> None:
        """Give the claim back, when the reversal turned out to do nothing."""
        async with self._db.write() as connection:
            await connection.execute(
                "UPDATE workbench_decisions SET reversed_at = NULL WHERE id = ?", (decision_id,)
            )
