# SPDX-License-Identifier: AGPL-3.0-or-later
"""Create Photo Set, with the proposal's name or with one somebody typed.

The Shoots page's "Make the set" is a split button: the main half makes the set under the name
the proposal carries (the creator's) and "Create with a name..." makes the same set called what
was typed. So the take route accepts an optional name, and the receipt records which name the set
was made with.

Two halves, held separately. The ROUTE: no body, an empty body and a body with no name all mean the
proposal's name (the page's Create Photo Set presses with no body at all), a name arrives tidied,
and a blank or a double quote is refused before anything is written. The SERVICE, on a real schema: the name
reaches the feature that makes Photo Sets, and the receipt says it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel import wiring
from sift.kernel.access import Role, Viewer
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.shoots.router import _service, router
from sift.slices.shoots.service import MadeSet, ShootError, ShootService
from sift.slices.shoots.store import Made, Proposal, Store
from sift.slices.shoots.tests.test_pass_end_to_end import (
    _CREATOR,
    _EPOCH,
    _FLOOR,
    _Index,
    _library,
    _Settings,
)

_VIEWER = Viewer(id="account-1", role=Role.ADMIN)


class _Taker:
    """The service, as far as the route reaches it: records the name each press handed in."""

    def __init__(self) -> None:
        self.names: list[str | None] = []

    async def make(self, viewer: Viewer, proposal_id: str, *, name: str | None = None) -> MadeSet:
        self.names.append(name)
        return MadeSet(
            photo_set_id="set-1", pictures=10, decision_id="decision-1", name=name or _CREATOR
        )

    async def waiting(self, *, limit: int = 20, offset: int = 0) -> tuple[list[Proposal], int]:
        return [_PROPOSAL], 1

    async def one(self, proposal_id: str) -> Proposal | None:
        return _PROPOSAL

    async def auto_file(self) -> bool:
        return False

    async def made_from(self, proposal_id: str) -> Made | None:
        return None


#: One shoot of three pictures, one of them carrying nobody.
_PROPOSAL = Proposal(
    id="p-1",
    person_id="person-1",
    name=_CREATOR,
    found_at=_EPOCH,
    pictures=3,
    asset_ids=("a-1", "a-2", "a-3"),
    unnamed_ids=("a-3",),
)


class _Row:
    """What the read that decides visibility hands back for one picture, as far as the list reads."""

    def __init__(self) -> None:
        self.art_version = "v1"
        self.asset = self
        self.media_type = "image"


class _Seen:
    """Every picture of the shoot is one this admin may be shown."""

    async def assets_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> dict[str, _Row]:
        return {one: _Row() for one in asset_ids}


def _client(taker: _Taker) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: _VIEWER
    app.dependency_overrides[csrf_protect] = lambda: None
    app.dependency_overrides[_service] = lambda: taker
    app.dependency_overrides[wiring.access] = lambda: _Seen()
    return TestClient(app)


def test_the_page_card_asks_the_boards_question_in_the_boards_words() -> None:
    """The Shoots page's card carries the two sentences `asking` words.

    "Do these 13 pictures of X belong together?" over "13 pictures of X that are in no Photo Set",
    on both, from `queue.asking`, off the numbers this list has already scoped.
    """
    answered = _client(_Taker()).get("/shoots")

    assert answered.status_code == 200
    (card,) = answered.json()["shoots"]
    assert card["question"] == f"Do these 3 photos of {_CREATOR} belong together?"
    assert card["detail"].startswith(f"3 photos of {_CREATOR} that are in no Photo Set")
    assert "1 of them with no person named" in card["detail"]


def test_the_page_card_sends_the_person_its_sentences_name() -> None:
    """The person's name on the card is a link to them.

    "Do these 13 pictures of X belong together?" names somebody with a page, so the name is not
    plain words. The card carries who its sentences name, in a history line's shape, and the
    name is exactly the run of characters both sentences say it as, which is what lets the page
    find it there and link it.
    """
    (card,) = _client(_Taker()).get("/shoots").json()["shoots"]

    assert card["links"] == [
        {"kind": "person", "id": "person-1", "name": _CREATOR, "href": None, "gone": False}
    ]
    assert f"of {_CREATOR} belong" in card["question"]
    assert f"of {_CREATOR} that" in card["detail"]


def test_a_shoot_opens_on_a_page_of_its_own_with_every_picture_and_the_cards_question() -> None:
    """A card on the Shoots wall opens its shoot at an address of its own, as a Photo Set opens:
    every picture of it, and the question the card asks, from one builder."""
    client = _client(_Taker())

    opened = client.get("/shoots/p-1")

    assert opened.status_code == 200
    shoot = opened.json()
    (card,) = client.get("/shoots").json()["shoots"]
    assert shoot == card
    assert [one["id"] for one in shoot["items"]] == ["a-1", "a-2", "a-3"]
    assert shoot["question"] == f"Do these 3 photos of {_CREATOR} belong together?"


def test_a_shoot_no_longer_waiting_or_with_nothing_to_show_is_not_found() -> None:
    """Answered on another screen, or holding only pictures this admin may not be shown: either
    way there is no question left to put on the page."""

    class _Gone(_Taker):
        async def one(self, proposal_id: str) -> Proposal | None:
            return None

    assert _client(_Gone()).get("/shoots/p-1").status_code == 404

    class _Unseen:
        async def assets_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> dict[str, _Row]:
            return {}

    client = _client(_Taker())
    client.app.dependency_overrides[wiring.access] = lambda: _Unseen()  # type: ignore[attr-defined]
    assert client.get("/shoots/p-1").status_code == 404


def test_a_shoot_already_answered_is_not_found_though_its_row_is_kept() -> None:
    """Answered is the link, and the proposal row outlives it so an undo can put the question
    back. So the row being there is not the question still standing: the page is a 404, as the
    Shoots page leaves the card off."""

    class _Answered(_Taker):
        async def made_from(self, proposal_id: str) -> Made | None:
            return Made(proposal_id=proposal_id, photo_set_id="set-1", decision_id=None, made_at=0)

    assert _client(_Answered()).get("/shoots/p-1").status_code == 404


def test_the_take_route_without_a_name_makes_the_proposals() -> None:
    """The ordinary press, three ways: the page's Create Photo Set sends no body at all."""
    taker = _Taker()
    client = _client(taker)

    assert client.post("/shoots/p-1/make").status_code == 200
    assert client.post("/shoots/p-1/make", json={}).status_code == 200
    answered = client.post("/shoots/p-1/make", json={"name": None})

    assert answered.status_code == 200
    assert taker.names == [None, None, None]
    assert answered.json()["name"] == _CREATOR


def test_the_take_route_with_a_name_hands_it_on_tidied() -> None:
    """Spaces round a typed name are not part of it; the name is what the set is called."""
    taker = _Taker()

    answered = _client(taker).post("/shoots/p-1/make", json={"name": "  Rooftop sitting  "})

    assert answered.status_code == 200
    assert taker.names == ["Rooftop sitting"]
    assert answered.json()["name"] == "Rooftop sitting"


@pytest.mark.parametrize("typed", ["   ", 'The "rooftop" one'])
def test_the_take_route_refuses_a_name_no_photo_set_may_have(typed: str) -> None:
    """Blank, or holding a double quote: the rule every name in Sift goes through."""
    taker = _Taker()

    answered = _client(taker).post("/shoots/p-1/make", json={"name": typed})

    assert answered.status_code == 422
    assert taker.names == [], "nothing was made"


class _Everything:
    """The read that decides visibility, answering that this admin may see every picture."""

    async def visible_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> set[str]:
        return set(asset_ids)


class _PhotoSets:
    """The feature that makes Photo Sets, through its seam: records what each set was called."""

    def __init__(self) -> None:
        self.called: list[str] = []

    async def make(self, asset_ids: Sequence[str], *, name: str) -> str | None:
        self.called.append(name)
        return f"set-{len(self.called)}"


class _Receipts:
    """The board's recorder: keeps each receipt as written."""

    def __init__(self) -> None:
        self.written: list[dict[str, Any]] = []

    async def record_on(self, connection: Any, **receipt: Any) -> str:
        self.written.append(receipt)
        return f"decision-{len(self.written)}"


async def _proposed(
    tmp_path: Path, *, longest: int | None = None
) -> tuple[Any, ShootService, _PhotoSets, _Receipts, str]:
    database = await _library(tmp_path)
    near = tuple((asset_id, 0.10) for asset_id in ("shoot-2", "shoot-3", "shoot-4"))
    photo_sets = _PhotoSets()
    receipts = _Receipts()
    store = Store(database, clock=lambda: _EPOCH)
    service = ShootService(
        database=database,
        store=store,
        access=_Everything(),  # type: ignore[arg-type]
        semantic=_Index(near),  # type: ignore[arg-type]
        photo_sets=photo_sets,  # type: ignore[arg-type]
        preferences=_Settings(),  # type: ignore[arg-type]
        recorder=receipts,
        least=_FLOOR,
        longest=longest,
    )
    await service.find()
    waiting, _total = await store.waiting(limit=1)
    return database, service, photo_sets, receipts, waiting[0].id


async def test_a_set_made_without_a_name_is_called_what_the_proposal_is(tmp_path: Path) -> None:
    database, service, photo_sets, receipts, proposal = await _proposed(tmp_path)
    try:
        made = await service.make(_VIEWER, proposal)

        assert photo_sets.called == [_CREATOR]
        assert made.name == _CREATOR
        (receipt,) = receipts.written
        assert json.loads(receipt["payload"])["name"] == _CREATOR
        assert "called" not in receipt["detail"], "the creator's own name is not news"
    finally:
        await database.close()


async def test_a_set_made_with_a_name_is_called_that_and_the_receipt_says_so(
    tmp_path: Path,
) -> None:
    database, service, photo_sets, receipts, proposal = await _proposed(tmp_path)
    try:
        made = await service.make(_VIEWER, proposal, name="Rooftop sitting")

        assert photo_sets.called == ["Rooftop sitting"]
        assert made.name == "Rooftop sitting"
        (receipt,) = receipts.written
        assert json.loads(receipt["payload"])["name"] == "Rooftop sitting"
        assert 'called "Rooftop sitting"' in receipt["detail"]
        assert receipt["title"] == f"Created a Photo Set for {_CREATOR}", (
            "who it is FOR is unchanged"
        )
    finally:
        await database.close()


async def test_a_name_longer_than_a_photo_set_may_have_is_refused_before_anything_is_made(
    tmp_path: Path,
) -> None:
    database, service, photo_sets, receipts, proposal = await _proposed(tmp_path, longest=8)
    try:
        with pytest.raises(ShootError):
            await service.make(_VIEWER, proposal, name="Rooftop sitting")

        assert photo_sets.called == []
        assert receipts.written == []
    finally:
        await database.close()


class _Placed(_Taker):
    """The service with a queue to be placed in: `second` is one down, anything else is off it."""

    def __init__(self) -> None:
        super().__init__()
        self.offsets: list[int] = []

    async def position_of(self, proposal_id: str) -> int | None:
        return 1 if proposal_id == "second" else None

    async def waiting(self, *, limit: int = 20, offset: int = 0) -> tuple[list[Proposal], int]:
        self.offsets.append(offset)
        return [_PROPOSAL], 2


def test_the_list_opens_at_the_proposal_its_address_names() -> None:
    """The page keeps its place in the address as the proposal it was left at, the way every other
    wall does; a proposal off the list opens the top instead of refusing."""
    placed = _Placed()
    client = _client(placed)

    assert client.get("/shoots", params={"from": "second", "limit": 1}).json()["offset"] == 1
    assert client.get("/shoots", params={"from": "answered", "offset": 5}).json()["offset"] == 0
    assert placed.offsets == [1, 0]


async def test_naming_the_rest_is_recorded_as_a_naming_with_the_creator_as_what_they_were_named(
    tmp_path: Path,
) -> None:
    """This receipt says the ledger's `named` verb: a person put on files is a naming, with the
    person as what the files were named WITH."""
    database, service, _photo_sets, receipts, proposal = await _proposed(tmp_path)
    try:
        # One picture of the shoot carrying nobody, which is what there is to name.
        last = await database.fetch_one(
            "SELECT asset_id FROM shoot_proposal_items WHERE proposal_id = ?"
            " ORDER BY position DESC LIMIT 1",
            (proposal,),
        )
        assert last is not None
        await database.execute(
            "UPDATE shoot_proposal_items SET named = 0 WHERE proposal_id = ? AND asset_id = ?",
            (proposal, last["asset_id"]),
        )
        await database.execute("DELETE FROM asset_people WHERE asset_id = ?", (last["asset_id"],))

        named = await service.name_the_rest(_VIEWER, proposal)

        assert named.files > 0
        (receipt,) = receipts.written
        assert receipt["verb"] == "named"
        assert (receipt["object"].kind, receipt["object"].name) == ("person", _CREATOR)
    finally:
        await database.close()


class _Refusing(_Taker):
    """The service answering every press with one refusal, and the other two presses' answers."""

    def __init__(self, refusal: Exception | None) -> None:
        super().__init__()
        self.refusal = refusal

    async def make(self, viewer: Viewer, proposal_id: str, *, name: str | None = None) -> MadeSet:
        if self.refusal is not None:
            raise self.refusal
        return await super().make(viewer, proposal_id, name=name)

    async def refuse(self, viewer: Viewer, proposal_id: str) -> int:
        if self.refusal is not None:
            raise self.refusal
        return 3

    async def name_the_rest(self, viewer: Viewer, proposal_id: str) -> Any:
        from sift.slices.shoots.service import Named

        if self.refusal is not None:
            raise self.refusal
        return Named(files=1, decision_id="decision-2")


def _served(service: _Taker) -> TestClient:
    """The router with the service published on the application, the way the app wires it."""
    from sift.slices.shoots.service import SERVICE

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: _VIEWER
    app.dependency_overrides[csrf_protect] = lambda: None
    wiring.provide(app, SERVICE, service)
    return TestClient(app)


def test_the_other_two_presses_answer_what_they_did() -> None:
    client = _served(_Refusing(None))

    assert client.post("/shoots/p-1/refuse").json() == {"refused": 3}
    assert client.post("/shoots/p-1/name-the-rest").json() == {
        "files": 1,
        "decision_id": "decision-2",
    }


@pytest.mark.parametrize("press", ["make", "refuse", "name-the-rest"])
def test_a_proposal_that_is_not_waiting_is_a_404_on_every_press(press: str) -> None:
    from sift.slices.shoots.service import NotFound

    answered = _served(_Refusing(NotFound("no such shoot"))).post(f"/shoots/p-1/{press}")

    assert answered.status_code == 404


@pytest.mark.parametrize("press", ["make", "name-the-rest"])
def test_a_press_the_service_cannot_carry_out_is_a_400_saying_why(press: str) -> None:
    answered = _served(_Refusing(ShootError("every picture already has somebody"))).post(
        f"/shoots/p-1/{press}"
    )

    assert answered.status_code == 400
    assert "already has somebody" in answered.text


def test_a_press_on_a_card_whose_pictures_are_filed_already_is_a_409_saying_so() -> None:
    """Not a 400: nothing is wrong with the request, the library moved under the card."""
    from sift.slices.shoots.service import AlreadyFiled

    answered = _served(_Refusing(AlreadyFiled("Those pictures are already in a Photo Set."))).post(
        "/shoots/p-1/make"
    )

    assert answered.status_code == 409
    assert answered.json() == {"detail": "Those pictures are already in a Photo Set."}
