# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every look for faces at a file is a line on its History, and a look made again forgets none.

`face_scans` keeps one row per file, the live state of the last look. Each look is also an act
on the record, written in the look's own transaction, and the History draws its lines from those.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# For its side effect: registering the tables the record is written to.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.access import sentences as say
from sift.kernel.access.history import Event, history_of_asset
from sift.kernel.access.history_events import events_of_asset
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.faces.models import ScanStatus
from sift.slices.faces.store import PassRecord, Store
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


@pytest.fixture
async def clip(
    content_store: ContentStore, library_store: LibraryStore, settings: Settings, tmp_path: Path
) -> Ingested:
    directory = tmp_path / "library"
    directory.mkdir()
    library: Root = await library_store.create_root(name="Clips", abs_path=directory)
    target = directory / "clip.mp4"
    target.write_bytes((CORPUS / "accepted.mp4").read_bytes())
    checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
    return await content_store.ingest(checked, root_id=library.id, rel_path="clip.mp4")


def _record(small: int, closer: int, largest: int | None = None) -> PassRecord:
    return PassRecord(
        status=ScanStatus.NONE_IDENTIFIED,
        depth="fast",
        coverage=1.0,
        frames_sampled=2,
        detector="test-detector",
        recognizer="test-recognizer",
        settings_digest="abcd1234",
        refused_small=small,
        refused_closer=closer,
        refused_largest=largest,
    )


def _words(event: Event) -> str:
    return say.text_of(event.pieces)


async def test_two_looks_at_one_file_leave_two_lines_each_with_its_own_answer(
    store: Store, clip: Ingested, temp_db: Database, access: Repository, actors: Actors
) -> None:
    await store.replace_pass(clip.asset.id, [], [], _record(2, 0, 40))
    await store.replace_pass(clip.asset.id, [], [], _record(0, 0))

    drawn = await history_of_asset(temp_db, access, actors.admin, clip.asset.id)

    lines = sorted(_words(one) for one in drawn if one.kind == "face_run")
    too_small = say.text_of(say.looked_for_faces(0, (), small=2, closer=0, why=say.Refusals(40)))
    none = say.text_of(say.looked_for_faces(0, (), small=0, closer=0))
    assert lines == sorted([too_small, none]), "a look made again took the earlier look's line"


async def test_each_look_is_an_act_on_the_record_with_what_it_came_to(
    store: Store, clip: Ingested, temp_db: Database, actors: Actors
) -> None:
    await store.replace_pass(clip.asset.id, [], [], _record(3, 1))

    (look,) = [
        one
        for one in await events_of_asset(temp_db, actors.admin, clip.asset.id)
        if one.verb == "face_run"
    ]

    assert look.actor_kind == "sift"
    facts = json.loads(look.payload)
    assert (facts["track_count"], facts["refused_small"], facts["refused_closer"]) == (0, 3, 1)
