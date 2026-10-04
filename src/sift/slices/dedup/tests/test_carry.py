# SPDX-License-Identifier: AGPL-3.0-or-later
"""Carrying an attribution onto the copies of a file.

An EXACT copy (the same bytes) cannot be sitting in the library unattributed while its twin
carries a person and a site: `assets.identity` is unique, so the same bytes are one asset with
several locations and every copy of them already carries whatever is on it.

What is real is the file that merely LOOKS the same. These tests are about that: a group whose
files are bit-for-bit identical on the fingerprint, where one of them knows something the others
do not.

The one that matters most is `test_a_group_that_disagrees_is_left_alone`. A tight group whose
files are all attributed can name people who do not overlap at all, so a carry that resolved a disagreement by copying one answer over the other would be writing
somebody's name onto a file that another decision says is not theirs.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from sift.kernel.access import Repository, Viewer
from sift.kernel.access.catalog import (
    COPIED,
    MADE_BY_A_PERSON,
    Carried,
    attribute_assets_on,
    attribution_of_files,
    create_person_on,
    file_assets_under_site_on,
)
from sift.kernel.access.history import history_of_asset
from sift.kernel.content.duplicates import DuplicateReads
from sift.kernel.db import Database
from sift.slices.dedup.grouping import Group
from sift.slices.dedup.queue import CARRY_NAME, CarriedAttributions
from sift.slices.dedup.service import CARRY_QUEUE, CarryOffer, DedupService
from sift.slices.dedup.tests.conftest import Library, Recorder
from sift.slices.dedup.tests.test_dedup import dials, set_fingerprint
from sift.slices.workbench.store import Store
from sift.testing.fixtures import FakeClock

pytestmark = pytest.mark.integration


@pytest.fixture
async def recorded(temp_db: Database) -> Store:
    await temp_db.initialize_schema()
    return Store(temp_db)


@pytest.fixture
async def reviewed(
    temp_db: Database,
    reads: DuplicateReads,
    recorder: Recorder,
    clock: FakeClock,
    recorded: Store,
) -> DedupService:
    """The service wired to a real record, and to a remover that removes nothing."""
    await temp_db.initialize_schema()
    return DedupService(temp_db, reads, recorder, clock=clock.now, recorder=recorded)


#: The same fingerprint on both files, which is the only setting this feature reads at.
_SAME = "0f0f0f0f0f0f0f0f"
#: One bit out, which is a near copy at the reader's dial and NOT a copy at the tightest one.
_NEAR = "0f0f0f0f0f0f0f0e"


async def _twins(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any, second: str = _SAME
) -> Group:
    """Two files the scan reads as bit-for-bit identical on the fingerprint, as their group."""
    one = await add_file(managed, "one.jpg", "accepted.jpg")
    two = await add_file(managed, "two.png", "accepted.png")
    await set_fingerprint(temp_db, one.asset.id, _SAME)
    await set_fingerprint(temp_db, two.asset.id, second)
    await service.scan()
    groups = await service.groups(dials())
    assert len(groups) == 1
    return groups[0]


async def _offer_on(service: DedupService, group: Group) -> CarryOffer | None:
    """The offer covering this group, or None where there is none.

    Read through `carry_offers`, which is what the screen reads: there is no per-group form and
    must not be one, because a group on the queue is clustered at the READER's dial while an offer
    is read bit-for-bit, so the two are regularly not the same set of files.
    """
    wanted = set(group.ids)
    return next(
        (one for one in await service.carry_offers(dials()) if set(one.ids) <= wanted), None
    )


async def _name_somebody(db: Database, asset_id: str, name: str = "Ada Lumen") -> str:
    """Put a person on one file, the way a folder read does. Returns the person."""
    async with db.write() as connection:
        person_id = await create_person_on(connection, name, made=MADE_BY_A_PERSON)
        assert person_id is not None
        await attribute_assets_on(
            connection, asset_ids=[asset_id], person_id=person_id, source="folder"
        )
    return person_id


async def _file_under_a_site(db: Database, asset_id: str, site: str = "Hollowgrain") -> None:
    """File one file under a site with nobody named, the way the filename pass does."""
    async with db.write() as connection:
        await file_assets_under_site_on(
            connection,
            asset_ids=[asset_id],
            site=site,
            source="filename",
            made=MADE_BY_A_PERSON,
        )


async def test_the_offer_says_what_it_would_write_before_anything_is_written(
    temp_db: Database, reviewed: DedupService, managed: Library, add_file: Any
) -> None:
    """The count comes first. An admin pressing this is entitled to know how many files it lands
    on, and the number they are shown is the same computation that then does the writing."""
    group = await _twins(temp_db, reviewed, managed, add_file)
    await _name_somebody(temp_db, group.ids[0])

    offer = await _offer_on(reviewed, group)

    assert offer is not None
    assert offer.source == group.ids[0]
    assert offer.targets == (group.ids[1],), "only the file that has nothing on it"
    assert offer.files == 1
    assert [one.name for one in offer.carried] == ["Ada Lumen"]
    assert offer.source_name == "one.jpg", "the file the sentence will name"


async def test_nothing_is_offered_where_there_is_nothing_to_carry(
    temp_db: Database, reviewed: DedupService, managed: Library, add_file: Any
) -> None:
    """Two files that both know nothing, and two that both know the same thing, are both nothing
    to offer: the first has nothing to carry and the second has nowhere to carry it."""
    group = await _twins(temp_db, reviewed, managed, add_file)
    assert await _offer_on(reviewed, group) is None

    person_id = await _name_somebody(temp_db, group.ids[0])
    async with temp_db.write() as connection:
        await attribute_assets_on(
            connection, asset_ids=[group.ids[1]], person_id=person_id, source="folder"
        )
    assert await _offer_on(reviewed, group) is None


async def test_a_group_that_disagrees_is_left_alone(
    temp_db: Database, reviewed: DedupService, managed: Library, add_file: Any
) -> None:
    """Three copies, two of them naming different people. Nothing is offered.

    A fingerprint match at distance nought is not proof of the same content (that is the whole
    reason the review queue exists), so a disagreement between two copies is not Sift's to settle
    by copying one of the answers over the other.
    """
    third = await add_file(managed, "three.webp", "accepted.webp")
    group = await _twins(temp_db, reviewed, managed, add_file)
    await set_fingerprint(temp_db, third.asset.id, _SAME)
    await reviewed.scan()
    (group,) = await reviewed.groups(dials())
    assert len(group.ids) == 3

    await _name_somebody(temp_db, group.ids[0], "Ada Lumen")
    await _name_somebody(temp_db, group.ids[1], "Briar Vance")

    assert await _offer_on(reviewed, group) is None


async def test_a_carry_writes_the_rows_and_says_where_they_came_from(
    temp_db: Database,
    reviewed: DedupService,
    recorded: Store,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The person and the site both travel, marked as copies, with a receipt per file."""
    group = await _twins(temp_db, reviewed, managed, add_file)
    await _name_somebody(temp_db, group.ids[0])
    await _file_under_a_site(temp_db, group.ids[0])
    offer = await _offer_on(reviewed, group)
    assert offer is not None

    assert await reviewed.carry(offer, actor=admin) == 1

    gained = (await attribution_of_files(temp_db, [group.ids[1]]))[group.ids[1]]
    assert sorted(one.kind for one in gained) == ["person", "username"]
    rows = await temp_db.fetch_all(
        "SELECT source FROM asset_people WHERE asset_id = ?", (group.ids[1],)
    )
    assert [row["source"] for row in rows] == [COPIED], "marked as a copy, not as a folder read"

    receipts = await temp_db.fetch_all(
        "SELECT queue, title FROM workbench_decisions WHERE queue = ?", (CARRY_QUEUE,)
    )
    assert len(receipts) == 1, "one receipt per FILE, and one file gained something"
    assert str(receipts[0]["title"]).startswith("Sift named Ada Lumen here, carried from")
    assert "carried from one.jpg" in str(receipts[0]["title"])


async def test_the_file_s_history_says_it_in_ONE_line_with_the_undo_on_it(
    temp_db: Database,
    reviewed: DedupService,
    recorded: Store,
    access: Repository,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The whole point of writing the receipt in the pane's own words.

    `history._one_line_per_act` drops a naming line into any decision whose title CONTAINS it, so
    the file gets one line (the fuller sentence, with the person still linked and this receipt's
    Undo on it), rather than two lines saying nearly the same thing. If the two ever drift apart,
    this is where it shows: the count goes to two.
    """
    group = await _twins(temp_db, reviewed, managed, add_file)
    await _name_somebody(temp_db, group.ids[0])
    offer = await _offer_on(reviewed, group)
    assert offer is not None
    await reviewed.carry(offer, actor=admin)

    events = await history_of_asset(temp_db, access, admin, group.ids[1])

    said = [one for one in events if "Ada Lumen" in one.what]
    assert len(said) == 1, "one act, one line"
    assert said[0].what == "Sift named Ada Lumen here, carried from one.jpg"
    assert said[0].undo is not None, "and the Undo is this file's own"
    assert [link.kind for link in said[0].links] == ["person"], "the name is still a way to her"


async def test_a_carry_never_overwrites_what_a_file_already_says(
    temp_db: Database,
    reviewed: DedupService,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """A row the file already carried stays its own decision, and is not counted as carried.

    The insert keeps the first answer, so an undo built from what the carry was ASKED to write
    would detach a row somebody else put there. It is built from what LANDED instead.
    """
    group = await _twins(temp_db, reviewed, managed, add_file)
    person_id = await _name_somebody(temp_db, group.ids[0])
    await _file_under_a_site(temp_db, group.ids[0])
    offer = await _offer_on(reviewed, group)
    assert offer is not None
    # And now somebody names the same person on the copy themselves, before the press lands.
    async with temp_db.write() as connection:
        await attribute_assets_on(
            connection, asset_ids=[group.ids[1]], person_id=person_id, source=None
        )

    assert await reviewed.carry(offer, actor=admin) == 1, "the site still landed"

    rows = await temp_db.fetch_all(
        "SELECT source FROM asset_people WHERE asset_id = ?", (group.ids[1],)
    )
    assert [row["source"] for row in rows] == [None], "their own row, untouched"


async def test_taking_one_carry_back_leaves_the_others_standing(
    temp_db: Database,
    reviewed: DedupService,
    recorded: Store,
    access: Repository,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """Undo is per file, because each copy is a separate claim.

    Somebody who decides one of three copies is not hers takes that one back; the other two are
    decisions they have not disagreed with, and a single receipt over all three could not say so.
    """
    third = await add_file(managed, "three.webp", "accepted.webp")
    group = await _twins(temp_db, reviewed, managed, add_file)
    await set_fingerprint(temp_db, third.asset.id, _SAME)
    await reviewed.scan()
    (group,) = await reviewed.groups(dials())
    await _name_somebody(temp_db, group.ids[0])
    offer = await _offer_on(reviewed, group)
    assert offer is not None
    assert await reviewed.carry(offer, actor=admin) == 2

    receipts = await temp_db.fetch_all(
        "SELECT id, payload FROM workbench_decisions WHERE queue = ? ORDER BY id", (CARRY_QUEUE,)
    )
    assert len(receipts) == 2
    undo = CarriedAttributions(reviewed, access)
    assert undo.name == CARRY_NAME
    assert await undo.reverse(admin, str(receipts[0]["id"]), str(receipts[0]["payload"])) is True

    left = await attribution_of_files(temp_db, [one for one in group.ids])
    assert sum(1 for one in group.ids if left[one]) == 2, "the source and the copy not taken back"


async def test_an_undo_leaves_a_row_that_was_not_this_carry_s(
    temp_db: Database,
    reviewed: DedupService,
    access: Repository,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The delete is guarded on the source word, so a row somebody wrote by hand survives it.

    Without that guard an undo would remove an attribution a person made, on the strength of a
    record saying a carry once named the same person.
    """
    group = await _twins(temp_db, reviewed, managed, add_file)
    person_id = await _name_somebody(temp_db, group.ids[0])
    async with temp_db.write() as connection:
        await attribute_assets_on(
            connection, asset_ids=[group.ids[1]], person_id=person_id, source=None
        )

    undo = CarriedAttributions(reviewed, access)
    assert (
        await reviewed.uncarry(
            asset_id=group.ids[1],
            carried=[Carried(kind="person", id=person_id, name="Ada Lumen")],
        )
        is False
    )
    assert await undo.reverse(admin, "nothing", "not json at all") is False

    rows = await temp_db.fetch_all(
        "SELECT person_id FROM asset_people WHERE asset_id = ?", (group.ids[1],)
    )
    assert [row["person_id"] for row in rows] == [person_id]


async def test_the_sweep_counts_only_the_groups_that_are_copies(
    temp_db: Database, reviewed: DedupService, managed: Library, add_file: Any
) -> None:
    """The backfill's count, and the one thing it must not do: read at the reader's dial.

    The pair here is one bit apart, which the queue's default setting shows as a near duplicate,
    and a carry reads only at the tightest setting there is, so this counts nothing.
    """
    group = await _twins(temp_db, reviewed, managed, add_file, second=_NEAR)
    await _name_somebody(temp_db, group.ids[0])

    assert await reviewed.carry_offers(dials()) == []

    identical = await add_file(managed, "four.webp", "accepted.webp")
    await set_fingerprint(temp_db, identical.asset.id, _SAME)
    await reviewed.scan()

    offers = await reviewed.carry_offers(dials())
    assert [offer.files for offer in offers] == [1]
    assert offers[0].targets == (identical.asset.id,)
    assert offers[0].source_name == "one.jpg"


async def test_a_carry_with_no_record_to_write_to_still_carries(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any, admin: Viewer
) -> None:
    """The record is how somebody sees what was decided, not what makes the deciding work: a
    service handed no recorder writes the attribution and no receipt."""
    group = await _twins(temp_db, service, managed, add_file)
    await _name_somebody(temp_db, group.ids[0])
    offer = await _offer_on(service, group)
    assert offer is not None

    assert await service.carry(offer, actor=admin) == 1

    left = await attribution_of_files(temp_db, list(group.ids))
    assert all(left[one] for one in group.ids)
    receipts = await temp_db.fetch_all(
        "SELECT id FROM workbench_decisions WHERE queue = ?", (CARRY_QUEUE,)
    )
    assert receipts == []


async def test_an_offer_with_nowhere_to_carry_or_nothing_to_carry_writes_nothing(
    temp_db: Database, reviewed: DedupService, managed: Library, add_file: Any, admin: Viewer
) -> None:
    group = await _twins(temp_db, reviewed, managed, add_file)
    await _name_somebody(temp_db, group.ids[0])
    offer = await _offer_on(reviewed, group)
    assert offer is not None

    assert await reviewed.carry(replace(offer, targets=()), actor=admin) == 0
    assert await reviewed.carry(replace(offer, carried=()), actor=admin) == 0

    left = await attribution_of_files(temp_db, [group.ids[1]])
    assert not left[group.ids[1]]
    receipts = await temp_db.fetch_all(
        "SELECT id FROM workbench_decisions WHERE queue = ?", (CARRY_QUEUE,)
    )
    assert receipts == []


async def test_taking_back_a_carry_of_nothing_takes_nothing(
    temp_db: Database, reviewed: DedupService, managed: Library, add_file: Any
) -> None:
    group = await _twins(temp_db, reviewed, managed, add_file)
    await _name_somebody(temp_db, group.ids[0])

    assert await reviewed.uncarry(asset_id=group.ids[0], carried=[]) is False
    assert (await attribution_of_files(temp_db, [group.ids[0]]))[group.ids[0]]
