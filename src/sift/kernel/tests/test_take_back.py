# SPDX-License-Identifier: AGPL-3.0-or-later
"""The catalog's take-back doors, held on a kernel library.

What one source filed on a file is read with every name it goes by, taken off only while it still
carries that source, and told to whoever draws it. A row a source made and nothing else holds is a
shell: removed with what its box brought, and put back whole by the Undo, which writes nothing
over a row somebody wrote since and nothing whose parent has gone. What an Undo puts back is kept
beside its receipt and goes with it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

# Imported for their side effect: the tables a stash-box, the faces and the downloads keep, which
# hold or come with the rows these doors take back.
import sift.slices.download.schema
import sift.slices.faces.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import changes
from sift.kernel.access import Role
from sift.kernel.access.catalog import take_back
from sift.kernel.access.catalog.take_back import (
    Filed,
    StillSaid,
    filed_by_on,
    filed_rows,
    forget_kept_on,
    holds_nothing_on,
    keep_for_undo_on,
    kept_for_undo_on,
    put_back_on,
    remove_shell_on,
    take_off_filed_on,
)
from sift.kernel.changes import About, ChangeBus
from sift.kernel.db import Database
from sift.testing.fixtures import World, create_user

pytestmark = pytest.mark.anyio

SOURCE = "stash_box"


@pytest.fixture
async def told() -> AsyncIterator[ChangeBus]:
    """A bus the doors are heard on, put back afterwards so no later test collects them."""
    bus = ChangeBus()
    changes.listens(bus)
    yield bus
    changes.listens(None)


async def _run(db: Database, sql: str, *rows: tuple[object, ...]) -> None:
    async with db.write() as connection:
        for row in rows or ((),):
            await connection.execute(sql, row)


async def _box_person(db: Database, person_id: str, name: str) -> None:
    await _run(
        db,
        "INSERT INTO people (id, name, name_sort, created_at, created_by_kind)"
        " VALUES (?, ?, ?, 1, 'box')",
        (person_id, name, name.casefold()),
    )


async def _box_site(db: Database, site_id: str, name: str) -> None:
    await _run(
        db,
        "INSERT INTO sites (id, name, name_sort, created_at, created_by_kind) VALUES (?, ?, ?, 1, 'box')",
        (site_id, name, name.casefold()),
    )


async def _box_tag(db: Database, tag_id: str, name: str) -> None:
    await _run(
        db,
        "INSERT INTO tags (id, name, name_sort, created_at, created_by_kind) VALUES (?, ?, ?, 1, 'box')",
        (tag_id, name, name.casefold()),
    )


async def _username(db: Database, username_id: str, site_id: str | None, name: str) -> None:
    await _run(
        db,
        "INSERT INTO usernames (id, site_id, name, name_sort, created_at) VALUES (?, ?, ?, ?, 1)",
        (username_id, site_id, name, name.casefold()),
    )


async def _holds_nothing(db: Database, kind: str, row_id: str) -> bool:
    async with db.write() as connection:
        return await holds_nothing_on(connection, kind, row_id)


#: Every table a shell's removal touches here, each read whole and in one order.
_WHOLE = {
    table: f"SELECT * FROM {table} ORDER BY 1"  # noqa: S608 (the names written out just here)
    for table in (
        "people",
        "people_aliases",
        "people_links",
        "face_references",
        "sites",
        "usernames",
        "site_art",
    )
}


async def _whole(db: Database) -> list[list[dict[str, object]]]:
    return [[dict(row) for row in await db.fetch_all(statement)] for statement in _WHOLE.values()]


# --- what a standing answer still says --------------------------------------------------------


def test_a_standing_answer_keeps_a_row_it_names_by_any_of_its_names() -> None:
    person = Filed("person", "a", "p", frozenset({"wren halloway", "wren h"}))
    tag = Filed("tag", "a", "t", frozenset({"outdoor", "outside"}))
    handle = Filed(
        "username", "a", "u", frozenset({"wrenh"}), sites=frozenset({"storefront", "store"})
    )
    nameless = Filed("username", "a", "n", frozenset(), sites=frozenset({"storefront", "store"}))

    said = StillSaid(
        people=frozenset({"wren h"}),
        tags=frozenset({"outside"}),
        accounts=frozenset({("store", "wrenh")}),
        sites=frozenset({"store"}),
    )
    assert said.says(person) and said.says(tag) and said.says(handle) and said.says(nameless)

    # The same names under the other kinds, the handle on another Site, and the Site unnamed: none.
    elsewhere = StillSaid(
        people=frozenset({"outside"}),
        tags=frozenset({"wren h"}),
        accounts=frozenset({("elsewhere", "wrenh"), ("store", "someone")}),
        sites=frozenset({"elsewhere"}),
    )
    assert not any(elsewhere.says(row) for row in (person, tag, handle, nameless))


# --- reading and taking off what a source filed -----------------------------------------------


async def _filed_by_the_box(temp_db: Database, world: World) -> None:
    """On `loose`: a person, a tag and a handle the box filed, a Site's nameless row it filed, a
    handle on no Site, and the world's own rows, which nobody's source wrote."""
    await _box_person(temp_db, "p-wren", "Wren Halloway")
    await _run(
        temp_db,
        "INSERT INTO people_aliases (id, person_id, alias) VALUES (?, ?, ?)",
        ("al-1", "p-wren", " Wren H "),
    )
    await _box_tag(temp_db, "t-out", "Outdoor")
    await _run(
        temp_db,
        "INSERT INTO tag_aliases (id, tag_id, alias) VALUES (?, ?, ?)",
        ("al-2", "t-out", "Outside"),
    )
    await _box_tag(temp_db, "t-sun", "Sunlit")
    await _box_site(temp_db, "s-store", "Storefront")
    await _run(
        temp_db,
        "INSERT INTO site_aliases (id, site_id, alias) VALUES (?, ?, ?)",
        ("al-3", "s-store", "Store"),
    )
    await _username(temp_db, "u-wren", "s-store", "WrenH")
    await _username(temp_db, "u-blank", "s-store", "")
    await _username(temp_db, "u-adrift", None, "adrift")
    await _run(
        temp_db,
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (world.loose, "p-wren", SOURCE, 5),
        (world.loose, world.person, None, 6),
    )
    await _run(
        temp_db,
        "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (world.loose, "t-out", SOURCE, None),
        (world.loose, "t-sun", SOURCE, None),
    )
    await _run(
        temp_db,
        "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at, post_id)"
        " VALUES (?, ?, ?, ?, ?)",
        (world.loose, "u-wren", SOURCE, 7, "post-1"),
        (world.loose, "u-blank", SOURCE, None, None),
        (world.loose, "u-adrift", SOURCE, None, None),
    )


async def test_what_a_source_filed_is_read_with_every_name_it_goes_by(
    temp_db: Database, world: World
) -> None:
    await _filed_by_the_box(temp_db, world)

    async with temp_db.write() as connection:
        filed = await filed_by_on(connection, world.loose, SOURCE)
        by_nobody = await filed_by_on(connection, world.loose, "swap")

    assert by_nobody == []
    assert filed == [
        Filed(
            "person",
            world.loose,
            "p-wren",
            frozenset({"wren halloway", "wren h"}),
            source=SOURCE,
            decided_at=5,
        ),
        Filed("tag", world.loose, "t-out", frozenset({"outdoor", "outside"}), source=SOURCE),
        Filed("tag", world.loose, "t-sun", frozenset({"sunlit"}), source=SOURCE),
        Filed("username", world.loose, "u-adrift", frozenset({"adrift"}), source=SOURCE),
        Filed(
            "username",
            world.loose,
            "u-blank",
            frozenset(),
            frozenset({"storefront", "store"}),
            "s-store",
            SOURCE,
        ),
        Filed(
            "username",
            world.loose,
            "u-wren",
            frozenset({"wrenh"}),
            frozenset({"storefront", "store"}),
            "s-store",
            SOURCE,
            7,
            "post-1",
        ),
    ]
    # As an Undo keeps them: each join row whole, a post only where a username carries one.
    assert filed_rows(filed[:1] + filed[-1:]) == [
        {
            "table": "asset_people",
            "row": {
                "asset_id": world.loose,
                "person_id": "p-wren",
                "source": SOURCE,
                "decided_at": 5,
                "box_id": None,
            },
        },
        {
            "table": "asset_usernames",
            "row": {
                "asset_id": world.loose,
                "username_id": "u-wren",
                "source": SOURCE,
                "decided_at": 7,
                "box_id": None,
                "post_id": "post-1",
            },
        },
    ]


async def test_a_row_comes_off_only_while_it_still_carries_its_source_and_is_told(
    temp_db: Database, world: World, told: ChangeBus
) -> None:
    await _filed_by_the_box(temp_db, world)
    async with temp_db.write() as connection:
        filed = await filed_by_on(connection, world.loose, SOURCE)
    # Somebody filed the tag again by hand between the read and the take-back: it stays.
    await _run(temp_db, "UPDATE asset_tags SET source = NULL WHERE tag_id = ?", ("t-out",))
    # Told: whoever was shared her. Not told: whoever was shared only the tag that stays.
    sharer, bystander = (
        await create_user(temp_db, Role.GUEST),
        await create_user(temp_db, Role.GUEST),
    )
    await _run(
        temp_db,
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES (?, ?, ?, ?, 'share', 1)",
        ("g-1", "person", "p-wren", sharer.id),
        ("g-2", "tag", "t-out", bystander.id),
    )
    window, elsewhere = told.subscribe(sharer.id), told.subscribe(bystander.id)

    async with temp_db.write() as connection:
        went = await take_off_filed_on(connection, filed)

    assert [one.target_id for one in went] == ["p-wren", "t-sun", "u-adrift", "u-blank", "u-wren"]
    assert window.take(as_admin=False).about == (About.LIBRARY,)
    assert elsewhere.take(as_admin=False).about == ()
    people = await temp_db.fetch_all(
        "SELECT person_id FROM asset_people WHERE asset_id = ?", (world.loose,)
    )
    assert [row["person_id"] for row in people] == [world.person]
    tags = await temp_db.fetch_all(
        "SELECT tag_id FROM asset_tags WHERE asset_id = ?", (world.loose,)
    )
    assert [row["tag_id"] for row in tags] == ["t-out"]
    assert (
        await temp_db.fetch_all("SELECT 1 FROM asset_usernames WHERE asset_id = ?", (world.loose,))
        == []
    )

    # Asked again, nothing is left to take: nothing went, and nobody is told.
    async with temp_db.write() as connection:
        again = await take_off_filed_on(connection, filed)
    assert again == []
    assert window.take(as_admin=False).about == ()


# --- what is a shell ----------------------------------------------------------------------------


async def test_a_shell_is_a_row_a_source_made_that_nothing_holds(
    temp_db: Database, world: World
) -> None:
    await _box_person(temp_db, "p-wren", "Wren Halloway")
    await _run(
        temp_db,
        "INSERT INTO people_aliases (id, person_id, alias) VALUES (?, ?, ?)",
        ("al-1", "p-wren", "Wren H"),
    )
    await _run(
        temp_db,
        "INSERT INTO face_references (id, person_id, crop_digest, embedding, quality, origin, recognizer,"
        " created_at) VALUES (?, 'p-wren', ?, ?, 0.9, ?, 'r', 1)",
        ("f-seed", "d-seed", b"\x00\x01", "seed"),
    )
    # What the box brought holds nothing: her other name and her starter face.
    assert await _holds_nothing(temp_db, "person", "p-wren")

    # A face somebody confirmed holds her, as a file filed under her does.
    await _run(
        temp_db,
        "INSERT INTO face_references (id, person_id, crop_digest, embedding, quality, origin, recognizer,"
        " created_at) VALUES (?, 'p-wren', ?, ?, 0.9, ?, 'r', 1)",
        ("f-mine", "d-mine", b"\x02", "confirmed"),
    )
    assert not await _holds_nothing(temp_db, "person", "p-wren")

    # Not a shell at all: a kind with no shell, no id, a person somebody made, one with a note.
    assert not await _holds_nothing(temp_db, "collection", world.collection)
    assert not await _holds_nothing(temp_db, "person", "")
    assert not await _holds_nothing(temp_db, "person", world.person)
    await _box_person(temp_db, "p-noted", "Bryn Calloway")
    await _run(temp_db, "UPDATE people SET notes = 'met at the shoot' WHERE id = ?", ("p-noted",))
    assert not await _holds_nothing(temp_db, "person", "p-noted")


async def test_a_grant_or_a_persons_act_holds_a_shell(temp_db: Database, world: World) -> None:
    _ = world
    await _box_site(temp_db, "s-shared", "Sharedfront")
    await _box_tag(temp_db, "t-touched", "Touched")
    await _box_tag(temp_db, "t-named", "Named")
    await _box_tag(temp_db, "t-sift", "Siftmade")
    for one in ("s-shared", "t-touched", "t-named", "t-sift"):
        kind = "site" if one.startswith("s-") else "tag"
        assert await _holds_nothing(temp_db, kind, one), one

    someone = await create_user(temp_db, Role.GUEST)
    await _run(
        temp_db,
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES ('g-1', 'site', 's-shared', ?, 'share', 1)",
        (someone.id,),
    )
    await _run(
        temp_db,
        "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at, actor_kind,"
        " object_kind, object_id) VALUES (?, 'q', 't', 'd', '{}', 1, ?, 'tag', ?)",
        ("d-user", "user", "t-touched"),
        ("d-sift", "sift", "t-sift"),
        ("d-subject", "user", None),
    )
    await _run(
        temp_db,
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id) VALUES ('d-subject', 'tag', 't-named')",
    )

    assert not await _holds_nothing(temp_db, "site", "s-shared")
    assert not await _holds_nothing(temp_db, "tag", "t-touched")
    assert not await _holds_nothing(temp_db, "tag", "t-named")
    assert await _holds_nothing(temp_db, "tag", "t-sift"), "an act of Sift's own holds nothing"


async def test_a_username_is_a_shell_only_while_named_and_unnumbered(
    temp_db: Database, world: World
) -> None:
    _ = world
    await _box_site(temp_db, "s-store", "Storefront")
    await _username(temp_db, "u-wren", "s-store", "wrenh")
    await _username(temp_db, "u-blank", "s-store", "")
    await _username(temp_db, "u-known", "s-store", "known")
    await _run(temp_db, "UPDATE usernames SET number = '42' WHERE id = 'u-known'")

    assert await _holds_nothing(temp_db, "username", "u-wren")
    assert not await _holds_nothing(temp_db, "username", "u-blank"), (
        "a Site's nameless row is its own"
    )
    assert not await _holds_nothing(temp_db, "username", "u-known")
    # A Site is held by a username on it, and not by its nameless row while nothing is filed there.
    assert not await _holds_nothing(temp_db, "site", "s-store")
    await _run(temp_db, "DELETE FROM usernames WHERE id IN ('u-wren', 'u-known')")
    assert await _holds_nothing(temp_db, "site", "s-store")
    await _run(
        temp_db,
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, 'u-blank')",
        (world.loose,),
    )
    assert not await _holds_nothing(temp_db, "site", "s-store")


async def test_a_table_that_is_not_there_holds_nothing_and_brings_nothing(
    temp_db: Database, world: World
) -> None:
    """A library whose ledger or a feature's tables are not there yet still answers."""
    _ = world
    await _box_tag(temp_db, "t-bare", "Bare")
    await _box_site(temp_db, "s-bare", "Barefront")
    await _run(
        temp_db, "INSERT INTO tag_aliases (id, tag_id, alias) VALUES ('al-9', 't-bare', 'Plain')"
    )
    for table in (
        "tag_stash_box_links",
        "workbench_decision_subjects",
        "workbench_decisions",
        "watermark_reads",
    ):
        await _run(temp_db, f"DROP TABLE IF EXISTS {table}")

    assert await _holds_nothing(temp_db, "tag", "t-bare")
    assert await _holds_nothing(temp_db, "site", "s-bare")
    async with temp_db.write() as connection:
        kept = await remove_shell_on(connection, "tag", "t-bare")
    assert [one["table"] for one in kept] == ["tags", "tag_aliases"]


# --- removing a shell and its Undo -------------------------------------------------------------


async def test_a_shell_goes_with_what_its_box_brought_and_comes_back_whole(
    temp_db: Database, world: World
) -> None:
    await _box_person(temp_db, "p-wren", "Wren Halloway")
    await _run(
        temp_db,
        "INSERT INTO people_aliases (id, person_id, alias) VALUES ('al-1', 'p-wren', 'Wren H')",
    )
    await _run(
        temp_db,
        "INSERT INTO people_links (id, person_id, url, created_at) VALUES ('l-1', 'p-wren', 'https://a.example/w', 1)",
    )
    await _run(
        temp_db,
        "INSERT INTO face_references (id, person_id, crop_digest, embedding, quality, origin, recognizer,"
        " created_at) VALUES ('f-seed', 'p-wren', 'd-seed', ?, 0.9, 'seed', 'r', 1)",
        (b"\x00\xff",),
    )
    await _box_site(temp_db, "s-store", "Storefront")
    await _username(temp_db, "u-blank", "s-store", "")
    await _username(temp_db, "u-wren", "s-store", "wrenh")
    await _run(
        temp_db,
        "INSERT INTO site_art (scope, path, stored_at, username_id) VALUES ('art-1', 'a.jpg', 1, 'u-wren')",
    )
    # Last edited long ago: her names and links landing above touched it, so it is set back here.
    await _run(temp_db, "UPDATE people SET edited_at = 77 WHERE id = 'p-wren'")
    await _run(temp_db, "UPDATE sites SET edited_at = 55 WHERE id = 's-store'")
    before = await _whole(temp_db)

    async with temp_db.write() as connection:
        nobody = await remove_shell_on(connection, "person", "p-nobody")
        person = await remove_shell_on(connection, "person", "p-wren")
        handle = await remove_shell_on(connection, "username", "u-wren")
        site = await remove_shell_on(connection, "site", "s-store")

    assert nobody == []
    # The row whole first, then what came with it; a picture's bytes as hex, so JSON carries them.
    assert [one["table"] for one in person] == [
        "people",
        "face_references",
        "people_aliases",
        "people_links",
    ]
    assert person[1]["row"]["embedding"] == {"$hex": "00ff"}  # type: ignore[index]
    # A pointer the delete only clears is kept as the pointer, by the row's key.
    assert handle[1] == {
        "table": "site_art",
        "set": "username_id",
        "to": "u-wren",
        "key": {"scope": "art-1"},
    }
    # A Site's nameless row goes with the Site rather than being left naming nothing.
    assert [one["table"] for one in site] == ["sites", "usernames"]
    after = await _whole(temp_db)
    assert [row["id"] for row in after[0]] == [world.person]
    assert after[1] == [] and after[3] == []
    assert [row["id"] for row in after[5]] == [world.username]
    assert after[6][0]["username_id"] is None

    # Put back in the order taken, parents first; a person's names coming back are not an edit.
    async with temp_db.write() as connection:
        put = await put_back_on(connection, [*site, *person, *handle])
    assert put == len(site) + len(person) + len(handle)
    assert await _whole(temp_db) == before

    # Pressed again, everything is there already: nothing is written twice.
    async with temp_db.write() as connection:
        assert await put_back_on(connection, [*site, *person, *handle]) == 0


async def test_an_undo_writes_nothing_it_was_not_given_room_for(
    temp_db: Database, world: World
) -> None:
    await _box_site(temp_db, "s-store", "Storefront")
    await _username(temp_db, "u-wren", "s-store", "wrenh")
    await _run(
        temp_db,
        "INSERT INTO site_art (scope, path, stored_at, username_id) VALUES ('art-1', 'a.jpg', 1, NULL)",
    )
    await _run(
        temp_db,
        "INSERT INTO site_art (scope, path, stored_at, username_id) VALUES ('art-2', 'b.jpg', 1, 'u-wren')",
    )
    kept: list[dict[str, object]] = [
        # A table outside the take-back's own, and none at all.
        {"table": "users", "row": {"id": "u-1", "username": "x"}},
        {"row": {"id": "nothing"}},
        # A row holding no column of its table.
        {"table": "tags", "row": {"nope": 1}},
        # A file gone since: nothing to put her back on.
        {"table": "asset_people", "row": {"asset_id": "gone", "person_id": world.person}},
        # A Site gone since: the handle comes back, under no Site.
        {
            "table": "usernames",
            "row": {"id": "u-adrift", "site_id": "s-gone", "name": "adrift", "created_at": 1},
        },
        # Pointers: no key, a column the table lacks, a key the table lacks, and one re-pointed since.
        {"table": "site_art", "set": "username_id", "to": "u-wren"},
        {"table": "site_art", "set": "nope", "to": "u-wren", "key": {"scope": "art-1"}},
        {"table": "site_art", "set": "username_id", "to": "u-wren", "key": {"nope": "art-1"}},
        {"table": "site_art", "set": "username_id", "to": "u-other", "key": {"scope": "art-2"}},
        # The one that has room: a pointer still clear.
        {"table": "site_art", "set": "username_id", "to": "u-wren", "key": {"scope": "art-1"}},
    ]

    async with temp_db.write() as connection:
        put = await put_back_on(connection, kept)

    assert put == 2
    assert await temp_db.fetch_one("SELECT 1 FROM users WHERE id = 'u-1'") is None
    assert await temp_db.fetch_one("SELECT 1 FROM asset_people WHERE asset_id = 'gone'") is None
    adrift = await temp_db.fetch_one("SELECT site_id FROM usernames WHERE id = 'u-adrift'")
    assert adrift is not None and adrift["site_id"] is None
    art = await temp_db.fetch_all("SELECT scope, username_id FROM site_art ORDER BY scope")
    assert [tuple(row) for row in art] == [("art-1", "u-wren"), ("art-2", "u-wren")]


async def test_an_undo_refuses_a_row_pointing_at_a_parent_nobody_listed(
    temp_db: Database, world: World
) -> None:
    """A table brought tomorrow, pointing somewhere the put-back was never told to look: a row that
    names such a parent is refused rather than written blind, and one that names none comes back."""
    _ = world
    await _box_tag(temp_db, "t-out", "Outdoor")
    await _run(temp_db, "DROP TABLE IF EXISTS tag_stash_box_links")
    await _run(temp_db, "CREATE TABLE unlisted_boxes (id TEXT PRIMARY KEY)")
    await _run(temp_db, "INSERT INTO unlisted_boxes (id) VALUES ('b-1')")
    await _run(
        temp_db,
        "CREATE TABLE tag_stash_box_links (tag_id TEXT NOT NULL REFERENCES tags,"
        " box_id TEXT REFERENCES unlisted_boxes(id), PRIMARY KEY (tag_id))",
    )

    async with temp_db.write() as connection:
        put = await put_back_on(
            connection,
            [
                {"table": "tag_stash_box_links", "row": {"tag_id": "t-out", "box_id": "b-1"}},
                {"table": "tag_stash_box_links", "row": {"tag_id": "t-out", "box_id": None}},
            ],
        )

    assert put == 1
    rows = await temp_db.fetch_all("SELECT tag_id, box_id FROM tag_stash_box_links")
    assert [tuple(row) for row in rows] == [("t-out", None)]


# --- what is kept for an Undo -------------------------------------------------------------------


async def test_what_a_receipt_keeps_comes_back_in_order_and_goes_with_it(
    temp_db: Database, world: World
) -> None:
    _ = world
    async with temp_db.write() as connection:
        await keep_for_undo_on(
            connection,
            "r-1",
            [{"table": "tags", "row": {"id": "a"}}, {"table": "tags", "row": {"id": "b"}}],
        )
        await keep_for_undo_on(connection, "r-2", [{"table": "tags", "row": {"id": "c"}}])
        # What only a damaged library holds: a row that is not JSON, and one that is not an object.
        await connection.execute(
            "INSERT INTO undo_rows (receipt_id, seq, row) VALUES ('r-1', 5, 'not json')"
        )
        await connection.execute(
            "INSERT INTO undo_rows (receipt_id, seq, row) VALUES ('r-1', 6, '[1]')"
        )
        kept = await kept_for_undo_on(connection, "r-1")
        await forget_kept_on(connection, "r-1")
        forgotten = await kept_for_undo_on(connection, "r-1")
    assert kept == [{"table": "tags", "row": {"id": "a"}}, {"table": "tags", "row": {"id": "b"}}]
    assert forgotten == []

    # The receipt removed by any door takes what it kept with it.
    await _run(
        temp_db,
        "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at) VALUES ('r-2', 'q', 't', 'd', '{}', 1)",
    )
    await _run(temp_db, "DELETE FROM workbench_decisions WHERE id = 'r-2'")
    assert await temp_db.fetch_all("SELECT 1 FROM undo_rows") == []


async def test_the_receipt_rule_waits_for_both_tables(temp_db: Database, world: World) -> None:
    _ = world
    await _run(temp_db, "DROP TRIGGER IF EXISTS undo_rows_go_with_their_receipt")
    await _run(temp_db, "DROP TABLE undo_rows")
    async with temp_db.write() as connection:
        await take_back._keep_undo_rows_with_their_receipts(connection)
    trigger = await temp_db.fetch_one(
        "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = 'undo_rows_go_with_their_receipt'"
    )
    assert trigger is None
