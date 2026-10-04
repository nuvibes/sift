# SPDX-License-Identifier: AGPL-3.0-or-later
"""A folder read again by a new reader keeps only the questions that reader asks.

A question is stored by the folder and the name it proposes, so a reader that now spells a folder's
name differently writes a second question beside the first: one folder, two cards, and the old one
is the reading that was improved away. The pass that reads the folder takes back what it no longer
asks, and only that: an answer is somebody's word, and a question another folder's reading may
still be asking waits until that folder is read too.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from sift.kernel.access import Viewer
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.suggestions import naming, service_pass
from sift.slices.suggestions.service import SuggestionService
from sift.slices.suggestions.tests.conftest import FakeFaces, Library

pytestmark = pytest.mark.integration


def _an_older_reader(monkeypatch: pytest.MonkeyPatch, spells: dict[str, str]) -> None:
    """Read each folder named in `spells` as naming somebody else, the way an older reader would."""

    def read(chain: tuple[str, ...], **kwargs: Any) -> naming.Reading:
        reading = naming.read_chain(chain, **kwargs)
        other = next((name for folder, name in spells.items() if folder in chain), None)
        return replace(reading, name=other) if other and reading.name else reading

    monkeypatch.setattr(service_pass, "read_chain", read)


def _a_newer_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reader as it is, under the next version, which is what makes every folder read again."""
    monkeypatch.setattr(service_pass, "read_chain", naming.read_chain)
    monkeypatch.setattr(service_pass, "PARSER_VERSION", naming.PARSER_VERSION + 1)


async def _claims(temp_db: Database) -> list[tuple[str, str]]:
    rows = await temp_db.fetch_all("SELECT proposed, state FROM folder_claims ORDER BY proposed")
    return [(str(row["proposed"]), str(row["state"])) for row in rows]


async def test_a_question_the_new_reader_no_longer_asks_is_taken_back(
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(5):
        await add_file(library, f"Nadia Vance/clip{index}.mp4")
    await name_folders()
    faces.looked = {"Nadia Vance": (5, 0)}
    _an_older_reader(monkeypatch, {"Nadia Vance": "Esme Wrenfield"})
    await service.rebuild()
    assert [one.proposed for one in (await service.pending(admin)).items] == ["Esme Wrenfield"]

    _a_newer_reader(monkeypatch)
    await service.rebuild()

    assert [one.proposed for one in (await service.pending(admin)).items] == ["Nadia Vance"]


async def test_an_answered_question_is_kept_whatever_the_new_reader_asks(
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    admin: Viewer,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(5):
        await add_file(library, f"Nadia Vance/clip{index}.mp4")
    await name_folders()
    faces.looked = {"Nadia Vance": (5, 0)}
    _an_older_reader(monkeypatch, {"Nadia Vance": "Esme Wrenfield"})
    await service.rebuild()
    (old,) = (await service.pending(admin)).items
    await service.reject(admin, old.id)

    _a_newer_reader(monkeypatch)
    await service.rebuild()

    assert await _claims(temp_db) == [("Esme Wrenfield", "rejected"), ("Nadia Vance", "pending")]


async def test_a_question_another_unread_folder_may_still_ask_is_kept(
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both of her folders ask about her folder above them. Only one of them moves, so only one is
    read: the question only the other one asks is still asked, and stays."""
    await add_file(library, "Nadia Vance/Videos/one.mp4")
    await add_file(library, "Nadia Vance/Pics/two.mp4")
    await name_folders()
    faces.looked = {"Nadia Vance": (3, 0)}
    _an_older_reader(monkeypatch, {"Pics": "Esme Wrenfield"})
    await service.rebuild()
    before = await _claims(temp_db)
    assert before == [("Esme Wrenfield", "pending"), ("Nadia Vance", "pending")]

    await add_file(library, "Nadia Vance/Videos/three.mp4")
    await service.rebuild()

    assert await _claims(temp_db) == before


async def _somebody_here(temp_db: Database, name: str) -> None:
    """Somebody already in the library, which is the premise of naming them from a title."""
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (new_id(), name)
    )


async def test_a_username_folders_question_asked_again_is_the_one_already_standing(
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    name_folders: Callable[[], Any],
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A folder named for one username on one site, read again under a new reader that reads it
    the same way: the question it asks is the one standing, and nothing is written twice."""
    await add_file(library, "harlowquin (RedGifs)/clip.mp4")
    await name_folders()
    await service.rebuild()
    before = await _claims(temp_db)
    assert before == [("harlowquin", "pending")]

    _a_newer_reader(monkeypatch)
    await service.rebuild()

    assert await _claims(temp_db) == before


async def test_a_person_named_in_the_files_asked_again_is_the_one_already_standing(
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    name_folders: Callable[[], Any],
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same for a folder named after nobody whose files name somebody the library holds."""
    await _somebody_here(temp_db, "Talia Brandt")
    for title in (
        "An evening with Talia Brandt.mp4",
        "Talia Brandt backstage clip two.mp4",
        "1080 Talia Brandt in Slow Motion   QHB Fashion Week 2023.mp4",
        "beach sunset.mp4",
    ):
        await add_file(library, f"Videos/{title}")
    await name_folders()
    await service.rebuild()
    before = await _claims(temp_db)
    assert before == [("Talia Brandt", "pending")]

    _a_newer_reader(monkeypatch)
    await service.rebuild()

    assert await _claims(temp_db) == before
