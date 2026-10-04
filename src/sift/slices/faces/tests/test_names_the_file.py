# SPDX-License-Identifier: AGPL-3.0-or-later
"""A question is not a name: which faces put their person on the FILE, and the one-time repair.

A face is in one of three states (Confirmed, Recognized by Sift, Needs your input), and
only the first two put the person under the file's People. The third is Sift asking, and
`person_id IS NOT NULL` alone would list every open question's person on the file.
`store.NAMES_THE_FILE` is the rule, read by every reader that lists a file's People from its faces,
and these tests hold each of them to it, including the two copies the kernel spells as literals
because it may not import this slice.

The second half is the repair for libraries an older rule wrote to, and the cover
invariant beside it: a person with an uploaded cover is never also given a face picture.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# For its side effect: registering the table the ledger is written to.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.faces.models import Attribution
from sift.slices.faces.store import NAMES_THE_FILE, Store
from sift.slices.faces.tests.conftest import make_person
from sift.slices.faces.tests.test_store import record

pytestmark = pytest.mark.integration

SOURCE = Path(__file__).resolve().parents[3]
CORPUS = SOURCE / "kernel" / "tests" / "fixtures" / "ingress"


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


async def people_on(database: Database, asset_id: str) -> list[str]:
    rows = await database.fetch_all(
        "SELECT person_id FROM asset_people WHERE asset_id = ? ORDER BY person_id", (asset_id,)
    )
    return [str(row["person_id"]) for row in rows]


# --- the rule ------------------------------------------------------------------------------------


def test_only_a_face_confirmed_by_you_or_recognized_by_sift_names_the_file() -> None:
    assert set(NAMES_THE_FILE) == {Attribution.CONFIRMED, Attribution.MATCHED}
    assert Attribution.SUGGESTED not in NAMES_THE_FILE


def _spelled(path: Path) -> list[set[str]]:
    """Every `ft.attribution IN (...)` a kernel file spells, as the set of words each one lists."""
    text = path.read_text(encoding="utf-8")
    return [
        set(re.findall(r"'([a-z]+)'", listed))
        for listed in re.findall(r"ft\.attribution IN \(([^)]*)\)", text)
    ]


@pytest.mark.parametrize(
    "where",
    ["kernel/access/constraints.py", "kernel/access/repository/asset_facets.py"],
)
def test_the_kernel_spells_the_same_rule(where: str) -> None:
    """The two places the kernel asks "did faces name somebody on this file" (the `enriched:faces`
    search condition and the Enriched by column's faces arm) ask it of the same states the
    file's People are written from. A drift would have the search find files whose People panel
    does not list anybody from a face, or miss ones it does."""
    found = _spelled(SOURCE / where)

    assert found, f"{where} no longer spells the faces rule; this test is reading nothing"
    assert all(one == {state.value for state in NAMES_THE_FILE} for one in found), found


# --- the writer and the readers ------------------------------------------------------------------


async def test_a_question_does_not_put_its_person_on_the_file(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lumen")
    (track_id,) = await record(store, clip.asset.id)
    await store.attribute(track_id, person, confidence=0.5, attribution=Attribution.SUGGESTED)

    added, removed = await store.reconcile_people(clip.asset.id)

    assert (added, removed) == ([], [])
    assert await people_on(temp_db, clip.asset.id) == []
    assert await store.people_with_faces(clip.asset.id) == []


@pytest.mark.parametrize("state", [Attribution.MATCHED, Attribution.CONFIRMED])
async def test_a_face_recognized_or_confirmed_does(
    store: Store, clip: Ingested, temp_db: Database, state: Attribution
) -> None:
    person = await make_person(temp_db, "Ada Lumen")
    (track_id,) = await record(store, clip.asset.id)
    await store.attribute(track_id, person, confidence=0.9, attribution=state)

    added, _removed = await store.reconcile_people(clip.asset.id)

    assert added == [person]
    assert await people_on(temp_db, clip.asset.id) == [person]
    assert await store.people_with_faces(clip.asset.id) == [person]


async def test_a_face_that_goes_back_to_being_a_question_takes_the_name_off(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """An agreement taken back leaves the face asked about again, and the file with it."""
    person = await make_person(temp_db, "Ada Lumen")
    (track_id,) = await record(store, clip.asset.id)
    await store.attribute(track_id, person, confidence=1.0, attribution=Attribution.CONFIRMED)
    await store.reconcile_people(clip.asset.id)

    await store.attribute(track_id, person, confidence=0.5, attribution=Attribution.SUGGESTED)
    _added, removed = await store.reconcile_people(clip.asset.id)

    assert removed == [person]
    assert await people_on(temp_db, clip.asset.id) == []


# --- the cover a name gives ------------------------------------------------------------------------


async def _cover(temp_db: Database, person: str) -> tuple[object, object, object]:
    row = await temp_db.fetch_one(
        "SELECT cover_asset_id, cover_track_id, cover_upload_id FROM people WHERE id = ?",
        (person,),
    )
    assert row is not None
    return row["cover_asset_id"], row["cover_track_id"], row["cover_upload_id"]


async def test_a_named_face_gives_the_whole_file_as_a_cover_and_never_the_face(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    """A cover nobody chose is the whole first picture filed under the person: the name a face
    puts on its file gives it, and the face itself is never cut out as one."""
    person = await make_person(temp_db, "Ada Lumen")
    (track_id,) = await record(store, clip.asset.id)

    await store.attribute(track_id, person, confidence=1.0, attribution=Attribution.CONFIRMED)
    await store.reconcile_people(clip.asset.id)

    assert await _cover(temp_db, person) == (clip.asset.id, None, None)


async def test_a_named_face_leaves_an_uploaded_cover_alone(
    store: Store, clip: Ingested, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lumen")
    await temp_db.execute(
        "UPDATE people SET cover_upload_id = 'uploaded-picture' WHERE id = ?", (person,)
    )
    (track_id,) = await record(store, clip.asset.id)

    await store.attribute(track_id, person, confidence=1.0, attribution=Attribution.CONFIRMED)
    await store.reconcile_people(clip.asset.id)

    assert await _cover(temp_db, person) == (None, None, "uploaded-picture")
