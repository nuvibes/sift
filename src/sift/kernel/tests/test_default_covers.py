# SPDX-License-Identifier: AGPL-3.0-or-later
"""An entity with no cover wears its first file's picture, whatever path left it empty.

The rule is kept by triggers on the rows a filing writes (`kernel/access/default_covers.py`), so
each test here writes the rows the way any writer would and reads the cover back. The one path the
triggers leave to their callers, a merge, is asked through `assign_if_empty`; the slice's merge test
holds the merge to calling it. A tag and a Site are left out of the rule, and catalog step 76 gave
back what it had given them: the last three tests.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

# The ledger's table, which step 76 writes its History lines into.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Role, default_covers, schema
from sift.kernel.access.default_covers import (
    COVER_TAKEN_AWAY,
    assign_if_empty,
    fill_every_empty,
    keep_true,
    standing,
    take_back_tags_and_sites,
)
from sift.kernel.covers import cover_payload
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.testing.fixtures import World, create_user
from sift.testing.logs import uncached_log

pytestmark = pytest.mark.anyio

_COVER_OF = {
    "person": "SELECT cover_asset_id FROM people WHERE id = ?",
    "site": "SELECT cover_asset_id FROM sites WHERE id = ?",
    "tag": "SELECT cover_asset_id FROM tags WHERE id = ?",
    "collection": "SELECT cover_asset_id FROM collections WHERE id = ?",
    "photo_set": "SELECT cover_asset_id FROM photo_sets WHERE id = ?",
}


async def _cover(database: Database, kind: str, entity_id: str) -> str | None:
    row = await database.fetch_one(_COVER_OF[kind], (entity_id,))
    assert row is not None
    return None if row["cover_asset_id"] is None else str(row["cover_asset_id"])


async def _person(database: Database, name: str) -> str:
    person_id = new_id()
    await database.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person_id, name)
    )
    return person_id


async def _file(database: Database, asset_id: str, person_id: str, at: int) -> None:
    await database.execute(
        "INSERT INTO asset_people (asset_id, person_id, decided_at) VALUES (?, ?, ?)",
        (asset_id, person_id, at),
    )


async def _clear_by_hand(database: Database, person_id: str) -> None:
    """What the people slice's cover statement writes for a PUT naming no picture."""
    await database.execute(
        "UPDATE people SET cover_asset_id = NULL, cover_upload_id = NULL, cover_at_ms = NULL,"
        " cover_frame = NULL, cover_cleared_at = 1, cover_by_default = NULL WHERE id = ?",
        (person_id,),
    )


async def test_a_filing_gives_every_kind_its_first_file(temp_db: Database, world: World) -> None:
    """The world files `solo` under a person, a tag and a Site, with no cover written by anybody."""
    assert await _cover(temp_db, "person", world.person) == world.solo
    assert (await standing(temp_db, "person", world.person)).by_default

    # A collection and a Photo Set made empty, then given two pictures at once: the first by the
    # arrangement, whatever the ids.
    shelf, album = new_id(), new_id()
    await temp_db.execute(
        "INSERT INTO collections (id, name, created_at) VALUES (?, 'shelf', 0)", (shelf,)
    )
    await temp_db.execute(
        "INSERT INTO photo_sets (id, name, created_at) VALUES (?, 'album', 0)", (album,)
    )
    async with temp_db.write() as connection:
        for position, asset_id in enumerate((world.twin, world.loose)):
            await connection.execute(
                "INSERT INTO collection_items (collection_id, asset_id, position, added_at)"
                " VALUES (?, ?, ?, 5)",
                (shelf, asset_id, position),
            )
            await connection.execute(
                "INSERT INTO photo_set_items (photo_set_id, asset_id, position, added_at)"
                " VALUES (?, ?, ?, 5)",
                (album, asset_id, position),
            )
    assert await _cover(temp_db, "collection", shelf) == world.twin
    assert await _cover(temp_db, "photo_set", album) == world.twin


async def test_a_picture_somebody_chose_is_never_replaced(temp_db: Database, world: World) -> None:
    person = await _person(temp_db, "Wren Halloway")
    await temp_db.execute("UPDATE people SET cover_upload_id = 'an-upload' WHERE id = ?", (person,))
    await _file(temp_db, world.solo, person, 1)
    assert await _cover(temp_db, "person", person) is None


async def test_only_a_file_there_to_read_is_a_cover_and_it_is_one_when_it_comes_back(
    temp_db: Database, world: World
) -> None:
    person = await _person(temp_db, "Esme Wrenfield")
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (world.loose,)
    )
    await _file(temp_db, world.loose, person, 1)
    assert await _cover(temp_db, "person", person) is None

    await temp_db.execute(
        "UPDATE asset_locations SET status = 'present' WHERE asset_id = ?", (world.loose,)
    )
    assert await _cover(temp_db, "person", person) == world.loose


async def test_a_cover_whose_file_leaves_the_library_is_the_next_first_file(
    temp_db: Database, world: World
) -> None:
    person = await _person(temp_db, "Nerith Reyd")
    await _file(temp_db, world.loose, person, 1)
    await _file(temp_db, world.twin, person, 2)
    await _file(temp_db, world.solo, person, 3)
    assert await _cover(temp_db, "person", person) == world.loose
    # One somebody chose goes the same way when its file does.
    await temp_db.execute(
        "UPDATE people SET cover_asset_id = ?, cover_by_default = NULL WHERE id = ?",
        (world.solo, person),
    )

    await temp_db.execute("DELETE FROM assets WHERE id = ?", (world.solo,))
    assert await _cover(temp_db, "person", person) == world.loose

    await temp_db.execute("DELETE FROM assets WHERE id = ?", (world.loose,))
    assert await _cover(temp_db, "person", person) == world.twin


async def test_a_default_whose_file_is_taken_off_moves_on_and_a_chosen_one_stays(
    temp_db: Database, world: World
) -> None:
    """A filing undone takes the rule's pick with it: the next first file, then the letter. A face
    named and taken back must not leave a person with no files wearing a file that is not theirs."""
    person = await _person(temp_db, "Tamsin Vellory")
    await _file(temp_db, world.loose, person, 1)
    await _file(temp_db, world.twin, person, 2)
    assert await _cover(temp_db, "person", person) == world.loose

    await temp_db.execute(
        "DELETE FROM asset_people WHERE person_id = ? AND asset_id = ?", (person, world.loose)
    )
    assert await _cover(temp_db, "person", person) == world.twin
    assert (await standing(temp_db, "person", person)).by_default

    await temp_db.execute(
        "DELETE FROM asset_people WHERE person_id = ? AND asset_id = ?", (person, world.twin)
    )
    assert await _cover(temp_db, "person", person) is None

    # A picture somebody chose stays where they put it when its file is taken off.
    await _file(temp_db, world.loose, person, 3)
    await _file(temp_db, world.twin, person, 4)
    await temp_db.execute(
        "UPDATE people SET cover_asset_id = ?, cover_by_default = NULL WHERE id = ?",
        (world.twin, person),
    )
    await temp_db.execute(
        "DELETE FROM asset_people WHERE person_id = ? AND asset_id = ?", (person, world.twin)
    )
    assert await _cover(temp_db, "person", person) == world.twin


async def test_a_cover_cleared_by_hand_stays_empty_until_one_is_chosen(
    temp_db: Database, world: World
) -> None:
    person = await _person(temp_db, "Wren Kastellan")
    await _file(temp_db, world.loose, person, 1)
    await _clear_by_hand(temp_db, person)

    await _file(temp_db, world.twin, person, 2)
    async with temp_db.write() as connection:
        assert not await assign_if_empty(connection, "person", person)
        await fill_every_empty(connection)
    assert await _cover(temp_db, "person", person) is None
    assert (await standing(temp_db, "person", person)).cleared

    # Chosen again, the mark goes; and when that file leaves, the rule is back.
    await temp_db.execute(
        "UPDATE people SET cover_asset_id = ?, cover_cleared_at = NULL WHERE id = ?",
        (world.twin, person),
    )
    await temp_db.execute("DELETE FROM assets WHERE id = ?", (world.twin,))
    assert await _cover(temp_db, "person", person) == world.loose


async def test_a_merge_asks_for_the_survivor(temp_db: Database, world: World) -> None:
    """A move is not a filing: the survivor is asked for after the carry, by its caller."""
    kept, going = await _person(temp_db, "Wrenna Sable"), await _person(temp_db, "Pell Quorley")
    await _file(temp_db, world.loose, going, 1)
    await temp_db.execute(
        "UPDATE asset_people SET person_id = ? WHERE person_id = ?", (kept, going)
    )
    assert await _cover(temp_db, "person", kept) is None
    async with temp_db.write() as connection:
        assert await assign_if_empty(connection, "person", kept)
        assert not await assign_if_empty(connection, "person", kept)
    assert await _cover(temp_db, "person", kept) == world.loose


async def test_the_catalog_step_fills_every_gap_once_and_counts_it(
    temp_db: Database, world: World
) -> None:
    # A library from before the rule: no triggers, and people and tags filed with no cover.
    async with temp_db.write() as connection:
        for statement in default_covers.drop_triggers():
            await connection.execute(statement)
    person = await _person(temp_db, "Sibyl Marrow")
    await _file(temp_db, world.twin, person, 1)
    assert await _cover(temp_db, "person", person) is None

    async with temp_db.write() as connection:
        await schema.initialize_catalog(connection, 74)
    assert await _cover(temp_db, "person", person) == world.twin
    # Filed under both, and wearing neither's picture: the rule leaves a tag and a Site.
    assert await _cover(temp_db, "tag", world.tag) is None
    assert await _cover(temp_db, "site", world.site) is None

    async with temp_db.write() as connection:
        assert set((await fill_every_empty(connection)).values()) == {0}


async def test_the_step_gives_every_face_sift_made_a_cover_back_to_the_whole_first_picture(
    temp_db: Database, world: World
) -> None:
    """A library at catalog 78: a face Sift cut out of a file wearing as somebody's cover. Step 79
    takes every such face off, and the rule gives the whole first picture filed under them, or the
    letter where none is there to read. An upload and a picture somebody chose stay."""
    faced = await _person(temp_db, "Oriel Pasque")
    await _file(temp_db, world.loose, faced, 1)
    await _file(temp_db, world.twin, faced, 2)
    alone = await _person(temp_db, "Tobin Querrel")
    uploaded = await _person(temp_db, "Ysolde Marrick")
    chosen = await _person(temp_db, "Cadell Brisk")
    await _file(temp_db, world.loose, chosen, 1)
    await _file(temp_db, world.twin, chosen, 2)
    # What the faces feature wrote: the face's file and the face, no default, no moment.
    for person in (faced, alone):
        await temp_db.execute(
            "UPDATE people SET cover_asset_id = ?, cover_track_id = 'a-face',"
            " cover_by_default = NULL WHERE id = ?",
            (world.twin, person),
        )
    await temp_db.execute(
        "UPDATE people SET cover_asset_id = NULL, cover_upload_id = 'an-upload',"
        " cover_by_default = NULL WHERE id = ?",
        (uploaded,),
    )
    await temp_db.execute(
        "UPDATE people SET cover_asset_id = ?, cover_by_default = NULL WHERE id = ?",
        (world.twin, chosen),
    )

    async with temp_db.write() as connection:
        await schema.initialize_catalog(connection, 78)

    row = await temp_db.fetch_one(
        "SELECT cover_asset_id, cover_track_id, cover_by_default FROM people WHERE id = ?",
        (faced,),
    )
    assert row is not None
    assert (row["cover_asset_id"], row["cover_track_id"]) == (world.loose, None)
    assert (await standing(temp_db, "person", faced)).by_default
    assert await _cover(temp_db, "person", alone) is None
    upload = await temp_db.fetch_one("SELECT cover_upload_id FROM people WHERE id = ?", (uploaded,))
    assert upload is not None and upload["cover_upload_id"] == "an-upload"
    assert await _cover(temp_db, "person", chosen) == world.twin

    # Said once: a second run finds no face to give back.
    async with temp_db.write() as connection:
        assert await default_covers.faces_back_to_the_rule(connection) == {
            "faces": 0,
            "pictured": 0,
        }


async def test_the_step_counts_the_faces_it_gave_back_and_the_people_it_pictured(
    temp_db: Database, world: World
) -> None:
    faced, alone = await _person(temp_db, "Oriel Vantry"), await _person(temp_db, "Tobin Aske")
    await _file(temp_db, world.loose, faced, 1)
    for person in (faced, alone):
        await temp_db.execute(
            "UPDATE people SET cover_asset_id = ?, cover_track_id = 'a-face',"
            " cover_by_default = NULL WHERE id = ?",
            (world.twin, person),
        )
    async with temp_db.write() as connection:
        assert await default_covers.faces_back_to_the_rule(connection) == {
            "faces": 2,
            "pictured": 1,
        }


async def test_a_trigger_taken_by_a_rebuild_is_put_back_at_boot_and_the_gap_filled(
    temp_db: Database, world: World
) -> None:
    await temp_db.execute("DROP TRIGGER default_cover_person_filed")
    person = await _person(temp_db, "Esme Wrenfield")
    await _file(temp_db, world.loose, person, 1)
    assert await _cover(temp_db, "person", person) is None

    async with temp_db.write() as connection:
        await keep_true(connection)
    assert await _cover(temp_db, "person", person) == world.loose
    another = await _person(temp_db, "Wren Aldabry")
    await _file(temp_db, world.twin, another, 1)
    assert await _cover(temp_db, "person", another) == world.twin


async def test_a_trigger_this_build_does_not_know_is_reported_and_left(
    temp_db: Database, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trigger under the rule's name that no kind of this build wants, left by a newer build or a
    hand, is named in the log for whoever reads it, and nothing of this build's is torn down."""
    _ = world
    await temp_db.execute(
        "CREATE TRIGGER default_cover_stray AFTER UPDATE OF name ON tags BEGIN SELECT 1; END"
    )
    uncached_log(monkeypatch, default_covers)

    with capture_logs() as logs:
        async with temp_db.write() as connection:
            await keep_true(connection)

    said = [one for one in logs if one["event"] == "covers.default.unknown_triggers"]
    assert [one["names"] for one in said] == [["default_cover_stray"]]
    assert not [one for one in logs if one["event"] == "covers.default.triggers_repaired"]
    person = await _person(temp_db, "Wren Ashdown")
    await _file(temp_db, world.loose, person, 1)
    assert await _cover(temp_db, "person", person) == world.loose


async def test_a_catalog_with_no_files_under_it_is_left_without_triggers(
    temp_db: Database,
) -> None:
    """The rule's triggers sit on the files' tables; a catalog brought up with none is not given
    triggers that would name tables which are not there."""
    await temp_db.execute("CREATE TABLE people (id TEXT PRIMARY KEY, cover_asset_id TEXT)")

    async with temp_db.write() as connection:
        await keep_true(connection)

    triggers = await temp_db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    assert triggers == []


async def test_a_kind_that_wears_no_cover_stands_nowhere(temp_db: Database, world: World) -> None:
    assert await standing(temp_db, "username", world.username) == default_covers.Standing()
    assert await standing(temp_db, "person", "nobody") == default_covers.Standing()


# --- a tag and a Site: named things the rule leaves alone ---------------------------------------


async def _site(database: Database, name: str, parent: str | None = None) -> str:
    site_id = new_id()
    await database.execute(
        "INSERT INTO sites (id, name, parent_id) VALUES (?, ?, ?)", (site_id, name, parent)
    )
    return site_id


async def _tag(database: Database, name: str) -> str:
    tag_id = new_id()
    await database.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)", (tag_id, name)
    )
    return tag_id


async def test_a_tag_and_a_site_a_network_too_never_take_a_files_picture(
    temp_db: Database, world: World
) -> None:
    """Filed, filed again, a copy come back, asked on purpose: a tag and a Site stay their letter or
    their icon. The world files `solo` under its tag and its Site before anything here."""
    network = await _site(temp_db, "a network")
    label = await _site(temp_db, "a label within", parent=network)
    handle = new_id()
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, 'x', 0)",
        (handle, label),
    )
    await temp_db.execute(
        "INSERT INTO asset_usernames (asset_id, username_id, decided_at) VALUES (?, ?, 1)",
        (world.loose, handle),
    )
    await temp_db.execute(
        "INSERT INTO asset_tags (asset_id, tag_id, decided_at) VALUES (?, ?, 1)",
        (world.loose, world.tag),
    )
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (world.twin,)
    )
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'present' WHERE asset_id = ?", (world.twin,)
    )
    async with temp_db.write() as connection:
        assert set(await fill_every_empty(connection)) == {"person", "collection", "photo_set"}
        with pytest.raises(KeyError):
            await assign_if_empty(connection, "site", label)
    for kind, entity in (
        ("tag", world.tag),
        ("site", world.site),
        ("site", label),
        ("site", network),
    ):
        assert await _cover(temp_db, kind, entity) is None, (kind, entity)
    assert default_covers.KINDS == ("person", "collection", "photo_set")
    assert not [name for name in default_covers.triggers() if "_site_" in name or "_tag_" in name]


async def test_the_step_gives_back_what_the_rule_gave_and_never_what_was_chosen(
    temp_db: Database, world: World
) -> None:
    """A library at catalog 75: tags and Sites wearing the rule's pick, others wearing a picture
    somebody chose, and the old triggers still on them. Step 76 takes the triggers off and gives
    back exactly the picks, and says so on History in lines of at most eight."""
    tags = [world.tag, *[await _tag(temp_db, f"tag {n}") for n in range(9)]]
    for tag_id in tags:
        await temp_db.execute(
            "UPDATE tags SET cover_asset_id = ?, cover_by_default = ? WHERE id = ?",
            (world.solo, world.solo, tag_id),
        )
    network = await _site(temp_db, "a network")
    for site_id in (world.site, network):
        await temp_db.execute(
            "UPDATE sites SET cover_asset_id = ?, cover_by_default = ? WHERE id = ?",
            (world.loose, world.loose, site_id),
        )
    # Chosen: a file with no note of a pick, the rule's own file with a moment somebody set, and a
    # stash-box's picture over a pick that has since moved away.
    chosen_file, chosen_moment = await _tag(temp_db, "chosen"), await _tag(temp_db, "moment")
    await temp_db.execute(
        "UPDATE tags SET cover_asset_id = ? WHERE id = ?", (world.twin, chosen_file)
    )
    await temp_db.execute(
        "UPDATE tags SET cover_asset_id = ?, cover_by_default = ?, cover_at_ms = 1500 WHERE id = ?",
        (world.solo, world.solo, chosen_moment),
    )
    boxed = await _site(temp_db, "a Site with a stash-box picture")
    await temp_db.execute(
        "UPDATE sites SET cover_upload_id = 'an-upload', cover_by_default = ? WHERE id = ?",
        (world.loose, boxed),
    )
    # One of the old triggers, which would put a pick straight back if it outlived the step.
    await temp_db.execute(
        "CREATE TRIGGER default_cover_tag_lost AFTER UPDATE OF cover_asset_id ON tags"
        " WHEN NEW.cover_asset_id IS NULL"
        " BEGIN UPDATE tags SET cover_asset_id = OLD.cover_asset_id WHERE id = NEW.id; END"
    )

    async with temp_db.write() as connection:
        await schema.initialize_catalog(connection, 75)

    for tag_id in tags:
        assert await _cover(temp_db, "tag", tag_id) is None
    for site_id in (world.site, network):
        assert await _cover(temp_db, "site", site_id) is None
    assert await _cover(temp_db, "tag", chosen_file) == world.twin
    moment = await temp_db.fetch_one(
        "SELECT cover_asset_id, cover_at_ms FROM tags WHERE id = ?", (chosen_moment,)
    )
    assert moment is not None and tuple(moment) == (world.solo, 1500)
    kept = await temp_db.fetch_one(
        "SELECT cover_upload_id, cover_by_default FROM sites WHERE id = ?", (boxed,)
    )
    assert kept is not None and tuple(kept) == ("an-upload", None)
    trigger = await temp_db.fetch_one(
        "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = 'default_cover_tag_lost'"
    )
    assert trigger is None

    events = await temp_db.fetch_all(
        "SELECT d.id, d.verb, d.actor_kind, d.payload, count(s.subject_id) AS named,"
        " min(s.kind) AS kind FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.actor_id = 'update' GROUP BY d.id ORDER BY d.id"
    )
    said = sorted((row["kind"], row["named"]) for row in events)
    assert said == [("site", 2), ("tag", 2), ("tag", 8)]
    assert {(row["verb"], row["actor_kind"], row["payload"]) for row in events} == {
        ("edited", "sift", COVER_TAKEN_AWAY)
    }

    # Once: run again, nothing is taken and nothing more is said.
    async with temp_db.write() as connection:
        assert await take_back_tags_and_sites(connection) == {"tag": 0, "site": 0}
    again = await temp_db.fetch_one(
        "SELECT count(*) FROM workbench_decisions WHERE actor_id = 'update'"
    )
    assert again is not None and again[0] == 3


async def test_the_step_gives_back_on_a_library_with_no_ledger_and_says_it_in_the_log(
    temp_db: Database, world: World
) -> None:
    """Step 76 run on a library whose ledger's table is not there yet: the picks still go back,
    and the counts it returns are what the log says, since there is no History to write them on."""
    await temp_db.execute(
        "UPDATE tags SET cover_asset_id = ?, cover_by_default = ? WHERE id = ?",
        (world.solo, world.solo, world.tag),
    )
    await temp_db.execute("DROP TABLE workbench_decision_subjects")
    await temp_db.execute("DROP TABLE workbench_decisions")

    async with temp_db.write() as connection:
        taken = await take_back_tags_and_sites(connection)

    assert taken == {"tag": 1, "site": 0}
    assert await _cover(temp_db, "tag", world.tag) is None


def test_a_cover_taken_away_says_what_every_cover_writer_says() -> None:
    assert cover_payload(None, None, None) == COVER_TAKEN_AWAY


# --- a file in Hidden ----------------------------------------------------------------------------


async def _hide(database: Database, asset_id: str) -> None:
    """An admin puts the file in Hidden, as the file's own Hide writes it."""
    admin = await create_user(database, Role.ADMIN)
    await database.execute(
        "INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, 1, 1, 1)",
        (asset_id, admin.id),
    )
    row = await database.fetch_one(
        "SELECT concealed FROM viewer_assets WHERE user_id = ? AND asset_id = ?",
        (admin.id, asset_id),
    )
    assert row is not None and row["concealed"] == 1


async def test_a_file_in_hidden_is_a_cover_only_where_every_file_is(
    temp_db: Database, world: World
) -> None:
    """Filed first or not, a file in Hidden gives way to one nobody hides; a person whose every
    file is in Hidden still wears the first of them."""
    await _hide(temp_db, world.loose)
    person = await _person(temp_db, "Ysolde Farrow")
    await _file(temp_db, world.loose, person, 1)
    await _file(temp_db, world.twin, person, 2)
    assert await _cover(temp_db, "person", person) == world.twin
    assert (await standing(temp_db, "person", person)).by_default

    alone = await _person(temp_db, "Tamsin Orrell")
    await _file(temp_db, world.loose, alone, 1)
    assert await _cover(temp_db, "person", alone) == world.loose


async def test_a_cover_put_in_hidden_later_gives_way_and_one_shown_again_takes_its_turn(
    temp_db: Database, world: World
) -> None:
    """The rule's pick follows Hidden as it moves: a cover hidden after it was picked goes to a
    file nobody hides, and where every file was hidden, the first one shown again is the cover."""
    person = await _person(temp_db, "Ysolde Farrow")
    await _file(temp_db, world.loose, person, 1)
    await _file(temp_db, world.twin, person, 2)
    assert await _cover(temp_db, "person", person) == world.loose

    await _hide(temp_db, world.loose)
    assert await _cover(temp_db, "person", person) == world.twin

    await _hide(temp_db, world.twin)
    assert await _cover(temp_db, "person", person) == world.loose, "every file hidden: the first"
    await temp_db.execute(
        "UPDATE asset_user_state SET hidden = 0 WHERE asset_id = ?", (world.twin,)
    )
    assert await _cover(temp_db, "person", person) == world.twin


async def test_the_step_picks_again_a_cover_in_hidden_where_a_file_nobody_hides_is_filed(
    temp_db: Database, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A library at catalog 88: the rule picked the first file, which is in Hidden. Step 89 moves
    the pick to the file nobody hides, leaves a person whose every file is hidden and a cover
    somebody chose where they are, logs how many, and a second run moves nothing."""
    async with temp_db.write() as connection:
        for statement in default_covers.drop_triggers():
            await connection.execute(statement)
    picked, every, chosen = (
        await _person(temp_db, "Ysolde Farrow"),
        await _person(temp_db, "Tamsin Orrell"),
        await _person(temp_db, "Cadell Brisk"),
    )
    for person in (picked, chosen):
        await _file(temp_db, world.loose, person, 1)
        await _file(temp_db, world.twin, person, 2)
    await _file(temp_db, world.loose, every, 1)
    for person in (picked, every):
        await temp_db.execute(
            "UPDATE people SET cover_asset_id = ?, cover_by_default = ? WHERE id = ?",
            (world.loose, world.loose, person),
        )
    await temp_db.execute(
        "UPDATE people SET cover_asset_id = ?, cover_by_default = NULL WHERE id = ?",
        (world.loose, chosen),
    )
    await _hide(temp_db, world.loose)
    uncached_log(monkeypatch, default_covers)

    with capture_logs() as logs:
        async with temp_db.write() as connection:
            await schema.initialize_catalog(connection, 88)

    assert await _cover(temp_db, "person", picked) == world.twin
    assert (await standing(temp_db, "person", picked)).by_default
    assert await _cover(temp_db, "person", every) == world.loose
    assert await _cover(temp_db, "person", chosen) == world.loose
    (said,) = [one for one in logs if one["event"] == "covers.default.out_of_hidden"]
    assert said["person"] == 1
    async with temp_db.write() as connection:
        assert set((await default_covers.out_of_hidden(connection)).values()) == {0}

    # A catalog made before the stored verdict exists has nothing to read: no cover moves.
    async def absent(_connection: object) -> bool:
        return False

    monkeypatch.setattr(default_covers, "_reads_hidden", absent)
    async with temp_db.write() as connection:
        assert set((await default_covers.out_of_hidden(connection)).values()) == {0}
