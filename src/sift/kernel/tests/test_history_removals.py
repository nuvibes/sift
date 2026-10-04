# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which stash-box a line about a box's filing names: the boxes that named a person on some files,
and the box a filing Sift took off on a box's answer was taken off for."""

from __future__ import annotations

import json
from typing import Any

import pytest

# Imported for their side effect: the stash-box tables and the ledger's.
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access.history_boxes import boxes_that_named
from sift.kernel.access.history_events import LedgerEvent, Thing
from sift.kernel.access.history_removals import _boxes_of_removal, removals_named
from sift.kernel.access.sentences_who import ANSWER_OF
from sift.kernel.db import Database
from sift.kernel.vocabulary import VIA_STASH

pytestmark = pytest.mark.anyio

NORTH, SOUTH = "Northlight", "Southwind"


async def _boxes_and_files(db: Database, *files: str) -> None:
    await db.initialize_schema()
    for box_id, name in (("b-north", NORTH), ("b-south", SOUTH)):
        await db.execute(
            "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, 0)",
            (box_id, name, f"https://{box_id}.invalid/graphql"),
        )
    for asset_id in files:
        await db.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
            (asset_id, f"digest-{asset_id}"),
        )


async def _applied(db: Database, asset_id: str, box_id: str) -> None:
    await db.execute(
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
        " VALUES (?, ?, 'scene', '[]', 'certain', 'applied', 0, 1)",
        (asset_id, box_id),
    )


async def test_the_boxes_that_named_her_are_said_the_most_files_first(temp_db: Database) -> None:
    """A filing naming no box, on a file two boxes answered, is no box's: never a guess."""
    await _boxes_and_files(temp_db, "a1", "a2", "a3", "a4")
    await temp_db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES ('p1', 'Wren', 'wren', 0)"
    )
    for asset_id, box_id in (("a1", "b-south"), ("a2", "b-north"), ("a3", "b-north"), ("a4", None)):
        await temp_db.execute(
            "INSERT INTO asset_people (asset_id, person_id, source, decided_at, box_id)"
            " VALUES (?, 'p1', 'stash_box', 1, ?)",
            (asset_id, box_id),
        )
    await _applied(temp_db, "a4", "b-north")
    await _applied(temp_db, "a4", "b-south")

    assert await boxes_that_named(temp_db, "p1", ["a1", "a2", "a3", "a4"]) == [NORTH, SOUTH]

    await temp_db.execute("DROP TABLE asset_stash_box_matches")
    assert await boxes_that_named(temp_db, "p1", ["a1"]) == []


def _removal(event_id: str, subjects: tuple[Thing, ...]) -> LedgerEvent:
    return LedgerEvent(
        id=event_id,
        at=100,
        verb="unlinked",
        actor_kind="sift",
        actor_id=VIA_STASH,
        user_id=None,
        object=Thing("tag", "t1", "poolside"),
        count=None,
        queue="ledger",
        payload="",
        title="",
        detail="",
        reversed_at=None,
        subjects=subjects,
    )


async def test_a_removal_naming_no_file_is_left_to_say_a_stash_box(temp_db: Database) -> None:
    """In App History a removal whose file this reader is not given names no box; the one beside it
    on a file with one applied answer names that box."""
    await _boxes_and_files(temp_db, "a1")
    await _applied(temp_db, "a1", "b-south")
    on_a_file = _removal("e1", (Thing("asset", "a1", "one.mp4"),))
    no_file = _removal("e2", ())

    named = {one.id: one.payload for one in await removals_named(temp_db, [on_a_file, no_file])}

    assert json.loads(named["e1"]) == {ANSWER_OF: [SOUTH]}
    assert named["e2"] == ""


def _receipt(payload: dict[str, object], box: str | None = None) -> dict[str, object]:
    return {"at": 100, "payload": json.dumps(payload), "box": box}


def _answer(box: str, payload: object) -> dict[str, object]:
    stored = payload if isinstance(payload, str) else json.dumps(payload)
    return {"box": box, "state": "applied", "payload": stored}


@pytest.mark.parametrize(
    ("kind", "receipts", "answers", "boxes"),
    [
        # The take-back's own list for this file, where it kept one per file.
        ("tag", [_receipt({"boxes": [NORTH, SOUTH], "by_file": {"a1": [SOUTH]}})], [], [SOUTH]),
        # A receipt that lists no boxes names the box it was written about.
        ("tag", [_receipt({}, box=NORTH)], [], [NORTH]),
        # Two boxes: the one whose readable answer names it; an unreadable one names nothing.
        (
            "tag",
            [_receipt({"boxes": [NORTH, SOUTH]})],
            [
                _answer(NORTH, "{"),
                _answer(NORTH, [{"name": "no fields"}]),
                _answer(SOUTH, [{"fields": {"tags": ["Poolside"]}}]),
            ],
            [SOUTH],
        ),
        # A Site is named by the answer's own Site or by one of its accounts.
        (
            "site",
            [_receipt({"boxes": [NORTH, SOUTH]})],
            [
                _answer(NORTH, [{"fields": {"accounts": ["junk", {"site": "Poolside"}]}}]),
                _answer(SOUTH, [{"fields": {"accounts": "junk"}}]),
            ],
            [NORTH],
        ),
    ],
)
def test_a_removal_names_the_box_its_take_back_was_for(
    kind: str, receipts: list[Any], answers: list[Any], boxes: list[str]
) -> None:
    assert _boxes_of_removal(100, kind, {"poolside"}, receipts, answers, "a1") == boxes
