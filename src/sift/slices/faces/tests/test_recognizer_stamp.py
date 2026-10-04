# SPDX-License-Identifier: AGPL-3.0-or-later
"""What described these numbers, written on every row that holds a description.

The stored squares are what make changing the recognizer cheap: a swap measures them again instead
of opening a file. The three tables that remember a decision, and the pile whose middle is an
average of descriptions, all hold numbers in one model's space, and without a stamp they say
nothing about which. Matching then compares a new description against an old one and answers confidently
about nothing.

The stamp is DERIVED rather than handed in, from the scan row of the file each face was found in.
That is what these check: that it is written at all, that it is the model that actually described
the file rather than whatever is configured, and that a model swap carries it forward with the
numbers it rewrites.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.kernel.sampling import FACE_SAMPLING_VERSION
from sift.slices.faces.models import (
    Appearance,
    Box,
    Described,
    Detection,
    Quality,
    ScanStatus,
)
from sift.slices.faces.store import PassRecord, Remeasured, Store
from sift.slices.faces.tests.conftest import person_vector
from sift.slices.faces.tuning import QUALITY_VERSION

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"

#: The model the pass in these tests ran under, and a second one for the swap. Words rather than
#: the model list's names, because what is being checked is that the value TRAVELS: a model list
#: name would pass just as well with the stamp read from the wrong place.
_THEN = "test-recognizer"
_NOW = "test-recognizer-two"


@pytest.fixture
async def library(library_store: LibraryStore, tmp_path: Path) -> Root:
    directory = tmp_path / "library"
    directory.mkdir()
    return await library_store.create_root(name="Clips", abs_path=directory)


@pytest.fixture
async def clip(content_store: ContentStore, library: Root, settings: Settings) -> Ingested:
    target = Path(str(library.abs_path)) / "clip.mp4"
    target.write_bytes((CORPUS / "accepted.mp4").read_bytes())
    checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
    return await content_store.ingest(checked, root_id=library.id, rel_path="clip.mp4")


def an_appearance(timestamp_ms: int = 0, who: int = 0) -> Appearance:
    detection = Detection(
        box=Box(x=10, y=10, width=100, height=100),
        score=0.9,
        landmarks=((1.0, 1.0),) * 5,
        timestamp_ms=timestamp_ms,
    )
    quality = Quality(pixels=100, sharpness=500.0, frontality=0.9, score=0.8, accepted=True)
    return Appearance(
        started_ms=timestamp_ms,
        ended_ms=timestamp_ms,
        seen_in=1,
        quality=0.8,
        faces=(
            Described(
                detection=detection,
                quality=quality,
                vector=person_vector(who),
                chip=np.zeros((1, 1, 3), dtype=np.uint8),
            ),
        ),
    )


def a_pass_record(recognizer: str = _THEN) -> PassRecord:
    return PassRecord(
        status=ScanStatus.NONE_IDENTIFIED,
        depth="fast",
        coverage=1.0,
        frames_sampled=1,
        detector="test-detector",
        recognizer=recognizer,
        settings_digest="abcd1234",
    )


async def _scanned(store: Store, asset_id: str, *, faces: int = 1) -> list[str]:
    return await store.replace_pass(
        asset_id,
        [an_appearance(index * 1000, who=index) for index in range(faces)],
        [[b"\xff\xd8\xff a picture"] for _ in range(faces)],
        a_pass_record(),
    )


async def test_a_scan_row_says_which_code_scored_and_sampled_it(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """The two numbers the tuning fingerprint folds, kept as numbers as well.

    Folded, they say the tuning moved and never which part of it; as columns they are a set a pass
    can be pointed at, which is the whole reason they were given columns.
    """
    await _scanned(store, clip.asset.id)

    row = await temp_db.fetch_one(
        "SELECT quality_version, sampling_version FROM face_scans WHERE asset_id = ?",
        (clip.asset.id,),
    )
    assert row is not None
    assert int(row["quality_version"]) == QUALITY_VERSION
    assert int(row["sampling_version"]) == FACE_SAMPLING_VERSION


async def test_a_naming_remembered_says_what_described_the_face(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    tracks = await _scanned(store, clip.asset.id)
    person = await _a_person(temp_db)

    await store.remember_confirmation(tracks[0], person)

    row = await temp_db.fetch_one("SELECT recognizer FROM face_confirmations", ())
    assert row is not None
    assert str(row["recognizer"]) == _THEN


async def test_a_face_set_aside_and_a_hand_grouping_say_it_too(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    tracks = await _scanned(store, clip.asset.id, faces=2)

    pile = await store.set_aside(tracks)
    assert pile is not None
    await store.remember_ignored(pile)
    moved = await store.move_tracks(tracks[:1], None)
    assert moved is not None
    await store.remember_grouping(moved)

    # Two statements written out rather than one built from a name: the rule this repository
    # keeps everywhere else, and ruff refuses the f-string outright.
    for statement in (
        "SELECT recognizer FROM face_ignored",
        "SELECT recognizer FROM face_grouping",
    ):
        rows = list(await temp_db.fetch_all(statement, ()))
        assert rows, statement
        assert {str(row["recognizer"]) for row in rows} == {_THEN}, statement


async def test_a_pile_says_what_described_the_faces_it_holds(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """Derived from the members after they are put in, never handed in: a stamp threaded through
    each of the seven methods that build or move a pile is a rule in seven places."""
    tracks = await _scanned(store, clip.asset.id, faces=2)

    pile = await store.set_aside(tracks)

    row = await temp_db.fetch_one("SELECT recognizer FROM face_piles WHERE id = ?", (pile,))
    assert row is not None
    assert str(row["recognizer"]) == _THEN


async def test_a_model_swap_carries_the_stamp_onto_the_decision_it_rewrites(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """The moment the old numbers stop existing is the only moment the decision can be found, and
    it is the same moment its stamp has to move. Left behind, the row would hold the new model's
    numbers under the old model's name and no read could tell."""
    tracks = await _scanned(store, clip.asset.id)
    person = await _a_person(temp_db)
    await store.remember_confirmation(tracks[0], person)
    faces = await store.faces_of(tracks[0])
    previous = await temp_db.fetch_one(
        "SELECT embedding FROM face_detections WHERE id = ?", (faces[0].id,)
    )
    assert previous is not None

    await store.remeasure_file(
        clip.asset.id,
        [
            Remeasured(
                id=faces[0].id,
                previous=bytes(previous["embedding"]),
                embedding=b"\x01" * 32,
                strength=1.0,
            )
        ],
        gone=[],
        recognizer=_NOW,
    )

    row = await temp_db.fetch_one("SELECT recognizer, embedding FROM face_confirmations", ())
    assert row is not None
    assert str(row["recognizer"]) == _NOW
    assert bytes(row["embedding"]) == b"\x01" * 32


async def test_every_read_that_decides_a_scan_is_stale_asks_the_two_code_versions(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """All four copies of the settled rule, held in step by one test.

    The rule is written out in four places (`settled_ids`, `unsettled_among`, `lack` and
    `settled_count`) because each asks it of a different shape of statement, and four copies is
    four chances to add a term to three of them.

    The state built here cannot arise from the code as it stands: the tuning fingerprint folds both
    versions, so a row scored by older code already has a different digest. That is exactly why it
    is built by hand. The columns exist so that a change to either one is a set a pass can be
    pointed at rather than a fingerprint that differs, and the day the fingerprint stops folding
    them the term in these four statements is the only thing left saying so.
    """
    await _scanned(store, clip.asset.id)
    async with temp_db.write() as connection:
        await connection.execute(
            "UPDATE face_scans SET quality_version = quality_version - 1 WHERE asset_id = ?",
            (clip.asset.id,),
        )

    digest = "abcd1234"
    assert await store.settled_ids(digest) == set()
    assert await store.settled_count(digest) == 0
    assert await store.unsettled_among([clip.asset.id], digest) == {clip.asset.id}

    lacking = store.lack(digest)
    rows = await temp_db.fetch_all(
        # `noqa: S608`: what is spliced is a module constant in the store, and this is the
        # same concatenation the content store makes of a `Lack` in production.
        "SELECT a.id FROM assets a WHERE"  # noqa: S608  # nosemgrep: sift-no-string-built-sql
        " " + lacking.condition,
        list(lacking.params),
    )
    assert [str(row["id"]) for row in rows] == [clip.asset.id]

    async with temp_db.write() as connection:
        await connection.execute(
            "UPDATE face_scans SET quality_version = quality_version + 1 WHERE asset_id = ?",
            (clip.asset.id,),
        )
    assert await store.settled_count(digest) == 1


async def _a_person(database: Database) -> str:
    from sift.kernel.ids import new_id

    person = new_id()
    async with database.write() as connection:
        await connection.execute(
            "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
            (person, "Marisol Vane", "marisol vane"),
        )
    return person
