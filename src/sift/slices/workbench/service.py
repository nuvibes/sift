# SPDX-License-Identifier: AGPL-3.0-or-later
"""The board, and taking a decision back, through each area's registered queue."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sift.kernel.access import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.log import get_logger
from sift.kernel.wiring import Part
from sift.kernel.workbench import Reversal, Summary, Workbench
from sift.slices.workbench.store import Store

log = get_logger(__name__)


class WorkbenchError(Exception):
    """Something the caller asked for cannot be done, with a sentence saying why."""


class NotFound(WorkbenchError):
    """No such decision, or none that can still be taken back."""


@dataclass(frozen=True, slots=True)
class Board:
    """What is waiting. What was decided is the feed's, on History's Decisions."""

    queues: list[Summary]


class WorkbenchService:
    """What the workbench itself owns: the board and undo."""

    def __init__(self, *, store: Store, workbench: Workbench) -> None:
        self._store = store
        self._workbench = workbench

    async def board(self, viewer: Viewer, only: Collection[str] | None = None) -> Board:
        return Board(queues=await self._workbench.board(viewer, only))

    async def undo(self, viewer: Viewer, decision_id: str) -> Reversal:
        """Put back what one decision did, claimed first so two presses cannot both reverse it."""
        undone, queue = await self._reverse(viewer, decision_id)
        log.info("workbench.undo", queue=queue, put_back=undone.put_back, of=undone.of)
        _rung(undone.put_back)
        return undone

    async def undo_each(self, viewer: Viewer, decision_ids: Sequence[str]) -> int:
        """Put back each decision through its own undo, one at a time, and say how many moved."""
        undone = 0
        queue = ""
        for one in decision_ids:
            try:
                put, queue = await self._reverse(viewer, one)
                undone += int(put.put_back > 0)
            except NotFound:
                # Already taken back, or from an area this build no longer has.
                continue
        log.info("workbench.undo_all", queue=queue, undone=undone, of=len(decision_ids))
        _rung(undone)
        return undone

    async def _reverse(self, viewer: Viewer, decision_id: str) -> tuple[Reversal, str]:
        """One reversal without its log line, so a fold of thousands writes one line."""
        decision = await self._store.decision(decision_id)
        if decision is None:
            raise NotFound("that decision isn't one that can be taken back")

        queue = self._workbench.reverser(decision.queue)
        if queue is None:
            # The area that made it is not in this build any more; nothing went wrong.
            raise NotFound("nothing in this version knows how to take that decision back")

        if not await self._store.mark_reversed(decision_id):
            raise NotFound("that decision has already been taken back")

        try:
            answered = await queue.reverse(viewer, decision.id, decision.payload)
        except Exception:
            await self._store.unmark_reversed(decision_id)
            raise

        undone = _as_reversal(answered)
        if undone.put_back == 0:
            await self._store.unmark_reversed(decision_id)
        # The decisions that rested on this one went back with it, so each reads as undone.
        for one in undone.along:
            await self._store.mark_reversed(one)
        return undone, decision.queue


def _as_reversal(answered: bool | Reversal) -> Reversal:
    """A reverser's answer as counts: a decision of one act went back whole or not at all."""
    if isinstance(answered, Reversal):
        return answered
    return Reversal(put_back=int(answered), of=1)


def _rung(moved: int) -> None:
    """The one bell for an Undo, rung once per press, so other tabs mark it taken back."""
    if moved > 0:
        announce_now(EVERY_ADMIN, About.LIBRARY)


SERVICE: Part[WorkbenchService] = Part("workbench_service")
