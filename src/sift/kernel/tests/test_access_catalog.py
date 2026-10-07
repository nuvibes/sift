# SPDX-License-Identifier: AGPL-3.0-or-later
"""The catalog's own reads and writes, asked directly.

Every one of these is reached through a feature on a running install. Asked here, each answers on a
library whose shape the test wrote, so what is checked is the rule the statement keeps (which file
is waiting, which filing a pass wrote, which box made a row) rather than whatever a feature happened
to hand it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

# Imported for their side effect: registering the tables the picture-reading pass, the record and
# the stash-boxes keep, so a kernel database has them.
import sift.slices.stash_boxes.schema
import sift.slices.watermarks.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import db
from sift.kernel.access import catalog
from sift.kernel.access.catalog import (
    COPIED,
    Carried,
    Made,
    NumberedUsername,
    NumberLearned,
    UsernameNumber,
)
from sift.kernel.access.history import Actor
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key
from sift.kernel.vocabulary import VIA_METADATA
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio

AT = 1_700_000_000
BOX = "01HX0000000000000000000501"
GONE_BOX = "01HX0000000000000000000502"
SOURCES = ("filename", "folder")


async def a_box(database: Database, box_id: str = BOX, name: str = "StashDB") -> None:
    await database.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at, slug) VALUES (?, ?, ?, 0, ?)",
        (box_id, name, f"https://{box_id.lower()}.invalid/graphql", name.lower()),
    )


async def a_site(database: Database, site_id: str, name: str, *links: str) -> None:
    await database.execute(
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        (site_id, name, sort_key(name), AT),
    )
    for url in links:
        await database.execute(
            "INSERT INTO site_links (id, site_id, url, label, created_at) VALUES (?, ?, ?, NULL, ?)",
            (new_id(), site_id, url, AT),
        )


# --- which Site an address is on ------------------------------------------------------------------


async def test_an_address_is_on_the_site_whose_own_address_holds_its_host(
    temp_db: Database, access: object
) -> None:
    # A page ABOUT the site, held by another Site with the lower id: read first, and not a site's
    # own address, so it is passed over.
    await a_site(
        temp_db, "01HX0000000000000000000510", "Marrowvale", "https://quillhouse.example/about"
    )
    await a_site(temp_db, "01HX0000000000000000000520", "Quillhouse", "https://quillhouse.example")

    assert await catalog.site_for(temp_db, "https://www.quillhouse.example/v/1") == "Quillhouse"
    assert await catalog.site_for(temp_db, "https://nowhere.example/v/1") is None
    async with temp_db.read() as connection:
        assert await catalog.site_on_host(connection, "") is None


# --- the picture-reading pass ---------------------------------------------------------------------


async def test_a_file_is_waiting_for_the_picture_pass_until_this_revision_reads_it(
    temp_db: Database, world: World
) -> None:
    await temp_db.execute(
        "UPDATE assets SET width = 8, height = 8 WHERE id IN (?, ?)", (world.solo, world.twin)
    )
    await temp_db.execute(
        "INSERT INTO watermark_scans (asset_id, revision, identity, found, scanned_at)"
        " SELECT id, 'r1', identity, 0, 0 FROM assets WHERE id = ?",
        (world.twin,),
    )

    assert await catalog.unread_by_picture(temp_db, "r1", limit=10) == [world.solo]
    assert await catalog.unread_by_picture_among(temp_db, "r1", [world.solo, world.twin]) == {
        world.solo
    }
    assert await catalog.unread_by_picture_among(temp_db, "r1", []) == set()
    assert await catalog.count_unread_by_picture(temp_db, "r1", limit=10) == 1
    # A new revision has read nothing yet.
    assert await catalog.count_unread_by_picture(temp_db, "r2", limit=10) == 2


async def test_nothing_waits_for_a_picture_pass_that_was_never_installed(
    temp_db: Database, world: World
) -> None:
    await temp_db.execute("DROP TABLE watermark_refusals")

    assert await catalog.unread_by_picture(temp_db, "r1", limit=10) == []
    assert await catalog.unread_by_picture_among(temp_db, "r1", [world.solo]) == set()
    assert await catalog.count_unread_by_picture(temp_db, "r1", limit=10) == 0


# --- who made a row -------------------------------------------------------------------------------


async def test_a_removed_box_is_forgotten_by_everything_it_made(
    temp_db: Database, world: World
) -> None:
    await temp_db.execute(
        "UPDATE people SET created_by_box_id = ? WHERE id = ?", (BOX, world.person)
    )
    await temp_db.execute("UPDATE sites SET created_by_box_id = ? WHERE id = ?", (BOX, world.site))
    await temp_db.execute("UPDATE tags SET created_by_box_id = ? WHERE id = ?", (BOX, world.tag))

    assert await catalog.clear_created_by_box(temp_db, BOX) == 3
    assert await catalog.clear_created_by_box(temp_db, BOX) == 0


async def test_who_made_a_row_is_said_in_the_historys_own_words(
    temp_db: Database, world: World, actors: Actors
) -> None:
    await a_box(temp_db)

    async def made(kind: str, *, user: str | None = None, box: str | None = None) -> None:
        await temp_db.execute(
            "UPDATE people SET created_by_kind = ?, created_by_user_id = ?, created_by_box_id = ?,"
            " created_by_via = ? WHERE id = ?",
            (kind, user, box, "stash" if box else None, world.person),
        )

    async def maker() -> catalog.EntityMaker | None:
        return await catalog.made_by(temp_db, actors.admin, "person", world.person)

    assert await catalog.made_by(temp_db, actors.admin, "shoot", world.person) is None
    assert await catalog.made_by(temp_db, actors.admin, "person", "") is None
    assert await catalog.made_by(temp_db, actors.admin, "person", GONE_BOX) is None
    # A row made before the catalog recorded who made it does not say.
    assert await maker() is None

    await made("box", box=BOX)
    by_box = await maker()
    assert by_box is not None
    assert (by_box.actor, by_box.box_id, by_box.box_name, by_box.box_slug, by_box.via) == (
        Actor.STASH_BOX,
        BOX,
        "StashDB",
        "stashdb",
        "stash",
    )

    # A box since removed is still a box: the row cannot say which, and it is not "somebody".
    await made("box", box=GONE_BOX)
    forgotten = await maker()
    assert forgotten is not None
    assert (forgotten.actor, forgotten.box_id, forgotten.box_name) == (
        Actor.STASH_BOX,
        None,
        None,
    )

    await made("user", user=actors.guest.id)
    by_another = await maker()
    assert by_another is not None
    assert by_another.actor == Actor.ANOTHER_USER


async def test_a_box_maker_is_a_nameless_box_where_the_stash_boxes_were_never_installed(
    temp_db: Database, world: World, actors: Actors
) -> None:
    await temp_db.execute("DROP TABLE stash_boxes")
    await temp_db.execute(
        "UPDATE people SET created_by_kind = 'box', created_by_box_id = ?,"
        " created_by_via = 'stash' WHERE id = ?",
        (BOX, world.person),
    )

    maker = await catalog.made_by(temp_db, actors.admin, "person", world.person)

    assert maker is not None
    assert (maker.actor, maker.box_id, maker.box_name, maker.via) == (
        Actor.STASH_BOX,
        None,
        None,
        "stash",
    )


# --- a site's own number for a username -----------------------------------------------------------


async def test_a_username_is_found_by_the_number_its_site_knows_it_by(
    temp_db: Database, world: World
) -> None:
    assert await catalog.username_for_number(temp_db, site="site", number="42") is None
    assert await catalog.username_number(temp_db, "01HX0000000000000000000599") == UsernameNumber()

    learned = NumberLearned(via=VIA_METADATA, agreed=3)
    made = Made(kind="sift", via=VIA_METADATA)
    assert await catalog.remember_username_number(
        temp_db, site="site", name="handle", number="42", learned=learned, made=made
    ) == (world.username, True)
    # A number is never overwritten by a second answer.
    assert await catalog.remember_username_number(
        temp_db, site="site", name="handle", number="43", learned=learned, made=made
    ) == (world.username, False)

    assert await catalog.username_for_number(temp_db, site="site", number="42") == (
        NumberedUsername(name="handle", via=VIA_METADATA, agreed=3)
    )
    assert await catalog.username_number(temp_db, world.username) == UsernameNumber(
        number="42", via=VIA_METADATA, agreed=3
    )


# --- what one pass filed, and which post it came from ---------------------------------------------


async def test_a_pass_files_by_post_and_reads_back_what_it_filed(
    temp_db: Database, world: World, actors: Actors
) -> None:
    async with temp_db.write() as connection:
        assert await catalog.link_username_to_asset_in_post_on(
            connection,
            asset_id=world.twin,
            username_id=world.username,
            post_id="post-1",
            source="filename",
        )
        # Already filed: the post is not stamped onto a row this pass did not write.
        assert not await catalog.link_username_to_asset_in_post_on(
            connection,
            asset_id=world.twin,
            username_id=world.username,
            post_id="post-9",
            source="filename",
        )
        await connection.execute(
            "INSERT INTO asset_usernames (asset_id, username_id, source) VALUES (?, ?, 'filename')",
            (world.loose, world.username),
        )

    assert await catalog.files_in_post(temp_db, username_id=world.username, post_id="post-1") == [
        world.twin
    ]
    assert await catalog.filings_without_a_post(temp_db, SOURCES) == [
        (world.loose, world.username, "loose.mp4")
    ]
    async with temp_db.write() as connection:
        assert await catalog.set_filing_post_on(
            connection, asset_id=world.loose, username_id=world.username, post_id="post-2"
        )
        assert not await catalog.set_filing_post_on(
            connection, asset_id=world.loose, username_id=world.username, post_id="post-3"
        )
    assert await catalog.filings_without_a_post(temp_db, SOURCES) == []

    assert await catalog.count_usernames_filed_from(temp_db, actors.admin, SOURCES) == 1
    assert await catalog.usernames_filed_from(
        temp_db, actors.admin, SOURCES, limit=10, offset=0
    ) == [(world.username, "handle", "site", None, 2)]
    assert await catalog.position_filed_from(temp_db, actors.admin, SOURCES, world.username) == 0
    assert await catalog.position_filed_from(temp_db, actors.admin, SOURCES, world.person) is None
    filed = await catalog.files_filed_under(
        temp_db, username_id=world.username, sources=SOURCES, limit=10, stamp=0
    )
    assert [(asset_id, filename) for asset_id, filename, _art in filed] == sorted(
        [(world.twin, "twin.mp4"), (world.loose, "loose.mp4")]
    )


async def test_a_username_whose_files_have_gone_missing_still_lists_them(
    temp_db: Database, world: World
) -> None:
    """The count beside a username counts every file filed under it; the list under the row
    shows the same files, missing ones included. A row that says 3,000 files and shows none is
    the fault this pins: a location marked missing still has its name to show."""
    async with temp_db.write() as connection:
        for asset_id in (world.twin, world.loose):
            await connection.execute(
                "INSERT INTO asset_usernames (asset_id, username_id, source)"
                " VALUES (?, ?, 'filename')",
                (asset_id, world.username),
            )
        await connection.execute(
            "UPDATE asset_locations SET status = 'missing' WHERE asset_id IN (?, ?)",
            (world.twin, world.loose),
        )

    filed = await catalog.files_filed_under(
        temp_db, username_id=world.username, sources=SOURCES, limit=10, stamp=0
    )
    assert sorted(asset_id for asset_id, _filename, _art in filed) == sorted(
        [world.twin, world.loose]
    )


async def test_an_undo_is_offered_on_the_newest_filing_of_each_file(
    temp_db: Database, world: World
) -> None:
    assert await catalog.decisions_for_files(temp_db, [], queue="filing") == {}
    for decision_id, asset_id, at in (
        ("01HX0000000000000000000531", world.solo, AT),
        ("01HX0000000000000000000532", world.solo, AT + 1),
        ("01HX0000000000000000000533", world.twin, AT),
    ):
        await temp_db.execute(
            "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at)"
            " VALUES (?, 'filing', 't', '', '{}', ?)",
            (decision_id, at),
        )
        await temp_db.execute(
            "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
            " VALUES (?, 'asset', ?)",
            (decision_id, asset_id),
        )

    assert await catalog.decisions_for_files(
        temp_db, [world.solo, world.twin, world.loose], queue="filing"
    ) == {world.solo: "01HX0000000000000000000532", world.twin: "01HX0000000000000000000533"}

    await temp_db.execute("DROP TABLE workbench_decision_subjects")
    assert await catalog.decisions_for_files(temp_db, [world.solo], queue="filing") == {}


# --- carrying an attribution onto a file's copies -------------------------------------------------


async def test_an_attribution_carries_onto_copies_once_and_comes_back_off(
    temp_db: Database, world: World
) -> None:
    assert await catalog.attribution_of_files(temp_db, []) == {}
    carried = await catalog.attribution_of_files(temp_db, [world.solo, world.twin])
    person = Carried(kind="person", id=world.person, name="person")
    username = Carried(
        kind="username", id=world.username, name="handle", site_id=world.site, site="site"
    )
    assert carried == {world.solo: [person, username], world.twin: []}

    async with temp_db.write() as connection:
        landed = await catalog.carry_attribution_on(
            connection, carried=carried[world.solo], to_asset_ids=[world.twin, world.loose]
        )
        again = await catalog.carry_attribution_on(
            connection, carried=carried[world.solo], to_asset_ids=[world.twin]
        )
    assert landed == {world.twin: [person, username], world.loose: [person, username]}
    assert again == {}
    copies = await temp_db.fetch_all(
        "SELECT COUNT(*) AS n FROM asset_people WHERE source = ?", (COPIED,)
    )
    assert int(copies[0]["n"]) == 2

    async with temp_db.write() as connection:
        removed = await catalog.uncarry_attribution_on(
            connection, asset_id=world.twin, carried=carried[world.solo]
        )
    assert removed == 2
    assert (await catalog.attribution_of_files(temp_db, [world.twin]))[world.twin] == []


async def test_the_attribution_of_any_number_of_files_is_two_statements(
    temp_db: Database, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Who is named and where each file is filed: one read each, however many files are asked."""
    heard: list[str] = []
    judged = db._judged

    @contextmanager
    def counted(stage: str, statement: Any, *rest: Any, **options: Any) -> Iterator[Any]:
        heard.append(db.statement_name(statement))
        with judged(stage, statement, *rest, **options) as timing:
            yield timing

    monkeypatch.setattr(db, "_judged", counted)
    many = [world.solo, *(new_id() for _ in range(1_200))]

    carried = await catalog.attribution_of_files(temp_db, many)

    assert len(heard) == 2
    assert len(carried) == len(many)
    assert [one.kind for one in carried[world.solo]] == ["person", "username"]


# --- kept local, and what has been enriched when --------------------------------------------------


async def test_a_thing_kept_local_is_refused_to_the_door_by_itself_or_by_what_it_is_filed_under(
    temp_db: Database, world: World
) -> None:
    assert not await catalog.kept_local(temp_db, "collection", world.collection)
    assert not await catalog.kept_local(temp_db, "person", "")
    assert not await catalog.kept_local(temp_db, "person", world.person)
    assert not await catalog.kept_local_over(temp_db, "")

    async with temp_db.write() as connection:
        assert not await catalog.set_kept_local_on(connection, "collection", world.collection, True)
        assert not await catalog.set_kept_local_on(connection, "person", "", True)
        assert await catalog.set_kept_local_on(connection, "person", world.person, True)
        assert await catalog.set_kept_local_on(connection, "asset", world.twin, True)

    assert await catalog.kept_local(temp_db, "person", world.person)
    # `solo` is kept local by the person it is filed under, `twin` by its own switch.
    assert await catalog.kept_local_over(temp_db, world.solo)
    assert await catalog.kept_local_over(temp_db, world.twin)
    assert not await catalog.kept_local_over(temp_db, world.loose)
    assert await catalog.kept_local_among(temp_db, []) == set()
    assert await catalog.kept_local_among(temp_db, [world.solo, world.twin, world.loose]) == {
        world.twin
    }


async def test_each_box_that_enriched_a_thing_is_listed_newest_first(
    temp_db: Database, world: World
) -> None:
    await a_box(temp_db)
    assert await catalog.enrichment_of(temp_db, "collection", world.collection) == []
    assert await catalog.enrichment_of(temp_db, "person", world.person) == []
    assert not await catalog.record_enrichment(
        temp_db, "collection", world.collection, BOX, automatic=True, at=AT
    )
    async with temp_db.write() as connection:
        assert not await catalog.record_enrichment_on(
            connection, "person", "", BOX, automatic=True, at=AT
        )

    assert await catalog.record_enrichment(
        temp_db, "person", world.person, BOX, automatic=False, at=AT, applied={"birthdate": 1}
    )
    assert await catalog.record_enrichment(
        temp_db, "person", world.person, BOX, automatic=True, at=AT + 5
    )
    assert await catalog.record_enrichment(
        temp_db, "person", world.person, GONE_BOX, automatic=True, at=AT + 1
    )

    runs = await catalog.enrichment_of(temp_db, "person", world.person)
    assert [(one.box_id, one.box_name, one.box_slug, one.at, one.automatic) for one in runs] == [
        (BOX, "StashDB", "stashdb", AT + 5, True),
        (GONE_BOX, None, None, AT + 1, True),
    ]


async def test_a_run_names_no_box_where_the_stash_boxes_were_never_installed(
    temp_db: Database, world: World
) -> None:
    await temp_db.execute("DROP TABLE stash_boxes")
    assert await catalog.record_enrichment(
        temp_db, "person", world.person, BOX, automatic=False, at=AT
    )

    runs = await catalog.enrichment_of(temp_db, "person", world.person)

    assert [(one.box_id, one.box_name, one.box_slug, one.at) for one in runs] == [
        (BOX, None, None, AT)
    ]
