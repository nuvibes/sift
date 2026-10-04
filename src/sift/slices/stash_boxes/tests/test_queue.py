# SPDX-License-Identifier: AGPL-3.0-or-later
"""Files a stash-box recognized, as a panel on the Organize board.

The judgement is not one a threshold can settle, and that is why this is a queue at all: everything
a rule COULD decide has already been thrown away: a fingerprint that matched nothing leaves no
row, a fuzzy answer with fifty entries in it is discarded as the no it really is, and an answer
whose length disagrees is graded down before it reaches here.

Two rules carry the file. A card only draws stills the user looking at it may be shown, which is
a scoped read on a screen full of ids; and taking a decision back puts the QUESTIONS among the
questions rather than putting values back, because a field a stash-box filled in is Sift's own from
the moment it landed and may have been corrected since.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

import sift.slices.workbench.schema  # noqa: F401 (a take-back writes a receipt)
from sift.kernel.access import Role, Viewer
from sift.kernel.db import Database
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.secret_store import SecretStore
from sift.kernel.workbench import ASSET
from sift.slices.stash_boxes.adapter import Box
from sift.slices.stash_boxes.queue import PREVIEW, TaggerQueue
from sift.slices.stash_boxes.service import EXACT, StashBoxService
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.anyio

A_KEY = b"0" * 32
ADMIN = Viewer(id="admin", role=Role.ADMIN)
HASHES = {"oshash": "abc"}


class _Adapter:
    def __init__(self) -> None:
        self.answer = [
            FoundRecord(
                source_id="box",
                remote_id="r1",
                subject=Subject.ASSET,
                name="A Clip",
                confidence=EXACT,
            )
        ]

    async def search(self, box: Box, term: str) -> list[FoundRecord]:  # pragma: no cover
        raise AssertionError("the pile is never filled by a name search")

    async def recognise(self, box: Box, hashes: Mapping[str, str]) -> list[FoundRecord]:
        _ = (box, hashes)
        return list(self.answer)


class _Access:
    """What this user may be shown. `hidden` is what it may not."""

    def __init__(self, hidden: set[str] | None = None) -> None:
        self.hidden = hidden or set()
        self.asked: list[str] = []

    async def get_asset(self, viewer: Viewer, asset_id: str) -> object | None:
        _ = viewer
        self.asked.append(asset_id)
        return None if asset_id in self.hidden else object()


async def _service(temp_db: Database) -> StashBoxService:
    await temp_db.initialize_schema()
    return StashBoxService(temp_db, SecretStore(temp_db), _Adapter())  # type: ignore[arg-type]


async def _a_box(service: StashBoxService) -> str:
    return await service.add(
        name="StashDB",
        endpoint="https://stashdb.example/graphql",
        api_key=None,
        master_key=A_KEY,
    )


async def _recognised(service: StashBoxService, temp_db: Database, *asset_ids: str) -> None:
    for asset_id in asset_ids:
        await temp_db.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
            (asset_id, asset_id),
        )
        await service.scan_one(asset_id, HASHES, A_KEY, length_ms=None, tolerance_ms=0)


# --- whether the panel is drawn at all ----------------------------------------------------------


async def test_a_library_that_has_never_been_scanned_is_offered_no_panel(
    temp_db: Database,
) -> None:
    """An empty panel reads as a feature that does not work rather than as one nobody has run."""
    service = await _service(temp_db)
    await _a_box(service)

    assert await TaggerQueue(service, _Access()).available() is False  # type: ignore[arg-type]


async def test_a_library_that_has_answered_everything_keeps_its_panel(
    temp_db: Database,
) -> None:
    """At zero, which is the state this screen is trying to reach and is worth showing."""
    service = await _service(temp_db)
    box = await _a_box(service)
    await _recognised(service, temp_db, "a1")
    await service.settle("a1", box, applied=True)
    queue = TaggerQueue(service, _Access())  # type: ignore[arg-type]

    assert await queue.available() is True
    assert (await queue.survey(ADMIN)).count == 0


async def test_one_files_settled_answers_are_its_own(temp_db: Database) -> None:
    """The chooser on one file reads the settled half for THAT file, so a file a box already
    matched says what matched rather than that nothing recognized it."""
    service = await _service(temp_db)
    box = await _a_box(service)
    await _recognised(service, temp_db, "a1", "a2")
    await service.settle("a1", box, applied=True)
    await service.settle("a2", box, applied=False)

    mine, counted = await service.answered(limit=10, asset_id="a1")
    everything, _ = await service.answered(limit=10)

    assert [(one.asset_id, one.state) for one in mine] == [("a1", "applied")]
    assert counted == 1
    assert {one.asset_id for one in everything} == {"a1", "a2"}


# --- the card -----------------------------------------------------------------------------------


async def test_the_card_draws_a_still_from_each_file_it_is_about(temp_db: Database) -> None:
    service = await _service(temp_db)
    await _a_box(service)
    await _recognised(service, temp_db, "a1", "a2")

    survey = await TaggerQueue(service, _Access()).survey(ADMIN)  # type: ignore[arg-type]

    assert survey.name == "tagger"
    assert survey.count == 2
    assert [one.id for one in survey.preview] == ["a1", "a2"]
    assert {one.kind for one in survey.preview} == {ASSET}
    # A SCREEN, not the API path that reads the file. Both begin with a slash and the two address
    # spaces are told apart by where they sit, so the wrong one would be a link to nothing; this is
    # the address the queue's own rows already use.
    assert [one.href for one in survey.preview] == ["/asset/a1", "/asset/a2"]


async def test_a_still_this_account_may_not_be_shown_is_left_off_the_card(
    temp_db: Database,
) -> None:
    service = await _service(temp_db)
    await _a_box(service)
    await _recognised(service, temp_db, "a1", "a2")

    survey = await TaggerQueue(service, _Access(hidden={"a1"})).survey(ADMIN)  # type: ignore[arg-type]

    assert [one.id for one in survey.preview] == ["a2"]
    assert survey.count == 2, "the count is what is waiting, not what fits on the card"


async def test_a_card_draws_no_more_stills_than_it_has_room_for(temp_db: Database) -> None:
    """Enough to recognise what a pile is about; not a gallery."""
    service = await _service(temp_db)
    await _a_box(service)
    await _recognised(service, temp_db, *[f"a{n}" for n in range(PREVIEW + 3)])

    survey = await TaggerQueue(service, _Access()).survey(ADMIN)  # type: ignore[arg-type]

    assert len(survey.preview) == PREVIEW


# --- what one decision was about ------------------------------------------------------------------


async def test_the_stills_from_one_decision_are_scoped_when_they_are_drawn(
    temp_db: Database,
) -> None:
    """Scoped through the resolver rather than trusted from the record: a file restricted since the
    decision is one this user may no longer see, and having agreed to it is not a licence."""
    service = await _service(temp_db)
    queue = TaggerQueue(service, _Access(hidden={"a2"}))  # type: ignore[arg-type]
    payload = json.dumps(
        {"matches": [{"asset_id": "a1", "box_id": "b"}, {"asset_id": "a2", "box_id": "b"}]}
    )

    pictures = await queue.pictures_of(ADMIN, payload)

    assert [one.id for one in pictures] == ["a1"]
    # The record's stills are addressed the same way the card's are: one function, so a press in
    # the record and a press on the board cannot land in two different address spaces.
    assert [one.href for one in pictures] == ["/asset/a1"]


async def test_a_record_that_cannot_be_read_draws_nothing_rather_than_failing(
    temp_db: Database,
) -> None:
    service = await _service(temp_db)
    queue = TaggerQueue(service, _Access())  # type: ignore[arg-type]

    for payload in ("not json", json.dumps(["a list"]), json.dumps({"matches": ["not a record"]})):
        assert await queue.pictures_of(ADMIN, payload) == ()


# --- taking a decision back -----------------------------------------------------------------------


async def test_an_undo_puts_the_questions_back_rather_than_the_values(
    temp_db: Database,
) -> None:
    """The QUESTION comes back: each file returns to the pile. What the answer wrote comes off
    with it, but only while it is still what the answer said (`test_taken_back.py`): a title
    corrected since it was imported is somebody's own and stays."""
    service = await _service(temp_db)
    box = await _a_box(service)
    await _recognised(service, temp_db, "a1")
    await service.settle("a1", box, applied=True)
    assert (await service.waiting(limit=0))[1] == 0
    queue = TaggerQueue(service, _Access())  # type: ignore[arg-type]

    # A real user: what the undo takes off is a receipt of its own, and a receipt names one.
    admin = await create_user(temp_db, Role.ADMIN)
    put_back = await queue.reverse(
        admin, "r1", json.dumps({"matches": [{"asset_id": "a1", "box_id": box}]})
    )

    assert put_back is True
    assert (await service.waiting(limit=0))[1] == 1


async def test_an_undo_of_a_file_this_account_may_not_see_puts_it_back_for_nobody(
    temp_db: Database,
) -> None:
    service = await _service(temp_db)
    box = await _a_box(service)
    await _recognised(service, temp_db, "a1")
    await service.settle("a1", box, applied=True)
    queue = TaggerQueue(service, _Access(hidden={"a1"}))  # type: ignore[arg-type]

    put_back = await queue.reverse(
        ADMIN, "r1", json.dumps({"matches": [{"asset_id": "a1", "box_id": box}]})
    )

    assert put_back is False
    assert (await service.waiting(limit=0))[1] == 0


async def test_an_undo_of_several_puts_back_the_ones_it_can(
    temp_db: Database,
) -> None:
    """A decision covers a page, and by the time it is taken back some of what it named can be
    gone. Putting back what is still there beats refusing the whole undo."""
    service = await _service(temp_db)
    box = await _a_box(service)
    await _recognised(service, temp_db, "a1")
    await service.settle("a1", box, applied=True)
    queue = TaggerQueue(service, _Access())  # type: ignore[arg-type]

    # A real user: what the undo takes off is a receipt of its own, and a receipt names one.
    admin = await create_user(temp_db, Role.ADMIN)
    put_back = await queue.reverse(
        admin,
        "r1",
        json.dumps(
            {
                "matches": [
                    {"asset_id": "a1", "box_id": box},
                    # Never settled, so there is nothing to put back for this one.
                    {"asset_id": "a1", "box_id": "another-box"},
                ]
            }
        ),
    )

    assert put_back is True
    assert (await service.waiting(limit=0))[1] == 1


async def test_a_record_missing_a_field_it_once_had_is_a_decision_that_cannot_be_reversed(
    temp_db: Database,
) -> None:
    """Every field is reached for rather than assumed. A record can outlive the version that wrote
    it, and "nothing was put back" is the honest reading, where reaching straight in would fail
    the request and read as the undo being broken."""
    service = await _service(temp_db)
    queue = TaggerQueue(service, _Access())  # type: ignore[arg-type]

    for payload in (
        "not json",
        json.dumps({"matches": ["not a record"]}),
        json.dumps({"matches": [{"box_id": "b"}]}),
        json.dumps({"matches": [{"asset_id": "a1"}]}),
    ):
        assert await queue.reverse(ADMIN, "r1", payload) is False


async def test_names_are_read_once_per_kind_and_only_where_the_viewer_may_see_them() -> None:
    """A person, a Site and a tag each named by their own batched read; one this viewer may not
    see is absent rather than named."""
    from types import SimpleNamespace

    from sift.slices.stash_boxes.queue import names_of

    asked: list[tuple[str, list[str]]] = []

    class Access:
        async def visible_people(self, viewer: Viewer, ids: list[str]) -> dict[str, object]:
            asked.append(("people", list(ids)))
            return {"p1": SimpleNamespace(name="Ada Byron")}

        async def visible_sites(self, viewer: Viewer, ids: list[str]) -> dict[str, object]:
            asked.append(("sites", list(ids)))
            return {"s1": SimpleNamespace(name="Northlight")}

        async def visible_tags(self, viewer: Viewer, ids: list[str]) -> dict[str, object]:
            asked.append(("tags", list(ids)))
            return {"t1": SimpleNamespace(name="outdoor")}

    named = await names_of(
        Access(),  # type: ignore[arg-type]
        Viewer(id="account-1", role=Role.ADMIN),
        [
            (Subject.PERSON, "p1"),
            (Subject.SITE, "s1"),
            (Subject.TAG, "t1"),
            (Subject.TAG, "t2"),
        ],
    )

    assert named == {
        (Subject.PERSON, "p1"): "Ada Byron",
        (Subject.SITE, "s1"): "Northlight",
        (Subject.TAG, "t1"): "outdoor",
    }
    assert asked == [("people", ["p1"]), ("sites", ["s1"]), ("tags", ["t1", "t2"])]
