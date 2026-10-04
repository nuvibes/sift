# SPDX-License-Identifier: AGPL-3.0-or-later
"""Faces another feature named, made to teach, and groups it proposes, told to one viewer.

A folder answer names a group inside its own transaction and cannot decode a picture there, so the
rest of a confirmation arrives afterwards through `FaceService.teach`: references, the remembered
decision, the People on the files. These are the real service and a real scanned face, because
what is being proved is that the one path by which a face becomes a reference is the path taken.

The proposal half is the review list's sentence ("41 of the 47 files in this group are in the
folder"), counted as the viewer may see it, and absent when the person or the folder is not one
they may be told about.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel.access import Role
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.faces.evidence import FaceEvidence
from sift.slices.faces.frames import Frame
from sift.slices.faces.models import Attribution
from sift.slices.faces.service import FaceService
from sift.slices.faces.store import Store
from sift.slices.faces.tests.conftest import (
    FakeDetector,
    FakePreferences,
    FakeRecognizer,
    draw_face,
    make_person,
    noisy_frame,
    person_vector,
)
from sift.slices.faces.tests.test_service import Scripted, install_reader
from sift.testing.fixtures import create_user, hide

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


@pytest.fixture
async def library(library_store: LibraryStore, tmp_path: Path) -> Root:
    directory = tmp_path / "library"
    directory.mkdir()
    return await library_store.create_root(name="Media", abs_path=directory)


async def _file_in(
    content_store: ContentStore,
    library_store: LibraryStore,
    library: Root,
    settings: Settings,
    rel_path: str,
) -> Ingested:
    folder = await library_store.upsert_folder(library.id, rel_path.rsplit("/", 1)[0])
    target = Path(str(library.abs_path)) / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((CORPUS / "accepted.mp4").read_bytes() + rel_path.encode())
    checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
    return await content_store.ingest(
        checked, root_id=library.id, rel_path=rel_path, folder_id=folder.id
    )


async def _one_face(
    service: FaceService,
    store: Store,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    asset_id: str,
) -> str:
    frame = noisy_frame(400, 300, seed=9)
    detector.placed = {0: [(draw_face(frame, x=60, y=40, size=180), 0.9)]}
    recognizer.rule = lambda chip: person_vector(1)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(asset_id)
    return (await store.tracks_of(asset_id))[0].id


async def _people_of(database: Database, asset_id: str) -> set[str]:
    rows = await database.fetch_all(
        "SELECT person_id FROM asset_people WHERE asset_id = ?", (asset_id,)
    )
    return {str(row["person_id"]) for row in rows}


async def _remembered(database: Database, person_id: str) -> int:
    row = await database.fetch_one(
        "SELECT COUNT(*) AS n FROM face_confirmations WHERE person_id = ?", (person_id,)
    )
    return int(row["n"]) if row is not None else 0


async def test_a_face_a_folder_answer_named_teaches_and_an_undo_takes_it_back(
    service: FaceService,
    store: Store,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    library: Root,
    settings: Settings,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """Named inside the answer's transaction, then taught: the face becomes a reference, the
    decision is remembered against its description so a rescan puts the name back, and the file
    carries the person. An undo that put the face back to unnamed takes all three away."""
    clip = await _file_in(content_store, library_store, library, settings, "Her/one.mp4")
    track_id = await _one_face(service, store, detector, recognizer, clip.asset.id)
    # What `name_group_recording` does inside the folder answer's transaction.
    await store.attribute(track_id, person, confidence=1.0, attribution=Attribution.CONFIRMED)

    assert await service.teach([track_id], person) == 1

    assert len(await store.references(person)) == 1
    assert await _remembered(temp_db, person) == 1
    assert await _people_of(temp_db, clip.asset.id) == {person}

    # The undo's own transaction puts the face back first, then asks for the rest.
    await store.attribute(track_id, None, confidence=None, attribution=None)
    assert await service.unteach([track_id], person) == 1

    assert await store.references(person) == []
    assert await _remembered(temp_db, person) == 0
    assert await _people_of(temp_db, clip.asset.id) == set()


async def test_only_a_face_still_carrying_the_person_is_taught_or_untaught(
    service: FaceService,
    store: Store,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    library: Root,
    settings: Settings,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """A face somebody renamed, or an undo reached first, is not this decision's to teach from,
    and a face named AGAIN since keeps what that later naming filed."""
    clip = await _file_in(content_store, library_store, library, settings, "Her/one.mp4")
    track_id = await _one_face(service, store, detector, recognizer, clip.asset.id)
    somebody_else = await make_person(temp_db, "Talia Brandt")
    await store.attribute(
        track_id, somebody_else, confidence=1.0, attribution=Attribution.CONFIRMED
    )

    assert await service.teach([track_id], person) == 0
    assert await store.references(person) == []

    await store.attribute(track_id, person, confidence=1.0, attribution=Attribution.CONFIRMED)
    await service.teach([track_id], person)
    assert await service.unteach([track_id], person) == 0
    assert len(await store.references(person)) == 1


async def test_taking_back_a_face_that_was_never_taught_takes_nothing(
    service: FaceService,
    store: Store,
    content_store: ContentStore,
    library_store: LibraryStore,
    library: Root,
    settings: Settings,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """The count is of faces that lost something: one with no remembered decision and no picture
    filed from it counts none."""
    clip = await _file_in(content_store, library_store, library, settings, "Her/one.mp4")
    track_id = await _one_face(service, store, detector, recognizer, clip.asset.id)

    assert await service.unteach([track_id], person) == 0
    assert await store.references(person) == []


async def test_a_proposal_is_told_as_the_viewer_may_see_it(
    service: FaceService,
    store: Store,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    library: Root,
    settings: Settings,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """Counted when the list is read, over the files this viewer may see: the group's files in the
    folder, and every file the group is in. A person the viewer may not be told about takes the
    whole proposal with them: a sentence naming her is a statement that she exists."""
    inside = await _file_in(content_store, library_store, library, settings, "Her/one.mp4")
    outside = await _file_in(content_store, library_store, library, settings, "Other/two.mp4")
    first = await _one_face(service, store, detector, recognizer, inside.asset.id)
    second = await _one_face(service, store, detector, recognizer, outside.asset.id)
    pile = (await store.add_piles([(person_vector(1), [first, second])]))[0]
    folder = await temp_db.fetch_one("SELECT id FROM folders WHERE rel_path = 'Her'")
    assert folder is not None
    await store.propose_pile(
        pile, person, reason="folder", folder_id=str(folder["id"]), files=1, of_files=1
    )
    admin = await create_user(temp_db, Role.ADMIN)

    told = await service.proposals_for(admin, [pile, "01HX00000000000000000000ZZ"])

    assert list(told) == [pile]
    [one] = told[pile]
    assert (one.person_id, one.person_name, one.reason) == (person, "Ada Lovelace", "folder")
    assert (one.folder_id, one.folder_name) == (str(folder["id"]), "Her")
    assert (one.in_folder, one.group_files) == (1, 2)

    await hide(temp_db, "person", person, admin.id)
    assert await service.proposals_for(admin, [pile]) == {}


async def test_an_undo_after_a_rescan_takes_back_the_face_the_rescan_found(
    service: FaceService,
    store: Store,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    library: Root,
    settings: Settings,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    preferences: FakePreferences,
) -> None:
    """A folder's Yes, then a rescan of one of its files, then Undo. The rescan finds the face again
    under a new id and puts its remembered name back on it; an Undo naming only the old id would
    leave that face named and its picture filed, and the re-match it asks for could then name many
    more faces from that one picture. The face found again is the one taken back."""
    clip = await _file_in(content_store, library_store, library, settings, "Her/one.mp4")
    track_id = await _one_face(service, store, detector, recognizer, clip.asset.id)
    await store.attribute(track_id, person, confidence=1.0, attribution=Attribution.CONFIRMED)
    assert await service.teach([track_id], person) == 1
    frame = noisy_frame(400, 300, seed=9)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id, again=True)
    (again,) = await store.tracks_of(clip.asset.id)
    assert again.id != track_id and again.attribution is Attribution.CONFIRMED

    evidence = FaceEvidence(
        temp_db,
        preferences=preferences,
        store=store,
        teacher=service,
        queue=object(),  # type: ignore[arg-type]
    )
    async with temp_db.write() as connection:
        assert await evidence.unname_faces(connection, [track_id]) == 1
    assert await service.unteach([track_id], person) == 1

    assert await store.references(person) == []
    assert await _remembered(temp_db, person) == 0
    assert await _people_of(temp_db, clip.asset.id) == set()
