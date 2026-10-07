# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Site's thread says how each of its usernames arrived, and every thread draws from what the
install actually has.

The record, the stash-box tables and the face tables belong to features, and a process that never
registered one has none of its tables. A thread that named a missing table would be a page that
does not draw, so each is asked with the tables there and with them gone.
"""

from __future__ import annotations

import pytest

# Imported for their side effect: registering the record's and the stash-boxes' tables.
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access.history import Actor
from sift.kernel.access.history_entity import (
    history_of_collection,
    history_of_photo_set,
    history_of_site,
    history_of_tag,
)
from sift.kernel.db import Database
from sift.kernel.sorting import sort_key
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio

AT = 1_700_000_000
SITE = "01HX0000000000000000000801"
BOX = "01HX0000000000000000000802"


async def arrived(
    database: Database,
    event_id: str,
    username_id: str,
    name: str,
    *,
    actor_kind: str | None,
    actor_id: str | None,
    payload: str = "{}",
) -> None:
    """A username on the Site, and the `added` event its arrival wrote."""
    await database.execute(
        "INSERT INTO usernames (id, site_id, name, name_sort, created_at) VALUES (?, ?, ?, ?, ?)",
        (username_id, SITE, name, sort_key(name), AT),
    )
    await database.execute(
        "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at, verb,"
        " actor_kind, actor_id, object_kind, object_id)"
        " VALUES (?, 'ledger', 't', '', ?, ?, 'added', ?, ?, 'site', ?)",
        (event_id, payload, AT, actor_kind, actor_id, SITE),
    )
    await database.execute(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
        " VALUES (?, 'username', ?, ?)",
        (event_id, username_id, name),
    )


async def test_a_sites_usernames_say_who_added_them_and_a_box_made_site_names_its_box(
    temp_db: Database, access: object, actors: Actors
) -> None:
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, 'StashDB', ?, 0)",
        (BOX, "https://stash-box.invalid/graphql"),
    )
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, created_at, created_by_kind, created_by_box_id)"
        " VALUES (?, 'Quillhouse', ?, ?, 'box', ?)",
        (SITE, sort_key("Quillhouse"), AT, BOX),
    )
    await arrived(
        temp_db,
        "01HX0000000000000000000811",
        "01HX0000000000000000000821",
        "quillmoss",
        actor_kind="sift",
        actor_id="filename",
    )
    await arrived(
        temp_db,
        "01HX0000000000000000000812",
        "01HX0000000000000000000822",
        "wrenna",
        actor_kind="user",
        actor_id=actors.admin.id,
    )
    await arrived(
        temp_db,
        "01HX0000000000000000000813",
        "01HX0000000000000000000823",
        "rowanp",
        actor_kind="user",
        actor_id=actors.guest.id,
    )
    await arrived(
        temp_db,
        "01HX0000000000000000000814",
        "01HX0000000000000000000824",
        "nadiav",
        actor_kind=None,
        actor_id=None,
        payload='{"backfilled": true}',
    )

    events = await history_of_site(temp_db, actors.admin, SITE)

    made = [one for one in events if one.actor is Actor.STASH_BOX]
    assert [one.actor_name for one in made] == ["StashDB"]
    said = {one.what for one in events if one.kind == "filed"}
    assert "You added the username wrenna to it" in said
    assert "Another user added the username rowanp to it" in said
    assert "The username nadiav was added to it before Sift recorded how" in said
    assert any(line.startswith("Sift added the username quillmoss to it") for line in said)


async def test_every_thread_draws_without_the_tables_a_feature_never_made(
    temp_db: Database, world: World, actors: Actors
) -> None:
    await temp_db.execute("DROP TABLE workbench_decision_subjects")
    await temp_db.execute("DROP TABLE workbench_decisions")
    await temp_db.execute("DROP TABLE stash_box_kept")

    threads = [
        await history_of_tag(temp_db, actors.admin, world.tag),
        await history_of_site(temp_db, actors.admin, world.site),
        await history_of_collection(temp_db, actors.admin, world.collection),
        await history_of_photo_set(temp_db, actors.admin, world.photo_set),
    ]

    # Each still draws the line that made it, from its own row.
    assert all(thread for thread in threads[:1] + threads[2:])


async def test_a_site_made_before_its_moment_was_kept_still_says_who_made_it(
    temp_db: Database, access: object, actors: Actors
) -> None:
    """A Site whose row says who made it and not when draws its arrival "before this was recorded"
    rather than an empty History. A Site that says neither keeps the empty one."""
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, created_by_kind, created_by_via)"
        " VALUES (?, 'Quillhouse', ?, 'sift', 'stash')",
        (SITE, sort_key("Quillhouse")),
    )
    events = await history_of_site(temp_db, actors.admin, SITE)
    assert [(one.at, one.actor) for one in events] == [(None, Actor.SIFT)]

    await temp_db.execute("UPDATE sites SET created_by_kind = NULL, created_by_via = NULL")
    assert await history_of_site(temp_db, actors.admin, SITE) == []


async def test_a_run_s_usernames_removed_since_are_counted_not_named_bare(
    temp_db: Database, actors: Actors
) -> None:
    """A box's run joined three usernames to a person and two were removed since. A removed one has
    no Site left to say it on, and two bare "wrenna" beside each other read as one row twice, so
    the line names the one still there on its Site and counts the rest as removed."""
    from sift.kernel.access.history_boxes import filled_named
    from sift.kernel.access.sentences import filled_field, text_of

    person = "01HX0000000000000000000831"
    run = "01HX0000000000000000000832"
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, 'FansDB', ?, 0)",
        (BOX, "https://stash-box.invalid/graphql"),
    )
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, 'Quillhouse', ?, ?)",
        (SITE, sort_key("Quillhouse"), AT),
    )
    await temp_db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, 'Wrenna Sable', ?, 0)",
        (person, sort_key("Wrenna Sable")),
    )
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, name_sort, person_id, created_at)"
        " VALUES ('01HX0000000000000000000841', ?, 'quillmoss', 'quillmoss', ?, ?)",
        (SITE, person, AT),
    )
    await temp_db.execute(
        "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at, verb,"
        " actor_kind, actor_id, object_kind, object_id)"
        " VALUES (?, 'ledger', 't', '', '{}', ?, 'linked', 'user', ?, 'person', ?)",
        (run, AT, actors.admin.id, person),
    )
    for username_id, name in (
        ("01HX0000000000000000000841", "quillmoss"),
        ("01HX0000000000000000000842", "wrenna"),
        ("01HX0000000000000000000843", "wrenna"),
    ):
        await temp_db.execute(
            "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
            " VALUES (?, 'username', ?, ?)",
            (run, username_id, name),
        )

    fields = await filled_named(temp_db, "person", person, BOX, at=AT, stored='{"accounts": 3}')

    assert fields is not None
    said = text_of(filled_field(fields[0]))
    assert said == "3 usernames (quillmoss on Quillhouse and 2 since removed)"
    assert "wrenna" not in said
