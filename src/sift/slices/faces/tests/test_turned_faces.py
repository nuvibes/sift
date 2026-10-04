# SPDX-License-Identifier: AGPL-3.0-or-later
"""A face turned past the quality bar's angle is kept and matched at the bar any face meets.

Large, sharp and whole, refused for its angle alone, it is a face a person recognizes, so a pass
keeps it (`quality.asked_only`) and a match names it like any other. What it may not do is shape
what Sift knows: no group carries it, and naming it files no reference. Each test below is the one
a mutation of its rule turns red; the faces are made-up descriptions, never pictures.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sift.kernel.access import Role
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.faces.models import (
    Appearance,
    Attribution,
    Box,
    Described,
    Detection,
    Quality,
    ScanStatus,
    Vector,
)
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.service import FaceService
from sift.slices.faces.store import PassRecord, Store
from sift.slices.faces.tests.conftest import person_vector
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"

#: The angle the default quality bar (balanced) names faces at is 0.30; these sit either side.
SQUARE_ON = 0.9
TURNED = 0.2

#: Against a person described by `person_vector(0)` this scores 0.993: over every line for naming.
CLOSE = person_vector(0, variant=1)

#: And this 0.5705: over the line for asking, under the line for naming somebody so thinly known.
AT_57 = person_vector(0, variant=12)


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


def _face(vector: Vector, frontality: float) -> Described:
    turned = frontality < 0.30
    return Described(
        detection=Detection(
            box=Box(x=10, y=10, width=300, height=300),
            score=0.9,
            landmarks=((1.0, 1.0),) * 5,
            timestamp_ms=0,
        ),
        quality=Quality(
            pixels=300,
            sharpness=800.0,
            frontality=frontality,
            score=0.8 * frontality,
            accepted=not turned,
        ),
        vector=vector,
        chip=np.zeros((1, 1, 3), dtype=np.uint8),
    )


async def _faces(store: Store, asset_id: str, faces: list[tuple[Vector, float]]) -> list[str]:
    """One appearance per face on the file, in order, with nobody on any of them yet."""
    return await store.replace_pass(
        asset_id,
        [
            Appearance(
                started_ms=0, ended_ms=0, seen_in=1, quality=0.8, faces=(_face(vector, angle),)
            )
            for vector, angle in faces
        ],
        [[b"\xff\xd8\xff picture %d" % index] for index in range(len(faces))],
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


async def _described(store: Store, person_id: str, pictures: int = 2) -> None:
    for index in range(pictures):
        await store.add_reference(
            person_id,
            vector=person_vector(0),
            quality=1.0,
            crop=f"reference-{person_id}-{index}".encode(),
            origin=FaceOrigin.ADDED,
            recognizer="test-recognizer",
        )


async def test_the_store_says_which_open_faces_are_turned_by_their_clearest_face(
    store: Store, picture: Ingested
) -> None:
    square, turned = await _faces(
        store, picture.asset.id, [(person_vector(0), SQUARE_ON), (person_vector(2), TURNED)]
    )

    assert await store.turned_away(0.30) == {turned}
    assert await store.turned_of([square, turned], 0.30) == {turned}
    # The line is the bar's: under a laxer one the same face is not turned.
    assert await store.turned_away(0.15) == set()
    (face,) = await store.faces_of(turned)
    assert face.turned(0.30) and not face.turned(0.15)


async def test_a_re_match_names_a_turned_face_that_clears_the_bar_as_it_names_any_face(
    service: FaceService, store: Store, person: str, picture: Ingested
) -> None:
    await _described(store, person)
    square, turned = await _faces(store, picture.asset.id, [(CLOSE, SQUARE_ON), (CLOSE, TURNED)])

    assert await service.rematch() == 2

    for track_id in (square, turned):
        named = await store.track(track_id)
        assert named is not None
        assert (named.person_id, named.attribution) == (person, Attribution.MATCHED)


async def test_a_turned_face_under_the_bar_is_asked_about_as_any_face_is(
    service: FaceService, store: Store, person: str, picture: Ingested
) -> None:
    """The bar is the same for both angles: a turned face that only clears the line for asking
    is a question, exactly as a square-on face at the same score is."""
    await _described(store, person)
    square, turned = await _faces(store, picture.asset.id, [(AT_57, SQUARE_ON), (AT_57, TURNED)])

    await service.rematch()

    for track_id in (square, turned):
        asked = await store.track(track_id)
        assert asked is not None
        assert (asked.person_id, asked.attribution) == (person, Attribution.SUGGESTED)


async def test_a_pass_names_a_turned_face_that_clears_the_bar(
    service: FaceService, store: Store, person: str, picture: Ingested
) -> None:
    """The scan's own path (`_attribute`), which judges a face the moment it is found."""
    await _described(store, person)
    (turned,) = await _faces(store, picture.asset.id, [(CLOSE, TURNED)])

    attached = await service._attribute([turned], await service.configuration())

    assert [track for track, _sure in attached[person]] == [turned]
    named = await store.track(turned)
    assert named is not None and named.attribution is Attribution.MATCHED


async def test_a_turned_face_joins_no_group(
    service: FaceService, store: Store, picture: Ingested
) -> None:
    square, turned = await _faces(
        store, picture.asset.id, [(person_vector(4), SQUARE_ON), (person_vector(4, 1), TURNED)]
    )

    await service.regroup()
    grouped = await store.track(square)
    alone = await store.track(turned)
    assert grouped is not None and grouped.pile_id is not None
    assert alone is not None and alone.pile_id is None

    await service.regroup(full=True)
    alone = await store.track(turned)
    assert alone is not None and alone.pile_id is None


async def test_naming_a_turned_face_by_hand_names_it_and_files_no_reference(
    service: FaceService, store: Store, person: str, picture: Ingested
) -> None:
    square, turned = await _faces(
        store, picture.asset.id, [(person_vector(6), SQUARE_ON), (person_vector(8), TURNED)]
    )

    await service.confirm(turned, person)
    await service.confirm(square, person)

    named = await store.track(turned)
    assert named is not None and named.attribution is Attribution.CONFIRMED
    (filed,) = await store.references(person)
    assert np.allclose(filed.vector, person_vector(6)), "the turned face was filed as a reference"


async def test_a_files_own_faces_say_which_one_is_turned(
    service: FaceService, store: Store, picture: Ingested, temp_db: Database
) -> None:
    square, turned = await _faces(
        store, picture.asset.id, [(person_vector(0), SQUARE_ON), (person_vector(2), TURNED)]
    )
    admin = await create_user(temp_db, Role.ADMIN)
    viewer = await service.viewer_for(admin.id)
    assert viewer is not None

    sightings = {
        one.track_id: one for one in await service.appearances_in(viewer, picture.asset.id)
    }

    assert sightings[turned].turned is True
    assert sightings[square].turned is False
