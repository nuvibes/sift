# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stash-box version 20: what an applied answer left on its file when a box asked again turned
it back into a question, taken back once, while a first question's file keeps its rows."""

from __future__ import annotations

import json

import pytest

from sift.kernel.access.sentences import BY_HAND, TOOK_BACK
from sift.kernel.db import Database
from sift.slices.stash_boxes.schema import initialize_stash_boxes
from sift.slices.stash_boxes.taken_back import left_by_a_refusal, repair
from sift.slices.stash_boxes.tests.test_taken_back import (
    CREATORS,
    _people,
    _receipts,
    _refused_file,
    _run,
    db,
)

__all__ = ["db"]

pytestmark = pytest.mark.anyio


async def _asked(db: Database, *found: int) -> None:
    """file-1's answer as a question again, after the box has answered about it `found` times."""
    await _refused_file(db)
    await db.execute(
        "UPDATE asset_stash_box_matches SET state = 'waiting', decided_at = NULL"
        " WHERE asset_id = 'file-1'"
    )
    await _run(
        db,
        "INSERT INTO stash_box_scans (id, asset_id, box_id, scanned_at, found)"
        " VALUES (?, 'file-1', ?, ?, ?)",
        *[(f"scan-{at}", CREATORS, at, one) for at, one in enumerate(found)],
    )


async def _repaired(db: Database) -> dict[str, int]:
    async with db.write() as connection:
        return await repair(connection, asked_again=True)


async def test_a_reopened_answer_s_rows_come_off_once_with_one_line(db: Database) -> None:
    await _asked(db, 1, 1)
    assert await left_by_a_refusal(db) == 8

    counts = await _repaired(db)
    again = await _repaired(db)

    assert (counts["files"], counts["people"], counts["tags"], counts["accounts"]) == (1, 2, 2, 4)
    assert not any(again.values())
    assert await left_by_a_refusal(db) == 0
    assert await _people(db, "file-1") == {"p-orla"}
    [line] = await _receipts(db)
    assert (line["actor_kind"], line["actor_id"]) == ("sift", "update")
    assert line["title"] == "Took back what FansDB said about 1 file"
    payload = json.loads(str(line["payload"]))
    assert (payload[TOOK_BACK], payload["by_file"]) == (BY_HAND, {"file-1": ["FansDB"]})


async def test_a_first_question_leaves_what_a_stash_import_filed(db: Database) -> None:
    await _asked(db, 0, 1)

    counts = await _repaired(db)

    assert not any(counts.values())
    assert await _people(db, "file-1") == {"p-wren", "p-ilsa", "p-orla"}
    assert await _receipts(db) == []


async def test_the_stash_box_step_runs_it_on_a_library_before_it(db: Database) -> None:
    await _asked(db, 1, 1)

    async with db.write() as connection:
        await initialize_stash_boxes(connection, 19)

    assert await left_by_a_refusal(db) == 0
    assert len(await _receipts(db)) == 1
