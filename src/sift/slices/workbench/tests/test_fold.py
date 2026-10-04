# SPDX-License-Identifier: AGPL-3.0-or-later
"""Undo all on a folded feed line puts back every receipt of the press through its own Undo, and
says how many moved out of how many."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from sift.kernel.access import Viewer
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.vocabulary import LEDGER_QUEUE
from sift.kernel.workbench import Workbench
from sift.slices.workbench.router import undo_all
from sift.slices.workbench.service import WorkbenchService
from sift.slices.workbench.tests.conftest import FakeQueue

pytestmark = pytest.mark.unit

#: A moment far enough from the epoch that a gap can be subtracted from it.
START = 1_700_000_000

FILED = "Filed under orla_fennimore on Instagram from the file's own name"


async def receipt(
    database: Database,
    *,
    queue: str = "filenames",
    user_id: str | None = None,
    at: int,
    title: str = FILED,
    payload: str = "{}",
) -> str:
    """One receipt at a moment the test chose.

    Straight into the table rather than through `record_on`, which stamps the clock itself: the
    whole of what is measured here is the SPACING of these rows.
    """
    decision_id = new_id()
    async with database.write() as connection:
        await connection.execute(
            "INSERT INTO workbench_decisions"
            " (id, queue, user_id, title, detail, payload, decided_at, reversed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (decision_id, queue, user_id, title, "One file filed.", payload, at),
        )
    return decision_id


async def test_undo_all_on_a_folded_feed_line_takes_back_each_act_that_line_said(
    service: WorkbenchService, temp_db: Database, workbench: Workbench, admin: Viewer
) -> None:
    """The feed's line is a press, read again here the way the feed drew it, and each of its acts
    goes through its own receipt's undo, so one already put back by hand is skipped, and the
    answer says both how many moved and how many the line stood for."""
    queue = FakeQueue(name="filenames")
    workbench.register(queue)
    ids = [await receipt(temp_db, at=START + step) for step in range(3)]
    await service.undo(admin, ids[0])

    answer = await undo_all(ids[1], temp_db, service, admin, kind=None, verb=None)

    assert (answer.undone, answer.of) == (2, 3)


async def test_undo_all_on_a_line_drawn_under_the_decisions_narrowing_reads_the_same_press(
    service: WorkbenchService, temp_db: Database, workbench: Workbench, admin: Viewer
) -> None:
    """History's Decisions is a narrowing of the feed, so its Undo all sends the narrowing back and
    the press is read the way that line was drawn: every decision of it, each through its own
    receipt's undo, exactly as the line on the unnarrowed feed does."""
    workbench.register(FakeQueue(name="filenames"))
    ids = [await receipt(temp_db, at=START + step) for step in range(3)]

    answer = await undo_all(ids[2], temp_db, service, admin, kind=None, verb=None, decisions=True)

    assert (answer.undone, answer.of) == (3, 3)


async def test_undo_all_on_a_line_of_acts_that_were_not_decisions_is_not_found(
    service: WorkbenchService, temp_db: Database, admin: Viewer
) -> None:
    """A line of plain record (a rename, a setting moved) has no receipt behind it, and says so
    rather than answering that nothing moved."""
    ids = [await receipt(temp_db, queue=LEDGER_QUEUE, at=START + step) for step in range(2)]

    with pytest.raises(HTTPException) as refused:
        await undo_all(ids[0], temp_db, service, admin, kind=None, verb=None)

    assert refused.value.status_code == 404
