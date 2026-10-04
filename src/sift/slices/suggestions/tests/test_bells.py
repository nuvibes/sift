# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who a folder suggestion tells. A press announces to the People and Sites it moved; the pass
rings the library bell once, when it ends, and never per folder; a Yes's receipt says which person
it was about, which is what the History threads fold its namings into."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository, Viewer
from sift.kernel.changes import About
from sift.kernel.db import Database
from sift.slices.suggestions import service_confirm, service_pass
from sift.slices.suggestions.service import SuggestionService
from sift.slices.suggestions.store import Store
from sift.slices.suggestions.tests.conftest import FakeFaces, FakePreferences, Library
from sift.slices.workbench.store import Store as DecisionStore

pytestmark = pytest.mark.integration


async def test_a_pass_rings_the_library_bell_once_and_a_pass_that_wrote_nothing_none(
    service: SuggestionService,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every writer of the pass says nothing, so without this Organize > Suggestions, People and
    Sites on another tab would keep what the pass changed until they reloaded."""
    rung: list[object] = []
    monkeypatch.setattr(service_pass, "announce_now", lambda _who, about: rung.append(about))
    for index in range(5):
        await add_file(library, f"Nadia Vance/clip{index}.mp4")
        await add_file(library, f"Orla Fennimore/clip{index}.mp4")
    await name_folders()
    faces.looked = {"Nadia Vance": (5, 5), "Orla Fennimore": (5, 5)}
    faces.piles_here = {"Nadia Vance": {"pile-1": 5}, "Orla Fennimore": {"pile-2": 5}}

    assert await service.rebuild() == 2
    assert rung == [About.LIBRARY]
    assert await service.rebuild() == 0
    assert rung == [About.LIBRARY]


async def test_a_yes_tells_the_library_and_its_receipt_names_the_person(
    store: Store,
    access: Repository,
    preferences: FakePreferences,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Yes puts a person on a folder's files and tells every other tab; its receipt names the
    person beyond being a subject, so the History fold can tell its namings are its own."""
    service = SuggestionService(
        store=store,
        access=access,
        faces=faces,
        preferences=preferences,
        recorder=DecisionStore(temp_db),
    )
    told: list[object] = []
    monkeypatch.setattr(service_confirm, "announce", lambda _who, about: told.append(about))
    for index in range(5):
        await add_file(library, f"Nadia Vance/clip{index}.mp4")
    await name_folders()
    faces.looked = {"Nadia Vance": (5, 5)}
    faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
    await service.rebuild()
    claim = (await service.pending(admin)).items[0]

    applied = await service.confirm(admin, claim.id)

    assert About.LIBRARY in told
    row = await temp_db.fetch_one(
        "SELECT object_kind, object_id FROM workbench_decisions WHERE id = ?",
        (applied.decision_id,),
    )
    assert row is not None
    assert (row["object_kind"], row["object_id"]) == ("person", applied.person_id)


class _Moved:
    """A folder's faces stamp, moved by the test so the pass reads the folder again."""

    def as_text(self) -> str:
        return "moved"


async def test_a_pass_whose_only_write_is_a_may_be_card_still_rings_its_bell(
    service: SuggestionService,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The folder pass puts up the may-be card on Faces > Groups and rings even when that is all
    it wrote, or a Groups page open on another tab would never show the card."""
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES ('nadia', 'Nadia Vance', 0)", ()
    )
    for index in range(6):
        await add_file(library, f"Nadia Vance/clip{index}.mp4")
    await name_folders()
    faces.looked = {"Nadia Vance": (6, 5)}
    faces.piles_here = {"Nadia Vance": {"pile-1": 4, "pile-2": 1}}
    await service.rebuild()
    rung: list[object] = []
    monkeypatch.setattr(service_pass, "announce_now", lambda _who, about: rung.append(about))
    folder = await temp_db.fetch_one("SELECT id FROM folders WHERE name = 'Nadia Vance'")
    assert folder is not None

    async def moved() -> dict[str, object]:
        return {str(folder["id"]): _Moved()}

    monkeypatch.setattr(faces, "stamps", moved)
    faces.piles_here = {"Nadia Vance": {"pile-1": 5}}
    faces.proposed.clear()

    assert await service.rebuild() == 0
    assert faces.proposed == [("pile-1", "nadia", str(folder["id"]), 5, 5)]
    assert rung == [About.LIBRARY]
