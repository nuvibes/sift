# SPDX-License-Identifier: AGPL-3.0-or-later
"""A picture on Insights is addressed the way its own wall addresses it, token and all.

The token is held to the server's own check (`names_its_cover`), so the address Insights draws is
one the cover route answers with a week-long keep, and a token naming another picture is not.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast
from urllib.parse import urlsplit

import pytest
from starlette.requests import Request

from sift.kernel.access import AssetView, Repository, Role
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.covers import ChosenCover, names_its_cover
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.serving import face_version
from sift.kernel.wire import HistoryPiece
from sift.slices.insights.models import NamedRow, cover_of
from sift.slices.insights.naming import cover_token, covered_cards, known
from sift.slices.insights.recaps_models import RecapCard
from sift.testing.fixtures import World, build_world, create_user

pytestmark = pytest.mark.unit

STAMP = 7
FRAME = CoverFrame(x=0.1, y=0.2, w=0.5, h=0.5)


def asked(address: str) -> Request:
    query = urlsplit(address).query.encode()
    return Request({"type": "http", "method": "GET", "query_string": query, "headers": []})


def test_every_kind_a_card_pictures_has_an_address() -> None:
    """A recap's picture is tokened by the address its kind is served at, so a kind with no address
    would leave its picture bare and unreadable."""
    from sift.slices.insights.naming import _COVERED

    assert all(cover_of(kind, "x") is not None for kind in _COVERED.values())


@pytest.mark.parametrize(
    "chosen",
    [
        ChosenCover(upload_id="up-1"),
        ChosenCover(upload_id="up-1", frame=FRAME),
        ChosenCover(asset_id="a-1"),
        ChosenCover(asset_id="a-1", at_ms=4200),
        ChosenCover(asset_id="a-1", at_ms=4200, frame=FRAME),
    ],
)
def test_a_cover_token_is_the_one_its_route_keeps(chosen: ChosenCover) -> None:
    address = f"/api/people/p-1/cover?v={cover_token(STAMP, chosen)}"
    assert names_its_cover(asked(address), chosen, stamp=STAMP)
    # Another picture behind the same address is not promised the keep.
    other = ChosenCover(asset_id="a-2", at_ms=chosen.at_ms, frame=chosen.frame)
    assert not names_its_cover(asked(address), other, stamp=STAMP)


def test_an_entity_with_no_cover_carries_the_readers_stamp_alone() -> None:
    assert cover_token(STAMP, ChosenCover()) == face_version(STAMP)


@pytest.fixture
async def library(temp_db: Database, access: Repository) -> World:
    built = World(**{name: new_id() for name in World.__slots__})
    await build_world(temp_db, built)
    return built


async def _with_cover(database: Database, person_id: str, asset_id: str) -> None:
    async with database.write() as connection:
        await connection.execute(
            "UPDATE people SET cover_asset_id = ?, cover_at_ms = 900 WHERE id = ?",
            (asset_id, person_id),
        )


async def test_a_person_s_picture_is_addressed_as_its_wall_keeps_it(
    temp_db: Database, access: Repository, library: World
) -> None:
    admin = await create_user(temp_db, Role.ADMIN)
    await _with_cover(temp_db, library.person, library.solo)

    found = (await known(access, temp_db, admin, "person", [library.person]))[library.person]

    assert found.cover is not None
    assert found.cover.startswith(f"{cover_of('person', library.person)}?v=")
    chosen = ChosenCover(asset_id=library.solo, at_ms=900)
    assert names_its_cover(asked(found.cover), chosen, stamp=admin.cache_stamp)


class _OneFile:
    """The two reads `known` asks about a file, answering with one view."""

    def __init__(self, view: AssetView) -> None:
        self.view = view

    async def assets_of(self, viewer: object, ids: object) -> dict[str, AssetView]:
        return {self.view.asset.id: self.view}

    async def names_on_disk(self, viewer: object, ids: object) -> dict[str, str]:
        return {}


async def test_a_file_s_still_carries_its_art_token_and_a_concealed_one_none(
    temp_db: Database, access: Repository, library: World
) -> None:
    admin = await create_user(temp_db, Role.ADMIN)
    view = replace((await access.assets_of(admin, [library.solo]))[library.solo], art_version="t")

    one, concealed = _OneFile(view), _OneFile(replace(view, concealed=True))
    shown = await known(cast(Repository, one), temp_db, admin, "asset", [library.solo])
    hidden = await known(cast(Repository, concealed), temp_db, admin, "asset", [library.solo])

    assert shown[library.solo].cover == f"{cover_of('asset', library.solo)}?v=t"
    assert hidden[library.solo].cover == cover_of("asset", library.solo)


def _card(kind: str, key: str) -> RecapCard:
    piece = HistoryPiece(text="it", kind=kind, id=key)
    return RecapCard(
        kind="top_person",
        id="c-1",
        statement=[piece],
        cover=cover_of(kind, key),
        rows=[NamedRow(piece=piece, value=1, unit="ms", cover=cover_of(kind, key))],
    )


async def test_an_opened_recap_s_pictures_carry_the_readers_token(
    temp_db: Database, access: Repository, library: World
) -> None:
    admin = await create_user(temp_db, Role.ADMIN)
    guest = await create_user(temp_db, Role.GUEST)
    bare = cover_of("person", library.person)

    (mine,) = await covered_cards(access, temp_db, admin, [_card("person", library.person)])
    (theirs,) = await covered_cards(access, temp_db, guest, [_card("person", library.person)])

    person = (await access.visible_people(admin, [library.person]))[library.person]
    chosen = ChosenCover(
        asset_id=person.cover_asset_id,
        at_ms=person.cover_at_ms,
        upload_id=person.cover_upload_id,
        frame=person.cover_frame,
    )
    tokened = f"{bare}?v={cover_token(admin.cache_stamp, chosen)}"
    assert (mine.cover, mine.rows[0].cover) == (tokened, tokened)
    # A thing this reader may not see is never asked about: its address stays bare, answered
    # the careful way, as any address without a token is.
    assert (theirs.cover, theirs.rows[0].cover) == (bare, bare)
