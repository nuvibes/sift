# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a folder answer does with FACES: a Yes teaches, an undo unteaches, a filed folder proposes.

Three things the folder reader hands across the seam and never does itself.

**A Yes on the Folders card teaches.** It names the group inside its transaction; the faces it
named are then handed over to become references and remembered decisions, and a re-match follows,
so a rescan keeps the names.

**An undo takes that back**, for the same faces and the person the folder was remembered as.

**A folder filed under somebody proposes its main unnamed group as them**, by the rule the
Folders card uses to say a group IS a folder (three fifths of the files with a face, at least
three), which is the guard against proposing a regular co-star.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from sift.kernel.access import Viewer
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.suggestions.service import SuggestionService
from sift.slices.suggestions.tests.conftest import FakeFaces, Library

pytestmark = pytest.mark.integration


async def _person(db: Database, name: str) -> str:
    person_id = new_id()
    await db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person_id, name)
    )
    return person_id


async def _folder(db: Database, name: str) -> str:
    row = await db.fetch_one("SELECT id FROM folders WHERE name = ?", (name,))
    assert row is not None
    return str(row["id"])


async def test_a_yes_on_the_folders_card_teaches_from_the_faces_it_named(
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    admin: Viewer,
) -> None:
    for index in range(5):
        await add_file(library, f"Nadia Vance/clip{index}.mp4")
    await name_folders()
    faces.looked = {"Nadia Vance": (5, 5)}
    faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
    await service.rebuild()

    claim = (await service.pending(admin)).items[0]
    applied = await service.confirm(admin, claim.id)

    assert faces.named_groups == [("pile-1", applied.person_id)]
    assert faces.taught == [(("face-in-pile-1",), applied.person_id)]


async def test_a_yes_that_named_no_group_teaches_nothing(
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    admin: Viewer,
) -> None:
    """A folder of body-only clips is answered on its name alone. No face was named, so there is
    nothing to teach from and no re-match to ask for."""
    for index in range(3):
        await add_file(library, f"Nadia Vance/clip{index}.mp4")
    await name_folders()
    faces.looked = {"Nadia Vance": (3, 0)}
    await service.rebuild()

    claim = (await service.pending(admin)).items[0]
    await service.confirm(admin, claim.id)

    assert faces.named_groups == []
    assert faces.taught == []


async def test_taking_the_yes_back_unteaches_the_same_faces(
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    admin: Viewer,
) -> None:
    for index in range(5):
        await add_file(library, f"Nadia Vance/clip{index}.mp4")
    await name_folders()
    faces.looked = {"Nadia Vance": (5, 5)}
    faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
    await service.rebuild()
    claim = (await service.pending(admin)).items[0]
    applied = await service.confirm(admin, claim.id)

    await service.take_back(applied.written, claim_id=claim.id)

    assert faces.unnamed == ["face-in-pile-1"]
    assert faces.untaught == [(("face-in-pile-1",), applied.person_id)]


async def test_a_folder_filed_under_a_known_name_proposes_its_main_group_as_them(
    service: SuggestionService,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
) -> None:
    """Rung two files the folder silently and still proposes the group. Four of the five files with
    a face are one unnamed group (most likely her, not yet recognized), so it is proposed."""
    nadia = await _person(temp_db, "Nadia Vance")
    for index in range(6):
        await add_file(library, f"Nadia Vance/clip{index}.mp4")
    await name_folders()
    faces.looked = {"Nadia Vance": (6, 5)}
    faces.piles_here = {"Nadia Vance": {"pile-1": 4, "pile-2": 1}}

    await service.rebuild()

    folder = await _folder(temp_db, "Nadia Vance")
    assert faces.proposed == [("pile-1", nadia, folder, 4, 5)]


async def test_a_group_short_of_three_fifths_is_not_proposed_and_what_was_is_taken_back(
    service: SuggestionService,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
) -> None:
    """The co-star guard. A group in half her files is somebody who is often WITH her, and a
    majority read off the group alone would propose exactly them."""
    nadia = await _person(temp_db, "Nadia Vance")
    for index in range(6):
        await add_file(library, f"Nadia Vance/clip{index}.mp4")
    await name_folders()
    faces.looked = {"Nadia Vance": (6, 6)}
    faces.piles_here = {"Nadia Vance": {"pile-1": 3, "pile-2": 3}}

    await service.rebuild()

    folder = await _folder(temp_db, "Nadia Vance")
    assert faces.proposed == []
    assert faces.withdrawn == [(folder, nadia)]


async def test_an_answered_folder_proposes_its_main_group_on_a_later_pass(
    service: SuggestionService,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
) -> None:
    """A folder filed last week is read again when a file lands in it, and by then its faces may
    have grouped. `Videos` names nobody, so only its standing filing can say whose it is, which
    is the case the answered half exists for."""
    nadia = await _person(temp_db, "Nadia Vance")
    for index in range(3):
        await add_file(library, f"Videos/clip{index}.mp4")
    await name_folders()
    faces.looked = {"Videos": (3, 3)}
    faces.named_here = {"Videos": {nadia: 3}}
    await service.rebuild()
    assert faces.proposed == []

    await add_file(library, "Videos/clip3.mp4")
    await name_folders()
    faces.looked = {"Videos": (4, 4)}
    faces.named_here = {}
    faces.piles_here = {"Videos": {"pile-1": 4}}
    await service.rebuild()

    folder = await _folder(temp_db, "Videos")
    assert faces.proposed == [("pile-1", nadia, folder, 4, 4)]


async def test_with_recognition_off_nothing_is_proposed_or_withdrawn(
    service: SuggestionService,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
) -> None:
    """Off is not "no main group": a proposal made while it was on is not the pass's to take back
    because nobody is looking now."""
    await _person(temp_db, "Nadia Vance")
    for index in range(3):
        await add_file(library, f"Nadia Vance/clip{index}.mp4")
    await name_folders()
    faces.on = False

    await service.rebuild()

    assert faces.proposed == []
    assert faces.withdrawn == []
