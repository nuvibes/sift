# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Yes on a name answers every folder asking it (`namesakes.file_namesakes_on`), and its Undo
takes every one of them back."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import sift.slices.workbench.schema  # noqa: F401  (registers the ledger's table)
from sift.kernel.access import Repository, Viewer
from sift.kernel.access.history_person import history_of_person
from sift.kernel.access.worded import worded_or_stored
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.wire import history_event
from sift.kernel.workbench import Workbench
from sift.slices.suggestions.queue import FolderQueue
from sift.slices.suggestions.service import SuggestionService
from sift.slices.suggestions.store import Store
from sift.slices.suggestions.tests.conftest import FakeFaces, FakePreferences, Library
from sift.slices.workbench.service import WorkbenchService
from sift.slices.workbench.store import Store as DecisionStore

pytestmark = pytest.mark.integration


@pytest.fixture
async def decisions(temp_db: Database) -> DecisionStore:
    return DecisionStore(temp_db)


@pytest.fixture
async def recording(
    store: Store,
    access: Repository,
    faces: FakeFaces,
    preferences: FakePreferences,
    decisions: DecisionStore,
) -> SuggestionService:
    """The service as the application builds it, writing a receipt for every decision."""
    return SuggestionService(
        store=store, access=access, faces=faces, preferences=preferences, recorder=decisions
    )


@pytest.fixture
def undoing(recording: SuggestionService, decisions: DecisionStore) -> WorkbenchService:
    workbench = Workbench()
    workbench.register(FolderQueue(recording))
    return WorkbenchService(store=decisions, workbench=workbench)


async def _people_of(db: Database, asset_id: str) -> set[str]:
    rows = await db.fetch_all("SELECT person_id FROM asset_people WHERE asset_id = ?", (asset_id,))
    return {str(row["person_id"]) for row in rows}


async def test_a_yes_files_the_other_folder_of_that_name_and_takes_its_question_back(
    service: SuggestionService,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    admin: Viewer,
) -> None:
    first = [await add_file(library, f"Models/Nadia Vance/clip{i}.mp4") for i in range(3)]
    other = [await add_file(library, f"Archive/Nadia Vance/clip{i}.mp4") for i in range(2)]
    await name_folders()
    faces.looked = {"Nadia Vance": (3, 0)}
    await service.rebuild()
    asked = (await service.pending(admin)).items
    assert len(asked) == 2

    applied = await service.confirm(admin, asked[0].id)

    assert (await service.pending(admin)).items == []
    for one in [*first, *other]:
        assert await _people_of(temp_db, one.asset.id) == {applied.person_id}
    # Neither folder is listed as filed without asking: the press answered both, and the Undo of
    # both is the Yes's own receipt.
    assert await service.filed(admin) == []


async def test_the_undo_of_a_yes_takes_back_every_folder_that_yes_filed(
    recording: SuggestionService,
    undoing: WorkbenchService,
    decisions: DecisionStore,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    admin: Viewer,
) -> None:
    files = [
        await add_file(library, f"{place}/Nadia Vance/clip{i}.mp4")
        for place in ("Models", "Archive", "Phone")
        for i in range(2)
    ]
    await name_folders()
    faces.on = False
    await recording.rebuild()
    asked = (await recording.pending(admin)).items
    assert len(asked) == 3

    applied = await recording.confirm(admin, asked[0].id)
    assert applied.files == len(files)
    found, total = await decisions.recent(limit=10, offset=0)
    assert total == 1
    assert "2 other folders with that name answered" in found[0].detail

    assert (await undoing.undo(admin, found[0].id)).put_back == 1

    for one in files:
        assert await _people_of(temp_db, one.asset.id) == set()
    standing = await temp_db.fetch_all("SELECT folder_id FROM folder_people")
    assert standing == []
    again = (await recording.pending(admin)).items
    assert sorted(one.id for one in again) == sorted(one.id for one in asked)
    assert await recording.filed(admin) == []


async def test_a_yes_reads_on_history_as_a_sentence_with_the_person_and_the_folder_linked(
    recording: SuggestionService,
    decisions: DecisionStore,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    admin: Viewer,
) -> None:
    """The line History draws for the Yes, through the one reader every History screen asks."""
    for place in ("Models", "Archive"):
        for i in range(2):
            await add_file(library, f"{place}/Nadia Vance/clip{i}.mp4")
    await name_folders()
    faces.on = False
    await recording.rebuild()
    asked = (await recording.pending(admin)).items
    applied = await recording.confirm(admin, asked[0].id)
    workbench = Workbench()
    workbench.register(FolderQueue(recording))
    found, _ = await decisions.recent(limit=10, offset=0)

    line = (await worded_or_stored(decisions.database, workbench, admin, [(found[0], 1)]))[
        found[0].id
    ]

    assert line.said == "You filed 4 files under Nadia Vance from the folder Nadia Vance"
    assert [(one.kind, one.id) for one in line.links] == [
        ("person", applied.person_id),
        ("folder", asked[0].folder_id),
    ]
    assert line.more.endswith(", 1 other folder with that name answered.")
    # The person's own History says the same line, and what else the Yes wrote under it.
    thread = await history_of_person(decisions.database, admin, applied.person_id, bench=workbench)
    (said,) = [one for one in thread if one.what.startswith("You filed")]
    assert said.what == "You filed 4 files under them from the folder Nadia Vance"
    assert history_event(said).more == line.more


async def test_a_namesake_settled_meanwhile_is_left_and_one_with_nothing_to_file_is_only_answered(
    service: SuggestionService,
    store: Store,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
) -> None:
    """The namesakes are read before the Yes's write opens. A question somebody else answered in
    between is left as they left it; a folder whose every file is held back is answered and files
    nothing; and a folder already answered as this person files its files and keeps its answer."""
    from sift.slices.suggestions.namesakes import Namesake, file_namesakes_on

    for place in ("Models", "Archive", "Phone"):
        await add_file(library, f"{place}/Nadia Vance/clip.mp4")
    await name_folders()
    faces.on = False
    await service.rebuild()
    settled, empty, standing = await store.every_pending()
    person_id = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Nadia Vance', 0)", (person_id,)
    )
    async with store.write() as connection:
        assert await store.settle_on(connection, settled.id, "rejected")
        assert await store.remember_folder_person_on(
            connection, folder_id=standing.folder_id, person_id=person_id
        )
        filed_here = tuple(await store.assets_under(standing.folder_id))
        also = await file_namesakes_on(
            service,
            connection,
            [
                Namesake(
                    settled.id,
                    settled.folder_id,
                    tuple(await store.assets_under(settled.folder_id)),
                ),
                Namesake(empty.id, empty.folder_id, ()),
                Namesake(standing.id, standing.folder_id, filed_here),
            ],
            person_id,
        )

    assert also.answered == ((empty.id, empty.folder_id), (standing.id, standing.folder_id))
    assert also.attributed == filed_here
    assert also.remembered == ()
    states = {
        str(row["id"]): str(row["state"])
        for row in await temp_db.fetch_all("SELECT id, state FROM folder_claims")
    }
    assert states[settled.id] == "rejected"


async def test_the_undo_of_a_yes_withdraws_the_may_be_cards_its_namesakes_put_up_and_says_so(
    recording: SuggestionService,
    undoing: WorkbenchService,
    decisions: DecisionStore,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A may-be card on a namesake folder had the Yes as its reason, so the Undo takes it down
    with the rest, and rings the library bell because the card's own writer tells nobody."""
    from sift.kernel.changes import About
    from sift.slices.suggestions import service_undo

    rung: list[object] = []
    monkeypatch.setattr(service_undo, "announce_now", lambda _who, about: rung.append(about))

    async def withdrew_one(folder_id: str, person_id: str) -> int:
        faces.withdrawn.append((folder_id, person_id))
        return 1

    monkeypatch.setattr(faces, "withdraw_proposals", withdrew_one)
    for place in ("Models", "Archive"):
        await add_file(library, f"{place}/Nadia Vance/clip.mp4")
    await name_folders()
    faces.on = False
    await recording.rebuild()
    asked = (await recording.pending(admin)).items
    applied = await recording.confirm(admin, asked[0].id)
    found, _total = await decisions.recent(limit=10, offset=0)

    await undoing.undo(admin, found[0].id)

    assert [person for _folder, person in faces.withdrawn] == [applied.person_id]
    assert rung == [About.LIBRARY]
