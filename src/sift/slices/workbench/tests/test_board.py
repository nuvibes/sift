# SPDX-License-Identifier: AGPL-3.0-or-later
"""The board: what registered, and which half of the screen each one is drawn in."""

from __future__ import annotations

import pytest

from sift.kernel.access import Repository, Viewer
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.serving import art_version, face_version
from sift.kernel.workbench import (
    ASSET,
    FACE,
    Band,
    Preview,
    Summary,
    Workbench,
)
from sift.slices.workbench.router import _pictures, _queue, _Tokens, _tokens
from sift.slices.workbench.service import Board, WorkbenchService
from sift.slices.workbench.tests.conftest import FakeQueue
from sift.testing.fixtures import World


async def test_a_registered_queue_appears(
    service: WorkbenchService, workbench: Workbench, admin: Viewer
) -> None:
    workbench.register(FakeQueue(name="folders", count=3))

    board = await service.board(admin)

    assert [one.name for one in board.queues] == ["folders"]
    assert board.queues[0].count == 3


async def test_a_queue_with_nothing_behind_it_does_not(
    service: WorkbenchService, workbench: Workbench, admin: Viewer
) -> None:
    """A queue with nothing behind it is absent, not an empty panel."""
    workbench.register(FakeQueue(name="folders", count=3))
    workbench.register(FakeQueue(name="faces", count=9, present=False))

    board = await service.board(admin)

    assert [one.name for one in board.queues] == ["folders"]


async def test_an_empty_queue_still_appears(
    service: WorkbenchService, workbench: Workbench, admin: Viewer
) -> None:
    """The opposite case, and the distinction the whole screen rests on. Nothing waiting is the
    GOAL state, and it has to read as finished rather than as absent."""
    workbench.register(FakeQueue(name="folders", count=0))

    board = await service.board(admin)

    assert [one.name for one in board.queues] == ["folders"]
    assert board.queues[0].count == 0


async def test_the_band_and_the_group_reach_the_wire(
    service: WorkbenchService, workbench: Workbench, admin: Viewer
) -> None:
    """The band and the group reach the wire; the band is the only thing the screen sorts by."""
    workbench.register(FakeQueue(name="folders", count=3))
    workbench.register(FakeQueue(name="ignored", count=90, band=Band.RECORD, group="faces"))

    board = await service.board(admin)

    assert [(one.name, one.band, one.group) for one in board.queues] == [
        ("folders", "decision", None),
        ("ignored", "record", "faces"),
    ]


async def test_a_queue_keeps_the_order_it_registered_in(
    service: WorkbenchService, workbench: Workbench, admin: Viewer
) -> None:
    workbench.register(FakeQueue(name="folders"))
    workbench.register(FakeQueue(name="unidentified"))
    workbench.register(FakeQueue(name="ignored"))

    board = await service.board(admin)

    assert [one.name for one in board.queues] == ["folders", "unidentified", "ignored"]


def test_claiming_a_name_twice_is_a_bug_rather_than_an_override(workbench: Workbench) -> None:
    workbench.register(FakeQueue(name="folders"))

    with pytest.raises(ValueError, match="already registered"):
        workbench.register(FakeQueue(name="folders"))


async def test_the_table_is_made_once_rather_than_on_every_boot(tmp_path: object) -> None:
    """The initializer does nothing when the version on disk is current."""
    from sift.slices.workbench.schema import VERSION, initialize_workbench

    class Refuses:
        async def execute(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("a database already at this version must not be rebuilt")

    # The declared version, so a new step is never run by passing a stale number.
    await initialize_workbench(Refuses(), VERSION)  # type: ignore[arg-type]


async def test_a_table_from_before_the_device_columns_gains_them_once(temp_db: Database) -> None:
    """A library written before a decision recorded which device made it gains the two columns
    on its next boot, and a step replayed over a table that already has them adds nothing."""
    from sift.slices.workbench.schema import initialize_workbench

    async with temp_db.write() as connection:
        await connection.execute(
            "CREATE TABLE workbench_decisions (id TEXT PRIMARY KEY, queue TEXT NOT NULL,"
            " title TEXT NOT NULL, detail TEXT NOT NULL, payload TEXT NOT NULL,"
            " decided_at INTEGER NOT NULL)"
        )
        await initialize_workbench(connection, 15)
        await initialize_workbench(connection, 15)
    columns = [
        str(row["name"])
        for row in await temp_db.fetch_all(
            "SELECT name FROM pragma_table_info('workbench_decisions')"
        )
    ]
    assert columns.count("client_kind") == 1
    assert columns.count("device_id") == 1


async def test_the_verb_and_the_purpose_reach_the_wire(
    service: WorkbenchService, workbench: Workbench, admin: Viewer
) -> None:
    """The verb, both wordings of the count, and the queue class's purpose reach the wire."""
    workbench.register(
        FakeQueue(
            name="folders",
            count=3,
            verb="folders to name",
            verb_one="folder to name",
            purpose="Folders whose names may be a person, for you to confirm.",
        )
    )
    workbench.register(FakeQueue(name="filed", band=Band.RECORD, group="folders", purpose=None))

    board = await service.board(admin)

    assert board.queues[0].verb == "folders to name"
    assert board.queues[0].verb_one == "folder to name"
    assert board.queues[0].purpose == "Folders whose names may be a person, for you to confirm."
    # A record is never a card, so it says nothing about what a card is for.
    assert board.queues[1].purpose is None


class TestTheTokenOnACardsPicture:
    """A card picture's token is minted once for the whole reply."""

    def test_a_file_carries_the_token_its_own_wall_addresses_it_by(self) -> None:
        """The same token the grid puts on the same file, so the card and the wall ask for one
        address and the browser holds one copy of the picture."""
        art = _Tokens(face="face-token", files={"asset-1": "art-1"})

        assert art.of(Preview(kind=ASSET, id="asset-1")) == "art-1"

    def test_a_face_carries_the_account_stamp_and_needs_no_read(self) -> None:
        """A face crop carries the account stamp and needs no read."""
        art = _Tokens(face="face-token", files={})

        assert art.of(Preview(kind=FACE, id="track-1")) == "face-token"

    def test_a_file_this_account_may_not_see_carries_none(self) -> None:
        """The read is scoped, so a file that did not come back is addressed bare and asked about
        on every use. Slower, never wrong."""
        art = _Tokens(face="face-token", files={})

        assert art.of(Preview(kind=ASSET, id="asset-1")) is None

    def test_a_kind_this_version_has_no_token_for_carries_none(self) -> None:
        """An unknown kind carries no token."""
        art = _Tokens(face="face-token", files={"thing-1": "art-1"})

        assert art.of(Preview(kind="something-new", id="thing-1")) is None


async def test_the_token_a_card_sends_is_the_one_the_file_itself_is_addressed_by(
    access: Repository, temp_db: Database, world: World, admin: Viewer
) -> None:
    """A card's token for a file is the one the grid uses for that file."""
    await temp_db.execute(
        "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, params, content_hash,"
        " created_at) VALUES (?, ?, 'thumb', 'a/b.jpg', '{}', 'digest-of-the-still', 0)",
        (new_id(), world.solo),
    )

    art = await _tokens(access, admin, [Preview(kind=ASSET, id=world.solo)])

    assert art.of(Preview(kind=ASSET, id=world.solo)) == art_version(
        "thumb:digest-of-the-still", admin.cache_stamp
    )
    # And a face needs no read at all: the crop is never rewritten, so its whole token is the
    # stamp that says what this user may see.
    assert art.of(Preview(kind=FACE, id="track-1")) == face_version(admin.cache_stamp)


async def test_a_file_nobody_asked_about_is_not_read(
    access: Repository, world: World, admin: Viewer
) -> None:
    """One read for the whole answer, over the files the answer is actually about. A board that
    asked per card would be a read per queue on the slowest screen in the application."""
    art = await _tokens(access, admin, [Preview(kind=FACE, id="track-1")])

    assert art.files == {}


def test_every_picture_on_the_board_is_reached_before_the_tokens_are_minted() -> None:
    """Every picture on the board is reached before the tokens are minted."""
    strip = Preview(kind=ASSET, id="on-the-strip")
    board = Board(
        queues=[
            Summary(
                name="folders",
                title="Folders",
                decision="Say whether a folder is somebody.",
                verb="folders to name",
                verb_one="folder to name",
                icon="folder",
                count=1,
                preview=(strip,),
            )
        ],
    )

    assert [one.id for one in _pictures(board)] == ["on-the-strip"]


def test_a_card_reaches_the_wire_as_what_it_shows_and_nothing_it_does_not() -> None:
    """A card crosses the wire with its stills and purpose and no question."""
    summary = Summary(
        name="folders",
        title="Folders to review",
        decision="Say whether a folder is the person its name suggests.",
        verb="folders to name",
        verb_one="folder to name",
        icon="folder",
        count=3,
        purpose="Folders whose names may be a person, for you to confirm.",
        preview=(Preview(kind=ASSET, id="asset-1", href="/asset/asset-1"),),
    )

    # The file is one this viewer may be shown (the way out draws no other) and carries no
    # token, which is a reply minted with nothing kept.
    view = _queue(summary, _Tokens(face="", files={"asset-1": None}))

    assert view.verb == "folders to name"
    assert view.purpose == "Folders whose names may be a person, for you to confirm."
    assert [one.id for one in view.preview] == ["asset-1"]
    # Bare because the reply was minted with nothing kept; what decides that is above.
    assert [one.art for one in view.preview] == [None]
    assert "first" not in view.model_dump()
