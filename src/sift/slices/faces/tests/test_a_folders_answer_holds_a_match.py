# SPDX-License-Identifier: AGPL-3.0-or-later
"""A face in a file whose folder was answered as somebody else is asked about, never named.

The folder read keeps a standing answer per folder (`folder_people`). A likeness to another person
does not overrule it on its own: the face waits under Needs your input. A gone copy's folder says
nothing about the file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import sift.slices.suggestions.schema
import sift.slices.workbench.schema  # noqa: F401 (its tables hold the receipts)
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.faces import tuning
from sift.slices.faces.models import Attribution
from sift.slices.faces.service import FaceService
from sift.slices.faces.store import Store
from sift.slices.faces.tests.conftest import make_person, person_vector
from sift.slices.faces.tests.test_rematch_questions import AT_57, _a_question, _described, _one_face

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


@pytest.fixture
async def library(library_store: LibraryStore, tmp_path: Path) -> Root:
    directory = tmp_path / "library"
    directory.mkdir()
    return await library_store.create_root(name="Clips", abs_path=directory)


@pytest.fixture
async def picture(content_store: ContentStore, library: Root, settings: Settings) -> Ingested:
    target = Path(str(library.abs_path)) / "still.jpg"
    target.write_bytes((CORPUS / "accepted.jpg").read_bytes())
    checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
    return await content_store.ingest(checked, root_id=library.id, rel_path="still.jpg")


async def _answered_as(database: Database, asset_id: str, person_id: str) -> None:
    """The file put in a folder of its own, answered as this person."""
    row = await database.fetch_one(
        "SELECT root_id FROM asset_locations WHERE asset_id = ?", (asset_id,)
    )
    assert row is not None
    folder_id = new_id()
    await database.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, NULL, ?, ?)",
        (folder_id, row["root_id"], folder_id, folder_id),
    )
    await database.execute(
        "UPDATE asset_locations SET folder_id = ? WHERE asset_id = ?", (folder_id, asset_id)
    )
    await database.execute(
        "INSERT INTO folder_people (folder_id, person_id, created_at) VALUES (?, ?, 0)",
        (folder_id, person_id),
    )


async def _attribution(store: Store, track_id: str) -> tuple[str | None, Attribution | None]:
    track = await store.track(track_id)
    assert track is not None
    return track.person_id, track.attribution


async def test_a_folder_answered_as_somebody_else_asks_instead_of_naming(
    service: FaceService, store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    other = await make_person(temp_db, "Bryn Calloway")
    await _described(store, person, person_vector(0))
    track_id = await _one_face(store, picture.asset.id, person_vector(0))
    await _answered_as(temp_db, picture.asset.id, other)

    assert await service.rematch() == 1

    assert await _attribution(store, track_id) == (person, Attribution.SUGGESTED)


async def test_a_folder_answered_as_the_same_person_names_as_before(
    service: FaceService, store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    await _described(store, person, person_vector(0))
    track_id = await _one_face(store, picture.asset.id, person_vector(0))
    await _answered_as(temp_db, picture.asset.id, person)

    assert await service.rematch() == 1

    assert await _attribution(store, track_id) == (person, Attribution.MATCHED)


async def test_a_gone_copys_folder_holds_nothing_back(
    service: FaceService, store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    other = await make_person(temp_db, "Bryn Calloway")
    await _described(store, person, person_vector(0))
    track_id = await _one_face(store, picture.asset.id, person_vector(0))
    await _answered_as(temp_db, picture.asset.id, other)
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (picture.asset.id,)
    )

    assert await service.rematch() == 1

    assert await _attribution(store, track_id) == (person, Attribution.MATCHED)


async def test_a_standing_question_that_clears_the_line_stays_a_question(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    track_id = await _a_question(service, store, person, picture, AT_57)
    await _answered_as(temp_db, picture.asset.id, await make_person(temp_db, "Bryn Calloway"))

    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.55)
    assert await service.rematch() == 0

    assert await _attribution(store, track_id) == (person, Attribution.SUGGESTED)


async def test_a_scan_asks_about_a_face_its_folder_says_is_somebody_else(
    service: FaceService, store: Store, person: str, picture: Ingested, temp_db: Database
) -> None:
    await _described(store, person, person_vector(0))
    track_id = await _one_face(store, picture.asset.id, person_vector(0))
    await _answered_as(temp_db, picture.asset.id, await make_person(temp_db, "Bryn Calloway"))

    configured = await service.configuration()
    attached = await service._attribute([track_id], configured, asset_id=picture.asset.id)

    assert attached == {}
    assert await _attribution(store, track_id) == (person, Attribution.SUGGESTED)
