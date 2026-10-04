# SPDX-License-Identifier: AGPL-3.0-or-later
"""A stash-box answer taken back leaves nothing behind.

Held here over a library on disk: the repair takes off every row a refused answer filed on a file no
answer still stands on, and the usernames and people that leaves holding nothing, with one History
line and an Undo that puts every row back, and is safe to run twice; a row somebody filed by hand,
a folder filed, or a standing answer still names stays; refusing an answer already applied takes
off what it wrote, and its Undo puts the rows and the answer back.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Role, Viewer
from sift.kernel.access import schema as catalog_schema
from sift.kernel.access.catalog import POINTERS_AT, POINTING_AT
from sift.kernel.access.sentences import BY_HAND, PICTURE_ALONE, TOOK_BACK
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.kernel.log import redact
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.secret_store import SecretStore
from sift.slices.stash_boxes.adapter import as_json
from sift.slices.stash_boxes.queue import NAME, TaggerQueue
from sift.slices.stash_boxes.schema import initialize_stash_boxes
from sift.slices.stash_boxes.service import StashBoxService
from sift.slices.stash_boxes.taken_back import RECEIPTS, left_by_a_refusal, repair
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.anyio

CREATORS = "box-creators"
SCENES = "box-scenes"

#: Everything a take-back touches, read whole, so "put back whole" is a comparison of two of these.
TABLES = (
    "SELECT * FROM people ORDER BY id",
    "SELECT * FROM people_aliases ORDER BY id",
    "SELECT * FROM usernames ORDER BY id",
    "SELECT * FROM sites ORDER BY id",
    "SELECT * FROM site_aliases ORDER BY id",
    "SELECT * FROM tags ORDER BY id",
    "SELECT * FROM tag_aliases ORDER BY id",
    "SELECT * FROM asset_tags ORDER BY asset_id, tag_id",
    "SELECT * FROM asset_people ORDER BY asset_id, person_id",
    "SELECT * FROM asset_usernames ORDER BY asset_id, username_id",
    "SELECT asset_id, box_id, state, decided_at FROM asset_stash_box_matches"
    " ORDER BY asset_id, box_id",
)


async def _whole(db: Database) -> list[list[dict[str, object]]]:
    return [[dict(row) for row in await db.fetch_all(sql)] for sql in TABLES]


class _Adapter:
    async def search(self, box: object, term: str) -> list[FoundRecord]:  # pragma: no cover
        raise AssertionError("nothing here asks a box")


class _Access:
    async def get_asset(self, viewer: Viewer, asset_id: str) -> object | None:
        _ = (viewer, asset_id)
        return object()


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
    """A library with two boxes, one Site, and the people and usernames the cases file."""
    await temp_db.initialize_schema()
    await _run(
        temp_db,
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, 0)",
        (CREATORS, "FansDB", "https://creators.example/graphql"),
        (SCENES, "StashDB", "https://scenes.example/graphql"),
    )
    await _run(
        temp_db,
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, ?, ?, 1)",
        ("site-store", "Storefront", "storefront"),
    )
    await _run(
        temp_db,
        "INSERT INTO people (id, name, name_sort, created_at, created_by_kind, created_by_box_id,"
        " edited_at) VALUES (?, ?, ?, 1, ?, ?, ?)",
        # A person the creators' box made from its answer, nothing else of hers here.
        ("p-wren", "Wren Halloway", "wren halloway", "box", CREATORS, 77),
        # A person somebody made, filed by the box on one file and by hand on another.
        ("p-ilsa", "Ilsa Marrow", "ilsa marrow", "user", None, None),
        # A person a folder put on the file.
        ("p-orla", "Orla Venn", "orla venn", "sift", None, None),
        # A person the scenes box made, whose answer still stands on its file.
        ("p-pell", "Pell Arden", "pell arden", "box", SCENES, None),
    )
    await _run(
        temp_db,
        "INSERT INTO people_aliases (id, person_id, alias, alias_sort, added_at)"
        " VALUES (?, ?, ?, ?, 2)",
        ("alias-wren", "p-wren", "Wren H", "wren h"),
    )
    await _run(
        temp_db,
        "INSERT INTO usernames (id, site_id, name, name_sort, person_id, created_at)"
        " VALUES (?, 'site-store', ?, ?, ?, 3)",
        # Her store, made by the answer that filed the file under it.
        ("u-wren", "wrenclips", "wrenclips", "p-wren"),
        # A store the box filed one file under and a folder filed another.
        ("u-orla", "orlavenn", "orlavenn", None),
    )
    for asset_id in ("file-1", "file-2", "file-3", "file-5"):
        await _run(
            temp_db,
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
            (asset_id, asset_id),
        )
    return temp_db


@pytest.fixture
async def admin(db: Database) -> Viewer:
    """The admin who presses: a real user, because a receipt names one."""
    return await create_user(db, Role.ADMIN)


async def _refused_file(db: Database) -> None:
    """file-1: an answer refused, which filed her store, her, Ilsa and Orla's store, two Sites the
    box made by their nameless rows and two tags the box made; a folder filed Orla on it. file-2:
    Ilsa by hand. file-3: Orla's store, and a username on the second box Site, by a folder. Ilsa
    carries the second tag."""
    await _run(
        db,
        "INSERT INTO sites (id, name, name_sort, created_at, created_by_kind, created_by_box_id)"
        " VALUES (?, ?, ?, 1, 'box', ?)",
        ("site-made", "Quillmark", "quillmark", CREATORS),
        ("site-held", "Fernhollow", "fernhollow", CREATORS),
    )
    await _run(
        db,
        "INSERT INTO site_aliases (id, site_id, alias, alias_sort, added_at) VALUES (?, ?, ?, ?, 2)",
        ("alias-made", "site-made", "Quillmark Studio", "quillmark studio"),
    )
    await _run(
        db,
        "INSERT INTO usernames (id, site_id, name, name_sort, created_at) VALUES (?, ?, ?, ?, 3)",
        ("u-made-nameless", "site-made", "", ""),
        ("u-held-nameless", "site-held", "", ""),
        ("u-held", "site-held", "fernclips", "fernclips"),
    )
    await _run(
        db,
        "INSERT INTO tags (id, name, name_sort, created_at, created_by_kind, created_by_box_id)"
        " VALUES (?, ?, ?, 1, 'box', ?)",
        ("t-made", "lantern", "lantern", CREATORS),
        ("t-held", "harbor", "harbor", CREATORS),
    )
    await _run(
        db,
        "INSERT INTO tag_aliases (id, tag_id, alias, alias_sort, added_at) VALUES (?, ?, ?, ?, 2)",
        ("alias-tag", "t-made", "lanterns", "lanterns"),
    )
    await _run(
        db,
        "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, 7)",
        ("file-1", "t-made", "stash_box"),
        ("file-1", "t-held", "stash_box"),
    )
    await _run(
        db,
        "INSERT INTO person_tags (person_id, tag_id) VALUES (?, ?)",
        ("p-ilsa", "t-held"),
    )
    await _run(
        db,
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
        " VALUES (?, ?, 'scene', ?, 'unsure', 'refused', 5, 6)",
        ("file-1", CREATORS, _answer(CREATORS, people=["Wren Halloway"])),
    )
    await _run(
        db,
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, 7)",
        ("file-1", "p-wren", "stash_box"),
        ("file-1", "p-ilsa", "stash_box"),
        ("file-1", "p-orla", "folder"),
        ("file-2", "p-ilsa", None),
    )
    await _run(
        db,
        "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at) VALUES (?, ?, ?, 8)",
        ("file-1", "u-wren", "stash_box"),
        ("file-1", "u-orla", "stash_box"),
        ("file-1", "u-made-nameless", "stash_box"),
        ("file-1", "u-held-nameless", "stash_box"),
        ("file-3", "u-orla", "folder"),
        ("file-3", "u-held", "folder"),
    )


async def _repaired(db: Database) -> dict[str, int]:
    async with db.write() as connection:
        return await repair(connection)


async def _people(db: Database, asset_id: str) -> set[str]:
    rows = await db.fetch_all("SELECT person_id FROM asset_people WHERE asset_id = ?", (asset_id,))
    return {str(row["person_id"]) for row in rows}


async def _usernames(db: Database, asset_id: str) -> set[str]:
    rows = await db.fetch_all(
        "SELECT username_id FROM asset_usernames WHERE asset_id = ?", (asset_id,)
    )
    return {str(row["username_id"]) for row in rows}


async def _there(db: Database, table: str, row_id: str) -> bool:
    statement = {
        "people": "SELECT 1 FROM people WHERE id = ?",
        "sites": "SELECT 1 FROM sites WHERE id = ?",
        "tags": "SELECT 1 FROM tags WHERE id = ?",
    }.get(table, "SELECT 1 FROM usernames WHERE id = ?")
    return await db.fetch_one(statement, (row_id,)) is not None


async def _receipts(db: Database) -> list[Mapping[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, verb, actor_kind, actor_id, title, detail, payload FROM workbench_decisions"
        " WHERE queue = ? ORDER BY id",
        (RECEIPTS,),
    )
    return [dict(row) for row in rows]


async def test_the_repair_takes_back_what_a_refused_answer_filed_and_the_shells_it_leaves(
    db: Database,
) -> None:
    await _refused_file(db)
    assert await left_by_a_refusal(db) == 8

    counts = await _repaired(db)

    assert counts == {
        "files": 1,
        "people": 2,
        "tags": 2,
        "accounts": 4,
        "accounts_removed": 1,
        "people_removed": 1,
        "sites_removed": 1,
        "tags_removed": 1,
    }
    # The log line prints these counts as they are: none sits under a key the log blanks as a name.
    printed = {key: redact(value, key, always_personal=True) for key, value in counts.items()}
    assert printed == counts
    assert await left_by_a_refusal(db) == 0
    # What a folder and a person filed stays, on this file and on the others.
    assert await _people(db, "file-1") == {"p-orla"}
    assert await _usernames(db, "file-1") == set()
    assert await _people(db, "file-2") == {"p-ilsa"}
    assert await _usernames(db, "file-3") == {"u-orla", "u-held"}
    # Her store and she held nothing else; Orla's store and Ilsa do.
    assert not await _there(db, "usernames", "u-wren")
    assert not await _there(db, "people", "p-wren")
    assert await _there(db, "usernames", "u-orla")
    assert await _there(db, "people", "p-ilsa")
    # The box's Site with nothing on it goes with its nameless row; the one a username stays on
    # keeps it. The box's tag on nothing goes; the one on Ilsa stays.
    assert not await _there(db, "sites", "site-made")
    assert not await _there(db, "usernames", "u-made-nameless")
    assert await _there(db, "sites", "site-held")
    assert not await _there(db, "tags", "t-made")
    assert await _there(db, "tags", "t-held")
    [line] = await _receipts(db)
    assert (line["verb"], line["actor_kind"], line["actor_id"]) == ("removed", "sift", "update")
    assert line["title"] == "Took back what FansDB said about 1 file"
    assert line["detail"] == (
        "Took off 2 people, 2 tags and 4 usernames. Removed 1 username, 1 person, 1 Site and 1 tag"
        " that nothing else held. Undo puts them all back."
    )
    payload = json.loads(str(line["payload"]))
    assert (payload[TOOK_BACK], payload["boxes"]) == (PICTURE_ALONE, ["FansDB"])
    assert payload["by_file"] == {"file-1": ["FansDB"]}


async def test_the_repair_takes_the_network_above_a_site_it_removed_and_a_username_of_nobody(
    db: Database,
) -> None:
    """A Site the box made goes with the network above it where that holds nothing else either;
    a username the box filed that names no person goes as any shell does."""
    await _refused_file(db)
    await _run(
        db,
        "INSERT INTO sites (id, name, name_sort, created_at, created_by_kind, created_by_box_id)"
        " VALUES (?, ?, ?, 1, 'box', ?)",
        ("site-net", "Quillmark network", "quillmark network", CREATORS),
    )
    await _run(db, "UPDATE sites SET parent_id = ? WHERE id = ?", ("site-net", "site-made"))
    await _run(
        db,
        "INSERT INTO usernames (id, site_id, name, name_sort, created_at) VALUES (?, ?, ?, ?, 3)",
        ("u-nobody", "site-made", "quillfan", "quillfan"),
    )
    await _run(
        db,
        "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at) VALUES (?, ?, ?, 8)",
        ("file-1", "u-nobody", "stash_box"),
    )

    counts = await _repaired(db)

    assert (counts["sites_removed"], counts["accounts_removed"]) == (2, 2)
    assert not await _there(db, "sites", "site-net")
    assert not await _there(db, "usernames", "u-nobody")


async def test_the_repair_run_twice_finds_nothing_the_second_time(db: Database) -> None:
    await _refused_file(db)
    await _repaired(db)

    again = await _repaired(db)

    assert not any(again.values())
    assert len(await _receipts(db)) == 1


async def test_a_file_an_answer_still_stands_on_is_not_repaired(db: Database) -> None:
    await _run(
        db,
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
        " VALUES (?, ?, 'scene', ?, ?, ?, 5, 6)",
        ("file-5", CREATORS, _answer(CREATORS, people=["Wren Halloway"]), "unsure", "refused"),
        ("file-5", SCENES, _answer(SCENES, people=["Pell Arden"]), "certain", "applied"),
    )
    await _run(
        db,
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, 7)",
        ("file-5", "p-pell", "stash_box"),
    )

    counts = await _repaired(db)

    assert not any(counts.values())
    assert await _people(db, "file-5") == {"p-pell"}


async def test_undo_of_the_repair_puts_every_row_back(db: Database, admin: Viewer) -> None:
    await _refused_file(db)
    before = await _whole(db)
    await _repaired(db)
    [line] = await _receipts(db)
    service = StashBoxService(db, SecretStore(db), _Adapter())  # type: ignore[arg-type]
    queue = TaggerQueue(service, _Access())  # type: ignore[arg-type]

    assert await queue.reverse(admin, str(line["id"]), str(line["payload"]))

    assert await _whole(db) == before
    kept = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM undo_rows WHERE receipt_id = ?", (str(line["id"]),)
    )
    assert kept is not None and int(kept["n"]) == 0


async def test_refusing_an_applied_answer_takes_back_what_it_wrote_and_keeps_what_stands(
    db: Database, admin: Viewer
) -> None:
    await _run(
        db,
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
        " VALUES (?, ?, 'scene', ?, 'certain', 'applied', 5, 6)",
        ("file-5", CREATORS, _answer(CREATORS, people=["Wren Halloway", "Pell Arden"])),
        ("file-5", SCENES, _answer(SCENES, people=["pell arden"])),
    )
    await _run(
        db,
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, 7)",
        ("file-5", "p-wren", "stash_box"),
        ("file-5", "p-pell", "stash_box"),
    )
    await _run(
        db,
        "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at) VALUES (?, ?, ?, 8)",
        ("file-5", "u-wren", "stash_box"),
    )
    before = await _whole(db)
    service = StashBoxService(db, SecretStore(db), _Adapter())  # type: ignore[arg-type]

    taken = await service.take_back(
        "file-5", CREATORS, actor=Actor.user(admin.id), reopen=False, writer=None
    )

    assert taken is not None
    # The scenes box still says Pell Arden, so she stays; the rest of what FansDB wrote goes.
    assert await _people(db, "file-5") == {"p-pell"}
    assert await _usernames(db, "file-5") == set()
    assert not await _there(db, "people", "p-wren")
    state = await db.fetch_one(
        "SELECT state FROM asset_stash_box_matches WHERE asset_id = 'file-5' AND box_id = ?",
        (CREATORS,),
    )
    assert state is not None and state["state"] == "refused"
    [line] = await _receipts(db)
    assert (line["actor_kind"], json.loads(str(line["payload"]))[TOOK_BACK]) == ("user", BY_HAND)
    # A second refusal of the same answer is nothing: it is not applied any more.
    assert (
        await service.take_back(
            "file-5", CREATORS, actor=Actor.user(admin.id), reopen=False, writer=None
        )
        is None
    )

    queue = TaggerQueue(service, _Access())  # type: ignore[arg-type]
    assert await queue.reverse(admin, str(line["id"]), str(line["payload"]))
    assert await _whole(db) == before


async def test_undoing_an_apply_takes_off_what_it_wrote_and_asks_again(
    db: Database, admin: Viewer
) -> None:
    await _run(
        db,
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
        " VALUES (?, ?, 'scene', ?, 'certain', 'applied', 5, 6)",
        ("file-5", CREATORS, _answer(CREATORS, people=["Ilsa Marrow"])),
    )
    await _run(
        db,
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, 7)",
        ("file-5", "p-ilsa", "stash_box"),
    )
    service = StashBoxService(db, SecretStore(db), _Adapter())  # type: ignore[arg-type]
    queue = TaggerQueue(service, _Access())  # type: ignore[arg-type]
    applied = json.dumps({"matches": [{"asset_id": "file-5", "box_id": CREATORS}]})

    assert await queue.reverse(admin, "receipt-apply", applied)

    assert await _people(db, "file-5") == set()
    state = await db.fetch_one(
        "SELECT state FROM asset_stash_box_matches WHERE asset_id = 'file-5' AND box_id = ?",
        (CREATORS,),
    )
    assert state is not None and state["state"] == "waiting"
    assert await left_by_a_refusal(db) == 0


async def test_every_table_that_points_at_a_person_or_a_username_is_placed(db: Database) -> None:
    """A table a feature adds that points at a person holds her until it is placed, so nothing is
    let go with a shell by omission. Read off this library's own foreign keys."""
    for table, placed in POINTING_AT.items():
        rows = await db.fetch_all(POINTERS_AT, (table,))
        found = {(str(row["tbl"]), str(row["col"])) for row in rows}
        listed = {(other, column) for other, column, _ in placed}
        assert found <= listed, f"points at {table} and is not placed: {sorted(found - listed)}"


def test_the_receipts_are_undone_by_this_pile() -> None:
    assert RECEIPTS == NAME


async def test_a_library_before_the_undo_rows_gains_the_table_and_again_changes_nothing(
    db: Database,
) -> None:
    await db.execute("DROP TABLE undo_rows")

    for _ in range(2):
        async with db.write() as connection:
            await catalog_schema.initialize_catalog(connection, 82)

    assert await db.fetch_all("SELECT * FROM undo_rows", ()) == []


async def test_the_stash_box_step_runs_the_repair_on_a_library_before_it(db: Database) -> None:
    await _refused_file(db)

    async with db.write() as connection:
        await initialize_stash_boxes(connection, 18)

    assert await left_by_a_refusal(db) == 0
    assert len(await _receipts(db)) == 1


async def test_what_a_receipt_kept_goes_when_the_ledger_forgets_the_receipt(db: Database) -> None:
    await _refused_file(db)
    await _repaired(db)
    [line] = await _receipts(db)
    kept = "SELECT COUNT(*) AS n FROM undo_rows WHERE receipt_id = ?"
    before = await db.fetch_one(kept, (str(line["id"]),))
    assert before is not None and int(before["n"]) > 0

    await db.execute("DELETE FROM workbench_decisions WHERE id = ?", (str(line["id"]),))

    after = await db.fetch_one(kept, (str(line["id"]),))
    assert after is not None and int(after["n"]) == 0


def test_what_the_standing_answers_say_is_read_as_the_writer_reads_it() -> None:
    """A Site's name and every username an answer gives, folded; an entry that is not a username,
    or names no Site or no handle, says nothing."""
    from sift.slices.stash_boxes.taken_back import still_said

    said = still_said(
        [
            {
                "people": [" Wren Halloway ", ""],
                "tags": ["Lantern"],
                "site": " Quillmark ",
                "accounts": [
                    "quillfan",
                    {"site": "OnlyFans", "handle": "QuillFan"},
                    {"site": "", "handle": "nosite"},
                    {"site": "Fernhollow", "handle": " "},
                ],
            },
            {"accounts": "not a list"},
        ]
    )

    assert said.people == frozenset({"wren halloway"})
    assert said.tags == frozenset({"lantern"})
    assert said.accounts == frozenset({("onlyfans", "quillfan")})
    assert said.sites == frozenset({"quillmark", "onlyfans"})


async def test_an_undo_reads_past_an_answer_it_cannot_read_back(db: Database) -> None:
    """A receipt from another version may hold an answer in a shape this one does not write: it
    is passed over, and the answers it can read are put back."""
    from sift.slices.stash_boxes.taken_back import ANSWERS, put_back_answers_on

    await _run(
        db,
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
        " VALUES (?, ?, 'scene', ?, 'certain', 'refused', 5, 9)",
        ("file-1", CREATORS, _answer(CREATORS, people=["Wren Halloway"])),
    )
    payload = {ANSWERS: [["file-1", CREATORS, "applied", 6, "refused"], ["too", "short"], "x"]}

    async with db.write() as connection:
        await put_back_answers_on(connection, "no-receipt", payload)

    row = await db.fetch_one(
        "SELECT state, decided_at FROM asset_stash_box_matches WHERE asset_id = 'file-1'"
    )
    assert row is not None and (row["state"], row["decided_at"]) == ("applied", 6)


async def test_an_answer_settled_by_another_press_while_its_fields_came_off_is_left_as_it_is(
    db: Database, admin: Viewer
) -> None:
    """The fields come off through the file's writer before the take-back's own write opens; an
    answer somebody settled in that moment is theirs, and nothing more is taken or recorded."""
    await _run(
        db,
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
        " VALUES (?, ?, 'scene', ?, 'certain', 'applied', 5, 6)",
        ("file-5", CREATORS, _answer(CREATORS, people=["Wren Halloway"])),
    )
    await _run(
        db,
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, 7)",
        ("file-5", "p-wren", "stash_box"),
    )

    class _SettledMeanwhile:
        async def read_as_written(self, values: Mapping[str, object]) -> Mapping[str, object]:
            return values

        async def take_back_fields(
            self, asset_id: str, offered: Mapping[str, object], *, still_said: Any
        ) -> tuple[dict[str, object], list[str]]:
            await db.execute(
                "UPDATE asset_stash_box_matches SET state = 'refused' WHERE asset_id = ?",
                (asset_id,),
            )
            return {}, []

    service = StashBoxService(db, SecretStore(db), _Adapter())  # type: ignore[arg-type]

    taken = await service.take_back(
        "file-5",
        CREATORS,
        actor=Actor.user(admin.id),
        reopen=False,
        writer=_SettledMeanwhile(),  # type: ignore[arg-type]
    )

    assert taken is None
    assert await _people(db, "file-5") == {"p-wren"}
    assert await _receipts(db) == []
