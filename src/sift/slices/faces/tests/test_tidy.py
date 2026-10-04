# SPDX-License-Identifier: AGPL-3.0-or-later
"""Face pictures nothing points at, and the pass that stops leaving them.

The dangerous mistake here is the opposite of a missed file: a sweep that removes a picture still in
use shows somebody a broken face where a person used to be, and a reference crop cannot be rebuilt
at all. So every test below is about what survives.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ml.store import LIBRARY_MODELS
from sift.kernel.tidy import Resources, build_all
from sift.slices.faces.crop import CHIP_SIZE
from sift.slices.faces.models import (
    Appearance,
    Box,
    Described,
    Detection,
    Origin,
    Quality,
    ScanStatus,
)
from sift.slices.faces.store import PassRecord, Store
from sift.slices.faces.tests.conftest import landmarks_for, person_vector
from sift.slices.faces.tidy import LeftoverFacePictures

_EPOCH = 1_700_000_000


@pytest.fixture
def resources(temp_db: Database, settings: Settings) -> Resources:
    return Resources(database=temp_db, settings=settings)


async def _asset(database: Database) -> str:
    asset_id = new_id()
    await database.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'image', ?)",
        (asset_id, f"digest-{asset_id}", _EPOCH),
    )
    return asset_id


def _described(quality: float = 0.8) -> Described:
    box = Box(x=10, y=10, width=120, height=120)
    return Described(
        detection=Detection(
            timestamp_ms=0,
            box=box,
            score=0.99,
            landmarks=landmarks_for(box),
        ),
        quality=Quality(
            pixels=120, sharpness=200.0, frontality=0.9, score=quality, accepted=True, reason=None
        ),
        vector=person_vector(0),
        chip=np.zeros((CHIP_SIZE, CHIP_SIZE, 3), dtype=np.uint8),
    )


async def _pass(store: Store, asset_id: str, *, picture: bytes) -> list[str]:
    return await store.replace_pass(
        asset_id,
        [Appearance(started_ms=0, ended_ms=0, seen_in=1, quality=0.8, faces=(_described(),))],
        [[picture]],
        PassRecord(
            status=ScanStatus.NONE_IDENTIFIED,
            depth="fast",
            coverage=1.0,
            frames_sampled=1,
            detector="stand-in",
            recognizer="stand-in",
            settings_digest="digest",
        ),
    )


# --- the pass that leaves nothing behind ----------------------------------------------------


@pytest.mark.asyncio
async def test_scanning_again_removes_the_pictures_it_replaces(
    store: Store, temp_db: Database
) -> None:
    """The leak this closes: one picture per face per rescan, forever.

    Nothing would notice, because the half left behind is by definition the half nothing asks for:
    a library scanned three times would hold three sets of face pictures and could reach one.
    """
    asset_id = await _asset(temp_db)
    await _pass(store, asset_id, picture=b"first pass")
    first = sorted(store.detected_root.rglob("*.jpg"))
    assert len(first) == 1

    await _pass(store, asset_id, picture=b"second pass")

    now = sorted(store.detected_root.rglob("*.jpg"))
    assert len(now) == 1
    assert now[0].read_bytes() == b"second pass"
    assert not first[0].exists()


@pytest.mark.asyncio
async def test_a_replaced_pass_takes_its_covers_with_it(store: Store, temp_db: Database) -> None:
    """A cover is named after its face rather than recorded in a table, so nothing would ever
    have collected it."""
    asset_id = await _asset(temp_db)
    (track_id,) = await _pass(store, asset_id, picture=b"first pass")
    cover = store.cover_path(track_id)
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"a cover")

    await _pass(store, asset_id, picture=b"second pass")

    assert not cover.exists()


@pytest.mark.asyncio
async def test_another_file_is_not_touched_by_a_rescan(store: Store, temp_db: Database) -> None:
    kept_asset = await _asset(temp_db)
    await _pass(store, kept_asset, picture=b"another file")
    rescanned = await _asset(temp_db)
    await _pass(store, rescanned, picture=b"first pass")

    await _pass(store, rescanned, picture=b"second pass")

    surviving = {path.read_bytes() for path in store.detected_root.rglob("*.jpg")}
    assert surviving == {b"another file", b"second pass"}


# --- what has already built up ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_picture_in_use_is_left_alone(
    resources: Resources, store: Store, temp_db: Database
) -> None:
    asset_id = await _asset(temp_db)
    await _pass(store, asset_id, picture=b"in use")

    assert (await LeftoverFacePictures(resources).survey()).count == 0
    assert await LeftoverFacePictures(resources).run() == 0
    assert [path.read_bytes() for path in store.detected_root.rglob("*.jpg")] == [b"in use"]


@pytest.mark.asyncio
async def test_a_picture_no_row_names_is_counted_and_removed(
    resources: Resources, store: Store
) -> None:
    stray = store.detected_root / "ab" / "abcdef.jpg"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"left behind")

    found = await LeftoverFacePictures(resources).survey()
    assert found.count == 1
    assert found.frees_bytes == len(b"left behind")

    assert await LeftoverFacePictures(resources).run() == 1
    assert not stray.exists()


@pytest.mark.asyncio
async def test_a_cover_for_a_face_that_still_exists_is_kept(
    resources: Resources, store: Store, temp_db: Database
) -> None:
    """Covers are the case a table-driven sweep gets wrong: nothing names them anywhere."""
    asset_id = await _asset(temp_db)
    (track_id,) = await _pass(store, asset_id, picture=b"in use")
    cover = store.cover_path(track_id)
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"a cover")

    assert (await LeftoverFacePictures(resources).survey()).count == 0
    assert cover.exists()


@pytest.mark.asyncio
async def test_a_cover_for_a_face_that_has_gone_is_removed(
    resources: Resources, store: Store
) -> None:
    cover = store.cover_path(new_id())
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"nobody's cover")

    assert (await LeftoverFacePictures(resources).survey()).count == 1
    assert await LeftoverFacePictures(resources).run() == 1
    assert not cover.exists()


@pytest.mark.asyncio
async def test_a_reference_picture_is_never_swept(
    resources: Resources, store: Store, temp_db: Database, person: str
) -> None:
    """The one picture here that cannot be rebuilt. It is the only copy of a face somebody chose."""
    reference_id = await store.add_reference(
        person_id=person,
        vector=person_vector(1),
        quality=0.9,
        crop=b"a reference",
        origin=Origin.ADDED,
        recognizer="stand-in",
    )
    assert reference_id is not None
    kept = store.crop_path(store.reference_root, reference_id)

    assert (await LeftoverFacePictures(resources).survey()).count == 0
    assert kept.read_bytes() == b"a reference"


@pytest.mark.asyncio
async def test_an_unclaimed_pack_face_is_never_swept(
    resources: Resources, store: Store, temp_db: Database
) -> None:
    """A pack's faces sit in the same directory and belong to nobody yet. They become references
    the moment somebody claims one, so sweeping them would throw away a pending import."""
    pack_id = await store.folder_import_pack("stand-in")
    entry_id = await store.keep_pack_entry(pack_id=pack_id, name="Somebody", aliases=(), links=())
    await store.keep_entry_face(
        entry_id,
        vector=person_vector(2),
        quality=0.9,
        crop=b"a pack face",
        digest="a-pack-face",
        recognizer="stand-in",
    )

    assert (await LeftoverFacePictures(resources).survey()).count == 0


@pytest.mark.asyncio
async def test_it_is_registered_and_runs_after_the_rows(resources: Resources) -> None:
    """Order is load-bearing: removing a file's rows strands its pictures, so this runs last."""
    order = [tidying.name for tidying in build_all(resources)]

    assert "leftover-face-pictures" in order
    assert order.index("stranded-assets") < order.index("leftover-face-pictures")


@pytest.mark.asyncio
async def test_an_empty_face_directory_is_not_an_error(
    resources: Resources, store: Store, tmp_path: Path
) -> None:
    """Nothing has ever been scanned, which is where every install starts."""
    settings = Settings(data_dir=tmp_path / "never-scanned", cache_dir=tmp_path / "cache")
    elsewhere = Resources(database=resources.database, settings=settings)

    assert (await LeftoverFacePictures(elsewhere).survey()).count == 0


@pytest.mark.asyncio
async def test_models_the_move_left_in_the_faces_folder_are_not_leftovers(
    resources: Resources, store: Store
) -> None:
    """Models once lived inside the faces folder, and the move into the device's store can leave
    some there: a move that failed and waits for the next start, or a file the store holds a
    different copy of. No row names them; the sweep steps over them.

    That can be two files, well over a hundred megabytes, that the walk would offer up as space to
    free, and it may be the only copy. A stray picture beside them is still a leftover, so the exemption
    is the directory and not the whole walk.
    """
    model = store.root / LIBRARY_MODELS / "accurate.recognizer.onnx"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"weights")
    stray = store.detected_root / "cd" / "cdef01.jpg"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"left behind")

    found = await LeftoverFacePictures(resources).survey()
    assert found.count == 1
    assert found.frees_bytes == len(b"left behind")

    assert await LeftoverFacePictures(resources).run() == 1
    assert model.exists()
    assert not stray.exists()
