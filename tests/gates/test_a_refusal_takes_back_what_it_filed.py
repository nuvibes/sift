# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file with a refused stash-box answer and none applied carries no row the box filed.

A take-back that looks each row up by the name the answer gave leaves usernames and people on
files whose answers were refused: a Site's People wall then lists creators with nothing of theirs
on it, and a clip stays filed under a store it was never from. Nothing errors. The only way to
notice is to count rows, which is what this does.

Over a library built from every component this build registers:

- every column that points at a person, a username, a Site or a tag is placed
  (`catalog.POINTING_AT`), so a take-back never lets go of something that holds one by omission;
- every row an Undo writes back points only at tables it can check (`catalog._PARENTS`);
- an answer applied and then refused leaves nothing behind (`taken_back.LEFT_BY_A_REFUSAL`), and
  the repair leaves nothing on a library seeded that way.

A schema invariant cannot hold this: what a refused answer filed is told apart by the
names its standing neighbours give, which no trigger can read, and a trigger that refused a box's
row on a file with no applied answer would refuse the Undo that puts such rows back on purpose.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

import sift.main  # noqa: F401 (imported for its side effect: every component registers its schema)
from sift.kernel.access.catalog import _PARENTS, _PUT_BACK_INTO, POINTERS_AT, POINTING_AT
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.secret_store import SecretStore
from sift.slices.stash_boxes.adapter import as_json
from sift.slices.stash_boxes.service import StashBoxService
from sift.slices.stash_boxes.taken_back import left_by_a_refusal, repair

pytestmark = [pytest.mark.gate, pytest.mark.anyio]

_KEYS = 'SELECT "table" AS parent, "to" AS target FROM pragma_foreign_key_list(?)'


@pytest.fixture
async def library(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "library.sqlite3")
    await database.connect()
    await database.initialize_schema()
    yield database
    await database.close()


async def test_every_column_that_points_at_what_a_take_back_may_remove_is_placed(
    library: Database,
) -> None:
    for table, placed in POINTING_AT.items():
        found = {
            (str(row["tbl"]), str(row["col"]))
            for row in await library.fetch_all(POINTERS_AT, (table,))
        }
        listed = {(other, column) for other, column, _ in placed}
        assert found == listed, (
            f"what points at {table} and is not placed: {sorted(found - listed)};"
            f" placed and not in this library: {sorted(listed - found)}"
        )


async def test_every_row_an_undo_writes_back_points_where_it_can_check(library: Database) -> None:
    for table in sorted(_PUT_BACK_INTO):
        for key in await library.fetch_all(_KEYS, (table,)):
            parent = str(key["parent"])
            target = str(key["target"]) if key["target"] else "id"
            assert target in _PARENTS.get(parent, {}), f"{table} points at {parent}.{target}"


def _answer(box_id: str, **fields: object) -> str:
    return as_json(
        [
            FoundRecord(
                source_id=box_id,
                remote_id="scene",
                subject=Subject.ASSET,
                name="A scene",
                confidence=0.5,
                fields=dict(fields),
            )
        ]
    )


async def _seed(library: Database, state: str) -> None:
    """One file, one box, one answer in `state`, and a person and a username it filed."""
    async with library.write() as connection:
        await connection.execute(
            "INSERT INTO stash_boxes (id, name, endpoint, created_at)"
            " VALUES ('box', 'FansDB', 'https://box.example/graphql', 0)"
        )
        await connection.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('f', 'f', 'video', 0)"
        )
        await connection.execute(
            "INSERT INTO sites (id, name, name_sort, created_at) VALUES ('s', 'Storefront', 's', 0)"
        )
        await connection.execute(
            "INSERT INTO people (id, name, name_sort, created_at, created_by_kind,"
            " created_by_box_id) VALUES ('p', 'Wren Halloway', 'w', 0, 'box', 'box')"
        )
        await connection.execute(
            "INSERT INTO usernames (id, site_id, name, name_sort, person_id, created_at)"
            " VALUES ('u', 's', 'wrenclips', 'wrenclips', 'p', 0)"
        )
        await connection.execute(
            "INSERT INTO asset_people (asset_id, person_id, source) VALUES ('f', 'p', 'stash_box')"
        )
        await connection.execute(
            "INSERT INTO asset_usernames (asset_id, username_id, source)"
            " VALUES ('f', 'u', 'stash_box')"
        )
        await connection.execute(
            "INSERT INTO asset_stash_box_matches"
            " (asset_id, box_id, remote_id, payload, grade, state, found_at)"
            " VALUES ('f', 'box', 'scene', ?, 'unsure', ?, 0)",
            (_answer("box", people=["Wren Halloway"]), state),
        )


async def test_an_answer_applied_and_then_refused_leaves_nothing_behind(library: Database) -> None:
    await _seed(library, "applied")
    service = StashBoxService(library, SecretStore(library), object())  # type: ignore[arg-type]

    await service.take_back("f", "box", actor=Actor.sift("stash"), reopen=False, writer=None)

    assert await left_by_a_refusal(library) == 0


async def test_the_repair_leaves_nothing_where_a_refusal_left_rows(library: Database) -> None:
    await _seed(library, "refused")
    assert await left_by_a_refusal(library) == 2

    async with library.write() as connection:
        counts = await repair(connection)

    assert await left_by_a_refusal(library) == 0
    assert counts["files"] == 1
