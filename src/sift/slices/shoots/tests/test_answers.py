# SPDX-License-Identifier: AGPL-3.0-or-later
"""The three answers a shoot takes, and taking each one back, against a real schema.

Make the set, not a set, name the rest, and the refusals each press has before it writes
anything: a proposal that is not there, one already made, one holding a picture this viewer may
not be shown, and a making the Photo Sets feature declines. Then the two undos, which have to take
back exactly what their answer did and nothing that was there before it.

The library is the one sitting `test_pass_end_to_end` builds; the meaning index, the Photo Sets
feature, the visibility read and the receipts are stood in for, each at its seam.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.access import Role, Viewer
from sift.kernel.db import Database
from sift.slices.shoots.service import AlreadyFiled, NotFound, ShootError, ShootService
from sift.slices.shoots.store import Store
from sift.slices.shoots.tests.test_make_with_a_name import _Receipts
from sift.slices.shoots.tests.test_pass_end_to_end import (
    _CREATOR,
    _EPOCH,
    _FLOOR,
    _IN_SET,
    _NAMED,
    _PHOTO_SET,
    _UNNAMED,
    _Index,
    _library,
)

pytestmark = [pytest.mark.integration]

_VIEWER = Viewer(id="account-1", role=Role.ADMIN)


class _Switch:
    """The preference that files a shoot with nobody asked."""

    def __init__(self, *, on: bool) -> None:
        self._on = on

    async def get_app(self, key: str) -> object:
        return self._on


class _PhotoSets:
    """The feature that makes Photo Sets, through its seam. `declines` is a making it refuses:
    the answer it gives when a picture went between the read and the write."""

    def __init__(self, *, declines: bool = False) -> None:
        self._declines = declines
        self.made: list[tuple[str, ...]] = []
        self.forgotten: list[tuple[str, str]] = []

    async def holding(self, asset_ids: Sequence[str]) -> str | None:
        return None

    async def make(
        self, asset_ids: Sequence[str], *, name: str, origin: str = "shoot"
    ) -> str | None:
        if self._declines:
            return None
        self.made.append(tuple(asset_ids))
        return f"set-{len(self.made)}"

    async def forget(self, photo_set_id: str, *, by: Viewer) -> None:
        self.forgotten.append((photo_set_id, by.id))


class _Sees:
    """The read that decides visibility: every picture but the ones named hidden."""

    def __init__(self, hidden: Sequence[str] = ()) -> None:
        self._hidden = set(hidden)

    async def visible_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> set[str]:
        return {one for one in asset_ids if one not in self._hidden}


def _near() -> tuple[tuple[str, float], ...]:
    """The seed's neighbours: the rest of the sitting, then the two carrying nobody."""
    return tuple((asset_id, 0.10) for asset_id in (*_NAMED[1:], *_UNNAMED))


def _service(
    database: Database,
    *,
    index: Any = None,
    photo_sets: _PhotoSets | None = None,
    sees: _Sees | None = None,
    automatic: bool = False,
    receipts: _Receipts | None = None,
) -> tuple[ShootService, Store]:
    store = Store(database, clock=lambda: _EPOCH)
    service = ShootService(
        database=database,
        store=store,
        access=sees or _Sees(),  # type: ignore[arg-type]
        semantic=index or _Index(_near()),  # type: ignore[arg-type]
        photo_sets=photo_sets or _PhotoSets(),
        preferences=_Switch(on=automatic),  # type: ignore[arg-type]
        recorder=receipts or _Receipts(),
        least=_FLOOR,
    )
    return service, store


async def _proposal(store: Store) -> str:
    waiting, total = await store.waiting(limit=10)
    assert total == 1, "the pass proposed the one sitting"
    return waiting[0].id


# --- the pass: what it leaves out ----------------------------------------------------------------


class _Scattered(_Index):
    """The creator's pictures described as four unrelated scenes: no two are one sitting."""

    async def describe_many(self, asset_ids: Sequence[str]) -> dict[str, list[float]]:
        self.described.append(tuple(asset_ids))
        return {
            asset_id: [1.0 if axis == step else 0.0 for axis in range(len(_NAMED))]
            for step, asset_id in enumerate(_NAMED)
        }


class _Undescribed(_Index):
    """An index holding nothing for any of this creator's pictures."""

    async def describe_many(self, asset_ids: Sequence[str]) -> dict[str, list[float]]:
        self.described.append(tuple(asset_ids))
        return {}


async def test_a_creator_whose_pictures_are_no_one_sitting_is_proposed_nothing(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    try:
        service, store = _service(database, index=_Scattered(_near()))

        found = await service.find()

        assert (found.creators, found.found) == (1, 0), "looked at, and nothing grouped"
        assert (await store.waiting(limit=10))[1] == 0
    finally:
        await database.close()


async def test_a_creator_the_index_has_never_described_is_not_widened_or_proposed(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    try:
        index = _Undescribed(_near())
        service, store = _service(database, index=index)

        found = await service.find()

        assert found.found == 0
        assert index.asked == [], "no neighbours were looked up for pictures with no numbers"
        assert (await store.waiting(limit=10))[1] == 0
    finally:
        await database.close()


async def test_pictures_already_turned_down_are_not_offered_again_under_the_floor(
    tmp_path: Path,
) -> None:
    """Two of the four turned down leaves two, under the floor of three: nothing is even read."""
    database = await _library(tmp_path)
    try:
        index = _Index(_near())
        service, store = _service(database, index=index)
        await store.refuse("an-earlier-proposal", list(_NAMED[:2]))

        found = await service.find()

        assert found.found == 0
        assert index.described == [], "the pool fell under the floor before the index was asked"
    finally:
        await database.close()


async def test_a_seed_with_no_neighbours_is_a_shoot_of_its_own_pictures_only(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    try:
        service, store = _service(database, index=_Index(()))

        await service.find()

        full = await store.one(await _proposal(store))
        assert full is not None
        assert full.asset_ids == _NAMED
        assert full.unnamed_ids == ()
    finally:
        await database.close()


async def test_the_widening_stops_at_the_first_neighbour_too_far_away(tmp_path: Path) -> None:
    """Neighbours come closest first, so one past the distance ends the reading: the second picture
    carrying nobody is further than one sitting and is not swept in."""
    from sift.slices.shoots.clustering import DISTANCE

    database = await _library(tmp_path)
    try:
        near = (
            *((asset_id, 0.10) for asset_id in _NAMED[1:]),
            (_UNNAMED[0], 0.10),
            (_UNNAMED[1], DISTANCE + 0.05),
        )
        service, store = _service(database, index=_Index(near))

        await service.find()

        full = await store.one(await _proposal(store))
        assert full is not None
        assert full.unnamed_ids == (_UNNAMED[0],)
    finally:
        await database.close()


# --- filing with nobody asked -----------------------------------------------------------------------


async def test_with_the_switch_on_a_shoot_is_made_with_a_receipt_and_no_question(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    try:
        photo_sets = _PhotoSets()
        receipts = _Receipts()
        service, store = _service(
            database, photo_sets=photo_sets, receipts=receipts, automatic=True
        )

        found = await service.find()

        assert (found.found, found.filed) == (1, 1)
        assert photo_sets.made == [(*_NAMED, *_UNNAMED)]
        (receipt,) = receipts.written
        assert receipt["user_id"] is None, "nobody pressed anything"
        assert (await store.waiting(limit=10))[1] == 0, "and nobody is asked about it"
    finally:
        await database.close()


async def test_with_the_switch_on_a_making_the_photo_sets_decline_is_not_counted_as_filed(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    try:
        receipts = _Receipts()
        service, _store = _service(
            database, photo_sets=_PhotoSets(declines=True), receipts=receipts, automatic=True
        )

        found = await service.find()

        assert (found.found, found.filed) == (1, 0)
        assert receipts.written == [], "no receipt for a set that was not made"
    finally:
        await database.close()


async def test_with_the_switch_on_a_card_an_earlier_pass_proposed_is_answered_and_leaves(
    tmp_path: Path,
) -> None:
    """The card a pass proposed before the switch was turned on is the one the filing answers.

    A filing that makes the set and leaves that card standing has the Shoots page offering
    pictures already in a Photo Set, and pressing the card makes a duplicate. So the filing goes
    through the proposal: the link a pressed yes writes, and the card off the list.
    """
    database = await _library(tmp_path)
    try:
        asked, store = _service(database)
        await asked.find()
        proposal = await _proposal(store)
        photo_sets = _PhotoSets()
        filing, _store = _service(database, photo_sets=photo_sets, automatic=True)

        found = await filing.find()

        assert found.filed == 1
        link = await filing.made_from(proposal)
        assert link is not None and link.photo_set_id == "set-1", "the card is the one answered"
        assert await filing.position_of(proposal) is None
        assert (await store.waiting(limit=10))[1] == 0
        assert len(photo_sets.made) == 1
    finally:
        await database.close()


async def _file(database: Database, photo_set_id: str, asset_ids: Sequence[str]) -> None:
    """These pictures put in a Photo Set somewhere else: by hand, a download, another pass."""
    async with database.write() as connection:
        await connection.execute(_PHOTO_SET, (photo_set_id, "Filed elsewhere", _EPOCH))
        for position, asset_id in enumerate(asset_ids):
            await connection.execute(_IN_SET, (photo_set_id, asset_id, position, _EPOCH))


async def test_a_card_whose_pictures_are_filed_already_is_refused_not_made_a_duplicate(
    tmp_path: Path,
) -> None:
    """Pressed after its pictures went into a Photo Set: nothing is made, and the card is answered
    by the set holding the most of them, so it leaves the list with the refusal."""
    database = await _library(tmp_path)
    try:
        photo_sets = _PhotoSets()
        receipts = _Receipts()
        service, store = _service(database, photo_sets=photo_sets, receipts=receipts)
        await service.find()
        proposal = await _proposal(store)
        await _file(database, "set-b", _NAMED[:1])
        await _file(database, "set-a", _NAMED[1:3])

        with pytest.raises(AlreadyFiled, match="already in a Photo Set"):
            await service.make(_VIEWER, proposal)

        assert photo_sets.made == [], "no duplicate"
        assert receipts.written == [], "and no receipt: nobody decided anything"
        link = await service.made_from(proposal)
        assert link is not None and link.photo_set_id == "set-a", "the set holding the most"
        assert link.decision_id is None
        assert await service.position_of(proposal) is None, "answered, so off the list"
    finally:
        await database.close()


async def test_a_pass_answers_a_standing_card_whose_pictures_are_filed_already(
    tmp_path: Path,
) -> None:
    """Every picture filed leaves the creator with nothing loose, so the pass never looks at
    them and never replaces their card, which would stand for ever. So the pass reads what is
    standing."""
    database = await _library(tmp_path)
    try:
        service, store = _service(database)
        await service.find()
        proposal = await _proposal(store)
        await _file(database, "set-a", (*_NAMED, *_UNNAMED))

        found = await service.find()

        assert found.creators == 0, "nobody with loose pictures was looked at"
        link = await service.made_from(proposal)
        assert link is not None and link.photo_set_id == "set-a"
        assert (await store.waiting(limit=10))[1] == 0
    finally:
        await database.close()


# --- making the set ------------------------------------------------------------------------------


async def test_a_made_proposal_leaves_the_queue_and_says_what_it_became(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        service, store = _service(database)
        await service.find()
        proposal = await _proposal(store)
        assert await service.position_of(proposal) == 0
        listed, total = await service.waiting()
        assert ([one.id for one in listed], total) == ([proposal], 1)

        made = await service.make(_VIEWER, proposal)

        assert await service.position_of(proposal) is None, "answered, so off the list"
        link = await service.made_from(proposal)
        assert link is not None and link.photo_set_id == made.photo_set_id
        assert link.decision_id == made.decision_id
        still = await service.one(proposal)
        assert still is not None and still.name == _CREATOR, "the proposal itself is kept"
    finally:
        await database.close()


async def test_a_proposal_that_is_not_there_cannot_be_made(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        photo_sets = _PhotoSets()
        service, _store = _service(database, photo_sets=photo_sets)

        with pytest.raises(NotFound):
            await service.make(_VIEWER, "no-such-proposal")

        assert photo_sets.made == []
    finally:
        await database.close()


async def test_a_proposal_already_made_is_not_made_a_second_time(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        photo_sets = _PhotoSets()
        service, store = _service(database, photo_sets=photo_sets)
        await service.find()
        proposal = await _proposal(store)
        await service.make(_VIEWER, proposal)

        with pytest.raises(NotFound, match="already been made"):
            await service.make(_VIEWER, proposal)

        assert len(photo_sets.made) == 1
    finally:
        await database.close()


async def test_a_proposal_holding_a_picture_this_viewer_may_not_see_is_not_theirs_to_make(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    try:
        photo_sets = _PhotoSets()
        service, store = _service(database, photo_sets=photo_sets, sees=_Sees([_NAMED[2]]))
        await service.find()

        with pytest.raises(NotFound):
            await service.make(_VIEWER, await _proposal(store))

        assert photo_sets.made == [], "a set they could not see all of was not filed"
    finally:
        await database.close()


async def test_a_making_the_photo_sets_decline_is_refused_with_no_receipt_and_no_link(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    try:
        receipts = _Receipts()
        service, store = _service(database, photo_sets=_PhotoSets(declines=True), receipts=receipts)
        await service.find()
        proposal = await _proposal(store)

        with pytest.raises(ShootError, match="aren't a set any more"):
            await service.make(_VIEWER, proposal)

        assert receipts.written == []
        assert await service.made_from(proposal) is None
        assert await service.position_of(proposal) == 0, "the question is still asked"
    finally:
        await database.close()


async def test_taking_a_made_set_back_puts_the_question_back(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        photo_sets = _PhotoSets()
        service, store = _service(database, photo_sets=photo_sets)
        await service.find()
        proposal = await _proposal(store)
        made = await service.make(_VIEWER, proposal)

        assert await service.take_back(made.photo_set_id, by=_VIEWER) is True

        assert photo_sets.forgotten == [(made.photo_set_id, _VIEWER.id)], "deleted as theirs"
        assert await service.made_from(proposal) is None
        assert await service.position_of(proposal) == 0
    finally:
        await database.close()


# --- not a set ---------------------------------------------------------------------------------------


async def test_not_a_set_remembers_every_picture_and_takes_the_card_away(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        service, store = _service(database)
        await service.find()
        proposal = await _proposal(store)

        refused = await service.refuse(_VIEWER, proposal)

        assert refused == len(_NAMED) + len(_UNNAMED)
        assert await service.one(proposal) is None
        assert await store.refused_among([*_NAMED, *_UNNAMED]) == {*_NAMED, *_UNNAMED}
    finally:
        await database.close()


async def test_not_a_set_on_a_proposal_that_is_not_there_is_refused(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        service, store = _service(database)

        with pytest.raises(NotFound):
            await service.refuse(_VIEWER, "no-such-proposal")

        assert await store.refused_among(list(_NAMED)) == set()
    finally:
        await database.close()


async def test_not_a_set_from_a_viewer_who_cannot_see_every_picture_is_refused(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    try:
        service, store = _service(database, sees=_Sees([_UNNAMED[0]]))
        await service.find()
        proposal = await _proposal(store)

        with pytest.raises(NotFound):
            await service.refuse(_VIEWER, proposal)

        assert await store.refused_among(list(_NAMED)) == set(), "nothing was remembered"
        assert await service.one(proposal) is not None
    finally:
        await database.close()


# --- naming the rest ---------------------------------------------------------------------------------


async def _creator_on(database: Database, asset_id: str) -> bool:
    row = await database.fetch_one(
        "SELECT 1 AS on_it FROM asset_people WHERE asset_id = ? AND person_id = 'creator'",
        (asset_id,),
    )
    return row is not None


async def test_naming_the_rest_and_taking_it_back_moves_exactly_those_files(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    try:
        service, store = _service(database)
        await service.find()
        proposal = await _proposal(store)

        named = await service.name_the_rest(_VIEWER, proposal)

        assert named.files == len(_UNNAMED)
        assert all([await _creator_on(database, one) for one in _UNNAMED])
        after = await service.one(proposal)
        assert after is not None and after.unnamed_ids == (), "the card stops offering it"

        assert await service.unname("creator", list(_UNNAMED), proposal_id=proposal) is True
        assert not any([await _creator_on(database, one) for one in _UNNAMED])
        assert all([await _creator_on(database, one) for one in _NAMED]), "only what it put on"
        # And the card offers to name them again: the naming's mark comes off with it.
        undone = await service.one(proposal)
        assert undone is not None and undone.unnamed_ids == _UNNAMED, "the card offers it again"
        assert await service.unname("creator", list(_UNNAMED)) is False, "an undo pressed twice"
    finally:
        await database.close()


@pytest.mark.parametrize(("person_id", "asset_ids"), [("", ["shoot-5"]), ("creator", [])])
async def test_an_undo_naming_nobody_or_nothing_changes_nothing(
    tmp_path: Path, person_id: str, asset_ids: list[str]
) -> None:
    database = await _library(tmp_path)
    try:
        service, _store = _service(database)

        assert await service.unname(person_id, asset_ids) is False
    finally:
        await database.close()


async def test_naming_the_rest_of_a_proposal_that_is_not_there_is_refused(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        service, _store = _service(database)

        with pytest.raises(NotFound):
            await service.name_the_rest(_VIEWER, "no-such-proposal")
    finally:
        await database.close()


async def test_naming_the_rest_where_every_picture_carries_somebody_says_so(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    try:
        service, store = _service(database, index=_Index(()))
        await service.find()

        with pytest.raises(ShootError, match="already carries somebody"):
            await service.name_the_rest(_VIEWER, await _proposal(store))
    finally:
        await database.close()


async def test_naming_the_rest_for_a_viewer_who_cannot_see_those_files_is_refused(
    tmp_path: Path,
) -> None:
    database = await _library(tmp_path)
    try:
        receipts = _Receipts()
        service, store = _service(database, sees=_Sees([_UNNAMED[1]]), receipts=receipts)
        await service.find()

        with pytest.raises(NotFound):
            await service.name_the_rest(_VIEWER, await _proposal(store))

        assert not await _creator_on(database, _UNNAMED[0]), "not even the file they can see"
        assert receipts.written == []
    finally:
        await database.close()


async def test_naming_files_somebody_named_by_hand_meanwhile_writes_no_receipt(
    tmp_path: Path,
) -> None:
    """Between the read and the press, both pictures were put under the creator by hand: the
    insert keeps that first answer, so this naming wrote nothing and records nothing."""
    database = await _library(tmp_path)
    try:
        receipts = _Receipts()
        service, store = _service(database, receipts=receipts)
        await service.find()
        proposal = await _proposal(store)
        async with database.write() as connection:
            for asset_id in _UNNAMED:
                await connection.execute(
                    "INSERT INTO asset_people (asset_id, person_id) VALUES (?, 'creator')",
                    (asset_id,),
                )

        named = await service.name_the_rest(_VIEWER, proposal)

        assert (named.files, named.decision_id) == (0, None)
        assert receipts.written == []
        still = await service.one(proposal)
        assert still is not None and still.unnamed_ids == _UNNAMED, "nothing marked as its doing"
    finally:
        await database.close()


async def test_the_records_pictures_are_the_ones_this_viewer_may_see(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    try:
        service, _store = _service(database, sees=_Sees([_NAMED[0]]))

        assert await service.visible_of(_VIEWER, list(_NAMED[:2])) == {_NAMED[1]}
    finally:
        await database.close()
