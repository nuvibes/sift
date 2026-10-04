# SPDX-License-Identifier: AGPL-3.0-or-later
"""When a thing was last edited: moved by an edit, never by a look, and read by every wall's order.

The triggers in `kernel.access.edited` are the one place the moment is written, so each table is
held here to the two halves of the rule: a change to what the thing IS moves it, and everything
else (a view, a heart, the stars, a no-op save, the default cover, another thing's deletion) leaves it where
it was. Then each wall is put in "Recently edited" and read back.
"""

from __future__ import annotations

import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository, edited, schema
from sift.kernel.access.edited import ASSET_RECORD_COLUMNS, NOT_AN_EDIT, TRIGGERS, keep_true
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio


async def _moment(database: Database, table: str, row_id: str) -> int | None:
    row = await database.fetch_one(
        # nosemgrep: sift-no-string-built-sql
        f"SELECT edited_at FROM {table} WHERE id = ?",  # noqa: S608
        (row_id,),
    )
    assert row is not None
    return None if row["edited_at"] is None else int(row["edited_at"])


async def _forget(database: Database, table: str, row_id: str) -> None:
    # nosemgrep: sift-no-string-built-sql
    await database.execute(f"UPDATE {table} SET edited_at = NULL WHERE id = ?", (row_id,))  # noqa: S608


async def _file_moment(database: Database, asset_id: str) -> int | None:
    row = await database.fetch_one(
        "SELECT edited_at FROM asset_edits WHERE asset_id = ?", (asset_id,)
    )
    return None if row is None else int(row["edited_at"])


async def test_every_column_of_a_record_is_an_edit_or_says_why_not(
    temp_db: Database, access: Repository
) -> None:
    """A column added to a record table and placed in neither list fails here, on purpose."""
    for record in edited._RECORDS:
        columns = {
            str(row["name"])
            for row in await temp_db.fetch_all(
                # nosemgrep: sift-no-string-built-sql
                f"SELECT name FROM pragma_table_info('{record.table}')"  # noqa: S608
            )
        }
        edits = {*record.columns, *(c for c, _ in record.pointers)}
        if record.covered:
            edits |= {*edited._COVER, *(("cover_track_id",) if record.face else ())}
        placed = edits | set(NOT_AN_EDIT[record.table])
        assert not edits & set(NOT_AN_EDIT[record.table]), record.table
        assert placed == columns, (record.table, sorted(columns ^ placed))


async def test_a_new_library_has_every_trigger(temp_db: Database, access: Repository) -> None:
    rows = await temp_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'edited_%'"
    )
    assert {str(row["name"]) for row in rows} == set(TRIGGERS)


async def test_no_opinion_is_an_edit_the_stars_included(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """The stars are a viewer's opinion like the heart: no table of them moves a thing's moment."""
    triggers = await temp_db.fetch_all(
        "SELECT tbl_name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'edited_%'"
    )
    assert not {str(row["tbl_name"]) for row in triggers} & set(edited.OPINIONS)
    # A song for the stars to be about: the shared library carries none.
    song = "01KZEDITEDS0NG000000000000"
    await temp_db.execute(
        "INSERT INTO songs (id, name, name_sort, created_at) VALUES (?, 'tune', 'tune', 0)", (song,)
    )
    rated = (
        ("person_user_state", "person_id", "people", world.person),
        ("site_user_state", "site_id", "sites", world.site),
        ("tag_user_state", "tag_id", "tags", world.tag),
        ("collection_user_state", "collection_id", "collections", world.collection),
        ("photo_set_user_state", "photo_set_id", "photo_sets", world.photo_set),
        ("song_user_state", "song_id", "songs", song),
    )
    assert {table for table, *_ in rated} | {"asset_user_state"} == set(edited.OPINIONS)
    for table, key, owner, row_id in rated:
        await _forget(temp_db, owner, row_id)
        await temp_db.execute(
            # nosemgrep: sift-no-string-built-sql
            f"INSERT INTO {table} ({key}, user_id, rating, updated_at) VALUES (?, ?, 6, 0)",  # noqa: S608
            (row_id, actors.admin.id),
        )
        # nosemgrep: sift-no-string-built-sql
        await temp_db.execute(f"UPDATE {table} SET rating = 9 WHERE {key} = ?", (row_id,))  # noqa: S608
        assert await _moment(temp_db, owner, row_id) is None, table


async def test_a_record_edit_moves_the_moment_and_nothing_else_does(
    temp_db: Database, world: World
) -> None:
    rows = (
        ("people", world.person),
        ("tags", world.tag),
        ("sites", world.site),
        ("collections", world.collection),
        ("photo_sets", world.photo_set),
        ("usernames", world.username),
    )
    for table, row_id in rows:
        await _forget(temp_db, table, row_id)
        # A save that wrote what was there, and the sort key alone: neither is an edit.
        # nosemgrep: sift-no-string-built-sql
        await temp_db.execute(f"UPDATE {table} SET name = name WHERE id = ?", (row_id,))  # noqa: S608
        # nosemgrep: sift-no-string-built-sql
        await temp_db.execute(f"UPDATE {table} SET name_sort = 'x' WHERE id = ?", (row_id,))  # noqa: S608
        assert await _moment(temp_db, table, row_id) is None, table
        # nosemgrep: sift-no-string-built-sql
        await temp_db.execute(f"UPDATE {table} SET name = 'Renamed' WHERE id = ?", (row_id,))  # noqa: S608
        assert await _moment(temp_db, table, row_id) is not None, table


async def test_a_loop_is_edited_by_its_name_its_bounds_and_its_tags(
    temp_db: Database, world: World
) -> None:
    loop = new_id()
    await temp_db.execute(
        "INSERT INTO loops (id, asset_id, start_ms, end_ms, created_at) VALUES (?, ?, 0, 1000, 0)",
        (loop, world.solo),
    )
    assert await _moment(temp_db, "loops", loop) is None
    await temp_db.execute("UPDATE loops SET end_ms = 2000 WHERE id = ?", (loop,))
    assert await _moment(temp_db, "loops", loop) is not None
    await _forget(temp_db, "loops", loop)
    await temp_db.execute(
        "INSERT INTO loop_tags (loop_id, tag_id, added_at) VALUES (?, ?, 0)", (loop, world.tag)
    )
    assert await _moment(temp_db, "loops", loop) is not None


async def test_a_list_row_is_an_edit_and_an_opinion_is_not(
    temp_db: Database, world: World, actors: Actors
) -> None:
    person = world.person
    await _forget(temp_db, "people", person)
    # The heart, the pin, hiding and the stars are this viewer's opinions, not what she is.
    await temp_db.execute(
        "INSERT INTO person_user_state (person_id, user_id, favorite, pinned, updated_at)"
        " VALUES (?, ?, 1, 1, 0)",
        (person, actors.admin.id),
    )
    assert await _moment(temp_db, "people", person) is None
    await temp_db.execute("UPDATE person_user_state SET rating = 8 WHERE person_id = ?", (person,))
    assert await _moment(temp_db, "people", person) is None

    await temp_db.execute(
        "INSERT INTO people_aliases (id, person_id, alias, alias_sort) VALUES (?, ?, ?, ?)",
        (new_id(), person, "Wren", sort_key("Wren")),
    )
    assert await _moment(temp_db, "people", person) is not None

    await _forget(temp_db, "people", person)
    await temp_db.execute(
        "INSERT INTO person_tags (person_id, tag_id) VALUES (?, ?)", (person, world.tag)
    )
    assert await _moment(temp_db, "people", person) is not None

    # A file arriving under her is the file's edit, never hers.
    await _forget(temp_db, "people", person)
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (world.twin, person)
    )
    assert await _moment(temp_db, "people", person) is None
    assert await _file_moment(temp_db, world.twin) is not None


async def test_another_things_deletion_is_not_an_edit(temp_db: Database, world: World) -> None:
    """A tag deleted takes its rows off a person and a file; that is the tag's act, not theirs."""
    await temp_db.execute(
        "INSERT INTO person_tags (person_id, tag_id) VALUES (?, ?)", (world.person, world.tag)
    )
    await _forget(temp_db, "people", world.person)
    await temp_db.execute("DELETE FROM asset_edits")
    await temp_db.execute("DELETE FROM tags WHERE id = ?", (world.tag,))
    assert await _moment(temp_db, "people", world.person) is None
    assert await _file_moment(temp_db, world.solo) is None

    # And the person deleted leaves the file where it was, while taking her off it by hand is not.
    await temp_db.execute("DELETE FROM people WHERE id = ?", (world.person,))
    assert await _file_moment(temp_db, world.solo) is None


async def test_the_default_cover_is_not_an_edit_and_a_chosen_one_is(
    temp_db: Database, world: World
) -> None:
    # The rule gave her `solo` when the world was built (her first file); here it moves its pick.
    await _forget(temp_db, "people", world.person)
    await temp_db.execute(
        "UPDATE people SET cover_asset_id = ?, cover_by_default = ? WHERE id = ?",
        (world.twin, world.twin, world.person),
    )
    assert await _moment(temp_db, "people", world.person) is None
    await temp_db.execute(
        "UPDATE people SET cover_asset_id = ? WHERE id = ?", (world.loose, world.person)
    )
    assert await _moment(temp_db, "people", world.person) is not None


async def test_a_file_is_edited_by_its_record_and_not_by_a_view(
    temp_db: Database, world: World, actors: Actors
) -> None:
    await temp_db.execute("DELETE FROM asset_edits")
    await temp_db.execute(
        "INSERT INTO asset_user_state (asset_id, user_id, view_count, updated_at)"
        " VALUES (?, ?, 1, 0)",
        (world.loose, actors.admin.id),
    )
    await temp_db.execute(
        "UPDATE asset_user_state SET view_count = 2, favorite = 1, last_viewed_at = 5"
        " WHERE asset_id = ?",
        (world.loose,),
    )
    assert await _file_moment(temp_db, world.loose) is None
    await temp_db.execute(
        "UPDATE asset_user_state SET rating = 6 WHERE asset_id = ?", (world.loose,)
    )
    assert await _file_moment(temp_db, world.loose) is None

    for column in ASSET_RECORD_COLUMNS:
        await temp_db.execute("DELETE FROM asset_edits")
        # nosemgrep: sift-no-string-built-sql
        await temp_db.execute(f"UPDATE assets SET {column} = 'x' WHERE id = ?", (world.loose,))  # noqa: S608
        assert await _file_moment(temp_db, world.loose) is not None, column


async def test_a_trigger_rebuilt_away_is_written_again(
    temp_db: Database, access: Repository
) -> None:
    await temp_db.execute("DROP TRIGGER edited_people")
    async with temp_db.write() as connection:
        await keep_true(connection)
    rows = await temp_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = 'edited_people'"
    )
    assert len(rows) == 1


async def test_the_step_reads_the_first_moments_off_the_ledger_once(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """A library at 76: the newest record edit on the ledger becomes the moment; a rating does not."""

    async def event(verb: str, kind: str, subject: str, at: int, object_kind: str = "") -> None:
        decision = new_id()
        await temp_db.execute(
            "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at, verb,"
            " object_kind) VALUES (?, 'ledger', '', '', '{}', ?, ?, ?)",
            (decision, at, verb, object_kind or None),
        )
        await temp_db.execute(
            "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
            " VALUES (?, ?, ?)",
            (decision, kind, subject),
        )

    for table, row_id in (("people", world.person), ("tags", world.tag), ("sites", world.site)):
        await _forget(temp_db, table, row_id)
    await temp_db.execute("DELETE FROM asset_edits")
    await event("renamed", "person", world.person, 100)
    await event("edited", "person", world.person, 300)
    await event("added", "tag", world.tag, 900)  # an arrival, not an edit
    await event("linked", "asset", world.twin, 400, "tag")
    await event("linked", "asset", world.loose, 450, "username")  # a filing, not the record
    for kind, subject in (("site", world.site), ("asset", world.loose)):
        # The stars are an opinion, like the heart: never a first moment.
        await temp_db.execute(
            "INSERT INTO opinions (id, user_id, subject_kind, subject_id, kind, after, at)"
            " VALUES (?, ?, ?, ?, 'rating', 7, 500)",
            (new_id(), actors.admin.id, kind, subject),
        )

    # A table from before the column: the step gives it one.
    await temp_db.execute("DROP TRIGGER edited_loops")
    await temp_db.execute("ALTER TABLE loops DROP COLUMN edited_at")

    async with temp_db.write() as connection:
        await schema.initialize_catalog(connection, 76)

    columns = await temp_db.fetch_all("SELECT name FROM pragma_table_info('loops')")
    assert "edited_at" in {str(row["name"]) for row in columns}
    assert await _moment(temp_db, "people", world.person) == 300
    assert await _moment(temp_db, "tags", world.tag) is None
    assert await _moment(temp_db, "sites", world.site) is None
    assert await _file_moment(temp_db, world.twin) == 400
    assert await _file_moment(temp_db, world.loose) is None

    # Run again, over moments it already knew: nothing moves.
    await temp_db.execute("UPDATE people SET edited_at = 999 WHERE id = ?", (world.person,))
    async with temp_db.write() as connection:
        await schema.initialize_catalog(connection, 76)
    assert await _moment(temp_db, "people", world.person) == 999


async def _make(database: Database, table: str, name: str, asset_id: str) -> str:
    row_id = new_id()
    if table == "loops":
        await database.execute(
            "INSERT INTO loops (id, asset_id, start_ms, end_ms, name, created_at)"
            " VALUES (?, ?, 0, 1000, ?, 0)",
            (row_id, asset_id, name),
        )
        return row_id
    await database.execute(
        # nosemgrep: sift-no-string-built-sql
        f"INSERT INTO {table} (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",  # noqa: S608
        (row_id, name, sort_key(name)),
    )
    return row_id


async def _edited_at(database: Database, table: str, row_id: str, at: int) -> None:
    # nosemgrep: sift-no-string-built-sql
    await database.execute(f"UPDATE {table} SET edited_at = ? WHERE id = ?", (at, row_id))  # noqa: S608


async def test_every_wall_of_things_orders_by_the_last_edit_then_by_creation(
    temp_db: Database, world: World, access: Repository, actors: Actors
) -> None:
    """Four people made in order (Ada, Bea, Cy, Di); Bea edited last, Cy before her; Ada and Di never.

    So the order is Bea, Cy (the edits, latest first), then Di, Ada (never edited, newest made
    first), and it is neither the name order nor either creation order.
    """
    admin = actors.admin

    async def wall(table: str) -> list[tuple[str, str | None]]:
        """The wall's rows as (id, name), in the order it came back."""
        if table == "people":
            people = await access.suggest_people(admin, sort="edited", limit=50)
            return [(one.id, one.name) for one in people.items]
        if table == "tags":
            tags = await access.list_tags(admin, sort="edited", limit=50)
            return [(one.id, one.name) for one in tags.items]
        if table == "sites":
            sites = await access.list_sites(admin, sort="edited", limit=50)
            return [(one.id, one.name) for one in sites.items]
        if table == "collections":
            shelf = await access.list_collections(admin, sort="edited", limit=50)
            return [(one.id, one.name) for one in shelf.items]
        if table == "photo_sets":
            sets = await access.list_photo_sets(admin, sort="edited", limit=50)
            return [(one.id, one.name) for one in sets.items]
        if table == "usernames":
            usernames = await access.list_usernames(admin, sort="edited", limit=50)
            return [(one.id, one.name) for one in usernames.items]
        loops = await access.list_loops(admin, sort="edited", limit=50)
        return [(one.id, one.name) for one in loops.items]

    for table in ("people", "tags", "sites", "collections", "photo_sets", "usernames", "loops"):
        made = {
            name: await _make(temp_db, table, name, world.solo)
            for name in ("Ada", "Bea", "Cy", "Di")
        }
        await _edited_at(temp_db, table, made["Cy"], 100)
        await _edited_at(temp_db, table, made["Bea"], 200)
        names = [name for row_id, name in await wall(table) if row_id in made.values()]
        assert names == ["Bea", "Cy", "Di", "Ada"], table


async def test_the_file_wall_orders_by_the_last_edit_then_by_arrival(
    temp_db: Database, world: World, actors: Actors, access: Repository
) -> None:
    await temp_db.execute("DELETE FROM asset_edits")
    await temp_db.execute("UPDATE assets SET added_at = 10 WHERE id = ?", (world.solo,))
    await temp_db.execute("UPDATE assets SET added_at = 20 WHERE id = ?", (world.twin,))
    await temp_db.execute("UPDATE assets SET added_at = 30 WHERE id = ?", (world.loose,))
    await temp_db.execute(
        "INSERT INTO asset_edits (asset_id, edited_at) VALUES (?, 500)", (world.solo,)
    )
    page = await access.visible_assets(actors.admin, sort="edited", limit=50)
    assert [one.asset.id for one in page.items] == [world.solo, world.loose, world.twin]


async def test_a_library_with_nothing_recorded_and_no_trigger_tables_is_left_alone(
    temp_db: Database, access: Repository
) -> None:
    """The step before the ledger exists fills nothing; the boot's repair waits for every table."""
    await temp_db.execute("DROP TABLE workbench_decision_subjects")
    async with temp_db.write() as connection:
        assert set((await edited.backfill(connection)).values()) == {0}
    await temp_db.execute("DROP TRIGGER edited_people")
    await temp_db.execute("DROP TABLE asset_edits")
    async with temp_db.write() as connection:
        await keep_true(connection)
    rows = await temp_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = 'edited_people'"
    )
    assert rows == []


async def test_a_files_song_is_an_edit_of_the_file_even_where_its_music_field_stays(
    temp_db: Database, world: World
) -> None:
    """Put on a song, moved to another of the same name (the Music field reads the same), and taken
    off: each moves the file's moment, as its people and tags do."""
    first, second = new_id(), new_id()
    for song_id in (first, second):
        await temp_db.execute(
            "INSERT INTO songs (id, name, name_sort, created_at) VALUES (?, 'Blue', 'blue', 0)",
            (song_id,),
        )
    await temp_db.execute("DELETE FROM asset_edits")
    await temp_db.execute(
        "INSERT INTO song_files (asset_id, song_id, added_at) VALUES (?, ?, 0)",
        (world.loose, first),
    )
    assert await _file_moment(temp_db, world.loose) is not None
    await temp_db.execute("DELETE FROM asset_edits")
    await temp_db.execute(
        "UPDATE song_files SET song_id = ? WHERE asset_id = ?", (second, world.loose)
    )
    assert await _file_moment(temp_db, world.loose) is not None
    await temp_db.execute("DELETE FROM asset_edits")
    await temp_db.execute("DELETE FROM song_files WHERE asset_id = ?", (world.loose,))
    assert await _file_moment(temp_db, world.loose) is not None
