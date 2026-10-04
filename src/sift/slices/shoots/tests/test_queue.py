# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Shoots card on the board, through a stub service: what the card says, including at zero."""

from __future__ import annotations

import sift.slices.shoots  # noqa: F401 (declares the shoots task)
from sift.kernel.access import Role, Viewer
from sift.kernel.access.history import Link
from sift.kernel.workbench import Band
from sift.slices.shoots.queue import ShootQueue, asking
from sift.slices.shoots.store import Proposal

_VIEWER = Viewer(id="account-1", role=Role.ADMIN)


class _NothingProposed:
    """A pass that has found nothing, the state of a fresh install."""

    async def waiting(self, *, limit: int = 20, offset: int = 0) -> tuple[list[Proposal], int]:
        return [], 0


class _OneProposed:
    """One shoot waiting, whose proposal is gone by the time it is read back."""

    async def waiting(self, *, limit: int = 20, offset: int = 0) -> tuple[list[Proposal], int]:
        return [
            Proposal(id="proposal-1", person_id="person-1", name="Wren Halloway", found_at=0)
        ], 1

    async def one(self, proposal_id: str) -> Proposal | None:
        return None


async def test_the_card_is_drawn_although_nothing_has_been_proposed() -> None:
    """The Shoots card is drawn at zero: the pass runs on every scan, so zero is nothing found."""
    card = ShootQueue(_NothingProposed())  # type: ignore[arg-type]

    assert await card.available() is True


async def test_an_empty_card_counts_nothing_and_draws_nothing() -> None:
    """Nothing proposed: a zero, no stills, and still a judgement rather than a log."""
    card = ShootQueue(_NothingProposed())  # type: ignore[arg-type]

    summary = await card.survey(_VIEWER)

    assert summary.count == 0
    assert summary.preview == ()
    assert card.band is Band.DECISION


async def test_a_proposal_gone_by_the_time_it_is_read_draws_no_stills() -> None:
    """A row that was there for the count and gone a moment later: the count stands and the strip
    is empty rather than the read failing."""
    card = ShootQueue(_OneProposed())  # type: ignore[arg-type]

    summary = await card.survey(_VIEWER)

    assert summary.count == 1
    assert summary.preview == ()


class _OneProposalPartlyConcealed:
    """A shoot of four pictures, two held back by this user's vault."""

    #: An unnamed picture on each side of the vault, so two wrong counts differ.
    _ALL = ("open-1", "shut-1", "open-2", "shut-2")
    _SEEN = ("open-1", "open-2")

    async def waiting(self, *, limit: int = 20, offset: int = 0) -> tuple[list[Proposal], int]:
        return [self._full()], 1

    async def one(self, proposal_id: str) -> Proposal | None:
        return self._full()

    async def visible_of(self, viewer: Viewer, asset_ids: tuple[str, ...]) -> set[str]:
        return set(self._SEEN)

    def _full(self) -> Proposal:
        return Proposal(
            id="proposal-1",
            person_id="person-1",
            name="Wren Halloway",
            found_at=0,
            pictures=len(self._ALL),
            asset_ids=self._ALL,
            unnamed_ids=("open-2", "shut-2"),
        )


async def test_the_card_draws_only_the_pictures_this_viewer_may_see() -> None:
    """The card draws only the pictures this viewer may see."""
    card = ShootQueue(_OneProposalPartlyConcealed())  # type: ignore[arg-type]

    summary = await card.survey(_VIEWER)

    assert [one.id for one in summary.preview] == ["open-1", "open-2"]
    assert [one.href for one in summary.preview] == ["/asset/open-1", "/asset/open-2"]


def test_the_shoots_page_names_the_person_its_sentences_say() -> None:
    """A card on the Shoots page links the person, and the name is exactly the run of characters
    both sentences say it as, which is what lets the card find it there and link it."""
    said = asking("person-1", "Wren Halloway", 2, 1)

    assert said.names == (Link(kind="person", id="person-1", name="Wren Halloway"),)
    assert "of Wren Halloway belong" in said.question
    assert "of Wren Halloway that" in said.detail
    assert said.detail.endswith("1 of them with no person named")
    assert asking("", "", 2, 0).names == ()


class _Undoer:
    """The service's two take-backs and its visibility read, recording what each was asked."""

    def __init__(self) -> None:
        self.taken_back: list[str] = []
        self.unnamed: list[tuple[str, list[str], str]] = []

    async def take_back(self, photo_set_id: str, *, by: Viewer) -> bool:
        self.taken_back.append(photo_set_id)
        return True

    async def unname(self, person_id: str, asset_ids: list[str], *, proposal_id: str = "") -> bool:
        self.unnamed.append((person_id, list(asset_ids), proposal_id))
        return True

    async def visible_of(self, viewer: Viewer, asset_ids: list[str]) -> set[str]:
        return {one for one in asset_ids if one != "hidden"}


async def test_undo_puts_back_whichever_of_the_two_decisions_it_was() -> None:
    import json

    undoer = _Undoer()
    card = ShootQueue(undoer)  # type: ignore[arg-type]

    made = json.dumps({"kind": "shoot", "photo_set_id": "set-1", "assets": ["a"]})
    named = json.dumps(
        {"kind": "named", "person_id": "person-1", "assets": ["a", "b"], "proposal_id": "shoot-1"}
    )
    # A naming recorded before the shoot was kept in its record: only the people come off.
    older = json.dumps({"kind": "named", "person_id": "person-1", "assets": ["c"]})
    assert await card.reverse(_VIEWER, "r1", made) is True
    assert await card.reverse(_VIEWER, "r2", named) is True
    assert await card.reverse(_VIEWER, "r3", older) is True

    assert undoer.taken_back == ["set-1"]
    assert undoer.unnamed == [("person-1", ["a", "b"], "shoot-1"), ("person-1", ["c"], "")]


async def test_a_record_that_cannot_be_read_is_reversed_as_nothing_put_back() -> None:
    """A payload an older version wrote, or none at all, is a decision this cannot reverse:
    answered honestly rather than failing the request as though Undo were broken."""
    import json

    undoer = _Undoer()
    card = ShootQueue(undoer)  # type: ignore[arg-type]

    for payload in ("{not json", json.dumps(["set-1"]), json.dumps({"kind": "named"})):
        assert await card.reverse(_VIEWER, "r", payload) is False
    assert (undoer.taken_back, undoer.unnamed) == ([], [])


async def test_the_stills_of_a_decision_are_only_those_the_reader_may_still_see() -> None:
    import json

    card = ShootQueue(_Undoer())  # type: ignore[arg-type]
    payload = json.dumps({"kind": "shoot", "photo_set_id": "s", "assets": ["a", "hidden", "b"]})

    shown = await card.pictures_of(_VIEWER, payload)

    assert [one.id for one in shown] == ["a", "b"]
    assert await card.pictures_of(_VIEWER, "{not json") == ()


def test_a_shoot_with_nobody_unnamed_says_no_count() -> None:
    said = asking("person-1", "Wren Halloway", 4, 0)
    assert said.detail == "4 photos of Wren Halloway that are in no Photo Set"
