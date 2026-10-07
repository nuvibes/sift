# SPDX-License-Identifier: AGPL-3.0-or-later
"""Disagreements gathered by person, one person's rows closest to her first, and the Yes
and the No over a run of them, the No with the receipt that puts every file back as it was."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import sift.slices.stash_boxes.schema
import sift.slices.suggestions.schema
import sift.slices.workbench.schema  # noqa: F401 (the receipts' table, for the No's receipt)
from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.access import sentences as say
from sift.kernel.access.history_sources import _NAMING_FOLDER
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.faces import recognize, service_identified
from sift.slices.faces.models import Attribution, StartersShow, ToCheckKind
from sift.slices.faces.queue import DisagreementsQueue, IdentifiedRecords
from sift.slices.faces.receipts import DISAGREEMENTS_QUEUE, IDENTIFIED_QUEUE
from sift.slices.faces.service import FaceService
from sift.slices.faces.service_disagreements import NamingFolder, filed_from, filed_rows
from sift.slices.faces.store import FiledOff, Store
from sift.slices.faces.tests import test_routes as routes
from sift.slices.faces.tests.conftest import RecordingReindexer, person_vector
from sift.slices.faces.tests.test_routes import (
    Scene,
    db_path,
    sign_in,
    turn_on,
    write,
)
from sift.slices.faces.tests.test_routes_answers import _a_file_filed_under
from sift.slices.faces.weights import pairing
from sift.slices.workbench.store import Store as WorkbenchStore
from sift.testing.fixtures import create_user, hide

pytestmark = pytest.mark.integration

app = routes.app
client = routes.client
scene = routes.scene

ROOT = "01HX00000000000000000ROOT9"
FOLDER = "01HX0000000000000000FLDR9"
WREN = "01HX000000000000000WREN01"
RASHA = "01HX00000000000000RASHA01"


@pytest.fixture
async def admin(temp_db: Database, access: Repository) -> Viewer:
    return await create_user(temp_db, Role.ADMIN)


@pytest.fixture
async def library(temp_db: Database, service: FaceService) -> Database:
    """One folder everybody's files sit in, and the two people the rows are about, each with one
    picture of her own the recognizer can compare with."""
    recognizer = (await service.configuration()).recognizer
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, 'Media', ?, 0)",
        (ROOT, "/library/media"),
    )
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) "
        "VALUES (?, ?, NULL, '', 'Media')",
        (FOLDER, ROOT),
    )
    for index, (person_id, name) in enumerate(((WREN, "Wren Halloway"), (RASHA, "Rasha Emberlin"))):
        await temp_db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person_id, name)
        )
        await temp_db.execute(
            "INSERT INTO face_references "
            "(id, person_id, crop_path, crop_digest, embedding, quality, origin, recognizer, "
            "created_at) VALUES (?, ?, 'crop.jpg', ?, ?, 1.0, 'confirmed', ?, 0)",
            (new_id(), person_id, f"ref-{index}", recognize.pack(person_vector(index)), recognizer),
        )
    return temp_db


async def _filed(
    temp_db: Database,
    service: FaceService,
    person_id: str,
    vector: tuple[float, ...] | None,
    *,
    source: str = "folder",
) -> tuple[str, str]:
    """A file a pass filed under her, with one face described by `vector`. For one of this file's
    two people the face is Sift's name for the other one, since a disagreement is a face named as
    somebody else; for anybody else (another file's fixture borrowing this helper) the face is
    nobody's, which is what a stash-box's question is about."""
    other = {WREN: RASHA, RASHA: WREN}.get(person_id)
    recognizer = (await service.configuration()).recognizer
    asset, track = new_id(), new_id()
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'image', 0)",
        (asset, f"digest-{asset}"),
    )
    await temp_db.execute(
        "INSERT INTO asset_locations "
        "(id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        (f"loc-{asset}", asset, ROOT, FOLDER, f"{asset}.jpg", f"{asset}.jpg"),
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, 7)",
        (asset, person_id, source),
    )
    await temp_db.execute(
        "INSERT INTO face_scans (asset_id, status, depth, coverage, frames_sampled, track_count, "
        "identified_count, detector, recognizer, settings_digest, scanned_at) "
        "VALUES (?, 'none_identified', 'fast', 1.0, 1, 1, 0, 'det', ?, 'digest', 0)",
        (asset, recognizer),
    )
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "pile_id, person_id, confidence, attribution, created_at) "
        "VALUES (?, ?, 0, 0, 1, 1.0, NULL, ?, ?, ?, 0)",
        (track, asset, other, None if other is None else 0.8, None if other is None else "matched"),
    )
    if vector is not None:
        await temp_db.execute(
            "INSERT INTO face_detections (id, track_id, timestamp_ms, box_x, box_y, box_w, "
            "box_h, score, quality, crop_path, crop_digest, embedding, created_at) "
            "VALUES (?, ?, 0, 0, 0, 100, 100, 0.9, 0.9, 'c.jpg', ?, ?, 0)",
            (new_id(), track, f"crop-{track}", recognize.pack(vector)),
        )
    return asset, track


def _between(near: float) -> tuple[float, ...]:
    """A face `near` of the way towards Wren's picture and the rest towards nobody in particular."""
    wren, elsewhere = person_vector(0), person_vector(7)
    mixed = [near * a + (1.0 - near) * b for a, b in zip(wren, elsewhere, strict=True)]
    length = sum(value * value for value in mixed) ** 0.5
    return tuple(value / length for value in mixed)


async def test_the_rows_are_gathered_by_person_most_first_and_add_up_to_the_tab(
    library: Database, service: FaceService, admin: Viewer
) -> None:
    """One row per person, the most files first, each counting what the tab counts."""
    for _ in range(2):
        await _filed(library, service, RASHA, None)
    await _filed(library, service, WREN, None, source="stash_box")

    people = await service.disagreeing_people(admin)
    _items, total, _small = await service.to_check(admin, kind=ToCheckKind.MISMATCH, limit=24)

    assert [(one.person_id, one.count, one.source) for one in people] == [
        (RASHA, 2, "folder"),
        (WREN, 1, "stash_box"),
    ]
    assert sum(one.count for one in people) == total == 3


async def test_the_faces_on_a_page_of_rows_are_named_in_one_read(
    library: Database,
    service: FaceService,
    store: Store,
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each row's face is named from one read for the page, never one read per row."""
    for _ in range(2):
        _asset, track = await _filed(library, service, RASHA, None)
        await store.attribute(track, WREN, confidence=0.9, attribution=Attribution.MATCHED)
    asked: list[str] = []
    one_at_a_time = service._repository.visible_person

    async def counted(viewer: Viewer, person_id: str) -> Any:
        asked.append(person_id)
        return await one_at_a_time(viewer, person_id)

    monkeypatch.setattr(service._repository, "visible_person", counted)

    items, total, _small = await service.to_check(admin, kind=ToCheckKind.MISMATCH, limit=24)

    assert total == len(items) == 2
    assert {face.person_id for item in items for face in item.faces} == {WREN}
    assert asked == []


async def test_a_person_filed_from_a_filename_is_said_so_with_no_folder_or_box_read(
    library: Database, service: FaceService, admin: Viewer
) -> None:
    await _filed(library, service, RASHA, None, source="filename")

    (person,) = await service.disagreeing_people(admin)

    assert (person.source, say.text_of(person.filed)) == ("filename", "Added from the filename")


async def test_her_rows_come_closest_to_her_first(
    library: Database, service: FaceService, admin: Viewer
) -> None:
    """The faces that may be her after all lead, so a No over what is left is the safe press; a
    face with no description has no number and goes last."""
    far, _ = await _filed(library, service, WREN, _between(0.1))
    unread, _ = await _filed(library, service, WREN, None)
    near, _ = await _filed(library, service, WREN, _between(0.6))

    items, total = await service.disagreements_of(admin, WREN, limit=10, offset=0)
    page, _total = await service.disagreements_of(admin, WREN, limit=1, offset=1)

    assert [item.id for item in items] == [near, far, unread]
    assert total == 3
    assert [item.id for item in page] == [far]
    assert {item.person_id for item in items} == {WREN}


async def test_a_no_takes_her_off_and_its_undo_puts_each_row_back_as_it_was(
    library: Database, service: FaceService, store: Store, admin: Viewer
) -> None:
    """The No writes its own receipt, and the queue's Undo restores the filing whole: the same
    word for where it came from, the same moment, and no refusal left behind."""
    written = WorkbenchStore(library)
    service._recorder = written
    first, _ = await _filed(library, service, WREN, None)
    second, _ = await _filed(library, service, WREN, None)
    other, _ = await _filed(library, service, RASHA, None)

    run = await service.answer_disagreements(admin, WREN, yes=False, asset_ids=None)

    assert run.changed == 2
    assert run.decision_id
    left = await library.fetch_all("SELECT asset_id FROM asset_people ORDER BY asset_id")
    assert [row["asset_id"] for row in left] == [other]
    refused = await library.fetch_all(
        "SELECT asset_id FROM asset_person_refusals WHERE person_id = ?", (WREN,)
    )
    assert {row["asset_id"] for row in refused} == {first, second}
    assert await service.disagreeing_people(admin) != []
    assert [one.person_id for one in await service.disagreeing_people(admin)] == [RASHA]

    receipt = await library.fetch_one(
        "SELECT queue, payload FROM workbench_decisions WHERE id = ?", (run.decision_id,)
    )
    assert receipt is not None
    assert receipt["queue"] == DISAGREEMENTS_QUEUE
    assert await DisagreementsQueue(service).reverse(admin, run.decision_id, receipt["payload"])

    back = await library.fetch_all(
        "SELECT asset_id, source, decided_at FROM asset_people WHERE person_id = ? "
        "ORDER BY asset_id",
        (WREN,),
    )
    assert [(row["asset_id"], row["source"], row["decided_at"]) for row in back] == sorted(
        [(first, "folder", 7), (second, "folder", 7)]
    )
    assert (
        await library.fetch_all(
            "SELECT asset_id FROM asset_person_refusals WHERE person_id = ?", (WREN,)
        )
        == []
    )
    assert not await DisagreementsQueue(service).reverse(admin, run.decision_id, "not json")


async def test_a_no_and_its_undo_leave_the_stash_box_on_the_filing(
    library: Database, service: FaceService, admin: Viewer
) -> None:
    """The box a filing names is part of the row the Undo puts back, so the file's History and
    the Disagreements header still say which box added her once the No is taken back."""
    service._recorder = WorkbenchStore(library)
    asset, _ = await _filed(library, service, WREN, None, source="stash_box")
    await library.execute(
        "UPDATE asset_people SET box_id = 'box-north' WHERE asset_id = ? AND person_id = ?",
        (asset, WREN),
    )

    run = await service.answer_disagreements(admin, WREN, yes=False, asset_ids=[asset])
    receipt = await library.fetch_one(
        "SELECT payload FROM workbench_decisions WHERE id = ?", (run.decision_id,)
    )
    assert receipt is not None
    assert await DisagreementsQueue(service).reverse(admin, run.decision_id, receipt["payload"])

    back = await library.fetch_one(
        "SELECT source, decided_at, box_id FROM asset_people WHERE asset_id = ? AND person_id = ?",
        (asset, WREN),
    )
    assert back is not None
    assert (back["source"], back["decided_at"], back["box_id"]) == ("stash_box", 7, "box-north")


async def test_a_page_or_a_pick_is_narrowed_to_her_rows(
    library: Database, service: FaceService, admin: Viewer
) -> None:
    """A file answered since, or never hers, is not part of the press: a No over a page naming
    somebody else's file takes nobody off it."""
    hers, _ = await _filed(library, service, WREN, None)
    not_hers, _ = await _filed(library, service, RASHA, None)

    run = await service.answer_disagreements(admin, WREN, yes=False, asset_ids=[not_hers])
    none = await service.answer_disagreements(admin, WREN, yes=False, asset_ids=[])

    assert (run.changed, none.changed) == (0, 0)
    kept = await library.fetch_all("SELECT asset_id FROM asset_people ORDER BY asset_id")
    assert {row["asset_id"] for row in kept} == {hers, not_hers}

    # With nothing to write a receipt into, the No still happens and offers no Undo.
    mine = await service.answer_disagreements(admin, WREN, yes=False, asset_ids=[hers])
    assert (mine.changed, mine.decision_id) == (1, "")


async def test_a_no_that_found_nothing_left_to_take_off_writes_no_receipt(
    library: Database, service: FaceService, admin: Viewer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row answered by somebody else between the read and the write leaves nothing to record."""
    await _filed(library, service, WREN, None)

    async def nothing(_person: str, _files: list[str]) -> list[FiledOff]:
        return []

    monkeypatch.setattr(service._store, "take_filed_off", nothing)
    run = await service.answer_disagreements(admin, WREN, yes=False, asset_ids=None)

    assert (run.changed, run.decision_id) == (0, "")


async def test_a_yes_names_the_face_as_her(
    library: Database, service: FaceService, store: Store, admin: Viewer
) -> None:
    """Yes is the naming every faces screen writes, and the row leaves the tab."""
    service._recorder = WorkbenchStore(library)
    asset, track = await _filed(library, service, WREN, None)
    alone, total = await service.disagreements_of(admin, WREN, limit=10, offset=0)
    assert ([item.id for item in alone], total) == ([asset], 1)

    run = await service.answer_disagreements(admin, WREN, yes=True, asset_ids=[asset])

    assert run.changed == 1
    assert (await store.tracks([track]))[track].person_id == WREN
    assert await service.disagreeing_people(admin) == []
    # With the naming's receipt, so the toast offers the Undo a No offers.
    assert run.decision_id
    written = await library.fetch_one(
        "SELECT queue FROM workbench_decisions WHERE id = ?", (run.decision_id,)
    )
    assert written is not None
    assert written["queue"] == IDENTIFIED_QUEUE


async def test_the_undo_of_a_yes_names_the_face_as_who_it_was_named_before(
    library: Database, service: FaceService, store: Store, admin: Viewer
) -> None:
    """A Yes takes the other name off the face, refused, to give it hers; its Undo takes hers off
    and puts the other name back as it was, the refusal forgotten."""
    written = WorkbenchStore(library)
    service._recorder = written
    asset, track = await _filed(library, service, WREN, None)

    run = await service.answer_disagreements(admin, WREN, yes=True, asset_ids=[asset])
    named = (await store.tracks([track]))[track]
    assert (named.person_id, named.attribution) == (WREN, Attribution.CONFIRMED)
    receipt = await library.fetch_one(
        "SELECT payload FROM workbench_decisions WHERE id = ?", (run.decision_id,)
    )
    assert receipt is not None

    assert await IdentifiedRecords(service).reverse(admin, run.decision_id, receipt["payload"])

    back = (await store.tracks([track]))[track]
    assert (back.person_id, back.attribution) == (RASHA, Attribution.MATCHED)
    assert back.confidence == 0.8
    assert await store.rejections() == {}


async def test_an_undo_puts_back_the_names_it_can_read_and_passes_over_the_rest(
    library: Database, service: FaceService, store: Store, admin: Viewer
) -> None:
    """A receipt whose list of earlier names holds one this build cannot read still names the face
    it can as who it was."""
    service._recorder = WorkbenchStore(library)
    asset, track = await _filed(library, service, WREN, None)
    run = await service.answer_disagreements(admin, WREN, yes=True, asset_ids=[asset])
    receipt = await library.fetch_one(
        "SELECT payload FROM workbench_decisions WHERE id = ?", (run.decision_id,)
    )
    assert receipt is not None
    payload = json.loads(receipt["payload"])
    payload["named_before"]["t-unread"] = [RASHA, "matched"]

    assert await IdentifiedRecords(service).reverse(admin, run.decision_id, json.dumps(payload))

    back = (await store.tracks([track]))[track]
    assert (back.person_id, back.attribution) == (RASHA, Attribution.MATCHED)


async def test_only_a_passs_filing_is_taken_off(library: Database, store: Store) -> None:
    """A name somebody put on by hand is not what a No about a pass's filing is about."""
    await library.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('a1', 'd1', 'image', 0)"
    )
    await library.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) "
        "VALUES ('a1', ?, NULL, 3)",
        (WREN,),
    )

    assert await store.take_filed_off(WREN, ["a1"]) == []
    assert await store.take_filed_off(WREN, []) == []
    assert await store.put_filed_back(WREN, []) == []
    assert await store.put_filed_back(WREN, [FiledOff("gone", "folder", 1)]) == []


async def test_an_undo_that_puts_nothing_back_tells_the_search_index_nothing(
    library: Database, service: FaceService, reindexer: RecordingReindexer
) -> None:
    """Every file the No took off has gone since: nothing changed, so the index is not asked to
    read anything again, and the queue's Undo says nothing moved."""
    assert await service.put_filed_back(WREN, [FiledOff("gone", "folder", 1)]) == 0
    assert reindexer.bulk_calls == []


async def test_the_board_s_crop_of_a_disagreement_leads_to_the_file(
    library: Database, service: FaceService, admin: Viewer
) -> None:
    """The row is about the file whose face and name disagree, so that is what its crop opens."""
    asset, track = await _filed(library, service, WREN, person_vector(7))

    summary = await DisagreementsQueue(service).survey(admin)

    assert summary.count == 1
    assert [(one.id, one.href) for one in summary.preview] == [(track, f"/asset/{asset}")]


def test_a_receipt_this_build_cannot_read_is_not_put_back() -> None:
    assert filed_rows('{"person_id": "p"}') is None
    assert filed_rows('{"person_id": "p", "files": [["a", "folder", null]]}') == (
        "p",
        [FiledOff("a", "folder", None)],
    )
    assert filed_rows('{"person_id": "p", "files": [["a", "stash_box", 7, "box-north"]]}') == (
        "p",
        [FiledOff("a", "stash_box", 7, "box-north")],
    )
    assert filed_rows('{"person_id": "p", "files": [["a"]]}') is None


# --- where the name came from, the folder named ---------------------------------------------------


async def _answered_as(library: Database, person_id: str, folder_id: str, path: str) -> None:
    """A folder under the library's top, answered as her: the standing rule a folder read keeps."""
    await library.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
        (folder_id, ROOT, FOLDER, path, path.rsplit("/", 1)[-1]),
    )
    await library.execute(
        "INSERT INTO folder_people (folder_id, person_id, created_at) VALUES (?, ?, 0)",
        (folder_id, person_id),
    )


async def _in(library: Database, asset: str, path: str) -> None:
    """The file moved into that folder."""
    await library.execute(
        "UPDATE asset_locations SET rel_path = ? WHERE asset_id = ?", (f"{path}/{asset}.jpg", asset)
    )


async def test_a_box_filing_names_the_box_and_a_filing_naming_none_says_a_stash_box(
    library: Database, service: FaceService, admin: Viewer
) -> None:
    """ "1 file, added by Northlight", from the box the filing names; one naming no box still
    reads "Added by a stash-box", never a box picked for it."""
    await library.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at)"
        " VALUES ('box-north', 'Northlight', 'https://northlight.invalid/graphql', 0)"
    )
    asset, _track = await _filed(library, service, WREN, None, source="stash_box")

    [person] = await service.disagreeing_people(admin)
    assert say.text_of(person.filed) == "Added by a stash-box"

    await library.execute(
        "UPDATE asset_people SET box_id = 'box-north' WHERE asset_id = ? AND person_id = ?",
        (asset, WREN),
    )
    [person] = await service.disagreeing_people(admin)
    assert say.text_of(person.filed) == "Added by Northlight"
    assert say.text_of(filed_from("stash_box", [], ["Northlight", "Southwind"])) == (
        "Added by Northlight and Southwind"
    )


async def test_the_nearest_folder_answered_as_her_is_named_and_leads_to_the_folder_view(
    library: Database, service: FaceService, admin: Viewer
) -> None:
    """Her folder, not the library's top that was also answered as her, as the words and as a way
    to that folder; the words are the server's, whole."""
    await library.execute(
        "INSERT INTO folder_people (folder_id, person_id, created_at) VALUES (?, ?, 0)",
        (FOLDER, WREN),
    )
    await _answered_as(library, WREN, "01HX0000000000000000WRENF", "Wren Halloway")
    for _ in range(2):
        asset, _track = await _filed(library, service, WREN, None)
        await _in(library, asset, "Wren Halloway")

    [person] = await service.disagreeing_people(admin)

    assert say.text_of(person.filed) == "Added from the name of the folder Wren Halloway"
    [folder] = say.things_in(person.filed)
    assert (folder.kind, folder.id, folder.href) == (
        "folder",
        "Wren Halloway",
        "/browse?folders=01HX0000000000000000WRENF",
    )


async def test_each_person_filed_from_a_folder_names_her_own_and_the_folders_are_read_once(
    library: Database, service: FaceService, admin: Viewer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Which folders this viewer may see is one answer for the whole wall, read for the first
    person a folder filed and kept for the rest."""
    for person_id, folder_id, path in (
        (WREN, "01HX0000000000000000WRENF", "Wren Halloway"),
        (RASHA, "01HX000000000000000RASHAF", "Rasha Emberlin"),
    ):
        await _answered_as(library, person_id, folder_id, path)
        asset, _track = await _filed(library, service, person_id, None)
        await _in(library, asset, path)
    reads: list[Viewer] = []
    visible_folders = service._repository.visible_folders

    async def counted(viewer: Viewer) -> object:
        reads.append(viewer)
        return await visible_folders(viewer)

    monkeypatch.setattr(service._repository, "visible_folders", counted)

    people = await service.disagreeing_people(admin)

    assert sorted(say.text_of(one.filed) for one in people) == [
        "Added from the name of the folder Rasha Emberlin",
        "Added from the name of the folder Wren Halloway",
    ]
    assert reads == [admin]


async def test_the_folder_named_here_is_the_one_a_file_s_own_history_names(
    library: Database, service: FaceService
) -> None:
    """One rule read two ways: this wall reads the nearest folder answered as her for a page of
    files at once, and a file's history reads it for one file. Both must answer the same folder
    for every file: at the library's top, in her folder, in a folder answered inside it, in one
    not answered inside it, and beside a folder whose name only starts the same."""
    await library.execute(
        "INSERT INTO folder_people (folder_id, person_id, created_at) VALUES (?, ?, 0)",
        (FOLDER, WREN),
    )
    await _answered_as(library, WREN, "01HX0000000000000000WRENF", "Wren Halloway")
    await _answered_as(library, WREN, "01HX0000000000000000WRENB", "Wren Halloway/Beach")
    places = (
        None,
        "Wren Halloway",
        "Wren Halloway/Beach",
        "Wren Halloway/Studio",
        "Wren Halloway 2",
    )
    expected = ("", "Wren Halloway", "Wren Halloway/Beach", "Wren Halloway", "")
    for place, path in zip(places, expected, strict=True):
        asset, _track = await _filed(library, service, WREN, None)
        if place is not None:
            await _in(library, asset, place)

        one = await library.fetch_one(_NAMING_FOLDER, (asset, WREN))
        assert one is not None
        [batch] = await service._naming_folders(WREN, [asset], {})
        named = await library.fetch_one(
            "SELECT rel_path FROM folders WHERE id = ?", (batch.folder_id,)
        )
        assert named is not None
        assert str(one["path"]) == str(named["rel_path"]) == path


async def test_several_folders_are_counted_the_most_first_and_the_first_few_named(
    library: Database, service: FaceService, admin: Viewer
) -> None:
    """How many, then the first three by how many of her files each holds, then the rest counted."""
    for index, (path, files) in enumerate(
        (("Studio", 1), ("Summer", 3), ("Archive", 1), ("Beach", 2))
    ):
        folder_id = f"01HX0000000000000000FOLD{index}"
        await _answered_as(library, WREN, folder_id, path)
        for _ in range(files):
            asset, _track = await _filed(library, service, WREN, None)
            await _in(library, asset, path)

    [person] = await service.disagreeing_people(admin)

    assert say.text_of(person.filed) == (
        "Added from the names of 4 folders: Summer, Beach, Archive and 1 more"
    )
    assert [one.text for one in say.things_in(person.filed)] == ["Summer", "Beach", "Archive"]


async def test_a_folder_the_viewer_may_not_see_is_counted_and_never_named(
    library: Database, service: FaceService
) -> None:
    """Read against folders seen as none: the folder is there and has no name to give."""
    await _answered_as(library, WREN, "01HX0000000000000000HIDDN", "Kept")
    asset, _track = await _filed(library, service, WREN, None)
    await _in(library, asset, "Kept")

    [hidden] = await service._naming_folders(WREN, [asset], {})

    assert (hidden.files, hidden.piece) == (1, None)
    assert say.text_of(filed_from("folder", [hidden])) == "Added from a folder name"
    assert say.text_of(filed_from("folder", [hidden, hidden])) == (
        "Added from the names of 2 folders"
    )


def test_the_words_for_every_other_way_a_name_came() -> None:
    shown = say.thing("folder", "Beach", "Beach", href="/browse?folders=b")
    two = [
        NamingFolder("a", 2, say.thing("folder", "Summer", "Summer")),
        NamingFolder("b", 1, shown),
    ]

    assert (
        say.text_of(filed_from("folder", two))
        == "Added from the names of 2 folders: Summer and Beach"
    )
    assert say.text_of(filed_from("folder", [])) == "Added from a folder name"
    assert say.text_of(filed_from("stash_box", two)) == "Added by a stash-box"
    assert say.text_of(filed_from("filename", [])) == "Added from the filename"
    assert say.text_of(filed_from("something newer", [])) == "Added by Sift"


# --- People Sift can recognize from starter pictures alone, set apart --------------------------------


async def test_the_people_known_from_starter_pictures_alone_are_set_apart(
    library: Database, service: FaceService, store: Store, admin: Viewer
) -> None:
    """Only them, or everybody else, narrowed before the page is taken, with both counts."""
    await library.execute(
        "UPDATE face_references SET origin = 'seed' WHERE person_id = ?", (RASHA,)
    )
    for person_id in (WREN, RASHA):
        _asset, track = await _filed(library, service, person_id, None)
        await store.attribute(track, person_id, confidence=0.9, attribution=Attribution.MATCHED)

    everybody, all_of_them = await service.identified_people(admin)
    only, only_total = await service.identified_people(admin, starters=StartersShow.ONLY)
    rest, rest_total = await service.identified_people(admin, starters=StartersShow.WITHOUT)

    assert all_of_them == 2
    assert {card.person_id for card in everybody} == {WREN, RASHA}
    assert ([card.person_id for card in only], only_total) == ([RASHA], 1)
    assert ([card.person_id for card in rest], rest_total) == ([WREN], 1)
    assert await service.starters_apart(admin) == (1, 1)
    assert await service.position_of_identified(admin, WREN, starters=StartersShow.ONLY) is None
    assert await service.position_of_identified(admin, WREN, starters=StartersShow.WITHOUT) == 0


async def test_a_page_of_cards_reads_its_faces_marks_and_best_once_for_the_page(
    library: Database,
    service: FaceService,
    store: Store,
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each card's moments, reference marks and best percentage: one read for the page, not one
    per card."""
    for person_id in (WREN, RASHA):
        _asset, track = await _filed(library, service, person_id, None)
        await store.attribute(track, person_id, confidence=0.9, attribution=Attribution.MATCHED)
    asked: Counter[str] = Counter()
    for name in ("picture_moments", "reference_tracks", "surest_matched"):
        real = getattr(store, name)

        async def counted(*args: Any, _real: Any = real, _name: str = name, **kw: Any) -> Any:
            asked[_name] += 1
            return await _real(*args, **kw)

        monkeypatch.setattr(store, name, counted)

    cards, total = await service.identified_people(admin)

    assert total == 2
    assert [card.surest for card in cards] == [0.9, 0.9]
    assert asked == {"picture_moments": 1, "reference_tracks": 1}


async def test_a_cards_best_reads_on_past_a_first_page_kept_from_this_viewer(
    library: Database,
    service: FaceService,
    store: Store,
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A card whose surest faces are all on files kept from this viewer reads on to the first it
    may see, and says nothing where it may see none."""
    monkeypatch.setattr(service_identified, "_SUREST_STEP", 1)
    hidden, track = await _filed(library, service, WREN, None)
    await store.attribute(track, WREN, confidence=0.9, attribution=Attribution.MATCHED)
    _shown, track = await _filed(library, service, WREN, None)
    await store.attribute(track, WREN, confidence=0.7, attribution=Attribution.MATCHED)
    lone, track = await _filed(library, service, RASHA, None)
    await store.attribute(track, RASHA, confidence=0.8, attribution=Attribution.MATCHED)
    for asset in (hidden, lone):
        await hide(library, "asset", asset, admin.id)

    cards, _total = await service.identified_people(admin)

    assert [card.surest for card in cards if card.person_id == WREN] == [0.7]
    assert await service._surest(admin, RASHA) is None


# --- the routes ----------------------------------------------------------------------------------


def _one(path: Path, sql: str, params: tuple[object, ...]) -> list[dict[str, object]]:
    """Rows read straight off the running application's database."""

    async def run() -> list[dict[str, object]]:
        database = Database(path, readers=1)
        await database.connect()
        try:
            return [dict(row) for row in await database.fetch_all(sql, params)]
        finally:
            await database.close()

    return asyncio.run(run())


def _a_picture_of(client: TestClient, scene: Scene) -> str:
    revision = pairing("accurate")[1].revision
    write(
        db_path(client),
        [
            (
                "INSERT INTO face_references "
                "(id, person_id, crop_path, crop_digest, embedding, quality, origin, recognizer, "
                "created_at) VALUES (?, ?, 'crop.jpg', 'digest', x'00000000', 1.0, 'confirmed', ?, 0)",
                (new_id(), scene.person, revision),
            )
        ],
    )
    return revision


def test_the_routes_gather_page_and_answer_with_an_undo(client: TestClient, scene: Scene) -> None:
    """The people, her page, a No over all of hers with its receipt, and History's Undo."""
    turn_on(client)
    sign_in(client, "admin")
    revision = _a_picture_of(client, scene)
    first, _ = _a_file_filed_under(client, scene, revision)
    second, _ = _a_file_filed_under(client, scene, revision)

    people = client.get("/api/faces/disagreements").json()
    page = client.get(f"/api/faces/disagreements/{scene.person}", params={"limit": 1}).json()
    refused = client.post(
        f"/api/faces/disagreements/{scene.person}",
        json={"yes": False, "scope": "all", "asset_ids": [first]},
    )
    # A page names its files: one that names none would otherwise read as all of hers.
    unnamed = client.post(
        f"/api/faces/disagreements/{scene.person}", json={"yes": False, "scope": "page"}
    )
    answered = client.post(f"/api/faces/disagreements/{scene.person}", json={"yes": False})

    assert people["total"] == 2
    assert [(one["person_id"], one["count"]) for one in people["people"]] == [(scene.person, 2)]
    assert page["total"] == 2
    assert [item["kind"] for item in page["items"]] == ["mismatch"]
    assert refused.status_code == 422
    assert unnamed.status_code == 422
    assert "needs the files" in str(unnamed.json()["detail"])
    assert answered.status_code == 200
    assert answered.json()["changed"] == 2
    decision = answered.json()["decision_id"]
    assert client.get("/api/faces/disagreements").json() == {"people": [], "total": 0}

    assert client.post(f"/api/workbench/decisions/{decision}/undo").status_code == 200
    back = _one(
        db_path(client),
        "SELECT asset_id FROM asset_people WHERE person_id = ? ORDER BY asset_id",
        (scene.person,),
    )
    assert {row["asset_id"] for row in back} >= {first, second}


def test_a_yes_over_a_page_names_the_faces_and_asks_for_a_rematch(
    client: TestClient, scene: Scene
) -> None:
    turn_on(client)
    sign_in(client, "admin")
    revision = _a_picture_of(client, scene)
    asset, track = _a_file_filed_under(client, scene, revision)

    answered = client.post(
        f"/api/faces/disagreements/{scene.person}",
        json={"yes": True, "scope": "page", "asset_ids": [asset]},
    )

    assert answered.json()["changed"] == 1
    assert answered.json()["decision_id"]
    named = _one(db_path(client), "SELECT person_id FROM face_tracks WHERE id = ?", (track,))
    assert named == [{"person_id": scene.person}]


def test_with_recognition_off_the_routes_say_nothing_and_refuse_a_write(
    client: TestClient, scene: Scene
) -> None:
    sign_in(client, "admin")

    assert client.get("/api/faces/disagreements").json() == {"people": [], "total": 0}
    assert client.get(f"/api/faces/disagreements/{scene.person}").json()["items"] == []
    assert (
        client.post(f"/api/faces/disagreements/{scene.person}", json={"yes": False}).status_code
        == 409
    )


def test_a_guest_is_refused(client: TestClient, scene: Scene) -> None:
    turn_on(client)
    sign_in(client, "guest", who="stranger")

    assert client.get("/api/faces/disagreements").status_code == 403


def test_the_wall_sets_the_starter_pictures_apart_and_counts_both(
    client: TestClient, scene: Scene
) -> None:
    """`starters` narrows the People Sift can recognize wall; the page counts both halves either way."""
    turn_on(client)
    sign_in(client, "admin")
    scene.attribute(client, how="matched")
    write(
        db_path(client),
        [
            (
                "INSERT INTO face_references "
                "(id, person_id, crop_path, crop_digest, embedding, quality, origin, recognizer, "
                "created_at) VALUES (?, ?, 'crop.jpg', 'digest', x'00000000', 1.0, 'seed', ?, 0)",
                (new_id(), scene.person, pairing("accurate")[1].revision),
            )
        ],
    )

    only = client.get("/api/faces/identified/people", params={"starters": "only"}).json()
    rest = client.get("/api/faces/identified/people", params={"starters": "without"}).json()

    assert [card["person_id"] for card in only["people"]] == [scene.person]
    assert rest["people"] == []
    assert (
        (only["starters_only"], only["others"]) == (rest["starters_only"], rest["others"]) == (1, 0)
    )
    assert client.get("/api/faces/identified/people", params={"starters": "x"}).status_code == 422
