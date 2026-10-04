# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which stash-box made a filing: the column (catalog 87), written by every filing from now on, and
given to filings from before it once (stash-box 21), with one History line and safe to run twice.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access.catalog.made import by_sift
from sift.kernel.access.catalog.people import attribute_assets_recording_on
from sift.kernel.access.catalog.usernames import file_assets_under_site_on
from sift.kernel.access.schema import initialize_catalog
from sift.kernel.access.sentences import text_of
from sift.kernel.access.sentences_songs import boxes_recorded
from sift.kernel.db import Database
from sift.kernel.migrations import column_exists
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.vocabulary import BOXES_RECORDED
from sift.slices.stash_boxes.adapter import as_json
from sift.slices.stash_boxes.filed_by import backfill
from sift.slices.stash_boxes.schema import initialize_stash_boxes

pytestmark = pytest.mark.anyio

NORTH = "box-north"
SOUTH = "box-south"


def _answer(box_id: str, **fields: object) -> str:
    return as_json(
        [
            FoundRecord(
                source_id=box_id,
                remote_id=f"scene-{box_id}",
                subject=Subject.ASSET,
                name="A scene",
                confidence=0.5,
                fields=dict(fields),
            )
        ]
    )


async def _run(db: Database, sql: str, *rows: tuple[Any, ...]) -> None:
    async with db.write() as connection:
        for row in rows:
            await connection.execute(sql, row)


@pytest.fixture
async def db(temp_db: Database) -> Database:
    """Two boxes, three files and the people and tag the boxes filed on them, none naming a box."""
    await temp_db.initialize_schema()
    await _run(
        temp_db,
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, 0)",
        (NORTH, "Northlight", "https://north.invalid/graphql"),
        (SOUTH, "Southwind", "https://south.invalid/graphql"),
    )
    await _run(
        temp_db,
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
        ("a-one", "digest-one"),
        ("a-two", "digest-two"),
        ("a-none", "digest-none"),
    )
    await _run(
        temp_db,
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, 1)",
        ("p-wren", "Wren Halloway", "wren halloway"),
        ("p-ilsa", "Ilsa Marrow", "ilsa marrow"),
        ("p-orla", "Orla Venn", "orla venn"),
    )
    await _run(
        temp_db,
        "INSERT INTO tags (id, name, name_sort, created_at) VALUES (?, ?, ?, 1)",
        ("t-dusk", "dusk", "dusk"),
    )
    await _run(
        temp_db,
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, 5)",
        # One applied answer: every row on the file is that box's.
        ("a-one", "p-wren", "stash_box"),
        # Two applied answers: each row goes to the one that names it.
        ("a-two", "p-wren", "stash_box"),
        ("a-two", "p-ilsa", "stash_box"),
        # Named by neither: no box is picked for it.
        ("a-two", "p-orla", "stash_box"),
        # No applied answer at all.
        ("a-none", "p-wren", "stash_box"),
        # Somebody's own filing is never touched.
        ("a-one", "p-ilsa", None),
    )
    await _run(
        temp_db,
        "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, 5)",
        ("a-one", "t-dusk", "stash_box"),
    )
    await _run(
        temp_db,
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
        " VALUES (?, ?, 'scene', ?, 'certain', 'applied', 0, ?)",
        ("a-one", NORTH, _answer(NORTH, people=["Wren Halloway"]), 1),
        ("a-two", SOUTH, _answer(SOUTH, people=["Ilsa Marrow"]), 1),
        ("a-two", NORTH, _answer(NORTH, people=["Wren Halloway", "Ilsa Marrow"]), 2),
    )
    return temp_db


async def _boxes(db: Database) -> dict[tuple[str, str], str | None]:
    rows = await db.fetch_all("SELECT asset_id, person_id, box_id FROM asset_people")
    return {(str(row["asset_id"]), str(row["person_id"])): row["box_id"] for row in rows}


async def test_every_filing_from_before_the_column_is_given_its_box_once(db: Database) -> None:
    async with db.write() as connection:
        first = await backfill(connection)
        second = await backfill(connection)

    assert first == {"rows": 4, "left": 2, "files": 2}
    assert second == {"rows": 0, "left": 2, "files": 0}
    assert await _boxes(db) == {
        ("a-one", "p-wren"): NORTH,
        ("a-one", "p-ilsa"): None,
        # The first answer applied that names her, never the later one that agreed.
        ("a-two", "p-wren"): NORTH,
        ("a-two", "p-ilsa"): SOUTH,
        ("a-two", "p-orla"): None,
        ("a-none", "p-wren"): None,
    }
    tag = await db.fetch_one("SELECT box_id FROM asset_tags WHERE asset_id = 'a-one'")
    assert tag is not None and tag["box_id"] == NORTH
    # One History line for the library, counting the files, said once and not per file.
    lines = await db.fetch_all(
        "SELECT payload FROM workbench_decisions WHERE verb = 'added' AND payload LIKE ?",
        (f"%{BOXES_RECORDED}%",),
    )
    assert [json.loads(str(row["payload"])) for row in lines] == [{BOXES_RECORDED: 2}]


async def test_the_step_runs_from_version_20_and_not_after(db: Database) -> None:
    async with db.write() as connection:
        await initialize_stash_boxes(connection, 21)
    assert (await _boxes(db))[("a-one", "p-wren")] is None
    async with db.write() as connection:
        await initialize_stash_boxes(connection, 20)
    assert (await _boxes(db))[("a-one", "p-wren")] == NORTH


async def test_catalog_87_adds_the_column_where_it_is_missing_and_twice_is_once(
    db: Database,
) -> None:
    async with db.write() as connection:
        for table in ("asset_people", "asset_tags", "asset_usernames"):
            await connection.execute(
                f"ALTER TABLE {table} DROP COLUMN box_id"  # nosemgrep: sift-no-string-built-sql
            )
        for _ in range(2):
            await initialize_catalog(connection, 86)
        for table in ("asset_people", "asset_tags", "asset_usernames"):
            assert await column_exists(connection, table, "box_id")


async def test_a_filing_written_now_carries_the_box_and_one_by_hand_none(db: Database) -> None:
    async with db.write() as connection:
        await connection.execute("DELETE FROM asset_people WHERE asset_id = 'a-none'")
        await attribute_assets_recording_on(
            connection, asset_ids=["a-none"], person_id="p-wren", source="stash_box", box_id=SOUTH
        )
        await attribute_assets_recording_on(
            connection, asset_ids=["a-none"], person_id="p-ilsa", source=None
        )
        await file_assets_under_site_on(
            connection,
            asset_ids=["a-none"],
            site="Harbor Clips",
            source="stash_box",
            made=by_sift("stash"),
            box_id=SOUTH,
        )
    boxes = await _boxes(db)
    assert (boxes[("a-none", "p-wren")], boxes[("a-none", "p-ilsa")]) == (SOUTH, None)
    filed = await db.fetch_one("SELECT box_id FROM asset_usernames WHERE asset_id = 'a-none'")
    assert filed is not None and filed["box_id"] == SOUTH


def test_the_line_counts_the_files_and_says_nothing_for_another_added() -> None:
    line = boxes_recorded("Sift", {BOXES_RECORDED: 12})
    assert line is not None
    assert text_of(line) == (
        "Sift recorded which stash-box added the people, tags and Sites on 12 files"
    )
    assert boxes_recorded("Sift", {}) is None
    assert boxes_recorded("Sift", {BOXES_RECORDED: True}) is None


async def _orla_on_a_fourth_file(db: Database, earlier: str, later: str) -> str | None:
    """Orla filed by a box on a file two boxes answered, Southwind first; the box she is given."""
    await _run(
        db,
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
        ("a-four", "digest-four"),
    )
    await _run(
        db,
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at)"
        " VALUES ('a-four', 'p-orla', 'stash_box', 5)",
        (),
    )
    await _run(
        db,
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
        " VALUES ('a-four', ?, 'scene', ?, 'certain', 'applied', 0, ?)",
        (SOUTH, earlier, 1),
        (NORTH, later, 2),
    )
    async with db.write() as connection:
        await backfill(connection)
    return (await _boxes(db))[("a-four", "p-orla")]


async def test_an_answer_kept_unreadable_names_nobody_and_the_step_goes_on(db: Database) -> None:
    assert await _orla_on_a_fourth_file(db, "{", _answer(NORTH, people=["Orla Venn"])) == NORTH


async def test_a_box_whose_studios_are_people_names_the_creator_by_its_studio(
    db: Database,
) -> None:
    """Read as the box reads it now: the studio Northlight names is Orla, its creator."""
    await _run(db, "UPDATE stash_boxes SET sites_are = 'person' WHERE id = ?", (NORTH,))
    southwind = _answer(SOUTH, people=["Ilsa Marrow"])
    assert await _orla_on_a_fourth_file(db, southwind, _answer(NORTH, site="Orla Venn")) == NORTH


@pytest.mark.parametrize(
    ("turned_from", "box"), [("Harbor Clips", SOUTH), ("Northlight Raw", None)]
)
async def test_a_username_turned_out_of_a_site_goes_to_the_answer_that_named_the_site(
    db: Database, turned_from: str, box: str | None
) -> None:
    """Sift read a Site a box made as her username on another Site; no answer names that username,
    and the one whose `site` named the Site it was turned from is the box that filed it. A Site no
    answer named leaves her without a box."""
    await _run(
        db,
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
        ("a-three", "digest-three"),
    )
    await _run(
        db, "INSERT INTO sites (id, name, created_at) VALUES (?, ?, 1)", ("s-vault", "Clipvault")
    )
    await _run(
        db,
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, 1)",
        ("u-harbor", "s-vault", "Harbor-Clips"),
    )
    await _run(
        db,
        "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at)"
        " VALUES (?, ?, 'stash_box', 5)",
        ("a-three", "u-harbor"),
    )
    await _run(
        db,
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
        " VALUES (?, ?, 'scene', ?, 'certain', 'applied', 0, ?)",
        (
            "a-three",
            NORTH,
            _answer(NORTH, accounts=[{"site": "Clipvault", "handle": "harborclips"}]),
            1,
        ),
        ("a-three", SOUTH, _answer(SOUTH, site="Harbor Clips"), 2),
    )
    await _run(
        db,
        "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at, verb)"
        " VALUES (?, 'tagger', '', '', ?, 3, 'moved')",
        (
            "d-turned",
            json.dumps({"kind": "turned", "site_name": turned_from, "username_id": "u-harbor"}),
        ),
    )

    async with db.write() as connection:
        await backfill(connection)

    filed = await db.fetch_one("SELECT box_id FROM asset_usernames WHERE asset_id = 'a-three'")
    assert filed is not None and filed["box_id"] == box
