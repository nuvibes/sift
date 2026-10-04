# SPDX-License-Identifier: AGPL-3.0-or-later
"""This feature as the workbench sees it: a count, a sentence, and how to take a decision back.

The reversal reads a record that may have been written by a different version of Sift: a restored
backup, an older release, a row somebody edited. So every field is reached for rather than assumed,
and a record it cannot read answers "nothing was put back" rather than failing the request. Those
are different things to whoever pressed Undo: one says the record is unreadable, the other says the
undo is broken.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from sift.kernel.access import Viewer
from sift.kernel.workbench import ASSET
from sift.slices.suggestions.queue import FiledQueue, FolderQueue
from sift.slices.suggestions.service import SuggestionService
from sift.slices.suggestions.tests.conftest import FakeFaces, Library

pytestmark = pytest.mark.integration


@pytest.fixture
def folders(service: SuggestionService) -> FolderQueue:
    return FolderQueue(service)


async def test_what_was_filed_without_asking_is_a_record_on_the_same_page(
    service: SuggestionService, admin: Viewer
) -> None:
    """A tab beside the questions, never a card: it cannot empty and it is not work. Each folder it
    lists wrote one receipt, whose Undo takes the folder back whole."""
    filed = FiledQueue(service)

    assert filed.group == FolderQueue.group
    assert filed.reversible is True
    assert await filed.available() is True
    found = await filed.survey(admin)
    assert found.name == "filed"
    assert found.count == 0
    assert await filed.pictures_of(admin, "{}") == ()
    assert await filed.reverse(admin, "receipt", "{}") is False


@pytest.mark.parametrize(
    "payload",
    [
        "not a record",
        "[]",
        '{"kind": "confirmed", "folder_id": "f", "person_id": "p"}',
        '{"kind": "silent", "person_id": "p"}',
        '{"kind": "silent", "folder_id": "f"}',
    ],
)
async def test_a_record_that_is_not_a_silent_write_puts_nothing_back(
    service: SuggestionService, admin: Viewer, payload: str
) -> None:
    filed = FiledQueue(service)

    assert await filed.pictures_of(admin, payload) == ()
    assert await filed.reverse(admin, "receipt", payload) is False


async def test_a_silent_write_about_a_folder_that_has_gone_puts_nothing_back(
    service: SuggestionService, admin: Viewer
) -> None:
    """A record whose file list does not read as one names no files, and a folder and a person
    that are no longer here have no standing answer to forget and nothing to refuse."""
    filed = FiledQueue(service)
    payload = '{"kind": "silent", "folder_id": "f", "person_id": "p", "assets": 3, "linked": true}'

    assert await filed.pictures_of(admin, payload) == ()
    assert await filed.reverse(admin, "receipt", payload) is False


async def test_a_silent_writes_pictures_are_the_files_it_filed_that_the_reader_may_see(
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
) -> None:
    """The record's row draws the files the folder pass put the person on, each linking to its
    file, and never one the reader may not be shown."""
    added = await add_file(library, "Reya Solberg/clip.mp4")
    payload = json.dumps(
        {
            "kind": "silent",
            "folder_id": "f",
            "person_id": "p",
            "assets": [added.asset.id, "01HX0000000000000000GONE1"],
        }
    )

    shown = await FiledQueue(service).pictures_of(admin, payload)

    assert [(one.kind, one.id, one.href) for one in shown] == [
        (ASSET, added.asset.id, f"/asset/{added.asset.id}")
    ]


async def test_it_is_always_there(folders: FolderQueue) -> None:
    """Reading folder names needs nothing switched on and no model downloaded, so this queue is
    never absent from the board: a count of zero here means there is nothing left to answer,
    which is the state the whole screen is trying to reach."""
    assert await folders.available() is True


async def test_it_says_what_is_waiting_and_what_the_decision_means(
    folders: FolderQueue,
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    admin: Viewer,
) -> None:
    await add_file(library, "Reya Solberg/clip.mp4")
    await name_folders()
    faces.on = False
    await service.rebuild()

    found = await folders.survey(admin)

    assert found.count == 1
    assert found.pending is True
    # The sentence is the card's, and it says what pressing yes actually does.
    assert "Yes adds every file in it to that person" in found.decision


async def test_the_pictures_are_files_rather_than_the_questions(
    folders: FolderQueue,
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    admin: Viewer,
) -> None:
    """A claim's own id addresses no picture. Handing it over as one would draw a broken image on
    every card, which is the kind of fault that looks like a styling problem."""
    added = await add_file(library, "Reya Solberg/clip.mp4")
    await name_folders()
    faces.on = False
    await service.rebuild()

    found = await folders.survey(admin)

    assert [(one.kind, one.id) for one in found.preview] == [(ASSET, added.asset.id)]


async def test_each_still_on_the_card_leads_to_the_folder_it_came_from(
    folders: FolderQueue,
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    admin: Viewer,
) -> None:
    """A still on a board card leads to the decision.

    The FOLDER rather than the file: what the card is about is the folder waiting for a yes or a no,
    and an address for one file inside it leads away from the decision. A claim is answered where it
    sits and has no screen of its own, so the address is the queue's page and a fragment naming the
    card: the arrangement the two duplicate queues already use, and the page marks its cards with
    the same words.
    """
    await add_file(library, "Reya Solberg/clip.mp4")
    await name_folders()
    faces.on = False
    await service.rebuild()

    found = await folders.survey(admin)

    [claim] = (await service.pending(admin)).items
    assert [one.href for one in found.preview] == [f"/organize/folders#claim-{claim.id}"]


async def test_a_queue_with_nothing_in_it_counts_nothing_and_draws_nothing(
    folders: FolderQueue, admin: Viewer
) -> None:
    found = await folders.survey(admin)

    assert found.count == 0
    assert found.preview == ()


class TestARecordItCannotRead:
    """Every one of these answers "nothing was put back" rather than raising.

    A payload from a version that wrote different fields is not a failure of the undo: there is
    simply nothing here it knows how to reverse, and saying so is the honest answer.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            "null",
            '"a string"',
            "{}",
            '{"kind": "confirmed"}',
            '{"kind": "confirmed", "claim_id": "one"}',
            '{"kind": "confirmed", "claim_id": "one", "written": "not an object"}',
            '{"kind": "ignored", "claim_id": "one"}',
        ],
    )
    async def test_it_puts_nothing_back(
        self, folders: FolderQueue, admin: Viewer, payload: str
    ) -> None:
        assert await folders.reverse(admin, "decision-1", payload) is False

    async def test_and_a_record_naming_nothing_that_exists_puts_nothing_back(
        self, folders: FolderQueue, admin: Viewer
    ) -> None:
        """Well-formed and about a claim this install has never had. The rows it names are not here
        either, so there is nothing to detach and nothing to reopen."""
        payload = json.dumps(
            {
                "kind": "confirmed",
                "claim_id": "01HX0000000000000000000000",
                "written": {"attributed": [], "faces": [], "created_people": []},
            }
        )

        assert await folders.reverse(admin, "decision-1", payload) is False


class TestThePicturesOnARecord:
    """What a decision filed, drawn on the record of having filed it.

    The record says "47 files under Reya Solberg". The pictures are what make that checkable
    without going and looking, and they are asked for through the resolver rather than trusted
    from the record: a file restricted since the decision is one this user may no longer be
    shown, and having filed it is not a licence to draw it.
    """

    async def test_they_are_the_files_the_decision_filed(
        self,
        folders: FolderQueue,
        service: SuggestionService,
        library: Library,
        add_file: Callable[..., Any],
        faces: FakeFaces,
        name_folders: Callable[[], Any],
        admin: Viewer,
    ) -> None:
        added = await add_file(library, "Reya Solberg/clip.mp4")
        payload = json.dumps(
            {
                "kind": "confirmed",
                "claim_id": "one",
                "written": {
                    "attributed": [[added.asset.id, "person-1"]],
                    "faces": [],
                    "created_people": [],
                },
            }
        )

        shown = await folders.pictures_of(admin, payload)

        assert [(one.kind, one.id) for one in shown] == [(ASSET, added.asset.id)]
        # Straight to the file, which is the thing that was filed under somebody.
        assert [one.href for one in shown] == [f"/asset/{added.asset.id}"]

    async def test_a_file_this_account_may_not_be_shown_is_not_drawn(
        self,
        folders: FolderQueue,
        admin: Viewer,
    ) -> None:
        """The record still shows (it is a record of what this user itself did), and the
        pictures of what it may no longer see do not."""
        payload = json.dumps(
            {
                "kind": "confirmed",
                "claim_id": "one",
                "written": {
                    "attributed": [["01HX0000000000000000GONE1", "person-1"]],
                    "faces": [],
                    "created_people": [],
                },
            }
        )

        assert await folders.pictures_of(admin, payload) == ()

    @pytest.mark.parametrize(
        "payload",
        [
            "not json at all",
            '"a string"',
            "{}",
            '{"kind": "confirmed", "written": "not an object"}',
            '{"kind": "confirmed", "written": {"attributed": []}}',
        ],
    )
    async def test_a_record_it_cannot_read_shows_nothing_rather_than_failing(
        self, folders: FolderQueue, admin: Viewer, payload: str
    ) -> None:
        """Same rule as the undo beside it: records outlive the code that wrote them, and a screen
        listing what has already been done must not be the place that discovers it."""
        assert await folders.pictures_of(admin, payload) == ()


async def test_the_board_card_resolves_no_folder(
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The board's card draws the count and the stills, so no row is resolved.

    Resolving a row reads the faces in its folder, the near miss against every name and the names
    in the filenames, which is what the Folders page draws beside each question and what no card
    shows. Counted on the face read, which is the dear one.
    """
    for folder in ("Reya Solberg", "Bryn Calloway", "Cassia Lynn"):
        await add_file(library, f"{folder}/clip.mp4")
    await name_folders()
    faces.on = False
    await service.rebuild()

    asked: list[str] = []
    real = faces.faces_in

    async def counted(folder_id: str) -> Any:
        asked.append(folder_id)
        return await real(folder_id)

    monkeypatch.setattr(faces, "faces_in", counted)

    # The page resolves every row it draws, which is what makes the count below mean something:
    # three questions are waiting, and all three are read when a screen shows all three.
    page = await service.pending(admin)
    assert len(page.items) == 3
    assert len(asked) == 3
    asked.clear()

    outline = await service.outline(admin)

    assert outline.total == 3
    assert len(outline.covers) == 3, "every still is on the card"
    assert asked == [], f"the card read the faces of {len(asked)} folders and draws none of them"
    assert [claim for claim, _cover in outline.covers] == [one.id for one in page.items]
