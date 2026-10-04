# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's thread and a tag's thread, read over a library whose record holds each shape once.

A receipt's payload is a string its queue wrote and a thread must draw whatever is in it; a link
row with no moment borrows the act the record kept for it; a copy's carried attribution is said
as the carry's receipt says it; and a tag's thread names what it was called when somebody merged
another tag into it.
"""

from __future__ import annotations

import json

import pytest

# Imported for their side effect: registering the tables the record, the stash-boxes, the editing
# feature, the moves, the watermarks and the downloads keep, so a kernel database has them.
import sift.slices.download.schema
import sift.slices.media_edit.schema
import sift.slices.organize.schema
import sift.slices.stash_boxes.schema
import sift.slices.watermarks.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.access import sentences as say
from sift.kernel.access.history import Actor, Event, history_of_asset
from sift.kernel.access.history_entity import history_of_tag
from sift.kernel.db import Database
from sift.kernel.ledger import Actor as Doer
from sift.kernel.ledger import Object, record_event
from sift.kernel.sorting import sort_key
from sift.kernel.vocabulary import Subject
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio

AT = 1_700_000_000
BY_METADATA = "01HX0000000000000000001101"
CARRIED = "01HX0000000000000000001102"
GONE_SET = "01HX0000000000000000001103"
BOX = "01HX0000000000000000001104"
MERGED_TAG = "01HX0000000000000000001105"
DOWNLOAD = "01HX0000000000000000001106"


async def person(database: Database, person_id: str, name: str) -> None:
    await database.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        (person_id, name, sort_key(name), AT),
    )


async def receipt(
    database: Database,
    receipt_id: str,
    asset_id: str,
    *,
    title: str,
    verb: str = "decided",
    payload: str = "{}",
    about: tuple[tuple[str, str], ...] = (),
    object_kind: str | None = None,
    object_id: str | None = None,
) -> None:
    """A queue's receipt about one file, and what else it was about."""
    await database.execute(
        "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at, verb,"
        " actor_kind, actor_id, object_kind, object_id)"
        " VALUES (?, 'testing', ?, '', ?, ?, ?, 'sift', 'filename', ?, ?)",
        (receipt_id, title, payload, AT, verb, object_kind, object_id),
    )
    for kind, subject_id in (("asset", asset_id), *about):
        await database.execute(
            "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
            " VALUES (?, ?, ?)",
            (receipt_id, kind, subject_id),
        )


def said(events: list[Event]) -> list[str]:
    return [one.what for one in events]


def hrefs(line: tuple[say.Piece, ...]) -> list[str]:
    found: list[str] = []
    for one in line:
        if one.href:
            found.append(one.href)
        found.extend(hrefs(one.rest))
    return found


async def test_a_files_receipts_draw_whatever_their_payload_holds(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    link = {"kind": "person", "id": world.person, "words": "person", "href": "/people/x"}
    for number, payload in enumerate(
        (
            "not json",
            "[1]",
            json.dumps({"link": {**link, "id": ""}}),
            json.dumps({"link": {**link, "kind": "not a kind"}}),
            json.dumps({"link": link}),
        )
    ):
        await receipt(
            temp_db,
            f"01HX000000000000000000112{number}",
            world.solo,
            title=f"Sift named person here, receipt {number}",
            payload=payload,
        )
    # A receipt about a Photo Set that is there and one that has gone: the one there is named.
    await receipt(
        temp_db,
        "01HX0000000000000000001130",
        world.solo,
        title="Added to a Photo Set",
        verb="added",
        about=(("photo_set", world.photo_set), ("photo_set", GONE_SET)),
        object_kind="photo_set",
        object_id=world.photo_set,
    )

    events = await history_of_asset(temp_db, access, actors.admin, world.solo)

    lines = said(events)
    for number in range(5):
        assert f"Sift named person here, receipt {number}" in lines
    linked = next(one for one in events if one.what.endswith("receipt 4"))
    assert [one.id for one in linked.links] == [world.person]
    # The receipt with a verb is worded from the record, and the Photo Set it names is linked.
    assert any(link.id == world.photo_set for one in events for link in one.links)


async def test_a_link_row_with_no_moment_is_said_by_the_act_the_record_kept(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await person(temp_db, BY_METADATA, "Rowan Pike")
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at)"
        " VALUES (?, ?, 'metadata', ?)",
        (world.solo, BY_METADATA, AT),
    )
    async with temp_db.write() as connection:
        await record_event(
            connection,
            actor=Doer.user(actors.admin.id),
            verb="linked",
            subject=Subject(kind="asset", id=world.solo, name="solo.mp4"),
            object=Object(kind="person", id=world.person),
        )
        await record_event(
            connection,
            actor=Doer.user(actors.admin.id),
            verb="filed",
            subject=Subject(kind="asset", id=world.solo, name="solo.mp4"),
            object=Object(kind="site", id=world.site),
        )

    events = await history_of_asset(temp_db, access, actors.admin, world.solo)

    named = next(one for one in events if one.kind == "named" and "person" in one.what)
    assert named.actor is Actor.YOU
    filed = next(one for one in events if one.kind == "filed")
    assert filed.actor is Actor.YOU
    assert next(one for one in events if "Rowan Pike" in one.what).via == "metadata"


async def test_a_carried_attribution_is_said_as_its_receipt_says_it(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await person(temp_db, CARRIED, "Neve Alder")
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at)"
        " VALUES (?, ?, 'copy', ?)",
        (world.solo, CARRIED, AT),
    )
    title = say.carried_sentence(from_name="twin.mp4", person="Neve Alder")
    await receipt(
        temp_db,
        "01HX0000000000000000001140",
        world.solo,
        title=title,
        verb="linked",
        about=(("person", CARRIED),),
        object_kind="person",
        object_id=CARRIED,
    )
    # A copy made from this file, whose own row still stands.
    await temp_db.execute(
        "INSERT INTO produced_files (id, asset_id, source_asset_id, operation, produced_at)"
        " VALUES ('p1', ?, ?, 'trim', ?)",
        (world.loose, world.solo, AT),
    )

    events = await history_of_asset(temp_db, access, actors.admin, world.solo)

    carry = next(one for one in events if one.what == title)
    assert [one.id for one in carry.links] == [CARRIED]
    assert not [one for one in events if one.kind == "named" and "Neve Alder" in one.what]


async def test_a_files_thread_draws_without_the_editing_feature(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await temp_db.execute("DROP TABLE produced_files")
    async with temp_db.write() as connection:
        await record_event(
            connection,
            actor=Doer.user(actors.admin.id),
            verb="renamed",
            subject=Subject(kind="asset", id=world.solo, name="solo.mp4"),
        )

    events = await history_of_asset(temp_db, access, actors.admin, world.solo)

    assert any(one.what.startswith("You renamed") for one in events)


async def test_a_file_the_features_never_read_draws_none_of_their_lines(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await temp_db.execute(
        "INSERT INTO watermark_refusals (asset_id, created_at) VALUES (?, ?)", (world.solo, AT)
    )
    refused = await history_of_asset(temp_db, access, actors.admin, world.solo)
    assert [one.actor for one in refused if one.kind == "watermark"] == [Actor.SOMEBODY]

    # A process that never registered these features has none of their tables, and the thread is
    # drawn from what is there.
    await temp_db.execute("DROP TABLE watermark_refusals")
    await temp_db.execute("DROP TABLE watermark_reads")
    await temp_db.execute("DROP TABLE watermark_scans")
    await temp_db.execute("DROP TABLE downloads")
    await temp_db.execute("DROP TABLE IF EXISTS semantic_indexed")
    await temp_db.execute("DROP TABLE IF EXISTS face_removals")
    await temp_db.execute("DROP TABLE IF EXISTS face_ignored")

    drawn = await history_of_asset(temp_db, access, actors.admin, world.solo)
    assert not [one for one in drawn if one.kind in ("watermark", "downloaded", "face_off")]


async def test_a_tags_thread_says_what_it_was_called_when_another_was_merged_into_it(
    temp_db: Database, world: World, actors: Actors
) -> None:
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, 'StashDB', ?, 0)",
        (BOX, "https://stash-box.invalid/graphql"),
    )
    await temp_db.execute(
        "INSERT INTO downloads (id, url, url_hash, created_at) VALUES (?, ?, ?, ?)",
        (DOWNLOAD, "https://quillhouse.example/v/1", "hash-1", AT),
    )
    many = [
        Subject(kind="asset", id=f"01HX00000000000000000012{n:02d}", name=f"clip{n}.mp4")
        for n in range(8)
    ]
    async with temp_db.write() as connection:
        await record_event(
            connection,
            actor=Doer.user(actors.admin.id),
            verb="merged",
            subject=Subject(kind="tag", id=MERGED_TAG, name="seaside"),
            object=Object(kind="tag", id=world.tag, name="tag"),
        )
        for name in ("seaside", "tag"):
            await record_event(
                connection,
                actor=Doer.user(actors.admin.id),
                verb="edited",
                subject=Subject(kind="asset", id=world.twin, name="twin.mp4"),
                object=Object(kind="tag", id=world.tag, name=name),
            )
        await record_event(
            connection,
            actor=Doer.box(BOX),
            verb="edited",
            subject=many,
            object=Object(kind="tag", id=world.tag, name="tag"),
        )
        await record_event(
            connection,
            actor=Doer.user(actors.admin.id),
            verb="linked",
            subject=Subject(kind="tag", id=world.tag, name="tag"),
            object=Object(kind="folder", id=world.leaf, name="leaf"),
        )
        await record_event(
            connection,
            actor=Doer.user(actors.admin.id),
            verb="linked",
            subject=Subject(kind="tag", id=world.tag, name="tag"),
            object=Object(kind="download", id=DOWNLOAD, name="v/1"),
        )

    events = await history_of_tag(temp_db, actors.admin, world.tag)

    lines = said(events)
    assert any(line.endswith(", when it was called seaside") for line in lines)
    assert [one for one in events if one.actor is Actor.STASH_BOX]
    addresses = [href for one in events for href in hrefs(one.pieces)]
    assert any(href.startswith("/browse?in=") for href in addresses)
    assert any(href.startswith("/downloads?row=") for href in addresses)
