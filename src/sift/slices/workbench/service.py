# SPDX-License-Identifier: AGPL-3.0-or-later
"""The board, and taking a decision back.

This knows what a queue IS and nothing about what any particular queue holds. Everything specific
(what a folder claim means, what naming a face group does, how to put either of them back) is
the registering area's, reached through the shape in `kernel/workbench`.

That is the whole point of the arrangement. A panel arriving in a later version registers itself and
changes nothing here, and this file does not grow a branch per feature.
"""

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
        """Put back what one decision did, and say how many of its acts went back.

        The record is claimed first and given back if the reversal turns out to do nothing, so two
        presses cannot both reverse, which is what a slow request produces every time somebody
        presses again rather than waits.
        """
        undone, queue = await self._reverse(viewer, decision_id)
        log.info("workbench.undo", queue=queue, put_back=undone.put_back, of=undone.of)
        _rung(undone.put_back)
        return undone

    async def undo_each(self, viewer: Viewer, decision_ids: Sequence[str]) -> int:
        """Put back each of these decisions through its own undo, and say how many moved.

        The loop behind the feed's "Undo all" (`ledger_router.undo_all`).

        **One at a time, through the per-receipt undo, and that is the whole design of it.** A
        folded line is a reading of the record and not a thing that was ever decided: there is no
        bulk reversal to write, and inventing one would be a second way to put a decision back that
        no queue had agreed to. So this is the press somebody would otherwise make N times, made
        once: every receipt goes through exactly the path its own Undo goes through, claimed on its
        own row, so one already taken back is skipped rather than reversed twice and a queue that
        refuses one does not cost the rest. It answers how many moved, because a run of four
        thousand where nine hundred were already back is a true answer no boolean can carry.

        **The price** is one write transaction per receipt on the request, so a couple of thousand
        filings go back in seconds. A library an order of magnitude larger would put a request in
        the tens of seconds, and that is the point at which this wants a job (`kernel/jobs`):
        countable, resumable, cancellable.
        """
        undone = 0
        queue = ""
        for one in decision_ids:
            try:
                put, queue = await self._reverse(viewer, one)
                undone += int(put.put_back > 0)
            except NotFound:
                # Already taken back, or from an area this build no longer has. Both are reasons
                # this receipt contributes nothing, and neither is a reason to stop: the rest of
                # the run is exactly as reversible as it was.
                continue
        log.info("workbench.undo_all", queue=queue, undone=undone, of=len(decision_ids))
        _rung(undone)
        return undone

    async def _reverse(self, viewer: Viewer, decision_id: str) -> tuple[Reversal, str]:
        """One reversal, without the announcement: whether anything moved, and whose queue it was.

        See `undo`, which is this plus the log line.

        Apart so that taking back a fold of four thousand writes ONE line rather than four thousand:
        a log that buries every other thing the application said is a log nobody can read, and the
        count is the fact worth keeping.
        """
        decision = await self._store.decision(decision_id)
        if decision is None:
            raise NotFound("that decision isn't one that can be taken back")

        queue = self._workbench.reverser(decision.queue)
        if queue is None:
            # The area that made it is not in this build any more. Saying so is better than
            # reporting a failure that reads as "it went wrong": nothing went wrong, and nothing
            # here can put it back.
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
        # The decisions that rested on this one and went back with it: each marked taken back, so
        # its own line reads as undone. One already taken back stays as it was.
        for one in undone.along:
            await self._store.mark_reversed(one)
        return undone, decision.queue


def _as_reversal(answered: bool | Reversal) -> Reversal:
    """A reverser's answer as counts: a decision of one act went back whole or not at all."""
    if isinstance(answered, Reversal):
        return answered
    return Reversal(put_back=int(answered), of=1)


#: What each area has waiting for somebody.
def _rung(moved: int) -> None:
    """The one bell for an Undo, rung once per press after every receipt it took back.

    The receipt's mark (`Store.mark_reversed`) and some reversers' writes tell nobody, so without
    this a tab that did not press would keep the line without its "taken back" until it reloaded.
    Once per press and not per receipt: a fold of four thousand is one re-read on every open
    screen, not four thousand. Nothing is rung where nothing moved.
    """
    if moved > 0:
        announce_now(EVERY_ADMIN, About.LIBRARY)


SERVICE: Part[WorkbenchService] = Part("workbench_service")
