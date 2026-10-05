# SPDX-License-Identifier: AGPL-3.0-or-later
"""A History line names a Site, a username or a folder only to a viewer who may be shown it."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest

from sift.kernel.access import Effect, ObjectType, Repository, Viewer
from sift.kernel.access.history import history_of_asset
from sift.kernel.access.history_boxes import unshown_said
from sift.kernel.access.history_entity import history_of_photo_set, history_of_tag
from sift.kernel.access.history_line import Actor, Detail, Event, Link
from sift.kernel.access.history_person import history_of_person
from sift.kernel.access.sentences import Piece, said, thing
from sift.kernel.db import Database
from sift.kernel.tests.test_history import (
    ADDED_AT,
    ASSET,
    LIBRARY,
    PERSON,
    make_file,
    name_person,
)
from sift.testing.fixtures import Actors, hide

pytestmark = pytest.mark.anyio

SITE = "01HX0000000000000000000951"
TOP = "01HX0000000000000000000952"
FOLDER = "01HX0000000000000000000953"
USERNAME = "01HX0000000000000000000954"
HIDDEN = ("Pier Nine Media", "harlowquin", "Northlight Raw", "Sandbar Runways")


@pytest.fixture
async def library(temp_db: Database, access: Repository, actors: Actors) -> None:
    """A file the guest is given, downloaded from a Site and named from folders they are not."""
    await make_file(temp_db)
    await temp_db.execute(
        "UPDATE asset_locations SET rel_path = 'Sandbar Runways/clip.mp4' WHERE asset_id = ?",
        (ASSET,),
    )
    await name_person(temp_db, source="folder", at=ADDED_AT + 100)
    await temp_db.execute("INSERT INTO sites (id, name) VALUES (?, 'Pier Nine Media')", (SITE,))
    await temp_db.execute(
        "INSERT INTO downloads (id, url, url_hash, state, site, site_id, username, asset_id,"
        " created_at, finished_at) VALUES ('d1', 'https://example.invalid/x', 'h', 'done',"
        " 'Pier Nine Media', ?, 'harlowquin', ?, ?, ?)",
        (SITE, ASSET, ADDED_AT, ADDED_AT + 5),
    )
    for folder_id, path, name in ((TOP, "", "Northlight Raw"), (FOLDER, "Sandbar Runways", None)):
        await temp_db.execute(
            "INSERT INTO folders (id, root_id, rel_path, name) VALUES (?, ?, ?, ?)",
            (folder_id, LIBRARY, path, name or path),
        )
    await access.grant(ObjectType.ITEM, ASSET, actors.guest.id, Effect.SHARE)


async def _answered_as(database: Database, folder_id: str) -> None:
    await database.execute(
        "INSERT INTO folder_people (folder_id, person_id, created_at) VALUES (?, ?, ?)",
        (folder_id, PERSON, ADDED_AT),
    )


async def _as(access: Repository, viewer: Viewer, *, vault_open: bool = False) -> Viewer:
    loaded = await access.load_viewer(viewer.id)
    assert loaded is not None
    return replace(loaded, show_hidden=True) if vault_open else loaded


def _pieces(pieces: tuple[Piece, ...]) -> Iterator[Piece]:
    for one in pieces:
        yield one
        yield from _pieces(one.rest)


async def _said(database: Database, access: Repository, viewer: Viewer) -> list[Event]:
    return [
        *await history_of_asset(database, access, viewer, ASSET),
        *await history_of_person(database, viewer, PERSON, access=access),
    ]


def _leaks(events: list[Event], ids: set[str]) -> list[str]:
    found = [one.text for event in events for one in _pieces(event.pieces) if one.id in ids]
    found += [
        link.name
        for event in events
        for group in event.detail
        for link in group.links
        if link.id in ids
    ]
    words = " ".join(event.what for event in events)
    return found + [name for name in HIDDEN if name in words]


@pytest.mark.parametrize("folder", [TOP, FOLDER])
async def test_a_guest_is_told_no_site_or_folder_they_may_not_be_shown(
    temp_db: Database, access: Repository, actors: Actors, library: None, folder: str
) -> None:
    await _answered_as(temp_db, folder)
    for open_ in (False, True):
        events = await _said(temp_db, access, await _as(access, actors.guest, vault_open=open_))
        assert _leaks(events, {SITE, TOP, FOLDER, USERNAME}) == []
        said_ = {event.kind: event.what for event in events}
        assert said_["downloaded"] == "Sift downloaded this file from a Site"
        assert [one.what for one in events if one.kind == "named"] == [
            "Sift named Neve Alder in this file from a folder name",
            "Sift named them on 1 file from a folder name",
        ]


async def test_a_guest_is_not_told_the_name_of_a_site_that_is_gone(
    temp_db: Database, access: Repository, actors: Actors, library: None
) -> None:
    await temp_db.execute("UPDATE downloads SET site_id = NULL, site = 'Northlight Raw'")
    guest = await history_of_asset(temp_db, access, await _as(access, actors.guest), ASSET)
    assert {one.kind: one.what for one in guest}["downloaded"] == (
        "Sift downloaded this file from a Site"
    )
    admin = await history_of_asset(temp_db, access, await _as(access, actors.admin), ASSET)
    assert "Northlight Raw" in {one.kind: one.what for one in admin}["downloaded"]


@pytest.mark.parametrize("folder", [TOP, FOLDER])
async def test_an_admin_is_told_the_site_and_the_folder_vault_shut_or_open(
    temp_db: Database, access: Repository, actors: Actors, library: None, folder: str
) -> None:
    await _answered_as(temp_db, folder)
    for open_ in (False, True):
        events = await _said(temp_db, access, await _as(access, actors.admin, vault_open=open_))
        said_ = {event.kind: event.what for event in events}
        assert said_["downloaded"] == "Sift downloaded this file from harlowquin on Pier Nine Media"
        name = "Northlight Raw" if folder == TOP else "Sandbar Runways"
        assert [one.what for one in events if one.kind == "named"] == [
            f"Sift named Neve Alder in this file from the folder {name}",
            f"Sift named them on 1 file from the folder {name}",
        ]


async def test_a_library_s_hidden_top_folder_is_named_to_an_admin_only_with_hidden_open(
    temp_db: Database, access: Repository, actors: Actors, library: None
) -> None:
    await _answered_as(temp_db, TOP)
    await temp_db.execute(
        "INSERT INTO photo_sets (id, name, origin, created_at, folder_id)"
        " VALUES ('set-1', 'Poolside Movie', 'folder', 0, ?)",
        (TOP,),
    )
    await hide(temp_db, "folder", TOP, actors.admin.id)
    for open_, said_ in ((False, "a folder name"), (True, "the folder Northlight Raw")):
        admin = await _as(access, actors.admin, vault_open=open_)
        named = [one.what for one in await _said(temp_db, access, admin) if one.kind == "named"]
        assert named[-1] == f"Sift named them on 1 file from {said_}"
        made = (await history_of_photo_set(temp_db, admin, "set-1", access=access))[0]
        assert made.what.endswith(said_.removesuffix(" name"))


async def test_a_guest_is_told_the_site_username_and_folder_they_are_given(
    temp_db: Database, access: Repository, actors: Actors, library: None
) -> None:
    await _answered_as(temp_db, TOP)
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, 'harlowquin', 0)",
        (USERNAME, SITE),
    )
    await temp_db.execute(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)", (ASSET, USERNAME)
    )
    await access.grant(ObjectType.FOLDER, TOP, actors.guest.id, Effect.SHARE)
    events = await _said(temp_db, access, await _as(access, actors.guest))
    said_ = {event.kind: event.what for event in events}
    assert said_["downloaded"] == "Sift downloaded this file from harlowquin on Pier Nine Media"
    assert "Sift named them on 1 file from the folder Northlight Raw" in [
        one.what for one in events
    ]


async def test_a_guest_shown_the_site_is_not_told_a_username_they_are_not(
    temp_db: Database, access: Repository, actors: Actors, library: None
) -> None:
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, 'quillmoss', 0)",
        (USERNAME, SITE),
    )
    await temp_db.execute(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)", (ASSET, USERNAME)
    )
    await access.grant(ObjectType.ITEM, ASSET, actors.guest.id, Effect.SHARE)
    events = await history_of_asset(temp_db, access, await _as(access, actors.guest), ASSET)
    downloaded = next(one for one in events if one.kind == "downloaded")
    assert downloaded.what == "Sift downloaded this file from Pier Nine Media"
    assert [(one.kind, one.id) for one in downloaded.pieces if one.kind] == [("site", SITE)]


async def test_every_thing_a_line_names_is_scoped_on_the_way_out(
    temp_db: Database, access: Repository, actors: Actors, library: None
) -> None:
    """Whatever reader built the line, a thing the viewer may not be shown leaves it nameless:
    in the words, behind a fold and in a line's detail, with its kind said once."""
    line = said(
        "Sift filed this from the site ",
        thing("site", SITE, "Pier Nine Media"),
        " for ",
        thing("download", "d1", "Pier Nine Media"),
        Piece(text="1 more", rest=said(", ", thing("folder", FOLDER, "Sandbar Runways"))),
    )
    group = Detail(kind="site", words="the site", links=(Link("site", SITE, "Pier Nine Media"),))
    event = Event(
        at=0, actor=Actor.SIFT, actor_name=None, kind="filed", pieces=line, detail=(group,)
    )

    guest = await _as(access, actors.guest)
    out = (await unshown_said(temp_db, access, guest, [event]))[0]
    assert out.what == "Sift filed this from a Site for a download1 more"
    assert _leaks([out], {SITE, FOLDER}) == []
    assert [(one.kind, one.id, one.name) for one in out.detail[0].links] == [("", "", "a Site")]

    for open_ in (False, True):
        admin = await _as(access, actors.admin, vault_open=open_)
        assert await unshown_said(temp_db, access, admin, [event]) == [event]


@pytest.mark.parametrize("thread", ["person", "tag"])
async def test_a_person_s_and_an_entity_s_thread_leave_through_the_same_door(
    temp_db: Database,
    access: Repository,
    actors: Actors,
    library: None,
    monkeypatch: pytest.MonkeyPatch,
    thread: str,
) -> None:
    from sift.kernel.access import history_entity, history_person

    line = said("Sift filed them under ", thing("site", SITE, "Pier Nine Media"))
    event = Event(at=1, actor=Actor.SIFT, actor_name=None, kind="filed", pieces=line)

    async def one_line(*args: object, **kwargs: object) -> list[Event]:
        return [event]

    monkeypatch.setattr(history_person, "_person_ledger", one_line)
    monkeypatch.setattr(history_entity, "_ledger_events", one_line)
    await temp_db.execute("INSERT INTO tags (id, name, created_at) VALUES ('tag-1', 'poolside', 0)")

    async def said_to(viewer: Viewer) -> list[str]:
        if thread == "person":
            events = await history_of_person(temp_db, viewer, PERSON, access=access)
        else:
            events = await history_of_tag(temp_db, viewer, "tag-1")
        return [one.what for one in events if one.kind == "filed"]

    assert await said_to(await _as(access, actors.guest)) == ["Sift filed them under a Site"]
    assert await said_to(actors.admin) == ["Sift filed them under Pier Nine Media"]


async def test_a_count_whose_page_is_an_admin_s_keeps_its_words_and_loses_its_way_there(
    temp_db: Database, access: Repository, actors: Actors, library: None
) -> None:
    line = said(
        "Sift found ",
        thing("face_pile", "pile-1", "8 faces"),
        " and matched ",
        thing("faces", PERSON, "3 more faces", href="/organize/known-people/x?show=matched"),
    )
    event = Event(at=0, actor=Actor.SIFT, actor_name=None, kind="face_run", pieces=line)

    out = (await unshown_said(temp_db, access, await _as(access, actors.guest), [event]))[0]
    assert out.what == "Sift found 8 faces and matched 3 more faces"
    assert [(one.kind, one.id, one.href) for one in out.pieces] == [(None, None, None)]

    for open_ in (False, True):
        admin = await _as(access, actors.admin, vault_open=open_)
        assert await unshown_said(temp_db, access, admin, [event]) == [event]
