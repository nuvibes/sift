# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Sift will not learn from its own names: a face turned away, a picture that has gone, a
picture she already holds, and a second face of hers in a file it already learned from."""

from __future__ import annotations

from pathlib import Path

import pytest

import sift.slices.workbench.schema  # noqa: F401 (its tables hold the receipts)
from sift.kernel.content import Ingested
from sift.kernel.db import Database
from sift.slices.faces import recognize
from sift.slices.faces.models import Appearance, Attribution, ScanStatus, Vector
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.service import FaceService
from sift.slices.faces.store import PassRecord, Store
from sift.slices.faces.tests.conftest import person_vector
from sift.slices.faces.tests.test_rematch_questions import (  # noqa: F401 (fixtures)
    _described_face,
    library,
    picture,
)

pytestmark = pytest.mark.integration

#: Enough faces people confirmed of her for Sift to learn from its own names.
WELL_KNOWN = 1000


async def _appearances(store: Store, asset_id: str, faces: list[list[Vector]]) -> list[str]:
    """One appearance per list, each face of it with a picture of its own."""
    return await store.replace_pass(
        asset_id,
        [
            Appearance(
                started_ms=0,
                ended_ms=0,
                seen_in=1,
                quality=0.8,
                faces=tuple(_described_face(vector) for vector in vectors),
            )
            for vectors in faces
        ],
        [
            [f"picture {index} {face}".encode() for face in range(len(vectors))]
            for index, vectors in enumerate(faces)
        ],
        PassRecord(
            status=ScanStatus.NONE_IDENTIFIED,
            depth="fast",
            coverage=1.0,
            frames_sampled=1,
            detector="test-detector",
            recognizer="test-recognizer",
            settings_digest="abcd1234",
        ),
    )


async def _named(store: Store, track_id: str, person: str, sure: float = 0.9) -> None:
    await store.attribute(track_id, person, confidence=sure, attribution=Attribution.MATCHED)


async def _learn(service: FaceService, person: str) -> int:
    # Confirmed pictures of her, so Sift's own picks have room under the cap.
    for index in range(3):
        await service._store.add_reference(
            person,
            vector=person_vector(0, 50 + index),
            quality=1.0,
            crop=f"confirmed {index}".encode(),
            origin=FaceOrigin.CONFIRMED,
            recognizer="test-recognizer",
        )
    return await service.learn_from_recognitions(
        {person: WELL_KNOWN}, await service.configuration()
    )


async def _learned(database: Database, person: str) -> list[bytes]:
    rows = await database.fetch_all(
        "SELECT embedding FROM face_references WHERE person_id = ? AND origin = 'recognized'",
        (person,),
    )
    return [bytes(row["embedding"]) for row in rows]


async def test_a_face_turned_away_is_passed_over_for_the_next_clearest(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,  # noqa: F811
    temp_db: Database,
) -> None:
    """With nothing recording, as well: the reference is filed all the same."""
    turned, square = person_vector(0, 1), person_vector(0, 2)
    (track,) = await _appearances(store, picture.asset.id, [[turned, square]])
    clearest = await temp_db.fetch_one(
        "SELECT id FROM face_detections WHERE track_id = ? AND embedding = ?",
        (track, recognize.pack(turned)),
    )
    assert clearest is not None
    await temp_db.execute(
        "UPDATE face_detections SET quality = 0.99, frontality = 0.0 WHERE id = ?",
        (clearest["id"],),
    )
    await _named(store, track, person)

    assert await _learn(service, person) == 1
    assert await _learned(temp_db, person) == [recognize.pack(square)]


@pytest.mark.parametrize("why", ["gone", "held"])
async def test_a_picture_gone_or_one_she_already_holds_teaches_nothing(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,  # noqa: F811
    temp_db: Database,
    why: str,
) -> None:
    (track,) = await _appearances(store, picture.asset.id, [[person_vector(0, 1)]])
    await _named(store, track, person)
    if why == "gone":
        row = await temp_db.fetch_one(
            "SELECT crop_path FROM face_detections WHERE track_id = ?", (track,)
        )
        assert row is not None
        Path(store.resolve(str(row["crop_path"]))).unlink()
    else:
        await store.add_reference(
            person,
            vector=person_vector(0),
            quality=1.0,
            crop=b"picture 0 0",
            origin=FaceOrigin.CONFIRMED,
            recognizer="test-recognizer",
        )

    assert await _learn(service, person) == 0
    assert await _learned(temp_db, person) == []


async def test_one_file_teaches_one_face_of_her_the_surest(
    service: FaceService,
    store: Store,
    person: str,
    picture: Ingested,  # noqa: F811
    temp_db: Database,
) -> None:
    less, more = person_vector(0, 1), person_vector(0, 2)
    tracks = await _appearances(store, picture.asset.id, [[less], [more]])
    await _named(store, tracks[0], person, sure=0.9)
    await _named(store, tracks[1], person, sure=0.95)

    assert await _learn(service, person) == 1
    assert await _learned(temp_db, person) == [recognize.pack(more)]
