# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where faces are kept, who may see one, and what deleting really deletes.

The visibility test is the one that matters here. **A face crop is a fragment of the file it came
from, so being shown one asks exactly the question being shown the file asks**, and it asks it of
the same rule, rather than of a second copy of that rule which would drift and turn this surface
into the way around the first one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# For its side effect: registering the table the ledger is written to, so a slice test's
# database has it. Registration happens at import and the fixtures migrate at setup.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.config import Settings
from sift.kernel.content import (
    ContentStore,
    Ingested,
    Root,
)
from sift.kernel.db import Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.faces.service import FaceService
from sift.slices.faces.store import Store
from sift.slices.faces.tests.conftest import make_person, person_vector
from sift.slices.faces.tests.test_store import _proposal_folder, record
from sift.slices.faces.tuning import MIN_PIXELS

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


async def test_the_floor_pass_is_owed_only_for_a_face_todays_floor_accepts(
    service: FaceService,
    store: Store,
    temp_db: Database,
    content_store: ContentStore,
    library: Root,
    settings: Settings,
) -> None:
    """What a start asks before it queues the floor pass, read against the floor configured now:
    a face refused under it is refused again, so that file is no reason to run."""
    floor = (await service.configuration()).bar.min_pixels
    assert floor < MIN_PIXELS, "the configured floor is under the one earlier scans took"
    target = Path(str(library.abs_path)) / "floor.mp4"
    target.write_bytes((CORPUS / "accepted.mp4").read_bytes() + b"floor")
    checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
    landed = await content_store.ingest(checked, root_id=library.id, rel_path="floor.mp4")
    await record(store, landed.asset.id, 1)

    for refused, owed in ((floor - 1, False), (floor + 1, True)):
        await temp_db.execute(
            "UPDATE face_scans SET refused_largest = ? WHERE asset_id = ?",
            (refused, landed.asset.id),
        )
        assert await service.floor_pass_owed() is owed
        assert await service.under_an_earlier_floor() == ([landed.asset.id] if owed else [])


async def test_a_library_without_the_record_has_no_run_standing_to_take_back(
    store: Store, temp_db: Database
) -> None:
    """The record's tables belong to another part of Sift; where they are not, an Undo of a
    naming finds nothing to take back rather than failing on a table that is not there."""
    await temp_db.execute("DROP TABLE IF EXISTS workbench_decision_subjects")
    await temp_db.execute("DROP TABLE IF EXISTS workbench_decisions")

    assert await store.standing_recognitions("01HX00000000000000PERSON1", "") == []


async def test_an_ask_about_no_files_answers_none(store: Store) -> None:
    """An empty list names no file, so it is answered as none, not with a statement SQLite cannot
    parse."""
    assert await store.scanned_at_of([]) == {}
    assert await store.box_questions("test-recognizer", most=5, asset_ids=[]) == []


async def test_a_rebuild_that_leaves_a_groups_faces_in_no_group_drops_its_proposal(
    store: Store, temp_db: Database, clip: Ingested
) -> None:
    """The arithmetic put none of the proposed group's faces in a group: there is no pile for the
    question to follow, so it goes rather than land on a group it was never about."""
    faces = await record(store, clip.asset.id, 3, distinct=True)
    (old,) = await store.replace_piles([(person_vector(0), faces[:2])])
    person = await make_person(temp_db, "Esme Wrenfield")
    folder = await _proposal_folder(temp_db, clip)
    assert await store.propose_pile(
        old, person, reason="folder", folder_id=folder, files=2, of_files=2
    )

    await store.replace_piles([(person_vector(7), faces[2:])])

    assert await temp_db.fetch_all("SELECT pile_id FROM face_pile_proposals", ()) == []
