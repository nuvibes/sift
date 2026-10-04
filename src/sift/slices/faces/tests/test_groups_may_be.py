# SPDX-License-Identifier: AGPL-3.0-or-later
"""A whole group compared with a person ("these groups may be her"): asked, never attached.

The review list's one question about several GROUPS at once (`ToCheckKind.MAY_BE`): each unnamed
group whose middle comes within `tuning.GROUP_ASK` of somebody's pictures, or that her folder
proposes, on one card per person, closest first. Yes confirms the faces the card showed and offers
the rest of each group; No refuses every face of the groups as her, which outlasts a regrouping.
Both write one receipt, and both are taken back by it.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# For its side effect: registering the table the ledger is written to.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.faces import recognize, tuning
from sift.slices.faces.jobs import FACE_REMATCH
from sift.slices.faces.models import Attribution, PileStatus, ToCheckKind, Vector
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.queue import (
    TO_NAME,
    IdentifiedRecords,
    SuggestionsQueue,
)
from sift.slices.faces.service import LIKENESS_REASON, FaceService, ToCheckView
from sift.slices.faces.store import Store
from sift.slices.faces.tests import test_routes as routes
from sift.slices.faces.tests.conftest import DIMENSION, make_person, unit
from sift.slices.faces.tests.test_routes import Scene, db_path, make_pile, sign_in, turn_on, write
from sift.slices.workbench.store import Store as WorkbenchStore
from sift.testing.fixtures import create_user
from sift.testing.library import hidden_row

pytestmark = pytest.mark.integration

# The route suite's own fixtures (a booted application, a client signed into it, and the smallest
# scene every faces route needs), taken by name so the wire is asked of the same app.
app = routes.app
client = routes.client
scene = routes.scene

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


@pytest.fixture
async def filed(
    content_store: ContentStore, library: Root, settings: Settings, temp_db: Database
) -> Ingested:
    """A file inside a folder of its own, which is what a folder's proposal is about."""
    target = Path(str(library.abs_path)) / "clips" / "filed.mp4"
    target.parent.mkdir()
    target.write_bytes((CORPUS / "accepted.mp4").read_bytes())
    checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(checked, root_id=library.id, rel_path="clips/filed.mp4")
    # The folder row a scan of the root would have made, and the file placed in it.
    folder = new_id()
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, NULL, ?, ?)",
        (folder, library.id, "clips", "clips"),
    )
    await temp_db.execute(
        "UPDATE asset_locations SET folder_id = ? WHERE asset_id = ?", (folder, ingested.asset.id)
    )
    return ingested


@pytest.fixture
async def admin(temp_db: Database) -> Viewer:
    return await create_user(temp_db, Role.ADMIN)


def axis(index: int) -> list[float]:
    values = [0.0] * DIMENSION
    values[index] = 1.0
    return values


def toward(**closeness: float) -> Vector:
    """A description `closeness[axis]` of the way along each named axis (a0, a1, ...), the rest of
    its length on an axis of its own, so its likeness to a person drawn on `a0` is exactly `a0`."""
    values = [0.0] * DIMENSION
    for name, amount in closeness.items():
        values[int(name[1:])] = amount
    rest = 1.0 - sum(amount * amount for amount in closeness.values())
    values[DIMENSION - 1] = math.sqrt(max(0.0, rest))
    return unit(values)


async def recognizer_of(service: FaceService) -> str:
    return (await service.configuration()).recognizer


async def pictures(store: Store, service: FaceService, person_id: str, on: int) -> None:
    """Give somebody a picture on one axis, which is what their gallery row then is."""
    await store.add_reference(
        person_id,
        vector=unit(axis(on)),
        quality=0.9,
        crop=b"\xff\xd8\xff picture of " + person_id.encode(),
        origin=FaceOrigin.ADDED,
        recognizer=await recognizer_of(service),
        pixels=200,
    )


async def group(
    temp_db: Database,
    store: Store,
    service: FaceService,
    asset_id: str,
    middle: Vector,
    faces: int,
    settings: Settings,
) -> tuple[str, list[str]]:
    """A group of `faces` unnamed faces on one file, each described by `middle`, with a picture
    on the disk behind each: confirming one files it as a reference."""
    recognizer = await recognizer_of(service)
    await temp_db.execute(
        "INSERT INTO face_scans (asset_id, status, depth, coverage, frames_sampled, track_count, "
        "identified_count, detector, recognizer, settings_digest, scanned_at) "
        "VALUES (?, 'none_identified', 'fast', 1.0, 1, 1, 0, 'test-detector', ?, 'abcd', 0) "
        "ON CONFLICT(asset_id) DO NOTHING",
        (asset_id, recognizer),
    )
    tracks: list[str] = []
    for _one in range(faces):
        track_id = new_id()
        stored = f"detected/{track_id[:2]}/{track_id}.jpg"
        picture = settings.data_dir / "faces" / stored
        picture.parent.mkdir(parents=True, exist_ok=True)
        picture.write_bytes(b"\xff\xd8\xff face " + track_id.encode())
        await temp_db.execute(
            "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
            "created_at) VALUES (?, ?, 0, 0, 1, 0.8, 0)",
            (track_id, asset_id),
        )
        await temp_db.execute(
            "INSERT INTO face_detections (id, track_id, timestamp_ms, box_x, box_y, box_w, box_h, "
            "score, quality, crop_path, crop_digest, embedding, created_at) "
            "VALUES (?, ?, 0, 0, 0, 64, 64, 0.9, 0.8, ?, ?, ?, 0)",
            (f"{track_id}D", track_id, stored, track_id, recognize.pack(middle)),
        )
        tracks.append(track_id)
    (pile_id,) = await store.add_piles([(middle, tracks)])
    return pile_id, tracks


async def cards(service: FaceService, viewer: Viewer) -> list[ToCheckView]:
    found, _total = await service.groups_that_may_be(viewer, limit=50, offset=0)
    return found


# --- who is asked about --------------------------------------------------------------------------


async def test_a_group_close_to_her_is_asked_about_closest_first_and_a_far_one_is_not(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """Above the tick line the group starts chosen; between the two lines it is shown unticked;
    under the ask line it is not on the card at all. The closest group leads."""
    person = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, person, 0)
    near, _ = await group(temp_db, store, service, clip.asset.id, toward(a0=0.5), 3, settings)
    close, _ = await group(temp_db, store, service, clip.asset.id, toward(a0=0.8), 4, settings)
    doubtful, _ = await group(temp_db, store, service, clip.asset.id, toward(a0=0.37), 5, settings)
    await group(temp_db, store, service, clip.asset.id, toward(a0=0.30), 6, settings)

    [card] = await cards(service, admin)

    assert card.kind is ToCheckKind.MAY_BE
    assert card.id == person
    assert card.person_name == "Ada Lovelace"
    assert [one.pile_id for one in card.groups] == [close, near, doubtful]
    assert [one.ticked for one in card.groups] == [True, True, False]
    assert [round(one.likeness or 0, 2) for one in card.groups] == [0.8, 0.5, 0.37]
    assert card.groups[0].reasons[0].kind == LIKENESS_REASON
    assert card.size == 4 + 3 + 5
    assert card.best is not None and round(card.best, 2) == 0.8


async def test_a_group_of_two_is_not_compared(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """Under `tuning.GROUP_AT_LEAST` a group's middle is one or two faces: the single-face scale,
    which the ordinary match reads face by face."""
    person = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, person, 0)
    await group(temp_db, store, service, clip.asset.id, toward(a0=0.9), 2, settings)

    assert await cards(service, admin) == []


async def test_a_group_is_offered_to_its_closest_person_only(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    ada = await make_person(temp_db, "Ada Lovelace")
    bryn = await make_person(temp_db, "Bryn Calloway")
    await pictures(store, service, ada, 0)
    await pictures(store, service, bryn, 1)
    await group(temp_db, store, service, clip.asset.id, toward(a0=0.6, a1=0.45), 3, settings)

    assert [card.id for card in await cards(service, admin)] == [ada]


async def test_a_group_mostly_refused_as_her_goes_to_the_next_closest(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """Refused as her face by face is her answer already, and the refused person is removed BEFORE
    the closest is chosen (the rule one face keeps), so the next one over the line is asked."""
    ada = await make_person(temp_db, "Ada Lovelace")
    bryn = await make_person(temp_db, "Bryn Calloway")
    await pictures(store, service, ada, 0)
    await pictures(store, service, bryn, 1)
    _pile, faces = await group(
        temp_db, store, service, clip.asset.id, toward(a0=0.6, a1=0.45), 3, settings
    )
    for track_id in faces[:2]:
        await store.reject(track_id, ada)

    assert [card.id for card in await cards(service, admin)] == [bryn]


async def test_a_person_this_viewer_may_not_be_told_about_has_no_card(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, person, 0)
    await group(temp_db, store, service, clip.asset.id, toward(a0=0.8), 3, settings)
    statement, parameters = hidden_row("person", person, admin.id)
    await temp_db.execute(statement, parameters)

    assert await cards(service, admin) == []


async def test_her_folders_group_joins_her_card_ticked_with_its_reason(
    service: FaceService,
    store: Store,
    temp_db: Database,
    filed: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """A folder's proposal is the second kind of reason on the same card, and it is ticked
    whatever the group scores: the likeness line does not measure a folder."""
    person = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, person, 0)
    pile, _faces = await group(temp_db, store, service, filed.asset.id, toward(a0=0.2), 3, settings)
    folder = await temp_db.fetch_one(
        "SELECT folder_id FROM asset_locations WHERE asset_id = ?", (filed.asset.id,)
    )
    assert folder is not None and folder["folder_id"] is not None
    await store.propose_pile(
        pile, person, reason="folder", folder_id=str(folder["folder_id"]), files=1, of_files=1
    )

    [card] = await cards(service, admin)

    [only] = card.groups
    assert only.pile_id == pile
    assert only.ticked is True
    assert [reason.kind for reason in only.reasons] == ["folder"]
    assert only.reasons[0].in_folder == 1


# --- the two answers -----------------------------------------------------------------------------


async def test_yes_confirms_the_faces_shown_offers_the_rest_and_undo_groups_them_again(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    written = WorkbenchStore(temp_db)
    service._recorder = written
    person = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, person, 0)
    pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 5, settings)
    shown, rest = faces[:2], faces[2:]
    references = await store.reference_count(person)

    run = await service.confirm_groups(admin, person, [pile], shown)

    assert (run.changed, run.offered) == (2, 3)
    after = await store.tracks(faces)
    assert all(after[one].attribution is Attribution.CONFIRMED for one in shown)
    assert all(
        after[one].person_id == person and after[one].attribution is Attribution.SUGGESTED
        for one in rest
    ), "the faces nobody was shown are asked about, never confirmed"
    assert await store.reference_count(person) == references + 2
    receipts, _total = await written.recent(limit=5, offset=0)
    [receipt] = [one for one in receipts if one.id == run.decision_id]
    assert receipt.title == "You said 1 group is Ada Lovelace"
    assert "waiting under Needs your input" in receipt.detail
    # The state is one word on every screen and receipt; who said so is the title's "You said".
    assert "are now Confirmed, and Sift" in receipt.detail
    # What the re-match's grouping does next: the group, emptied by the press, goes.
    await store.drop_empty_piles()

    assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload) is True

    back = await store.tracks(faces)
    assert all(back[one].person_id is None for one in faces)
    assert await store.reference_count(person) == references
    assert all(back[one].pile_id is not None for one in faces), (
        "a face taken back sits in a group again, or it is on no screen at all"
    )


async def test_naming_a_group_from_its_own_card_writes_the_receipt_a_yes_writes(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """Naming a group under Faces to name writes the receipt a "these groups may be her" card
    writes, not only History lines nobody can take back. Same act, same receipt, same Undo."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    person = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, person, 0)
    _pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 5, settings)
    shown, rest = faces[:2], faces[2:]
    references = await store.reference_count(person)

    run = await service.name_groups(admin, shown, person)

    assert (run.changed, run.offered) == (2, 3)
    receipts, _total = await written.recent(limit=5, offset=0)
    [receipt] = [one for one in receipts if one.id == run.decision_id]
    assert receipt.title == "You said 1 group is Ada Lovelace"
    assert sorted(json.loads(receipt.payload)["offered"]) == sorted(rest)
    await store.drop_empty_piles()

    assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload) is True

    back = await store.tracks(faces)
    assert all(back[one].person_id is None for one in faces)
    assert await store.reference_count(person) == references


async def test_naming_faces_one_by_one_writes_a_record_for_the_user_and_undo_takes_it_back(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """Naming faces on the popout (`confirm_many`) or confirming one (`confirm`) is recorded for
    the User who pressed, under the queue the other face decisions use, with the track ids; and it
    has a way back, like every other face decision."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    person = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, person, 0)
    _pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 3, settings)
    references = await store.reference_count(person)

    assert await service.confirm_many(faces[:2], person, viewer=admin) == 2
    await service.confirm(faces[2], person, viewer=admin)

    receipts, _total = await written.recent(limit=5, offset=0)
    titles = sorted(one.title for one in receipts)
    assert titles == ["You named 1 face as Ada Lovelace", "You named 2 faces as Ada Lovelace"]
    for receipt in receipts:
        recorded = json.loads(receipt.payload)
        assert recorded["act"] == "named-groups"
        assert set(recorded["track_ids"]) <= set(faces)
    await store.drop_empty_piles()

    for receipt in receipts:
        assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload)

    back = await store.tracks(faces)
    assert all(back[one].person_id is None for one in faces)
    assert await store.reference_count(person) == references


async def test_confirming_a_face_nobody_pressed_writes_no_record(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
) -> None:
    written = WorkbenchStore(temp_db)
    service._recorder = written
    person = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, person, 0)
    _pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 2, settings)

    assert await service.confirm_many(faces, person) == 2

    receipts, _total = await written.recent(limit=5, offset=0)
    assert receipts == []


async def test_yes_confirms_nothing_outside_the_groups_the_card_offers(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """The ids are narrowed, never trusted: a face of a group the card does not offer, sent under
    a group it does, is not confirmed, and a group it does not offer names nothing."""
    person = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, person, 0)
    offered, faces = await group(
        temp_db, store, service, clip.asset.id, toward(a0=0.7), 3, settings
    )
    far, strangers = await group(
        temp_db, store, service, clip.asset.id, toward(a0=0.1), 3, settings
    )

    run = await service.confirm_groups(admin, person, [offered], [strangers[0]])
    assert run.changed == 0
    run = await service.confirm_groups(admin, person, [far], strangers)
    assert run.changed == 0

    assert all(one.person_id is None for one in (await store.tracks(faces + strangers)).values())


async def test_no_refuses_every_face_outlasts_a_regrouping_and_undo_asks_again(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    written = WorkbenchStore(temp_db)
    service._recorder = written
    person = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, person, 0)
    pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 4, settings)

    run = await service.refuse_groups(admin, person, [pile])

    assert run.changed == 4
    rejections = await store.rejections()
    assert all(person in rejections.get(one, ()) for one in faces)
    assert all(one.person_id is None for one in (await store.tracks(faces)).values())
    assert await cards(service, admin) == []
    # The group stays where it was, for somebody to say who it is.
    waiting, _total = await service.piles(admin, PileStatus.OPEN)
    assert [one.id for one in waiting] == [pile]
    # Rebuilt from scratch, under whatever id: still not offered as her.
    await service.regroup(full=True)
    assert await cards(service, admin) == []

    receipts, _total = await written.recent(limit=5, offset=0)
    [receipt] = [one for one in receipts if one.id == run.decision_id]
    assert receipt.title == "You said 1 group is not Ada Lovelace"
    assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload) is True

    assert [card.id for card in await cards(service, admin)] == [person]
    assert all(one.person_id is None for one in (await store.tracks(faces)).values())


async def test_no_settles_her_folders_proposal_so_it_is_never_made_again(
    service: FaceService,
    store: Store,
    temp_db: Database,
    filed: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")
    pile, _faces = await group(temp_db, store, service, filed.asset.id, toward(a0=0.2), 3, settings)
    folder = await temp_db.fetch_one(
        "SELECT folder_id FROM asset_locations WHERE asset_id = ?", (filed.asset.id,)
    )
    assert folder is not None
    await store.propose_pile(
        pile, person, reason="folder", folder_id=str(folder["folder_id"]), files=1, of_files=1
    )

    assert (await service.refuse_groups(admin, person, [pile])).changed == 3

    state = await temp_db.fetch_one(
        "SELECT state FROM face_pile_proposals WHERE pile_id = ? AND person_id = ?", (pile, person)
    )
    assert state is not None and state["state"] == "refused"


async def test_undoing_the_no_brings_her_folders_proposal_back(
    service: FaceService,
    store: Store,
    temp_db: Database,
    filed: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """The refusal is what settled the proposal, so taking the refusal back puts the proposal back
    under Needs your input with its folder reason, or the undo would be half done."""
    person = await make_person(temp_db, "Ada Lovelace")
    pile, faces = await group(temp_db, store, service, filed.asset.id, toward(a0=0.2), 3, settings)
    folder = await temp_db.fetch_one(
        "SELECT folder_id FROM asset_locations WHERE asset_id = ?", (filed.asset.id,)
    )
    assert folder is not None
    await store.propose_pile(
        pile, person, reason="folder", folder_id=str(folder["folder_id"]), files=1, of_files=1
    )
    await service.refuse_groups(admin, person, [pile])

    assert await service.unrefuse_groups(person, list(faces)) == 3

    state = await temp_db.fetch_one(
        "SELECT state FROM face_pile_proposals WHERE pile_id = ? AND person_id = ?", (pile, person)
    )
    assert state is not None and state["state"] == "pending"


async def test_somebody_known_by_starters_alone_is_offered_with_the_stash_box_reason(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """A starter picture is a box's picture of her, not hers: a group that looks like it is offered
    as "looks like FansDB's pictures", which is weaker evidence than looking like her own."""
    person = await make_person(temp_db, "Ada Lovelace")
    await store.add_reference(
        person,
        vector=unit(axis(0)),
        quality=0.9,
        crop=b"\xff\xd8\xff starter of " + person.encode(),
        origin=FaceOrigin.SEED,
        recognizer=await recognizer_of(service),
        pixels=200,
        source="FansDB",
    )
    await group(temp_db, store, service, clip.asset.id, toward(a0=0.8), 4, settings)
    await group(temp_db, store, service, clip.asset.id, toward(a0=0.37), 5, settings)

    [card] = await cards(service, admin)

    assert [reason.kind for one in card.groups for reason in one.reasons] == [
        "stash-box",
        "stash-box",
    ]
    # The box is named, so the card says "Compared with FansDB's pictures", never "a stash-box's".
    assert [reason.box_names for one in card.groups for reason in one.reasons] == [
        ("FansDB",),
        ("FansDB",),
    ]
    # The box's likeness is still a likeness, and ticked by the same line as her own: weaker
    # evidence never starts chosen where the stronger kind would not.
    assert [one.ticked for one in card.groups] == [True, False]


# --- where the tier sits on the list -------------------------------------------------------------


async def test_needs_your_input_reads_her_questions_then_the_groups_that_may_be_her(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """Two tiers on one tab, paged by one offset and counted as one, and the board's card counts
    them together."""
    ada = await make_person(temp_db, "Ada Lovelace")
    bryn = await make_person(temp_db, "Bryn Calloway")
    await pictures(store, service, ada, 0)
    await pictures(store, service, bryn, 1)
    await group(temp_db, store, service, clip.asset.id, toward(a1=0.7), 3, settings)
    _asked, questions = await group(
        temp_db, store, service, clip.asset.id, toward(a2=0.9), 1, settings
    )
    await store.attribute(questions[0], ada, confidence=0.5, attribution=Attribution.SUGGESTED)

    tiers = (ToCheckKind.PERSON, ToCheckKind.MAY_BE)
    page, total, _small = await service.to_check(admin, kind=tiers)

    assert [(one.kind, one.id) for one in page] == [
        (ToCheckKind.PERSON, ada),
        (ToCheckKind.MAY_BE, bryn),
    ]
    assert total == 2
    second, _total, _small = await service.to_check(admin, kind=tiers, limit=1, offset=1)
    assert [(one.kind, one.id) for one in second] == [(ToCheckKind.MAY_BE, bryn)]
    assert await service.position_in_to_check(admin, bryn, kind=tiers) == 1
    assert [one.kind for one in (await service.to_check(admin, kind=ToCheckKind.PERSON))[0]] == [
        ToCheckKind.PERSON
    ]

    # The whole list pages the tiers by one offset, so the groups nobody has named start after it.
    stranger, _faces = await group(
        temp_db, store, service, clip.asset.id, toward(a3=0.9), tuning.STRANGER_FLOOR, settings
    )
    third, _total, _small = await service.to_check(admin, limit=1, offset=2)
    assert [(one.kind, one.id) for one in third] == [(ToCheckKind.GROUP, stranger)]

    summary = await SuggestionsQueue(service).survey(admin)
    assert summary.count == 2


def test_the_group_lines_sit_between_the_measured_populations() -> None:
    """The measured bounds the two lines were chosen inside (see `tuning.GROUP_ASK`): above every
    group measured as somebody else (0.307) and below every group measured as the person (0.600),
    with the tick line no lower than the ask line."""
    assert 0.307 < tuning.GROUP_ASK <= tuning.GROUP_TICK < 0.600


# --- what a card offers, and what a press may reach ------------------------------------------------


async def _folder_of(temp_db: Database, asset_id: str) -> str:
    row = await temp_db.fetch_one(
        "SELECT folder_id FROM asset_locations WHERE asset_id = ?", (asset_id,)
    )
    assert row is not None and row["folder_id"] is not None
    return str(row["folder_id"])


async def test_the_boards_still_of_the_groups_that_may_be_her_opens_the_group_it_is_from(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """With no question standing about her own faces, the board's Faces card draws a still of the
    groups that may be her, and pressing it opens the group the crop is from, not her page: those
    faces are in a group, not on her page."""
    person = await make_person(temp_db, "Bryn Calloway")
    await pictures(store, service, person, 1)
    pile, _faces = await group(temp_db, store, service, clip.asset.id, toward(a1=0.7), 3, settings)

    summary = await SuggestionsQueue(service).survey(admin)

    assert summary.count == 1
    assert [one.href for one in summary.preview] == [f"/organize/{TO_NAME}/{pile}"]


async def test_her_folders_proposal_is_not_made_where_most_of_the_group_was_refused_as_her(
    service: FaceService,
    store: Store,
    temp_db: Database,
    filed: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """Refused face by face is her answer already, and a folder is weaker evidence than that answer:
    the proposal stands in the table and is not offered."""
    person = await make_person(temp_db, "Ada Lovelace")
    pile, faces = await group(temp_db, store, service, filed.asset.id, toward(a0=0.2), 3, settings)
    folder = await _folder_of(temp_db, filed.asset.id)
    await store.propose_pile(pile, person, reason="folder", folder_id=folder, files=1, of_files=1)
    assert [card.id for card in await cards(service, admin)] == [person]

    for track_id in faces[:2]:
        await store.reject(track_id, person)

    assert await cards(service, admin) == []


async def test_two_groups_proposed_from_one_folder_are_each_said_and_one_from_an_empty_folder_is_not(
    service: FaceService,
    store: Store,
    temp_db: Database,
    filed: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """The folder's files are asked once however many groups it proposed, and a proposal naming a
    folder none of the group's files is in says nothing: a sentence counting none of them in it
    would be a claim the folder cannot make."""
    ada = await make_person(temp_db, "Ada Lovelace")
    bryn = await make_person(temp_db, "Bryn Calloway")
    folder = await _folder_of(temp_db, filed.asset.id)
    root = await temp_db.fetch_one("SELECT root_id FROM folders WHERE id = ?", (folder,))
    assert root is not None
    elsewhere = new_id()
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, NULL, ?, ?)",
        (elsewhere, root["root_id"], "elsewhere", "elsewhere"),
    )
    first, _ = await group(temp_db, store, service, filed.asset.id, toward(a2=0.9), 3, settings)
    second, _ = await group(temp_db, store, service, filed.asset.id, toward(a3=0.9), 3, settings)
    outside, _ = await group(temp_db, store, service, filed.asset.id, toward(a4=0.9), 3, settings)
    # A folder has one main face per person, so the two groups it names are two people's.
    for pile, who, where in (
        (first, ada, folder),
        (second, bryn, folder),
        (outside, ada, elsewhere),
    ):
        await store.propose_pile(pile, who, reason="folder", folder_id=where, files=1, of_files=1)

    said = await service.proposals_for(admin, [first, second, outside])

    assert sorted(said) == sorted([first, second])
    assert await service.proposals_for(admin, [new_id()]) == {}, "a group nobody proposed"
    assert all(one.in_folder == 1 for pile in said for one in said[pile])


async def test_a_group_whose_every_file_is_kept_from_the_viewer_puts_nobody_on_a_card(
    service: FaceService,
    store: Store,
    access: Repository,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """A card is its groups' faces: a group whose faces are all on a file in the shut vault draws
    nothing, and a card with no group left to draw is not a card: it would name her beside a
    file this viewer is not told exists."""
    person = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, person, 0)
    await group(temp_db, store, service, clip.asset.id, toward(a0=0.8), 3, settings)
    assert [card.id for card in await cards(service, admin)] == [person]

    assert await access.set_asset_vault(replace(admin, show_hidden=True), clip.asset.id, vault=True)

    assert await cards(service, admin) == []


async def test_a_yes_or_a_no_about_somebody_this_viewer_may_not_be_told_about_changes_nothing(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """The answer "nothing changed" rather than a refusal, so a press cannot ask whether somebody
    exists; and neither answer touches a face."""
    person = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, person, 0)
    pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.8), 3, settings)
    statement, parameters = hidden_row("person", person, admin.id)
    await temp_db.execute(statement, parameters)

    assert (await service.confirm_groups(admin, person, [pile], faces)).changed == 0
    assert (await service.refuse_groups(admin, person, [pile])).changed == 0
    assert all(one.person_id is None for one in (await store.tracks(faces)).values())
    assert await store.rejections() == {}


async def test_a_no_about_a_group_the_card_does_not_offer_refuses_nothing(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, person, 0)
    far, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.1), 3, settings)

    run = await service.refuse_groups(admin, person, [far])

    assert (run.changed, run.decision_id) == (0, "")
    rejections = await store.rejections()
    assert not any(person in rejections.get(one, ()) for one in faces)


async def test_a_yes_whose_faces_were_named_meanwhile_names_nothing_and_leaves_the_folder_asking(
    service: FaceService,
    store: Store,
    temp_db: Database,
    filed: Ingested,
    settings: Settings,
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another press lands between the card being read and this one being written: its faces
    already carry somebody, so this press names none of them, writes no receipt and settles no
    proposal: the folder's question stays for the person who has not answered it."""
    person = await make_person(temp_db, "Ada Lovelace")
    other = await make_person(temp_db, "Bryn Calloway")
    pile, faces = await group(temp_db, store, service, filed.asset.id, toward(a0=0.2), 3, settings)
    folder = await _folder_of(temp_db, filed.asset.id)
    await store.propose_pile(pile, person, reason="folder", folder_id=folder, files=1, of_files=1)
    touchable = service.touchable_faces

    async def answered_meanwhile(viewer: Viewer, track_ids: list[str]):  # type: ignore[no-untyped-def]
        allowed = await touchable(viewer, track_ids)
        for track_id in track_ids:
            await store.attribute(
                track_id, other, confidence=1.0, attribution=Attribution.CONFIRMED
            )
        return allowed

    monkeypatch.setattr(service, "touchable_faces", answered_meanwhile)

    run = await service.confirm_groups(admin, person, [pile], faces[:2])

    assert run.changed == 0
    assert all(one.person_id == other for one in (await store.tracks(faces[:2])).values())
    state = await temp_db.fetch_one(
        "SELECT state FROM face_pile_proposals WHERE pile_id = ? AND person_id = ?", (pile, person)
    )
    assert state is not None and state["state"] == "pending"


async def test_naming_a_group_face_that_already_carries_somebody_writes_nothing(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """A face offered as somebody keeps its place in the group; naming it from the group's card
    names nobody (a naming is for faces nobody is on), so no receipt claims a press that did
    nothing."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    person = await make_person(temp_db, "Ada Lovelace")
    other = await make_person(temp_db, "Bryn Calloway")
    _pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 3, settings)
    await store.attribute(faces[0], other, confidence=0.5, attribution=Attribution.SUGGESTED)

    run = await service.name_groups(admin, faces[:1], person)

    assert (run.changed, run.decision_id) == (0, "")
    assert (await store.tracks(faces[:1]))[faces[0]].person_id == other
    _receipts, total = await written.recent(limit=5, offset=0)
    assert total == 0


async def test_naming_faces_in_no_group_names_only_them_and_writes_no_group_receipt(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    written = WorkbenchStore(temp_db)
    service._recorder = written
    person = await make_person(temp_db, "Ada Lovelace")
    _pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 3, settings)
    await temp_db.execute("UPDATE face_tracks SET pile_id = NULL WHERE id = ?", (faces[0],))

    run = await service.name_groups(admin, faces[:1], person)

    assert (run.changed, run.offered) == (1, 0)
    after = await store.tracks(faces)
    assert (after[faces[0]].person_id, after[faces[0]].attribution) == (
        person,
        Attribution.CONFIRMED,
    )
    assert all(after[one].person_id is None for one in faces[1:])
    receipts, _total = await written.recent(limit=5, offset=0)
    assert [one.title for one in receipts if "group" in one.title] == []


async def test_a_yes_with_no_history_wired_still_names_and_offers_no_undo(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, person, 0)
    pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 3, settings)

    run = await service.confirm_groups(admin, person, [pile], faces[:1])

    assert (run.changed, run.offered, run.decision_id) == (1, 2, "")


async def test_undoing_a_yes_leaves_every_face_answered_since_as_it_now_stands(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """Undo works from the receipt and never over a newer decision: a face confirmed by the press
    and named as somebody else since is not this Undo's to take back, and one that took nothing
    back asks for no regrouping."""
    person = await make_person(temp_db, "Ada Lovelace")
    other = await make_person(temp_db, "Bryn Calloway")
    await pictures(store, service, person, 0)
    pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 3, settings)
    assert (await service.confirm_groups(admin, person, [pile], faces[:1])).changed == 1
    references = await store.reference_count(person)
    for track_id in faces:
        await store.attribute(track_id, other, confidence=1.0, attribution=Attribution.CONFIRMED)

    assert await service.unname_groups(person, faces[:1], faces[1:]) == 0

    assert all(one.person_id == other for one in (await store.tracks(faces)).values())
    assert await store.reference_count(person) == references


async def test_undoing_a_no_skips_a_face_named_since_and_forgets_one_left_in_no_group(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """A face named since carries a newer decision, so its refusal is left with it; a face whose
    group has gone since is still nobody's, so its refusal is forgotten like the rest."""
    person = await make_person(temp_db, "Ada Lovelace")
    other = await make_person(temp_db, "Bryn Calloway")
    await pictures(store, service, person, 0)
    pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 3, settings)
    assert (await service.refuse_groups(admin, person, [pile])).changed == 3
    named, loose, grouped = faces
    await store.attribute(named, other, confidence=1.0, attribution=Attribution.CONFIRMED)
    await temp_db.execute("UPDATE face_tracks SET pile_id = NULL WHERE id = ?", (loose,))

    assert await service.unrefuse_groups(person, [named, loose, grouped, new_id()]) == 2

    rejections = await store.rejections()
    assert person in rejections.get(named, ())
    assert person not in rejections.get(loose, ())
    assert person not in rejections.get(grouped, ())


async def test_the_second_card_of_the_groups_tier_is_found_where_the_tab_draws_it(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    ada = await make_person(temp_db, "Ada Lovelace")
    bryn = await make_person(temp_db, "Bryn Calloway")
    await pictures(store, service, ada, 0)
    await pictures(store, service, bryn, 1)
    await group(temp_db, store, service, clip.asset.id, toward(a0=0.9), 3, settings)
    await group(temp_db, store, service, clip.asset.id, toward(a1=0.7), 3, settings)

    drawn = [card.id for card in await cards(service, admin)]
    assert drawn == [ada, bryn]
    assert await service.position_in_to_check(admin, bryn, kind=ToCheckKind.MAY_BE) == 1
    assert await service.position_in_to_check(admin, new_id(), kind=ToCheckKind.MAY_BE) is None


# --- over the wire -------------------------------------------------------------------------------


def test_the_card_goes_over_the_wire_and_its_yes_names_the_faces_it_showed(
    client: TestClient, scene: Scene
) -> None:
    """Needs your input asks for two tiers by repeating `kind`; a `may_be` card carries its groups,
    each with its faces, its tick and its reasons. A Yes that names no faces is refused."""
    turn_on(client)
    sign_in(client, "admin")
    pile = make_pile(client, scene)
    write(
        db_path(client),
        [
            (
                "INSERT INTO face_pile_proposals (pile_id, person_id, reason, folder_id, files, "
                "of_files, state, created_at, updated_at) "
                "VALUES (?, ?, 'folder', ?, 1, 1, 'pending', 0, 0)",
                (pile, scene.person, scene.folder),
            )
        ],
    )

    refused = client.post(f"/api/faces/may-be/{scene.person}/confirm", json={"pile_ids": [pile]})
    assert refused.status_code == 400

    answer = client.get("/api/faces/to-check", params=[("kind", "person"), ("kind", "may_be")])

    assert answer.status_code == 200
    [card] = answer.json()["items"]
    assert (card["kind"], card["id"], card["person_name"]) == (
        "may_be",
        scene.person,
        "Ada Lovelace",
    )
    [one] = card["groups"]
    assert one["pile_id"] == pile
    assert one["ticked"] is True
    assert [face["track_id"] for face in one["faces"]] == [scene.track]
    assert one["reasons"] == [
        {
            "kind": "folder",
            "folder_id": scene.folder,
            "folder_name": "clips",
            "in_folder": 1,
            "group_files": 1,
            "box_names": [],
        }
    ]

    named = client.post(
        f"/api/faces/may-be/{scene.person}/confirm",
        json={"pile_ids": [pile], "track_ids": [scene.track]},
    )
    assert named.status_code == 200
    assert named.json()["changed"] == 1
    assert named.json()["decision_id"]


# --- what an Undo of a naming takes back beyond its faces ----------------------------------------


class _Asked:
    """The work queue as an Undo meets it: what it was asked to run."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def enqueue_when_settled(self, job_type: str, *_: object, **__: object) -> str:
        self.asked.append(job_type)
        return "job"


async def _cover(temp_db: Database, person_id: str) -> tuple[object, object, object]:
    row = await temp_db.fetch_one(
        "SELECT cover_asset_id, cover_track_id, cover_by_default FROM people WHERE id = ?",
        (person_id,),
    )
    assert row is not None
    return row["cover_asset_id"], row["cover_track_id"], row["cover_by_default"]


async def _retired(temp_db: Database, reference_id: str) -> bool:
    row = await temp_db.fetch_one(
        "SELECT retired_at FROM face_references WHERE id = ?", (reference_id,)
    )
    assert row is not None
    return row["retired_at"] is not None


async def _undo_every(service: FaceService, written: WorkbenchStore, admin: Viewer) -> None:
    receipts, _total = await written.recent(limit=20, offset=0)
    for receipt in receipts:
        if receipt.queue == "identified":
            assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload)


async def test_a_naming_gives_the_whole_file_and_its_undo_takes_it_and_never_one_chosen_since(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """Naming somebody with no picture gives them the whole file as their cover, never the face
    (a cover nobody chose is the first picture filed under them); taking the name back takes the
    file off them and the picture with it (here, no other file: nothing). A picture chosen for
    somebody since the naming is a newer decision and stays."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    ada = await make_person(temp_db, "Ada Lovelace")
    bryn = await make_person(temp_db, "Bryn Calloway")
    _pile, hers = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 1, settings)
    _other, his = await group(temp_db, store, service, clip.asset.id, toward(a1=0.7), 1, settings)
    await service.confirm_many(hers, ada, viewer=admin)
    await service.confirm_many(his, bryn, viewer=admin)
    assert await _cover(temp_db, ada) == (clip.asset.id, None, clip.asset.id)
    # A picture chosen for him since: a chosen cover is no default.
    await temp_db.execute("UPDATE people SET cover_by_default = NULL WHERE id = ?", (bryn,))

    await _undo_every(service, written, admin)

    assert await _cover(temp_db, ada) == (None, None, None)
    assert await _cover(temp_db, bryn) == (clip.asset.id, None, None)


async def test_sifts_own_match_gives_no_face_cover_and_an_older_receipts_undo_takes_one_back(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """A re-match that recognizes somebody's first face gives them the whole file as a cover, never
    the face, and its receipt names no cover. A receipt written before (when a match made the face
    the cover) still takes that face off; a question gives nobody a cover."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    ada = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, ada, 0)
    _pile, faces = await group(temp_db, store, service, clip.asset.id, unit(axis(0)), 1, settings)

    assert await service.rematch() == 1

    matched = await store.track(faces[0])
    assert matched is not None and matched.attribution is Attribution.MATCHED
    assert await _cover(temp_db, ada) == (clip.asset.id, None, clip.asset.id)
    [receipt] = [one for one in (await written.recent(limit=5, offset=0))[0]]
    assert "cover" not in json.loads(receipt.payload)

    # As a library from before catalog 79 holds it: the face made her cover, the receipt says so.
    await temp_db.execute(
        "UPDATE people SET cover_track_id = ?, cover_by_default = NULL WHERE id = ?",
        (faces[0], ada),
    )
    older = json.dumps({**json.loads(receipt.payload), "cover": faces[0]})
    assert await IdentifiedRecords(service).reverse(admin, receipt.id, older)

    assert (await _cover(temp_db, ada))[1] is None


async def test_undoing_her_first_picture_of_her_own_brings_back_the_starters_it_retired(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """Her first own picture retires the stash-box's starters; taking that naming back leaves her
    with no picture of her own, so the starters are in use again. Somebody who has a picture of
    her own by then keeps them retired: the rule that retired them still holds."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    recognizer = await recognizer_of(service)
    ada = await make_person(temp_db, "Ada Lovelace")
    bryn = await make_person(temp_db, "Bryn Calloway")
    starters: dict[str, str] = {}
    for who in (ada, bryn):
        starter = await store.add_reference(
            who,
            vector=unit(axis(0)),
            quality=0.9,
            crop=b"\xff\xd8\xff starter of " + who.encode(),
            origin=FaceOrigin.SEED,
            recognizer=recognizer,
            source="FansDB",
        )
        assert starter is not None
        starters[who] = starter
    _pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 2, settings)
    await service.confirm_many(faces[:1], ada, viewer=admin)
    await service.confirm_many(faces[1:], bryn, viewer=admin)
    assert await _retired(temp_db, starters[ada]) and await _retired(temp_db, starters[bryn])
    # A picture of his own, filed by hand after the naming.
    await pictures(store, service, bryn, 1)

    await _undo_every(service, written, admin)

    assert await _retired(temp_db, starters[ada]) is False
    assert await _retired(temp_db, starters[bryn]) is True


async def test_a_naming_that_files_no_picture_of_her_own_retires_no_starter_and_names_none(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The face's picture cannot be read, so nothing of hers is filed and her starters stay in
    use; the receipt names no starter, so its Undo has none to bring back."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    ada = await make_person(temp_db, "Ada Lovelace")
    starter = await store.add_reference(
        ada,
        vector=unit(axis(0)),
        quality=0.9,
        crop=b"\xff\xd8\xff starter of ada",
        origin=FaceOrigin.SEED,
        recognizer=await recognizer_of(service),
        source="FansDB",
    )
    assert starter is not None
    _pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 1, settings)

    async def unreadable(_path: object) -> None:
        return None

    monkeypatch.setattr(store, "picture_bytes", unreadable)

    await service.confirm_many(faces[:1], ada, viewer=admin)

    assert await _retired(temp_db, starter) is False
    (receipt,), _total = await written.recent(limit=1, offset=0)
    payload = json.loads(receipt.payload)
    assert payload["references"] == {faces[0]: []}
    assert "starters" not in payload


async def test_undoing_a_yes_puts_the_folder_proposal_it_accepted_back_to_asking(
    service: FaceService,
    store: Store,
    temp_db: Database,
    filed: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """A Yes settles the folder's proposal as accepted; taking the Yes back puts it back to asking,
    the state it held before the press, for a group that outlives the Undo."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    ada = await make_person(temp_db, "Ada Lovelace")
    await pictures(store, service, ada, 0)
    pile, faces = await group(temp_db, store, service, filed.asset.id, toward(a0=0.2), 4, settings)
    folder = await _folder_of(temp_db, filed.asset.id)
    await store.propose_pile(pile, ada, reason="folder", folder_id=folder, files=1, of_files=1)
    # One face she already said is not her is neither confirmed nor offered, so the group lives on.
    await store.reject(faces[3], ada)

    run = await service.confirm_groups(admin, ada, [pile], faces[:2])

    assert run.changed == 2
    state = "SELECT state FROM face_pile_proposals WHERE pile_id = ? AND person_id = ?"
    row = await temp_db.fetch_one(state, (pile, ada))
    assert row is not None and row["state"] == "accepted"
    receipts, _total = await written.recent(limit=5, offset=0)
    [receipt] = [one for one in receipts if one.id == run.decision_id]
    assert json.loads(receipt.payload)["proposals"] == [pile]

    assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload)

    row = await temp_db.fetch_one(state, (pile, ada))
    assert row is not None and row["state"] == "pending"


async def test_an_undo_that_took_pictures_away_asks_for_a_rematch_and_one_that_took_none_does_not(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """Her gallery lost the pictures the naming filed, so the faces are compared again, as after
    any change to somebody's pictures. An Undo that removed none has changed no gallery."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    ada = await make_person(temp_db, "Ada Lovelace")
    _pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 1, settings)
    await service.confirm_many(faces, ada, viewer=admin)
    (receipt,), _total = await written.recent(limit=5, offset=0)
    queue = _Asked()
    records = IdentifiedRecords(service, queue=queue)  # type: ignore[arg-type]

    assert await records.reverse(admin, receipt.id, receipt.payload)
    assert queue.asked == [FACE_REMATCH]

    await records.reverse(admin, receipt.id, receipt.payload)
    assert queue.asked == [FACE_REMATCH], "the second press took nothing away"


async def test_undoing_a_naming_leaves_the_same_picture_filed_by_hand_before_it(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    settings: Settings,
    admin: Viewer,
) -> None:
    """A picture is one reference row whoever filed it, so naming a face whose picture she already
    held files nothing new. Its Undo removes the rows its press created, which here is none: the
    picture filed by hand is that earlier decision's and stays."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    ada = await make_person(temp_db, "Ada Lovelace")
    _pile, faces = await group(temp_db, store, service, clip.asset.id, toward(a0=0.7), 1, settings)
    by_hand = await store.add_reference(
        ada,
        vector=unit(axis(0)),
        quality=0.9,
        crop=b"\xff\xd8\xff face " + faces[0].encode(),
        origin=FaceOrigin.ADDED,
        recognizer=await recognizer_of(service),
    )
    assert by_hand is not None
    await service.confirm_many(faces, ada, viewer=admin)
    (receipt,), _total = await written.recent(limit=5, offset=0)
    assert json.loads(receipt.payload)["references"] == {faces[0]: []}

    assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload)

    assert [one.id for one in await store.references(ada)] == [by_hand]
