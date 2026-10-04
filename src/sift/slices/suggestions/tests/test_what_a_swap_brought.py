# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the folder pass makes of the folders a swap made, and of a folder holding other people's.

A swap lands everything it brings under one folder, `Swap-<short id>`, inside it `People/<person>`
for a taken person's files, `Sites/<Site>` for a file filed under a Site, and the swap's folder
itself for a file with neither. Read as an ordinary folder, the swap's folder is a folder whose
faces are mostly the person with the most files in it, so rung 1 would give it to her without
asking and the silent write would reach everybody else's files: a face nobody has named vetoes
nothing, and a file with no face vetoes nothing either.

The rule held here: the swap's folder, its `People` and its `Sites` are containers and never one
person's; a person's folder under `People` is hers by its name; and a folder holding somebody
else's own folder is not given to one person by its faces, swap or no swap.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

# Imported for its side effect: the record of decisions is the workbench slice's table.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository, Viewer, attribute_assets_on
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.workbench import Workbench
from sift.slices.suggestions.queue import FiledQueue
from sift.slices.suggestions.service import Arrivals, SuggestionService, SwapFolders
from sift.slices.suggestions.store import Store
from sift.slices.suggestions.tests.conftest import FakeFaces, FakePreferences, Library
from sift.slices.workbench.service import WorkbenchService
from sift.slices.workbench.store import Store as DecisionStore

pytestmark = pytest.mark.integration

SWAP = "Swap-5ZSE8PKP"


async def _person(db: Database, name: str) -> str:
    person_id = new_id()
    await db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person_id, name)
    )
    return person_id


async def _folder(db: Database, rel_path: str) -> str:
    row = await db.fetch_one("SELECT id FROM folders WHERE rel_path = ?", (rel_path,))
    assert row is not None
    return str(row["id"])


async def _people_of(db: Database, asset_id: str) -> set[str]:
    rows = await db.fetch_all("SELECT person_id FROM asset_people WHERE asset_id = ?", (asset_id,))
    return {str(row["person_id"]) for row in rows}


async def _answered(db: Database) -> dict[str, set[str]]:
    rows = await db.fetch_all(
        "SELECT f.rel_path AS rel_path, fp.person_id AS person_id "
        "FROM folder_people fp JOIN folders f ON f.id = fp.folder_id"
    )
    found: dict[str, set[str]] = {}
    for row in rows:
        found.setdefault(str(row["rel_path"]), set()).add(str(row["person_id"]))
    return found


async def _files(
    library: Library, add_file: Callable[..., Any], folder: str, count: int
) -> list[str]:
    return [(await add_file(library, f"{folder}/clip{i}.mp4")).asset.id for i in range(count)]


def _swap_service(
    store: Store,
    access: Repository,
    faces: FakeFaces,
    preferences: FakePreferences,
    db: Database,
    *,
    record: bool = True,
) -> SuggestionService:
    """The pass with the swap's record behind it: the swap's folder, its People and its Sites as
    containers, and each person's folder under People as theirs by name. `record=False` is a
    library that holds no record of any swap."""

    async def made() -> Arrivals:
        if not record:
            return Arrivals()
        rows = await db.fetch_all("SELECT id, rel_path FROM folders")
        by_path = {str(row["rel_path"]): str(row["id"]) for row in rows}
        containers = {
            folder_id
            for path, folder_id in by_path.items()
            if path in (SWAP, f"{SWAP}/People", f"{SWAP}/Sites")
            or path.startswith(f"{SWAP}/Sites/")
        }
        by_name = {
            folder_id for path, folder_id in by_path.items() if path.startswith(f"{SWAP}/People/")
        }
        return Arrivals(containers=frozenset(containers), by_name=frozenset(by_name))

    return SuggestionService(
        store=store,
        access=access,
        faces=faces,
        preferences=preferences,
        swap_folders=SwapFolders(made=made),
    )


async def test_a_swap_brings_three_people_and_each_keeps_only_their_own(
    store: Store,
    access: Repository,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    preferences: FakePreferences,
    name_folders: Callable[[], Any],
    admin: Viewer,
) -> None:
    """A swap of three people, small enough to count by hand.

    Her face is named on most of the files with a face (22 of 34, past three fifths), the second
    person's faces are a group nobody has named, the third's are named as them on three of four,
    and a Site's files and three loose ones carry nobody. Read as one folder, the swap's folder
    would be hers.
    """
    nadia = await _person(temp_db, "Nadia Vance")
    harlow = await _person(temp_db, "harlowquin")
    priya = await _person(temp_db, "Priya Sandoval")
    hers = await _files(library, add_file, f"{SWAP}/People/Nadia Vance", 23)
    harlows = await _files(library, add_file, f"{SWAP}/People/harlowquin", 21)
    priyas = await _files(library, add_file, f"{SWAP}/People/Priya Sandoval", 4)
    sites = await _files(library, add_file, f"{SWAP}/Sites/Northlight", 2)
    loose = await _files(library, add_file, SWAP, 3)
    await name_folders()
    faces.looked = {
        SWAP: (53, 34),
        "Nadia Vance": (23, 22),
        "harlowquin": (21, 7),
        "Priya Sandoval": (4, 4),
        "Northlight": (2, 1),
    }
    faces.named_here = {
        SWAP: {nadia: 22, priya: 3},
        "Nadia Vance": {nadia: 21},
        "Priya Sandoval": {priya: 3},
    }
    faces.piles_here = {"harlowquin": {"pile-b": 6}}
    # Her face is named on none of the third person's files; theirs is, on three of the four.
    faces.contradicts = {nadia: set(priyas[:3])}
    service = _swap_service(store, access, faces, preferences, temp_db)

    await service.rebuild()

    for one in hers:
        assert await _people_of(temp_db, one) == {nadia}
    for one in harlows:
        assert await _people_of(temp_db, one) == {harlow}
    for one in priyas:
        assert await _people_of(temp_db, one) == {priya}
    for one in sites + loose:
        assert await _people_of(temp_db, one) == set()
    assert await _answered(temp_db) == {
        f"{SWAP}/People/Nadia Vance": {nadia},
        f"{SWAP}/People/harlowquin": {harlow},
        f"{SWAP}/People/Priya Sandoval": {priya},
    }
    # And no question is asked about the swap's folder or a Site's folder as somebody.
    assert [item.proposed for item in (await service.pending(admin)).items] == []


async def test_a_swap_folder_is_nobodys_even_with_one_person_in_it(
    store: Store,
    access: Repository,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    preferences: FakePreferences,
    name_folders: Callable[[], Any],
) -> None:
    """Nobody else's folder is inside to say it is a collection, and it is still not hers: the
    loose files and the Site's files arrived with nobody on them, and stay that way."""
    nadia = await _person(temp_db, "Nadia Vance")
    await _files(library, add_file, f"{SWAP}/People/Nadia Vance", 6)
    others = await _files(library, add_file, f"{SWAP}/Sites/Northlight", 2)
    others += await _files(library, add_file, SWAP, 2)
    await name_folders()
    faces.looked = {SWAP: (10, 8), "Nadia Vance": (6, 6)}
    faces.named_here = {SWAP: {nadia: 6}, "Nadia Vance": {nadia: 6}}
    service = _swap_service(store, access, faces, preferences, temp_db)

    await service.rebuild()

    for one in others:
        assert await _people_of(temp_db, one) == set()
    assert await _answered(temp_db) == {f"{SWAP}/People/Nadia Vance": {nadia}}


async def test_a_persons_folder_a_swap_made_is_hers_by_name_not_by_a_co_stars_faces(
    store: Store,
    access: Repository,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    preferences: FakePreferences,
    name_folders: Callable[[], Any],
) -> None:
    nadia = await _person(temp_db, "Nadia Vance")
    harlow = await _person(temp_db, "harlowquin")
    harlows = await _files(library, add_file, f"{SWAP}/People/harlowquin", 6)
    await name_folders()
    # Nadia is in five of the six: she is who harlowquin is filmed with.
    faces.looked = {"harlowquin": (6, 6)}
    faces.named_here = {"harlowquin": {nadia: 5}}
    service = _swap_service(store, access, faces, preferences, temp_db)

    await service.rebuild()

    for one in harlows:
        assert await _people_of(temp_db, one) == {harlow}
    assert await _answered(temp_db) == {f"{SWAP}/People/harlowquin": {harlow}}


async def test_a_folder_somebody_named_swap_is_read_like_any_other(
    store: Store,
    access: Repository,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    preferences: FakePreferences,
    name_folders: Callable[[], Any],
) -> None:
    """The rule keys on what a swap RECORDED, never on the name: a library holding no record of a
    swap has no swap folders, and one of its folders called `Swap-...` is hers by her faces."""
    nadia = await _person(temp_db, "Nadia Vance")
    clips = await _files(library, add_file, SWAP, 5)
    await name_folders()
    faces.looked = {SWAP: (5, 5)}
    faces.named_here = {SWAP: {nadia: 5}}
    service = _swap_service(store, access, faces, preferences, temp_db, record=False)

    await service.rebuild()

    for one in clips:
        assert await _people_of(temp_db, one) == {nadia}
    assert await _answered(temp_db) == {SWAP: {nadia}}


async def test_a_folder_holding_somebody_elses_own_folder_is_not_given_away_by_its_faces(
    service: SuggestionService,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
) -> None:
    """Not a swap at all: `Favorites` holds her folder and another person's. Her face is on most of
    its files with a face, and the other person's files are body-only clips with nothing to veto.
    The folder is a collection of people, and each folder inside is still its own person's."""
    nadia = await _person(temp_db, "Nadia Vance")
    priya = await _person(temp_db, "Priya Sandoval")
    await _files(library, add_file, "Favorites/Nadia Vance", 8)
    priyas = await _files(library, add_file, "Favorites/Priya Sandoval", 5)
    loose = await _files(library, add_file, "Favorites", 2)
    await name_folders()
    faces.looked = {"Favorites": (15, 10), "Nadia Vance": (8, 8), "Priya Sandoval": (5, 0)}
    faces.named_here = {"Favorites": {nadia: 8}, "Nadia Vance": {nadia: 8}}

    await service.rebuild()

    for one in priyas:
        assert await _people_of(temp_db, one) == {priya}
    for one in loose:
        assert await _people_of(temp_db, one) == set()
    assert await _answered(temp_db) == {
        "Favorites/Nadia Vance": {nadia},
        "Favorites/Priya Sandoval": {priya},
    }


async def test_a_folder_holding_only_her_own_folders_is_still_hers(
    service: SuggestionService,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
) -> None:
    """The refusal is about somebody ELSE: `Videos` holding `Nadia Vance` and her loose clips is
    hers, folder and all."""
    nadia = await _person(temp_db, "Nadia Vance")
    await _files(library, add_file, "Videos/Nadia Vance", 4)
    loose = await _files(library, add_file, "Videos", 3)
    await name_folders()
    faces.looked = {"Videos": (7, 7), "Nadia Vance": (4, 4)}
    faces.named_here = {"Videos": {nadia: 7}, "Nadia Vance": {nadia: 4}}

    await service.rebuild()

    for one in loose:
        assert await _people_of(temp_db, one) == {nadia}
    assert (await _answered(temp_db))["Videos"] == {nadia}


async def test_a_folder_filed_without_asking_is_taken_back_whole_and_stays_taken_back(
    store: Store,
    access: Repository,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    preferences: FakePreferences,
    name_folders: Callable[[], Any],
    admin: Viewer,
) -> None:
    """The silent write's one receipt, and its Undo: her off every file it put her on (and on none
    that carried her before), the folder no longer hers, its may-be card gone, and a later pass that
    reads the same faces leaves it alone."""
    decisions = DecisionStore(temp_db)
    service = SuggestionService(
        store=store, access=access, faces=faces, preferences=preferences, recorder=decisions
    )
    board = Workbench()
    board.register(FiledQueue(service))
    undoing = WorkbenchService(store=decisions, workbench=board)
    nadia = await _person(temp_db, "Nadia Vance")
    clips = await _files(library, add_file, "Videos", 5)
    async with temp_db.write() as connection:
        await attribute_assets_on(connection, asset_ids=clips[:1], person_id=nadia)
    await name_folders()
    faces.looked = {"Videos": (5, 5)}
    faces.named_here = {"Videos": {nadia: 5}}
    await service.rebuild()
    assert await _answered(temp_db) == {"Videos": {nadia}}

    found, total = await decisions.recent(limit=10, offset=0)
    assert total == 1
    assert found[0].title == "Added 4 files in the folder Videos to Nadia Vance"
    subjects = await temp_db.fetch_all(
        "SELECT kind, subject_id FROM workbench_decision_subjects WHERE decision_id = ?",
        (found[0].id,),
    )
    assert {(str(row["kind"]), str(row["subject_id"])) for row in subjects} == {
        ("folder", await _folder(temp_db, "Videos")),
        ("person", nadia),
        *(("asset", one) for one in clips[1:]),
    }

    reversal = await undoing.undo(admin, found[0].id)

    assert reversal.put_back == reversal.of == 1
    assert await _people_of(temp_db, clips[0]) == {nadia}
    for one in clips[1:]:
        assert await _people_of(temp_db, one) == set()
    assert await _answered(temp_db) == {}
    assert faces.withdrawn[-1] == (await _folder(temp_db, "Videos"), nadia)

    more = (await add_file(library, "Videos/clip9.mp4")).asset.id
    await name_folders()
    faces.looked = {"Videos": (6, 6)}
    faces.named_here = {"Videos": {nadia: 6}}
    await service.rebuild()

    for one in [*clips[1:], more]:
        assert await _people_of(temp_db, one) == set()
    assert await _answered(temp_db) == {}


async def test_a_folder_whose_files_all_carried_her_is_recorded_and_taken_back_as_a_folder(
    store: Store,
    access: Repository,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    preferences: FakePreferences,
    name_folders: Callable[[], Any],
    admin: Viewer,
) -> None:
    """Nothing to put on any file, and still a decision: the folder became hers, which is what
    files tomorrow's arrivals under her. Its Undo forgets that and leaves every file as it was."""
    decisions = DecisionStore(temp_db)
    service = SuggestionService(
        store=store, access=access, faces=faces, preferences=preferences, recorder=decisions
    )
    board = Workbench()
    board.register(FiledQueue(service))
    undoing = WorkbenchService(store=decisions, workbench=board)
    nadia = await _person(temp_db, "Nadia Vance")
    clips = await _files(library, add_file, "Videos", 3)
    async with temp_db.write() as connection:
        await attribute_assets_on(connection, asset_ids=clips, person_id=nadia)
    await name_folders()
    faces.looked = {"Videos": (3, 3)}
    faces.named_here = {"Videos": {nadia: 3}}
    await service.rebuild()

    found, _ = await decisions.recent(limit=10, offset=0)
    assert [one.title for one in found] == ["Added the folder Videos to Nadia Vance"]

    async def one_card_went(folder_id: str, person_id: str) -> int:
        faces.withdrawn.append((folder_id, person_id))
        return 1

    faces.withdraw_proposals = one_card_went  # type: ignore[method-assign]
    reversal = await undoing.undo(admin, found[0].id)

    assert reversal.put_back == 1
    assert await _answered(temp_db) == {}
    for one in clips:
        assert await _people_of(temp_db, one) == {nadia}


async def test_the_librarys_own_folder_holding_somebody_elses_folder_is_not_given_away(
    service: SuggestionService,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    name_folders: Callable[[], Any],
) -> None:
    """The same refusal at the top of a library, whose own folder has no path inside it."""
    nadia = await _person(temp_db, "Nadia Vance")
    await _person(temp_db, "Priya Sandoval")
    loose = [(await add_file(library, f"clip{i}.mp4")).asset.id for i in range(4)]
    await _files(library, add_file, "Priya Sandoval", 2)
    await name_folders()
    row = await temp_db.fetch_one("SELECT name FROM folders WHERE rel_path = ''")
    assert row is not None
    top = str(row["name"])
    faces.looked = {top: (6, 4), "Priya Sandoval": (2, 0)}
    faces.named_here = {top: {nadia: 4}}

    await service.rebuild()

    for one in loose:
        assert await _people_of(temp_db, one) == set()


async def _filed_by_an_older_build(
    db: Database, folder: str, person_id: str, asset_ids: list[str], *, source: str = "folder"
) -> None:
    """A folder the pass added before it wrote any record: the answer and the rows, nothing else."""
    async with db.write() as connection:
        await attribute_assets_on(
            connection, asset_ids=asset_ids, person_id=person_id, source=source
        )
        await connection.execute(
            "INSERT OR IGNORE INTO folder_people (folder_id, person_id, created_at) "
            "SELECT id, ?, 0 FROM folders WHERE rel_path = ?",
            (person_id, folder),
        )


async def test_take_back_on_a_row_takes_back_a_folder_added_before_any_record_and_undo_puts_it_back(
    store: Store,
    access: Repository,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    preferences: FakePreferences,
    name_folders: Callable[[], Any],
    admin: Viewer,
) -> None:
    """Folders the pass added before it wrote any record have no History line to undo from.
    The row's press reads the library instead: the person comes off what the folder pass put them
    on under that folder, and off nothing a person or a swap put there; one line is written at the
    press, and its Undo puts every part back."""
    decisions = DecisionStore(temp_db)
    service = SuggestionService(
        store=store, access=access, faces=faces, preferences=preferences, recorder=decisions
    )
    board = Workbench()
    board.register(FiledQueue(service))
    undoing = WorkbenchService(store=decisions, workbench=board)
    nadia = await _person(temp_db, "Nadia Vance")
    clips = await _files(library, add_file, "Videos", 5)
    by_hand, by_swap, by_the_pass = clips[0], clips[1], clips[2:]
    async with temp_db.write() as connection:
        await attribute_assets_on(connection, asset_ids=[by_hand], person_id=nadia)
    await _filed_by_an_older_build(temp_db, "Videos", nadia, [by_swap], source="swap")
    await _filed_by_an_older_build(temp_db, "Videos", nadia, by_the_pass)
    videos = await _folder(temp_db, "Videos")
    (row,) = await service.filed(admin)
    assert row.folder_id == videos

    taken = await service.take_back_folder(admin, folder_id=videos, person_id=nadia)

    assert taken.files == 3
    for one in by_the_pass:
        assert await _people_of(temp_db, one) == set()
    assert await _people_of(temp_db, by_hand) == {nadia}
    assert await _people_of(temp_db, by_swap) == {nadia}
    assert await service.filed(admin) == []
    assert faces.withdrawn[-1] == (videos, nadia)
    found, total = await decisions.recent(limit=10, offset=0)
    assert total == 1
    assert found[0].id == taken.decision_id
    assert found[0].title == "Removed 3 files in the folder Videos from Nadia Vance"
    # A second press finds the first one's work done.
    again = await service.take_back_folder(admin, folder_id=videos, person_id=nadia)
    assert (again.files, again.decision_id) == (0, None)

    # The same faces on the next pass do not add it again.
    await name_folders()
    faces.looked = {"Videos": (5, 5)}
    faces.named_here = {"Videos": {nadia: 5}}
    faces.piles_here = {"Videos": {"pile-1": 4}}
    await service.rebuild()
    assert await _answered(temp_db) == {}
    assert faces.proposed == [], "the may-be card came back for a folder taken back"
    for one in by_the_pass:
        assert await _people_of(temp_db, one) == set()

    reversal = await undoing.undo(admin, str(taken.decision_id))

    assert reversal.put_back == 1
    for one in by_the_pass:
        assert await _people_of(temp_db, one) == {nadia}
    assert await _answered(temp_db) == {"Videos": {nadia}}
    assert await store.folder_refusals() == set()


async def test_a_folder_answered_with_no_file_the_pass_filed_is_taken_back_as_the_folder_alone(
    store: Store,
    access: Repository,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    preferences: FakePreferences,
    admin: Viewer,
) -> None:
    """A standing answer with nothing the pass filed under it still comes off, and its line says
    the folder rather than "0 files"; a folder or a person no longer here takes nothing back and
    puts nothing back."""
    decisions = DecisionStore(temp_db)
    service = SuggestionService(
        store=store, access=access, faces=faces, preferences=preferences, recorder=decisions
    )
    nadia = await _person(temp_db, "Nadia Vance")
    (clip,) = await _files(library, add_file, "Videos", 1)
    async with temp_db.write() as connection:
        await attribute_assets_on(connection, asset_ids=[clip], person_id=nadia)
    await _filed_by_an_older_build(temp_db, "Videos", nadia, [])
    videos = await _folder(temp_db, "Videos")

    gone = new_id()
    assert (await service.take_back_folder(admin, folder_id=videos, person_id=gone)).files == 0
    taken = await service.take_back_folder(admin, folder_id=videos, person_id=nadia)

    assert (taken.files, taken.forgot) == (0, True)
    found, _total = await decisions.recent(limit=10, offset=0)
    assert [one.title for one in found] == ["Removed the folder Videos from Nadia Vance"]
    assert await _people_of(temp_db, clip) == {nadia}
    for folder_id, person_id in ((gone, nadia), (videos, gone)):
        assert not await service.put_folder_back(
            admin,
            folder_id=folder_id,
            person_id=person_id,
            asset_ids=[clip],
            linked=True,
            refused=True,
        )
    assert await _answered(temp_db) == {}


async def test_putting_a_folder_back_restores_only_what_its_take_back_wrote(
    store: Store,
    access: Repository,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    preferences: FakePreferences,
    admin: Viewer,
) -> None:
    """A take back that forgot no answer puts none back, and one that refused nothing leaves a
    refusal standing; an answer put back with no faces to show proposes no may-be card."""
    service = SuggestionService(store=store, access=access, faces=faces, preferences=preferences)
    nadia = await _person(temp_db, "Nadia Vance")
    await _files(library, add_file, "Videos", 1)
    videos = await _folder(temp_db, "Videos")
    async with store.write() as connection:
        await store.refuse_folder_person_on(connection, folder_id=videos, person_id=nadia)

    assert not await service.put_folder_back(
        admin, folder_id=videos, person_id=nadia, asset_ids=[], linked=False, refused=False
    )
    assert await _answered(temp_db) == {}
    assert await store.folder_refusals() == {(videos, nadia)}

    faces.on = True
    assert await service.put_folder_back(
        admin, folder_id=videos, person_id=nadia, asset_ids=[], linked=True, refused=True
    )
    assert await _answered(temp_db) == {"Videos": {nadia}}
    assert await store.folder_refusals() == set()
    assert faces.proposed == []


async def test_a_folder_taken_back_stays_off_its_person_when_a_folder_above_it_is_read_again(
    store: Store,
    access: Repository,
    temp_db: Database,
    library: Library,
    add_file: Callable[..., Any],
    faces: FakeFaces,
    preferences: FakePreferences,
    name_folders: Callable[[], Any],
    admin: Viewer,
) -> None:
    """`Nadia Vance` is hers and stays hers; `Nadia Vance/Extras` was taken back from her. The next
    time a file lands in her folder, its standing answer reaches everything under it except the
    folder somebody took back."""
    service = SuggestionService(store=store, access=access, faces=faces, preferences=preferences)
    nadia = await _person(temp_db, "Nadia Vance")
    await _files(library, add_file, "Nadia Vance", 2)
    extras = await _files(library, add_file, "Nadia Vance/Extras", 2)
    await name_folders()
    await service.rebuild()
    assert await _people_of(temp_db, extras[0]) == {nadia}
    await service.take_back_folder(
        admin, folder_id=await _folder(temp_db, "Nadia Vance/Extras"), person_id=nadia
    )
    assert await _people_of(temp_db, extras[0]) == set()

    await add_file(library, "Nadia Vance/clip9.mp4")
    # And somebody else's folder is read past her refusal: it is hers, not theirs.
    tobias = await _person(temp_db, "Tobias Ellery")
    his = await _files(library, add_file, "Tobias Ellery", 2)
    await name_folders()
    await service.rebuild()

    for one in extras:
        assert await _people_of(temp_db, one) == set()
    for one in his:
        assert await _people_of(temp_db, one) == {tobias}
