# SPDX-License-Identifier: AGPL-3.0-or-later
"""A History line names a Site, tag or username only to a viewer who may be shown it."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import replace

import pytest

import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Effect, ObjectType, Repository, Viewer
from sift.kernel.access.history import history_of_asset
from sift.kernel.access.history_boxes import filled_named
from sift.kernel.access.sentences import FilledField, Line, Piece
from sift.kernel.db import Database
from sift.kernel.ledger import Actor as ActorOf
from sift.kernel.ledger import Object, record_event
from sift.kernel.tests.test_history_entity import (
    BOX,
    LINKED_AT,
    SITE,
    USERNAME,
    link_site,
    make_file,
    make_site,
    make_username,
)
from sift.kernel.vocabulary import Subject as LedgerSubject
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio

SHARED = "01HX0000000000000000000881"
NETWORK = "01HX0000000000000000000882"
TAG = "01HX0000000000000000000883"
PERSON = "01HX0000000000000000000884"
JOINED = "01HX0000000000000000000885"
UNSEEN = "01HX0000000000000000000886"


@pytest.fixture
async def library(temp_db: Database, access: Repository, actors: Actors) -> None:
    """A Site, a person and a file the guest is given; a network, tag and username they are not."""
    await make_site(temp_db)
    await make_username(temp_db)
    await make_file(temp_db, SHARED)
    await temp_db.execute(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)", (SHARED, USERNAME)
    )
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Delphine Ostrow', 0)", (PERSON,)
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (SHARED, PERSON)
    )
    await temp_db.execute("INSERT INTO sites (id, name) VALUES (?, 'Harbour Network')", (NETWORK,))
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, 'jettyone', 0)",
        (JOINED, NETWORK),
    )
    await temp_db.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, 'poolside', 0)", (TAG,)
    )
    await temp_db.execute(
        "INSERT INTO site_tags (site_id, tag_id, added_at) VALUES (?, ?, ?)", (SITE, TAG, LINKED_AT)
    )
    await link_site(temp_db)
    await temp_db.execute(
        "UPDATE site_stash_box_links SET payload = ? WHERE site_id = ?",
        ('[{"fields": {"parent": "harbour network", "tags": ["poolside"]}}]', SITE),
    )
    async with temp_db.write() as connection:
        made = await record_event(
            connection,
            actor=ActorOf.user(actors.admin.id),
            verb="linked",
            subject=LedgerSubject(kind="username", id=JOINED, name="jettyone"),
            object=Object(kind="person", id=PERSON, name="Delphine Ostrow"),
        )
        await connection.execute(
            "UPDATE workbench_decisions SET decided_at = ? WHERE id = ?", (LINKED_AT, made)
        )
    await access.grant(ObjectType.ITEM, SHARED, actors.guest.id, Effect.SHARE)


def _pieces(line: Line) -> Iterator[Piece]:
    for one in line:
        yield one
        yield from _pieces(one.rest)


def _things(fields: Sequence[FilledField] | None) -> set[tuple[str | None, str | None, str]]:
    return {
        (one.kind, one.id, one.text)
        for field in fields or ()
        for value in field.values
        for one in _pieces(value)
    }


async def _as(access: Repository, viewer: Viewer, *, vault_open: bool = False) -> Viewer:
    loaded = await access.load_viewer(viewer.id)
    assert loaded is not None
    return replace(loaded, show_hidden=True) if vault_open else loaded


_ADMINS_NAMES = {
    ("site", NETWORK, "Harbour Network"),
    ("tag", TAG, "poolside"),
    ("username", JOINED, "jettyone"),
}


async def _box_said(
    database: Database, viewer: Viewer | None
) -> set[tuple[str | None, str | None, str]]:
    site = await filled_named(
        database, "site", SITE, BOX, at=LINKED_AT, stored='{"parent": 1, "tags": 1}', viewer=viewer
    )
    person = await filled_named(
        database, "person", PERSON, BOX, at=LINKED_AT, stored='{"accounts": 1}', viewer=viewer
    )
    return _things(site) | _things(person)


async def test_a_box_line_names_nothing_the_guest_may_not_be_shown(
    temp_db: Database, access: Repository, actors: Actors, library: None
) -> None:
    for open_ in (False, True):
        guest = await _as(access, actors.guest, vault_open=open_)
        said = await _box_said(temp_db, guest)
        assert not said & _ADMINS_NAMES
        assert {"harbour network", "a tag", "a username"} <= {text for _, _, text in said}
        assert not {one for one in said if one[1] in (NETWORK, TAG, JOINED)}


async def test_a_box_line_names_the_same_for_an_admin_vault_shut_or_open(
    temp_db: Database, access: Repository, actors: Actors, library: None
) -> None:
    assert await _box_said(temp_db, None) >= _ADMINS_NAMES
    for open_ in (False, True):
        admin = await _as(access, actors.admin, vault_open=open_)
        assert await _box_said(temp_db, admin) == await _box_said(temp_db, None)


async def test_a_box_line_names_what_the_guest_is_given(
    temp_db: Database, access: Repository, actors: Actors, library: None
) -> None:
    given = "01HX0000000000000000000887"
    await make_file(temp_db, given)
    await temp_db.execute(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)", (given, JOINED)
    )
    await temp_db.execute("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (given, TAG))
    await access.grant(ObjectType.ITEM, given, actors.guest.id, Effect.SHARE)

    assert await _box_said(temp_db, await _as(access, actors.guest)) >= _ADMINS_NAMES


async def _removed_lines(database: Database, access: Repository, viewer: Viewer) -> list[Line]:
    events = await history_of_asset(database, access, viewer, SHARED)
    return [one.pieces for one in events if one.kind == "removed"]


async def test_a_file_s_history_names_no_person_or_tag_the_guest_may_not_be_shown(
    temp_db: Database, access: Repository, actors: Actors, library: None
) -> None:
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Fenn Marchetti', 0)", (UNSEEN,)
    )
    async with temp_db.write() as connection:
        for thing in (
            Object(kind="person", id=UNSEEN, name="Fenn Marchetti"),
            Object(kind="tag", id=TAG, name="poolside"),
        ):
            await record_event(
                connection,
                actor=ActorOf.user(actors.admin.id),
                verb="unlinked",
                subject=LedgerSubject(kind="asset", id=SHARED),
                object=thing,
            )

    for open_ in (False, True):
        lines = await _removed_lines(
            temp_db, access, await _as(access, actors.guest, vault_open=open_)
        )
        texts = {"".join(one.text for one in line) for line in lines}
        assert texts == {
            "Another user removed a person from this file",
            "Another user removed a tag from this file",
        }
        assert not [one for line in lines for one in _pieces(line) if one.id in (UNSEEN, TAG)]

        lines = await _removed_lines(
            temp_db, access, await _as(access, actors.admin, vault_open=open_)
        )
        assert {(one.kind, one.id, one.text) for line in lines for one in _pieces(line)} >= {
            ("person", UNSEEN, "Fenn Marchetti"),
            ("tag", TAG, "poolside"),
        }
