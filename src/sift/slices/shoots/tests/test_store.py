# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rows behind a proposed shoot, against a real database carrying Sift's own schema.

A real one rather than a hand-built set of tables, for the reason the plan gate uses one: what these
statements mean depends on the foreign keys and the indexes that actually ship, and a test that
built its own would be checking a schema nobody runs.

Pointed at the three things the tables exist to promise: a pass replaces a creator's proposals
rather than piling them up, a refusal is remembered against the pictures, and a proposal that has
been turned into something is off the list without being deleted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel.db import Database
from sift.slices.shoots.store import Proposal, Shoot, Store

pytestmark = [pytest.mark.integration]

_EPOCH = 1_700_000_000

_PERSON = "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)"
_ASSET = """
INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, added_at)
VALUES (?, ?, 'image', 10, ?, ?)
"""


async def _library(tmp_path: Path) -> Database:
    database = Database(tmp_path / "shoots.sqlite3")
    await database.connect()
    await database.initialize_schema()
    async with database.write() as connection:
        await connection.execute(_PERSON, ("person-1", "Wren Halloway", _EPOCH))
        for one in range(6):
            await connection.execute(
                _ASSET, (f"asset-{one}", f"identity-{one}", f"{one}.jpg", _EPOCH)
            )
    return database


@pytest.mark.asyncio
async def test_a_pass_replaces_what_the_last_one_proposed(tmp_path: Path) -> None:
    """A picture filed since the last pass is not part of the shoot any more, and a card asking
    about it would be asking about something that is no longer true."""
    database = await _library(tmp_path)
    try:
        store = Store(database, clock=lambda: _EPOCH)
        await store.replace_for(
            "person-1", "Wren Halloway", [Shoot(asset_ids=("asset-0", "asset-1", "asset-2"))]
        )
        await store.replace_for(
            "person-1", "Wren Halloway", [Shoot(asset_ids=("asset-3", "asset-4", "asset-5"))]
        )
        waiting, total = await store.waiting(limit=10)
        assert total == 1
        full = await store.one(waiting[0].id)
        assert full is not None
        assert full.asset_ids == ("asset-3", "asset-4", "asset-5")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_proposal_found_again_keeps_its_id(tmp_path: Path) -> None:
    """Every start runs a pass, and a pass over an unchanged library finds the same shoots: each
    keeps its id and when it was found, while one that changed is a new proposal and one not found
    again goes. An answered one is never found again (see the next test's link)."""
    database = await _library(tmp_path)
    try:
        store = Store(database, clock=lambda: _EPOCH)
        same = Shoot(asset_ids=("asset-0", "asset-1", "asset-2"))
        await store.replace_for(
            "person-1", "Wren Halloway", [same, Shoot(asset_ids=("asset-3", "asset-4"))]
        )
        before = {tuple(one.asset_ids): one.id for one in await _all(store)}

        later = Store(database, clock=lambda: _EPOCH + 60)
        await later.replace_for(
            "person-1",
            "Wren Halloway",
            [
                Shoot(asset_ids=("asset-2", "asset-1", "asset-0"), unnamed=frozenset({"asset-0"})),
                Shoot(asset_ids=("asset-3", "asset-4", "asset-5")),
            ],
        )
        after = await _all(later)

        assert len(after) == 2
        kept = next(one for one in after if one.id == before[same.asset_ids])
        assert kept.asset_ids == ("asset-2", "asset-1", "asset-0")
        assert kept.unnamed_ids == ("asset-0",)
        assert kept.found_at == _EPOCH
        assert before[("asset-3", "asset-4")] not in {one.id for one in after}
    finally:
        await database.close()


async def _all(store: Store) -> list[Proposal]:
    waiting, _ = await store.waiting(limit=10)
    found = [await store.one(one.id) for one in waiting]
    return [one for one in found if one is not None]


@pytest.mark.asyncio
async def test_a_refusal_is_remembered_against_the_pictures(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        store = Store(database, clock=lambda: _EPOCH)
        await store.replace_for(
            "person-1", "Wren Halloway", [Shoot(asset_ids=("asset-0", "asset-1", "asset-2"))]
        )
        waiting, _ = await store.waiting(limit=10)
        proposal = waiting[0]

        assert await store.refuse(proposal.id, ["asset-0", "asset-1"]) == 2
        # And the question goes off the board with the answer. A refusal that left the card up
        # would be a card asking something somebody has already answered.
        _, total = await store.waiting(limit=10)
        assert total == 0
        assert await store.one(proposal.id) is None

        # Again is not a second no: the standing no is already there.
        assert await store.refuse(proposal.id, ["asset-0"]) == 0
        assert await store.refused_among(["asset-0", "asset-2"]) == {"asset-0"}
        assert await store.refused_among([]) == set()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_proposal_that_was_made_into_something_is_off_the_list(tmp_path: Path) -> None:
    """Off the list and still on disk: the link is the record of what a press did, and a pass that
    re-ran would otherwise offer the shoot again beside what it already became."""
    database = await _library(tmp_path)
    try:
        store = Store(database, clock=lambda: _EPOCH)
        await store.replace_for(
            "person-1",
            "Wren Halloway",
            [Shoot(asset_ids=("asset-0", "asset-1", "asset-2"), unnamed=frozenset({"asset-2"}))],
        )
        waiting, _ = await store.waiting(limit=10)
        proposal = waiting[0]
        async with database.write() as connection:
            await store.link_on(
                connection,
                proposal_id=proposal.id,
                photo_set_id="set-1",
                decision_id="decision-1",
            )
        _, total = await store.waiting(limit=10)
        assert total == 0
        made = await store.made_from(proposal.id)
        assert made is not None
        assert made.photo_set_id == "set-1"
        assert made.decision_id == "decision-1"

        # And taking the decision back makes the shoot a question again rather than deleting it.
        assert await store.forget_link("set-1") == 1
        _, total = await store.waiting(limit=10)
        assert total == 1
        assert await store.made_from(proposal.id) is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_picture_carrying_nobody_is_marked_and_can_be_named(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        store = Store(database, clock=lambda: _EPOCH)
        await store.replace_for(
            "person-1",
            "Wren Halloway",
            [
                Shoot(
                    asset_ids=("asset-0", "asset-1", "asset-2"),
                    unnamed=frozenset({"asset-1", "asset-2"}),
                )
            ],
        )
        waiting, _ = await store.waiting(limit=10)
        full = await store.one(waiting[0].id)
        assert full is not None
        assert full.unnamed_ids == ("asset-1", "asset-2")
        assert await store.pictures_of(full.id) == ["asset-0", "asset-1", "asset-2"]

        await store.mark_named(full.id, ["asset-1"])
        again = await store.one(full.id)
        assert again is not None
        assert again.unnamed_ids == ("asset-2",)
        # The proposal itself stays: naming is not an answer to whether this is a shoot.
        assert again.pictures == 3
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_proposal_that_names_nothing_is_not_a_proposal(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        store = Store(database, clock=lambda: _EPOCH)
        assert await store.one("no-such-proposal") is None
        assert await store.made_from("no-such-proposal") is None
        assert await store.replace_for("person-1", "Wren Halloway", []) == []
        _, total = await store.waiting(limit=10)
        assert total == 0
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_proposal_under_the_floor_is_dissolved_and_a_bigger_one_is_left(
    tmp_path: Path,
) -> None:
    """When the floor rises, the cards written under the old one go.

    Both directions in one case, because the danger of a sweeping delete is what it takes with it:
    the small proposal goes, the one at the floor stays, and nothing is refused: the pictures are
    back in the pool and may be proposed again.
    """
    database = await _library(tmp_path)
    try:
        store = Store(database, clock=lambda: _EPOCH)
        await store.replace_for(
            "person-1",
            "Wren Halloway",
            [
                Shoot(asset_ids=("asset-0", "asset-1")),
                Shoot(asset_ids=("asset-2", "asset-3", "asset-4")),
            ],
        )

        assert await store.dissolve_under(3) == 1

        waiting, total = await store.waiting(limit=10)
        assert total == 1
        assert waiting[0].pictures == 3
        assert await store.refused_among(["asset-0", "asset-1"]) == set()
        # Nothing to drop is not a write, and answers nothing dropped.
        assert await store.dissolve_under(3) == 0
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_proposal_is_found_at_its_place_on_the_list(tmp_path: Path) -> None:
    """`from` on the Shoots page: a proposal's position is its place in the list the page reads,
    and a proposal that is off the list (answered, or never there) has none, so the page opens
    at the top rather than somewhere that no longer means anything."""
    database = await _library(tmp_path)
    try:
        store = Store(database, clock=lambda: _EPOCH)
        await store.replace_for(
            "person-1",
            "Wren Halloway",
            [
                Shoot(asset_ids=("asset-0", "asset-1")),
                Shoot(asset_ids=("asset-2", "asset-3")),
                Shoot(asset_ids=("asset-4", "asset-5")),
            ],
        )
        waiting, _ = await store.waiting(limit=10)
        # The same order the page is read in, ties on the clock included.
        assert [await store.position_of(one.id) for one in waiting] == [0, 1, 2]

        async with database.write() as connection:
            await store.link_on(
                connection,
                proposal_id=waiting[0].id,
                photo_set_id="set-1",
                decision_id="decision-1",
            )
        assert await store.position_of(waiting[0].id) is None
        assert await store.position_of(waiting[2].id) == 1
        assert await store.position_of("no-such-proposal") is None
    finally:
        await database.close()


async def test_a_schema_already_at_its_version_is_left_as_it_is() -> None:
    """Nothing is created twice: a database already holding the tables is not written to."""
    from sift.slices.shoots.schema import VERSION, initialize

    class Untouchable:
        async def execute(self, *args: object) -> None:
            raise AssertionError("a table was created again")

    await initialize(Untouchable(), VERSION)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_proposal_reads_its_creators_name_as_it_is_now(tmp_path: Path) -> None:
    """The pass wrote the creator's name at the time; a rename since reads at once, on the list
    and on the one proposal a press makes a Photo Set from, without waiting for another pass."""
    database = await _library(tmp_path)
    try:
        store = Store(database, clock=lambda: _EPOCH)
        await store.replace_for(
            "person-1", "Wren Halloway", [Shoot(asset_ids=("asset-0", "asset-1", "asset-2"))]
        )
        await database.execute("UPDATE people SET name = 'Wren Ashdown' WHERE id = 'person-1'")
        waiting, _ = await store.waiting(limit=10)
        assert [one.name for one in waiting] == ["Wren Ashdown"]
        full = await store.one(waiting[0].id)
        assert full is not None and full.name == "Wren Ashdown"
    finally:
        await database.close()
