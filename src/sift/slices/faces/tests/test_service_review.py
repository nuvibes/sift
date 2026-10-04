# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the review screens read: the pictures, faces removed and moved by hand, the counts and
places of cards, where a reference came from, and the People Sift can recognize wall."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import numpy as np
import pytest

from sift.kernel.access import Actionable, Role, Viewer
from sift.kernel.content import Ingested
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sampling import face_frames
from sift.slices.faces import (
    recognize,
    tuning,
)
from sift.slices.faces import settings as face_settings
from sift.slices.faces.frames import Frame
from sift.slices.faces.models import (
    Appearance,
    Attribution,
    PileStatus,
    ScanStatus,
    Vector,
)
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.queue import IdentifiedRecords
from sift.slices.faces.schema import FACE_BAND_KIND
from sift.slices.faces.service import (
    FaceService,
)
from sift.slices.faces.store import PassRecord, Store
from sift.slices.faces.tests import test_service, test_service_scanning
from sift.slices.faces.tests.conftest import (
    FakeDetector,
    FakePreferences,
    FakeRecognizer,
    draw_face,
    make_person,
    noisy_frame,
    person_vector,
)
from sift.slices.faces.tests.test_service import (
    Scripted,
    _one_face_recognized_as,
    _two_files_of_one_stranger,
    by_shade,
    install_reader,
    three_people_frame,
)
from sift.slices.faces.tests.test_service_grouping import (
    _described,
)
from sift.slices.workbench.store import Store as WorkbenchStore
from sift.testing.fixtures import create_user
from sift.testing.library import hidden_row

pytestmark = pytest.mark.integration

#: The fixtures this file shares with the files they are defined in, found here by name.
clip = test_service.clip
library = test_service.library
other_clip = test_service.other_clip
restore_pipeline = test_service.restore_pipeline
third_clip = test_service.third_clip
listed = test_service_scanning.listed


# --- the picture a person looks at ---------------------------------------------------------------


async def test_a_cover_is_cut_from_the_frame_and_kept(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Cut on first request rather than when a cover is chosen, so a cover already set is fixed by
    being looked at instead of having to be set again."""
    frame = noisy_frame(400, 300, seed=21)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(0)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    track = (await store.tracks_of(clip.asset.id))[0]

    cut = await service.cover_file(track.id)

    assert cut is not None
    assert cut.is_file()
    # Asked again, the picture already on disk is handed back rather than decoded a second time.
    assert await service.cover_file(track.id) == cut


async def test_a_cover_for_a_face_that_is_not_there_is_nothing(service: FaceService) -> None:
    assert await service.cover_file("no-such-track") is None


async def test_a_cover_for_a_face_whose_file_has_gone_is_nothing(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A row outlives the file it names. There is nothing to cut from, and the caller falls back to
    the recognizer's square, which is what was being shown before."""
    frame = noisy_frame(400, 300, seed=22)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(0)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    track = (await store.tracks_of(clip.asset.id))[0]
    await temp_db.execute("DELETE FROM asset_locations WHERE asset_id = ?", (clip.asset.id,))

    assert await service.cover_file(track.id) is None


async def test_a_decision_naming_no_faces_touches_nothing(
    service: FaceService, temp_db: Database
) -> None:
    """A call naming nothing is refused rather than treated as a call about everything."""
    admin = await create_user(temp_db, Role.ADMIN)

    # Nothing in, nothing out, and crucially not "everything". A track that names no row is
    # refused rather than concealed: there is no vault to unlock that would produce it.
    assert await service.touchable_faces(admin, []) == Actionable(
        allowed=(), concealed=(), refused=()
    )
    assert await service.touchable_faces(admin, ["no-such-track"]) == Actionable(
        allowed=(), concealed=(), refused=("no-such-track",)
    )
    assert await service.set_aside([]) is None


async def test_whether_somebody_may_be_acted_on_at_all(
    service: FaceService, temp_db: Database
) -> None:
    admin = await create_user(temp_db, Role.ADMIN)
    person_id = await make_person(temp_db, "Ada")

    assert await service.may_see_person(admin, person_id) is True
    assert await service.may_see_person(admin, "no-such-person") is False


async def test_how_strong_somebodys_recognition_is(service: FaceService, temp_db: Database) -> None:
    person_id = await make_person(temp_db, "Ada")

    assert (await service.recognition_of(person_id)).references == 0
    assert (await service.recognition_of(person_id)).verdict == "none"


async def test_a_cover_for_an_appearance_with_no_faces_recorded_is_nothing(
    service: FaceService, store: Store, temp_db: Database, clip: Ingested
) -> None:
    """A track exists only where a pass produced one, so this is a database that has been edited,
    and the answer is the same fallback rather than a crash."""
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, created_at)"
        " VALUES ('bare-track', ?, 0, 0, 1, 0.9, 0)",
        (clip.asset.id,),
    )

    assert await service.cover_file("bare-track") is None


async def test_a_cover_the_encoder_could_not_write_is_nothing(
    service: FaceService,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The decoder produced a frame and the encoder produced nothing out of it. Nothing is written
    and the caller falls back, rather than a zero-length file being left where a picture goes."""
    frame = noisy_frame(400, 300, seed=24)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(0)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    track = (await store.tracks_of(clip.asset.id))[0]

    from sift.slices.faces import crop as cropping

    async def wrote_nothing(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(cropping, "encode_portrait", wrote_nothing)

    assert await service.cover_file(track.id) is None
    assert not store.cover_path(track.id).exists()


async def test_raising_the_smallest_pile_worth_showing_keeps_the_small_ones_back(
    service: FaceService,
    store: Store,
    monkeypatch: pytest.MonkeyPatch,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The floor is one, so nothing is held back today, which leaves the code that would hold
    something back unexercised, and a floor nobody can raise is not a setting.

    Raised to two, a pile of one stops being written. That is the behaviour the number describes,
    and it is checked here rather than assumed from the number's name.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    monkeypatch.setattr(tuning, "MIN_PILE_SIZE", 3)

    assert await service.regroup() == 0
    assert await store.piles(PileStatus.OPEN) == []


# --- faces somebody removed -------------------------------------------------------------------


async def test_a_removed_face_is_gone_and_its_picture_with_it(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """No screen brings these back, so the picture has no reader either."""
    await _two_files_of_one_stranger(service, detector, recognizer, clip, clip)
    tracks = await store.tracks_of(clip.asset.id)
    assert tracks
    pictures = [store.resolve(face.crop_path) for face in await store.faces_of(tracks[0].id)]
    assert all(picture.is_file() for picture in pictures)

    removed = await service.remove_faces([tracks[0].id])

    assert removed == 1
    assert await store.tracks_of(clip.asset.id) == []
    assert not any(picture.exists() for picture in pictures)


async def test_a_removed_face_does_not_come_back_on_the_next_scan(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The whole point of it. A face lives in a row a rescan replaces outright, so without
    remembering the removal the same detection is found again and asked about again, forever."""
    await _two_files_of_one_stranger(service, detector, recognizer, clip, clip)
    tracks = await store.tracks_of(clip.asset.id)
    await service.remove_faces([tracks[0].id])

    status = await service.scan(clip.asset.id)

    assert await store.tracks_of(clip.asset.id) == []
    assert status is ScanStatus.NO_FACES


async def test_removing_a_face_from_one_file_leaves_the_same_person_in_another(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Removals are per file on purpose. What somebody said is "this, here, is no use", and a
    removal that applied everywhere would quietly delete a real face from a file nobody mentioned.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    tracks = await store.tracks_of(clip.asset.id)

    await service.remove_faces([tracks[0].id])
    await service.scan(other_clip.asset.id)

    assert len(await store.tracks_of(other_clip.asset.id)) == 1


async def test_what_a_removed_face_measured_is_kept(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The only evidence anybody will have about where the quality bar should sit.

    Every face here got THROUGH the bar, so each one is a case it was wrong about, and the three
    measurements are what say on which of them. A combined score cannot answer that.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, clip)
    tracks = await store.tracks_of(clip.asset.id)

    await service.remove_faces([tracks[0].id])

    measured = await store.removal_measurements()
    assert len(measured) == 1
    quality, pixels, sharpness, frontality = measured[0]
    assert 0 < quality <= 1
    assert pixels and pixels > 0
    assert sharpness is not None
    assert frontality is not None


async def test_a_rescan_keeps_the_faces_that_were_not_removed(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Two people in one file, one of them removed. The other has to survive every later scan.

    A removal that took the whole file's faces with it would be the same fault as one that applied
    library-wide, one step smaller, and it would look like the scan simply finding less.
    """
    frame, boxes = three_people_frame()
    detector.placed = {0: [(box, 0.9) for box in boxes]}
    recognizer.rule = by_shade({210: 0, 170: 2, 130: 4})
    await install_reader(service, Scripted([frame]))
    await service.scan(clip.asset.id)
    tracks = await store.tracks_of(clip.asset.id)
    assert len(tracks) == 3

    await service.remove_faces([tracks[0].id])
    await service.scan(clip.asset.id)

    left = await store.tracks_of(clip.asset.id)
    assert len(left) == 2


async def test_a_scan_of_a_file_with_removals_that_match_nothing_keeps_everything(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A file that has had something removed goes on being scanned normally.

    Matching by likeness means the comparison runs on every later scan of that file, and a face
    that is nothing like what was removed has to come through it untouched. Otherwise one removal
    would quietly cost a file every face it ever finds again.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, clip)
    tracks = await store.tracks_of(clip.asset.id)
    await service.remove_faces([tracks[0].id])

    recognizer.rule = lambda chip: person_vector(9)
    await service.scan(clip.asset.id)

    assert len(await store.tracks_of(clip.asset.id)) == 1


async def test_removing_nothing_removes_nothing(service: FaceService, store: Store) -> None:
    assert await service.remove_faces([]) == 0


async def test_agreeing_to_an_appearance_files_one_reference_however_many_frames_it_holds(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
) -> None:
    """One press, one reference. The number that says how well somebody is covered has to count
    the times a person agreed to something, not the frames a tracker happened to keep.

    An appearance is stored as several frames. Filing all of them would make agreeing to eight
    faces add thirty-odd references, so the bar would move in jumps of four or five and call a
    person comprehensively covered on the strength of eight moments, and on a long video, where one
    appearance can carry thirty frames, a single press would supply almost the whole gallery. It
    would also contradict this feature's own rule elsewhere: the reference audit calls two
    references this alike a near-duplicate that "adds nothing to matching and only slows it down",
    and the frames of one appearance are mostly past that line.

    The appearance is built here rather than scanned, because what is being tested is what happens
    to a track holding SEVERAL faces, and how many frames a fake reader yields is the fixture's
    business rather than this rule's.
    """
    person = await make_person(temp_db, "Ada Lovelace")
    track_id = (
        await store.replace_pass(
            clip.asset.id,
            [
                Appearance(
                    started_ms=0,
                    ended_ms=1000,
                    seen_in=3,
                    quality=0.9,
                    faces=(
                        # Two views of the same instant, and one genuinely different.
                        _described(0, quality=0.5, vector=person_vector(0)),
                        _described(500, quality=0.9, vector=person_vector(0)),
                        _described(1000, quality=0.7, vector=person_vector(3)),
                    ),
                )
            ],
            [[b"\xff\xd8\xff one", b"\xff\xd8\xff two", b"\xff\xd8\xff three"]],
            PassRecord(
                status=ScanStatus.NONE_IDENTIFIED,
                depth="fast",
                coverage=1.0,
                frames_sampled=3,
                detector="test-detector",
                recognizer="test-recognizer",
                settings_digest="abcd1234",
            ),
        )
    )[0]
    assert len(await store.faces_of(track_id)) == 3, "the fixture stopped holding several frames"

    await service.confirm(track_id, person)

    # One, and the clearest of the three. What a gallery is meant to hold is a picture per time
    # somebody was asked and said yes; three frames of one moment is one answer wearing three
    # coats, and every reference is blended into a single description, so the extra two only pull
    # that description toward this instant.
    assert await store.reference_count(person) == 1


async def test_a_poor_crop_is_named_but_never_becomes_a_reference(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
) -> None:
    """Naming a face and learning from it are two decisions, and both are made.

    Every reference a person has is blended into a single averaged description, and that average is
    what a new face is compared against. So a crop of a phone held in front of somebody pulls the
    average toward a phone. Agreeing is still right (it is that person and the file should say so),
    but using it as evidence of what they look like is not.
    """
    person = await make_person(temp_db, "Ada Lovelace")
    track_id = (
        await store.replace_pass(
            clip.asset.id,
            [
                Appearance(
                    started_ms=0,
                    ended_ms=1000,
                    seen_in=2,
                    quality=0.9,
                    faces=(
                        _described(0, quality=0.95, vector=person_vector(0)),
                        _described(500, quality=0.05, vector=person_vector(6)),
                    ),
                )
            ],
            [[b"\xff\xd8\xff good", b"\xff\xd8\xff useless"]],
            PassRecord(
                status=ScanStatus.NONE_IDENTIFIED,
                depth="fast",
                coverage=1.0,
                frames_sampled=2,
                detector="test-detector",
                recognizer="test-recognizer",
                settings_digest="abcd1234",
            ),
        )
    )[0]

    await service.confirm(track_id, person)

    named = await store.track(track_id)
    assert named is not None and named.person_id == person, "the face should still carry the name"
    assert await store.reference_count(person) == 1, "the useless crop was learned from anyway"


# --- the edges of moving faces by hand ------------------------------------------------------------


async def test_moving_no_faces_at_all_is_not_a_move(service: FaceService) -> None:
    """A call naming nothing is not a call about everything. Without this, "move the selection"
    with an empty selection would make a group holding nobody."""
    assert await service.move_faces([], None) is None
    assert await service.move_faces([], "01HX0000000000000000000099") is None


async def test_moving_a_named_face_takes_the_name_off_on_the_way(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A face cannot be listed under somebody and sitting in a group of strangers at the same time.

    The screens offer this over any face, including one already recognized, so the name has to come
    off here rather than the caller being asked to do it in two steps.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)
    person = await make_person(temp_db, "Ada Lovelace")
    await service.confirm(tracks[0].id, person)

    made = await service.move_faces([tracks[0].id], None)

    assert made is not None
    again = await store.track(tracks[0].id)
    assert again is not None
    assert again.person_id is None, "the face was moved into a group of strangers still named"


async def test_a_grouping_is_not_put_back_onto_a_face_that_now_has_a_name(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A hand-made pile is a claim about strangers, and somebody who now has a name is not one.

    Reached the ordinary way: the faces were grouped by hand, then recognized, and the rescan that
    follows must leave them where the name put them rather than dragging them back into the pile.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)
    made = await service.move_faces([tracks[0].id], None)
    assert made is not None
    person = await make_person(temp_db, "Ada Lovelace")
    for track in await store.tracks_of(clip.asset.id):
        await service.confirm(track.id, person)

    put_back = await service._group_again(
        clip.asset.id, [track.id for track in await store.tracks_of(clip.asset.id)]
    )

    assert put_back == set()


async def test_a_face_with_nothing_recorded_behind_it_claims_no_decision(
    service: FaceService, temp_db: Database, clip: Ingested
) -> None:
    """A track whose descriptions have gone, which is what a half-finished pass leaves behind.

    There is nothing to compare, so it claims nothing. A version that fell through to the first
    remembered decision would put somebody else's answer on it.
    """
    from sift.kernel.ids import new_id

    track_id = new_id()
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "created_at) VALUES (?, ?, 0, 0, 1, 0.5, 0)",
        (track_id, clip.asset.id),
    )

    decided = await service._already_decided([track_id], [(("a-pile", b""), person_vector(0))])

    assert decided == []


async def test_only_a_decision_about_this_particular_face_is_put_back(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Remembered decisions are matched by description, and a file usually holds more than one.

    Two claims are made here: a decision about somebody else in the same file is passed over, and
    a face with no matching decision at all comes back with nothing rather than the nearest thing.
    """
    frame = noisy_frame(400, 300, seed=4)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(0)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    track_id = (await store.tracks_of(clip.asset.id))[0].id

    both = await service._already_decided(
        [track_id],
        [(("somebody-else", b""), person_vector(4)), (("this-face", b""), person_vector(0))],
    )
    neither = await service._already_decided(
        [track_id], [(("somebody-else", b""), person_vector(4))]
    )

    assert both == [(track_id, ("this-face", b""))], (
        "a decision about somebody else in the same file was put back on this face"
    )
    assert neither == [], "a face with no matching decision was given the nearest one"


async def test_everybodys_reference_count_is_nothing_while_recognition_is_off(
    service: FaceService,
    store: Store,
    temp_db: Database,
    preferences: FakePreferences,
) -> None:
    """Read by a picker on a screen somebody is already on, so it answers rather than raising,
    and the thresholds still travel with it, or the screen reads its counts against nothing.

    Somebody is given a reference first, so that the empty answer is the feature being off rather
    than there being nothing to count either way.
    """
    person_id = await make_person(temp_db, "Ada Lovelace")
    await store.add_reference(
        person_id,
        vector=person_vector(0),
        quality=0.9,
        crop=b"\xff\xd8\xff picture",
        origin=FaceOrigin.CONFIRMED,
        recognizer="test-recognizer",
        pixels=112,
    )
    assert await store.reference_counts() == {person_id: 1}

    preferences.set("faces.enabled", False)

    found = await service.reference_strengths()

    assert found.people == {}
    assert found.target > found.strong > found.floor > 0


async def test_the_walls_verdict_for_a_person_is_the_one_their_own_page_reads(
    service: FaceService,
    store: Store,
    temp_db: Database,
) -> None:
    """One rule, read from two screens.

    A wall of people that banded the count for itself could say "Identifies them" over a person
    whose page says "well". The wall's answer carries each person's `Strength`, so the token beside
    the count is the property the page reads.
    """
    person_id = await make_person(temp_db, "Ada Lovelace")
    for index in range(tuning.MIN_REFERENCES + 1):
        await store.add_reference(
            person_id,
            vector=person_vector(0),
            quality=1.0,
            crop=f"picture-{index}".encode(),
            origin=FaceOrigin.ADDED,
            recognizer="test-recognizer",
        )

    found = await service.reference_strengths()
    alone = await service.recognition_of(person_id)

    assert found.people[person_id].references == alone.references == tuning.MIN_REFERENCES + 1
    assert found.people[person_id].verdict == alone.verdict == "fair"
    assert (found.target, found.floor, found.strong) == (alone.target, alone.floor, alone.strong)


async def test_a_decision_about_one_face_is_never_put_back_onto_another(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """All three memories, asked about faces they are not about.

    Each of them is keyed on a description and each runs over every face a rescan just found, so a
    file usually offers them faces that other decisions were made about, or none at all. The
    claim is that a decision is put back on the face it was made about and on nothing else; without
    it, one person's answer lands on the next face in the file.
    """
    frame, boxes = three_people_frame()
    detector.placed = {0: [(box, 0.9) for box in boxes]}
    recognizer.rule = by_shade({210: 0, 170: 2, 130: 4})
    await install_reader(service, Scripted([frame]))
    await service.scan(clip.asset.id)
    tracks = [track.id for track in await store.tracks_of(clip.asset.id)]
    assert len(tracks) == 3
    person = await make_person(temp_db, "Ada Lovelace")

    await service.set_aside([tracks[0]])
    await service.confirm(tracks[1], person)
    assert await service.move_faces([tracks[2]], None) is not None

    assert await service._set_aside_again(clip.asset.id, tracks[1:]) == set()
    assert await service._name_again(clip.asset.id, [tracks[0], tracks[2]]) == set()
    assert await service._group_again(clip.asset.id, [tracks[0]]) == set()


async def test_the_work_left_is_counted_in_moments_rather_than_in_files(
    service: FaceService, temp_db: Database, clip: Ingested
) -> None:
    """The number an estimate of the time remaining is built on.

    Two files left says nothing about how long two files take. What a pass spends its time on is
    moments (one seek, one decode, one look), and a moment costs about the same whatever it was
    cut from, which is what makes a rate taken from one stretch of a library worth applying to the
    next. A long file therefore has to weigh more here than a short one, and that is the whole
    point: counting them equally is the estimate that lurches every time the queue reaches a run of
    videos.
    """
    # 300000 rather than 300_000: the underscore is Python's, and this is SQL. SQLite only learned
    # to read it in 3.46, so a separator here parses on some machines and is a syntax error on
    # others, including the one CI runs on.
    await temp_db.execute("UPDATE assets SET duration_ms = 300000 WHERE id = ?", (clip.asset.id,))
    await temp_db.execute(
        "INSERT INTO jobs (id, type, state, priority, payload, max_attempts, attempts, progress, "
        "created_at, updated_at) VALUES ('job-1', 'face_scan', 'queued', 0, ?, 3, 0, 0, 0, 0)",
        (f'{{"asset_id": "{clip.asset.id}"}}',),
    )

    admin = await create_user(temp_db, Role.ADMIN)
    files, moments = await service.work_left(admin)

    assert files == 1
    assert moments == len(face_frames(300_000, density=(await service.configuration()).density))
    assert moments > files


async def test_nothing_queued_is_no_work_rather_than_an_estimate_of_nothing(
    service: FaceService, temp_db: Database
) -> None:
    """Zero has to come back as zero. A moment count taken from an empty sample would be a division
    by nothing, and an estimate built on it is the bar sitting at 'about 0 minutes' forever."""
    admin = await create_user(temp_db, Role.ADMIN)

    assert await service.work_left(admin) == (0, 0)


# --- how many groups are waiting, as the wall reports it -----------------------------------------
#
# What these test is the number the wall itself carries. The property that matters: a count that
# drifts UPWARDS from what is drawn publishes how much somebody was kept out of, which is the thing
# the whole permission model exists to withhold.


async def _open_count(service: FaceService, viewer: Viewer, status: PileStatus) -> int:
    """How many groups the wall reports for this user. Its own helper because these tests are
    about the number rather than the page, and `page_size=0` is what asks for it alone."""
    return (await service.piles(viewer, status, page_size=0))[1]


async def test_the_count_is_the_number_of_groups_the_wall_actually_shows(
    service: FaceService,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The wall's count and the wall's list are one answer: the total is how many groups it
    lists."""
    admin = await create_user(temp_db, Role.ADMIN)
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()

    listed, total = await service.piles(admin, PileStatus.OPEN, page_size=50)
    assert listed, "needs a group for the count to be about anything"

    assert total == len(listed)


async def test_the_count_is_what_THIS_account_may_see_and_not_what_exists(
    service: FaceService,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A guest shown none of the files counts none of the groups, while an admin counts them.

    The two answers have to DISAGREE for this to prove anything. A fixture where everything is
    visible would pass with the permission rules deleted from the statement entirely, which is
    exactly the mistake that would put a stranger's face count on a guest's rail.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    guest = await create_user(temp_db, Role.GUEST)
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()

    seen_by_admin = await _open_count(service, admin, PileStatus.OPEN)
    assert seen_by_admin > 0, "needs a group the admin can see"

    assert await _open_count(service, guest, PileStatus.OPEN) == 0
    assert await _open_count(service, guest, PileStatus.OPEN) != seen_by_admin


async def test_a_group_reaches_the_count_as_soon_as_ONE_of_its_files_is_shared(
    service: FaceService,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A group is a question about a person, not about a file, so any reachable file raises it.

    **Both files are shared, and that is what makes this test able to fail.** The join counted over
    produces one row per visible FACE, and this stranger has a face in each of two files, so
    counting rows rather than DISTINCT groups gives two, and the rail would say two people are
    waiting when there is one. Sharing a single file cannot catch that: one visible face makes the
    two answers agree, and the test passes with the defect fully present.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    guest = await create_user(temp_db, Role.GUEST)
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()

    for shared in (clip, other_clip):
        await temp_db.execute(
            "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect,"
            " created_at) VALUES (?, 'item', ?, ?, 'share', 0)",
            (new_id(), shared.asset.id, guest.id),
        )

    assert await _open_count(service, admin, PileStatus.OPEN) == 1, "one stranger, one group"
    assert await _open_count(service, guest, PileStatus.OPEN) == 1, (
        "one group, however many of its faces the files they can reach carry between them"
    )


async def test_the_wall_is_ordered_by_how_many_faces_are_VISIBLE_not_by_anything_else(
    service: FaceService,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    third_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Largest first, and the order is a disclosure rather than a convenience.

    Ordering by anything other than what this viewer may see puts a mostly-concealed group above one
    they can see all of, and the gap between where a group sits and the number printed on it is then
    a measure of what is being kept back.

    **The fixture has to make the size order disagree with the id order**, or an order by id passes
    this and the property is untested: every group in every other fixture here has exactly one
    face, so a ranking replaced with the id alone would survive them all.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    frame = noisy_frame(400, 300, seed=4)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))

    # One stranger in one file, then a different stranger in two, so the bigger group is the one
    # scanned LAST, and size order and creation order pull opposite ways.
    recognizer.rule = lambda chip: person_vector(0)
    await service.scan(clip.asset.id)
    recognizer.rule = lambda chip: person_vector(1)
    await service.scan(other_clip.asset.id)
    await service.scan(third_clip.asset.id)
    await service.regroup()

    listed, total = await service.piles(admin, PileStatus.OPEN, page_size=50)

    assert total == 2, "two strangers, two groups"
    assert [view.size for view in listed] == [2, 1], "largest first"
    by_id = sorted(view.id for view in listed)
    assert [view.id for view in listed] != by_id, (
        "the fixture no longer discriminates: the size order and the id order agree, so an "
        "implementation that ranked by id alone would pass this test with the property removed"
    )


# --- where a card sits, so an address can carry a place --------------------------------------
#
# Six walls carry `?from=` now, and every one of them resolves it through one of these. The rule
# they all share is the one worth testing: a position is an answer about whether something is
# THERE, so a row this user may not see must have no position at all: the same None a row that
# never existed gets. Anything else and a link becomes a way of asking what is being kept back.


async def test_where_a_pile_sits_is_where_the_page_actually_puts_it(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Taken from the same ranked list the page is taken from, so the two cannot drift apart."""
    admin = await create_user(temp_db, Role.ADMIN)
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    listed, _total = await service.piles(admin, PileStatus.OPEN, page_size=50)
    assert listed, "needs a pile for a position to mean anything"

    for expected, view in enumerate(listed):
        assert await service.position_of_pile(admin, PileStatus.OPEN, view.id) == expected


async def test_a_pile_that_is_not_there_has_no_position(
    service: FaceService, temp_db: Database
) -> None:
    admin = await create_user(temp_db, Role.ADMIN)

    assert await service.position_of_pile(admin, PileStatus.OPEN, "no-such-pile") is None


async def test_where_a_face_sits_inside_its_pile_is_where_that_page_puts_it(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    admin = await create_user(temp_db, Role.ADMIN)
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    found = await service.pile(admin, pile_id)
    assert found is not None
    view, _total = found
    assert len(view.faces) == 2, "needs a second face, or position zero proves nothing"

    for expected, face in enumerate(view.faces):
        assert await service.position_of_face(admin, pile_id, face.track_id) == expected


async def test_a_face_that_is_not_in_that_pile_has_no_position(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Both halves of the refusal: no such pile, and a real pile asked about a face outside it."""
    admin = await create_user(temp_db, Role.ADMIN)
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])

    assert await service.position_of_face(admin, "no-such-pile", "no-such-face") is None
    assert await service.position_of_face(admin, pile_id, "no-such-face") is None


async def test_where_somebody_sits_on_the_identified_wall_is_where_that_page_puts_them(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    admin = await create_user(temp_db, Role.ADMIN)
    person_id = await make_person(temp_db, "Ada Lovelace")
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    tracks = [
        track for asset in (clip, other_clip) for track in await store.tracks_of(asset.asset.id)
    ]
    await store.attribute(
        tracks[0].id, person_id, confidence=1.0, attribution=Attribution.CONFIRMED
    )

    cards, _total = await service.identified_people(admin)
    assert [card.person_id for card in cards] == [person_id]

    assert await service.position_of_identified(admin, person_id) == 0
    assert await service.position_of_identified(admin, "nobody") is None


async def test_where_an_appearance_sits_follows_the_narrowing_the_screen_is_using(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A position taken over everything means nothing on a screen showing only the proposals.

    The settled face sorts ahead of the proposal, so unnarrowed the proposal is at one and narrowed
    it is at zero. Two different numbers for the same face is exactly why the narrowing is passed
    through rather than assumed.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    person_id = await make_person(temp_db, "Ada Lovelace")
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    tracks = [
        track for asset in (clip, other_clip) for track in await store.tracks_of(asset.asset.id)
    ]
    # The proposal first, so that the settled face (attributed after it) leads the screen.
    # Ordered most-recent-first, and if the proposal led, both numbers below would be zero and the
    # test would pass against a lookup that ignores the narrowing entirely.
    await store.attribute(
        tracks[0].id, person_id, confidence=0.5, attribution=Attribution.SUGGESTED
    )
    await store.attribute(
        tracks[1].id, person_id, confidence=1.0, attribution=Attribution.CONFIRMED
    )

    everything = await service.identified_for(admin, person_id, limit=10)
    order = [face.track_id for face in everything.items]
    proposal = tracks[0].id
    assert order.index(proposal) == 1, "the settled face has to lead, or the two numbers agree"

    assert await service.position_of_appearance(admin, person_id, proposal) == 1
    narrowed = await service.position_of_appearance(
        admin, person_id, proposal, attribution=Attribution.SUGGESTED
    )
    assert narrowed == 0
    assert await service.position_of_appearance(admin, person_id, "no-such-face") is None


async def test_a_pile_emptied_between_the_two_reads_is_skipped_rather_than_drawn_blank(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one race the wall can lose, forced rather than waited for.

    The page is built in two reads: which piles this viewer may see any of, then the faces of each.
    Somebody naming the last face of a pile between them leaves a row with nothing to draw, and a
    card with no faces in it is worse than one fewer card. There is no way to make that happen by
    timing, so the second read is made to answer as it would have.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    assert await store.piles(PileStatus.OPEN), "needs a pile to lose"

    async def _emptied(pile_ids: Any, *_args: object, **_kwargs: object) -> dict[str, list[Any]]:
        # Every pile asked about, each holding nothing, which is exactly what the batched read
        # answers when the faces have all been named between the two reads. Keyed off the argument
        # rather than returning a bare empty dictionary, so this stays a stand-in for the real
        # answer rather than a shape the caller happens to survive.
        return {one: [] for one in pile_ids}

    monkeypatch.setattr(store, "tracks_in_piles", _emptied)

    listed, total = await service.piles(admin, PileStatus.OPEN, page_size=50)

    assert listed == [], "a pile with nothing left to show is skipped, not drawn empty"
    assert total == 1, "the count is a beat stale for one request, which is the accepted cost"


# --- two branches the batched settle path left behind --------------------------------------------


async def test_settling_a_batch_that_changed_nothing_tells_the_search_index_nothing(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A settle whose People were already right must not ring the search index.

    The index is told PER FILE and cannot be batched, so a settle that told it about every file in
    the batch regardless would be one re-index per file for a press that moved nobody, and the
    whole point of the batched path is that naming a group across twenty-four files stops costing
    twenty-four of everything.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    both = [clip.asset.id, other_clip.asset.id]

    told: list[str] = []

    async def noting(asset_id: str) -> None:
        told.append(asset_id)

    monkeypatch.setattr(service._reindexer, "touched", noting)

    # Twice. The first settle is what makes the second one a no-op, and without it this would be
    # asserting that a path which never runs tells nobody.
    await service._settle_all(both)
    told.clear()
    await service._settle_all(both)

    assert told == [], "a settle that moved nobody re-indexed the files anyway"


async def test_a_face_with_a_person_and_no_name_cache_asks_who_they_are(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The cache MISS, which the group screens take and the file screen never does.

    `appearances_in` fills the cache for every person on the tracks before drawing any of them, so
    the miss cannot happen there. The two group views pass no cache at all: they draw a pile, and
    a pile is strangers, so the one face in a group that DOES have a person is the only thing
    that takes this path, and nothing else reaches it.

    Asked directly, because arranging a pile that still holds a named face means regrouping around
    one: a state this is not about and would be testing instead.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)
    person_id = await make_person(temp_db, "Ada")

    named = await service._sighting(admin, replace(tracks[0], person_id=person_id), moments={})

    assert named.person_name == "Ada"

    # And handed a cache that has not heard of them, it asks and WRITES THE ANSWER IN, which is
    # what makes it a cache rather than a lookup: a missing key means "not asked yet", so a screen
    # drawing a hundred faces of six people asks six times instead of a hundred.
    cache: dict[str, str | None] = {}
    again = await service._sighting(
        admin, replace(tracks[0], person_id=person_id), moments={}, names=cache
    )
    assert again.person_name == "Ada"
    assert cache == {person_id: "Ada"}
    # And a person this viewer may not be told about is a face with no name rather than an error.
    hidden = await service._sighting(
        admin, replace(tracks[0], person_id="01M0NOSUCHPERSON000000000"), moments={}
    )
    assert hidden.person_name is None


# --- which appearance a reference came from ---------------------------------------------------


async def test_a_confirmed_face_is_marked_as_one_sift_learns_from_after_its_crops_move(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    other_clip: Ingested,
) -> None:
    """The mark that goes out on a rescan, and the repair that brings it back.

    Found by the BYTES of its picture, a reference has a true identity that does not survive a
    rescan: fresh crops re-encode differently, so the join stops finding anything and the only
    reader is a mark on a screen. Unrepaired, a good share of confirmed appearances would say Sift
    learns from nothing, with the pictures sitting right there.

    The drift is staged rather than rescanned for: what a rescan does to this row is exactly what
    is written here (the claim points at nothing and the crop's identity has moved), and driving
    a whole second pass to produce two changed columns would be a slow test of the scan rather than
    a test of the repair.
    """
    person_id = await make_person(temp_db, "Ada Lovelace")
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    track = (await store.tracks_of(clip.asset.id))[0]

    await service.confirm(track.id, person_id)

    assert track.id in await store.reference_tracks([track.id]), (
        "a confirmation files the face, so the appearance that filed it wears the mark"
    )


async def test_the_people_wall_asks_who_everybody_is_in_one_read(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    third_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The People Sift can recognize wall settles every card's name against the viewer in ONE read.

    The question is a permission decision per person, and asked one person at a time seventeen
    cards would be seventeen reads of the people table on every visit to the wall and every board
    card that previews it. `_names_for` asks the repository for the whole batch; a person
    this viewer may not be told about is simply absent from the answer, which is the same
    decision taken once.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.scan(third_clip.asset.id)
    tracks = [
        track
        for asset in (clip, other_clip, third_clip)
        for track in await store.tracks_of(asset.asset.id)
    ]
    cast = ("Anouk Vestergaard", "Bryn Calloway", "Cassia Lynn")
    assert len(tracks) >= 3, "needs a face per person, or one read and many reads are the same"
    people = [await make_person(temp_db, name) for name in cast]
    for track, person_id in zip(tracks, people, strict=False):
        await store.attribute(
            track.id, person_id, confidence=1.0, attribution=Attribution.CONFIRMED
        )

    real = service._repository
    reads: dict[str, int] = {"visible_people": 0, "visible_person": 0}

    class Counted:
        """The real repository, with the two ways of asking about people counted."""

        def __getattr__(self, name: str) -> Any:
            if name in reads:
                reads[name] += 1
            return getattr(real, name)

    monkeypatch.setattr(service, "_repository", Counted())

    cards, total = await service.identified_people(admin)

    assert total == len(cast)
    assert sorted(card.person_name or "" for card in cards) == sorted(cast)
    assert reads == {"visible_people": 1, "visible_person": 0}, (
        f"the wall asked about people {reads} times for {len(cast)} cards"
    )


async def _one_face_named_by_a_scan(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> Any:
    """A file whose one face a scan names on its own; hands back the frame to scan it again with."""
    frame = noisy_frame(400, 300, seed=14)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(0)
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=1.0,
        crop=b"reference-picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    return frame


async def test_a_rescan_that_finds_the_same_face_named_writes_no_second_receipt(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    """The receipt is the naming's, not the scan's: "Sift recognized X here" is not written again
    on every rescan of the same face, and the one receipt still reaches the face the rescan
    found, so its Undo still works."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    frame = await _one_face_named_by_a_scan(service, store, clip, detector, recognizer, person)
    (before,) = await store.tracks_of(clip.asset.id)

    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id, again=True)

    (now,) = await store.tracks_of(clip.asset.id)
    assert now.id != before.id and now.attribution is Attribution.MATCHED
    receipts, total = await written.recent(limit=5, offset=0)
    assert total == 1
    admin = await create_user(temp_db, Role.ADMIN)
    assert await IdentifiedRecords(service).reverse(admin, receipts[0].id, receipts[0].payload)
    back = await store.track(now.id)
    assert back is not None and back.attribution is Attribution.SUGGESTED


async def test_a_name_taken_back_stays_a_question_across_a_rescan(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    """An Undo of Sift's own name asks instead, and a rescan (which replaces the face) would meet a
    face with nothing on it and name it again. The face found again is still the one taken back."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    frame = await _one_face_named_by_a_scan(service, store, clip, detector, recognizer, person)
    (receipt,), _total = await written.recent(limit=5, offset=0)
    admin = await create_user(temp_db, Role.ADMIN)
    assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload)

    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id, again=True)

    (now,) = await store.tracks_of(clip.asset.id)
    assert (now.person_id, now.attribution) == (person, Attribution.SUGGESTED)
    row = await temp_db.fetch_one("SELECT asked_by FROM face_tracks WHERE id = ?", (now.id,))
    assert row is not None and row["asked_by"] == "undone"
    _receipts, total = await written.recent(limit=5, offset=0)
    assert total == 1, "the rescan recorded a name nobody gave"


async def test_a_face_found_again_too_differently_to_pair_keeps_its_one_receipt(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    """A rescan of a video looks at other moments, so the face it finds again can be described too
    differently to pair by description: a clip rescanned at another depth would get another
    "Sift recognized X here" for the same person in the same file each time. The name Sift had
    given pairs it instead, and the one receipt still reaches it."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    frame = await _one_face_named_by_a_scan(service, store, clip, detector, recognizer, person)
    moved = person_vector(0, 4)
    assert recognize.similarity(person_vector(0), moved) < tuning.ALREADY_DECIDED

    recognizer.rule = lambda chip: moved
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id, again=True)

    (now,) = await store.tracks_of(clip.asset.id)
    assert (now.person_id, now.attribution) == (person, Attribution.MATCHED)
    receipts, total = await written.recent(limit=5, offset=0)
    assert total == 1, "the rescan recorded the same name on the same file a second time"
    admin = await create_user(temp_db, Role.ADMIN)
    assert await IdentifiedRecords(service).reverse(admin, receipts[0].id, receipts[0].payload)
    back = await store.track(now.id)
    assert back is not None and back.attribution is Attribution.SUGGESTED


async def test_a_confirmation_found_again_too_differently_is_put_back_where_only_one_face_fits(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    """A video rescan describes the confirmed face too differently to pair by description; with one
    confirmed face of hers lost and one face found again put on her, the confirmation goes there:
    Confirmed, never a fresh "Sift recognized X here". A picture of her from this file whose
    face an earlier pass cut away is handed to the face put back, so it is hers again."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    frame = await _one_face_named_by_a_scan(service, store, clip, detector, recognizer, person)
    (face,) = await store.tracks_of(clip.asset.id)
    await service.confirm(face.id, person)
    orphan = await store.add_reference(
        person,
        vector=person_vector(0, 1),
        quality=1.0,
        crop=b"her-picture-in-this-file",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
        asset_id=clip.asset.id,
    )
    _receipts, before = await written.recent(limit=5, offset=0)

    recognizer.rule = lambda chip: person_vector(0, 4)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id, again=True)

    (now,) = await store.tracks_of(clip.asset.id)
    assert (now.person_id, now.attribution) == (person, Attribution.CONFIRMED)
    _receipts, after = await written.recent(limit=5, offset=0)
    assert after == before, "the rescan recorded a confirmed face as Sift's own match"
    handed = await temp_db.fetch_one("SELECT track_id FROM face_references WHERE id = ?", (orphan,))
    assert handed is not None and handed["track_id"] == now.id


async def test_a_confirmation_is_not_moved_by_the_person_where_two_faces_could_take_it(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """Two faces of hers found again, neither paired by description: which one was confirmed is a
    guess, and a confirmation is never moved on a guess."""
    await _one_face_named_by_a_scan(service, store, clip, detector, recognizer, person)
    (face,) = await store.tracks_of(clip.asset.id)
    await service.confirm(face.id, person)

    # Two faces of hers, each too far from the confirmed one to pair (0.72) and from each other to
    # be one appearance (0.04, under `tuning.TRACKLET_MERGE`), yet each over the line for her
    # name, and a stranger. Told apart by their shade, as `three_people_frame` draws them.
    again, boxes = three_people_frame()
    detector.placed = {0: [(box, 0.9) for box in boxes]}
    variants = {210: person_vector(0, 8), 170: person_vector(0, -8), 130: person_vector(9)}

    def rule(chip: np.ndarray) -> Vector:
        shade = int(round(float(np.percentile(chip, 90)) / 10) * 10)
        return variants[min(variants, key=lambda key: abs(key - shade))]

    recognizer.rule = rule
    await install_reader(service, Scripted([again]))
    await service.scan(clip.asset.id, again=True)

    hers = [one for one in await store.tracks_of(clip.asset.id) if one.person_id == person]
    assert len(hers) == 2
    assert Attribution.CONFIRMED not in {one.attribution for one in hers}


async def test_a_name_taken_back_stays_a_question_when_the_face_is_found_again_differently(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    """The Undo's "do not decide this for me" outlives a rescan that describes the face too
    differently to pair by description: the question it left about that person pairs by the person."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    frame = await _one_face_named_by_a_scan(service, store, clip, detector, recognizer, person)
    (receipt,), _total = await written.recent(limit=5, offset=0)
    admin = await create_user(temp_db, Role.ADMIN)
    assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload)

    recognizer.rule = lambda chip: person_vector(0, 4)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id, again=True)

    (now,) = await store.tracks_of(clip.asset.id)
    assert (now.person_id, now.attribution) == (person, Attribution.SUGGESTED)
    row = await temp_db.fetch_one("SELECT asked_by FROM face_tracks WHERE id = ?", (now.id,))
    assert row is not None and row["asked_by"] == "undone"
    _receipts, total = await written.recent(limit=5, offset=0)
    assert total == 1, "the rescan recorded a name nobody gave"


# --- the People Sift can recognize wall, and what it may show ---------------------------------------


async def test_a_card_whose_newest_face_is_hidden_reads_on_to_one_it_may_show(
    service: FaceService,
    store: Store,
    access: Any,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The card draws her newest faces, and the newest is in the shut vault: it reads on down her
    own list to one it may show rather than drawing fewer than it counts, and the surest figure
    is the surest of what this viewer may see, never the number off a hidden file."""
    admin = await create_user(temp_db, Role.ADMIN)
    person_id = await make_person(temp_db, "Anouk Vestergaard")
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    (hidden,) = await store.tracks_of(clip.asset.id)
    (shown,) = await store.tracks_of(other_clip.asset.id)
    await store.attribute(hidden.id, person_id, confidence=0.95, attribution=Attribution.MATCHED)
    await store.attribute(shown.id, person_id, confidence=0.8, attribution=Attribution.MATCHED)
    # Moved apart by hand, because a clock can step backwards: the hidden one is the newer.
    await temp_db.execute("UPDATE face_tracks SET attributed_at = 20 WHERE id = ?", (hidden.id,))
    await temp_db.execute("UPDATE face_tracks SET attributed_at = 10 WHERE id = ?", (shown.id,))
    assert await access.set_asset_vault(replace(admin, show_hidden=True), clip.asset.id, vault=True)

    [card], total = await service.identified_people(admin, faces_per_card=1)

    assert (total, card.person_id, card.matched) == (1, person_id, 1)
    assert [face.track_id for face in card.faces] == [shown.id]
    assert card.surest == pytest.approx(0.8)


async def test_a_card_whose_count_outlives_the_face_it_counted_draws_none_and_claims_no_confidence(
    service: FaceService,
    store: Store,
    access: Any,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """The wall is counted from stored per-viewer counts and drawn from the files' own answer; where
    the two disagree (a count still holding a face the vault now hides), the card reads to the
    end of her faces and stops, drawing nothing and saying no confidence rather than one taken
    from a file this viewer may not see."""
    admin = await create_user(temp_db, Role.ADMIN)
    await _one_face_recognized_as(service, store, clip, detector, recognizer, person)
    assert await access.set_asset_vault(replace(admin, show_hidden=True), clip.asset.id, vault=True)
    await temp_db.execute(
        "UPDATE viewer_entity_counts SET concealed = 0 WHERE user_id = ? AND kind = ?",
        (admin.id, FACE_BAND_KIND),
    )

    [card], _total = await service.identified_people(admin, faces_per_card=1)

    assert card.person_id == person
    assert card.faces == []
    assert card.surest is None


# --- the boot repairs ------------------------------------------------------------------------------


# --- the switch, over the lists of work -----------------------------------------------------------


async def test_with_the_feature_off_every_list_of_work_is_empty_and_nothing_is_taught(
    service: FaceService, preferences: FakePreferences, temp_db: Database
) -> None:
    """Off is quiet rather than refused for the reads a screen makes: nothing has looked, so there
    is nothing to ask about, and a folder answer that named faces teaches nothing while off."""
    admin = await create_user(temp_db, Role.ADMIN)
    preferences.set(face_settings.ENABLED_KEY, False)

    assert await service.look_alikes(admin) == ([], 0)
    assert await service.filed_faces_that_do_not_match(admin, limit=10, offset=0) == ([], 0)
    assert await service.groups_that_may_be(admin, limit=10, offset=0) == ([], 0)
    assert await service.to_check(admin) == ([], 0, 0)
    assert await service.position_in_to_check(admin, new_id()) is None
    assert await service.proposals_for(admin, [new_id()]) == {}
    assert await service.teach([new_id()], new_id()) == 0
    assert await service.unteach([new_id()], new_id()) == 0


async def test_a_proposal_is_gathered_only_where_the_file_is_shown_and_the_person_may_be_told(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A proposal card is a sentence naming her: a guest is not asked about a face on a file never
    given to them, nor about one on a file they were given whose person they may not be told."""
    admin = await create_user(temp_db, Role.ADMIN)
    guest = await create_user(temp_db, Role.GUEST)
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    person_id = await make_person(temp_db, "Ada Lovelace")
    for asset in (clip, other_clip):
        (track,) = await store.tracks_of(asset.asset.id)
        await store.attribute(
            track.id, person_id, confidence=0.6, attribution=Attribution.SUGGESTED
        )
    await temp_db.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES (?, 'item', ?, ?, 'share', 0)",
        (new_id(), clip.asset.id, guest.id),
    )

    rows, total = await service.look_alikes(admin)
    assert (total, rows[0][0], rows[0][2]) == (1, person_id, 2)
    assert await service.look_alikes(guest) == ([], 0)


async def _two_proposals_for(
    service: FaceService,
    store: Store,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    clip: Ingested,
    other_clip: Ingested,
    person_id: str,
) -> list[str]:
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    faces = [
        track.id for asset in (clip, other_clip) for track in await store.tracks_of(asset.asset.id)
    ]
    for track_id in faces:
        await store.attribute(
            track_id, person_id, confidence=0.72, attribution=Attribution.SUGGESTED
        )
    return faces


async def test_agreeing_with_every_proposal_names_nothing_it_may_not_reach_or_that_was_answered(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing changed, in the same words, for somebody this viewer may not be told about and for
    somebody with no proposal standing; and a press whose faces were all answered between the card
    and the write agrees with none of them."""
    admin = await create_user(temp_db, Role.ADMIN)
    person_id = await make_person(temp_db, "Ada Lovelace")
    other = await make_person(temp_db, "Bryn Calloway")
    withheld = await make_person(temp_db, "Cassia Lynn")
    statement, parameters = hidden_row("person", withheld, admin.id)
    await temp_db.execute(statement, parameters)
    faces = await _two_proposals_for(
        service, store, detector, recognizer, clip, other_clip, person_id
    )

    assert (await service.confirm_look_alikes(admin, withheld)).changed == 0
    assert (await service.confirm_look_alikes(admin, other)).changed == 0

    touchable = service.touchable_faces

    async def answered_meanwhile(viewer: Viewer, track_ids: Any) -> Any:
        allowed = await touchable(viewer, track_ids)
        for track_id in track_ids:
            await store.attribute(
                track_id, other, confidence=1.0, attribution=Attribution.CONFIRMED
            )
        return allowed

    monkeypatch.setattr(service, "touchable_faces", answered_meanwhile)

    run = await service.confirm_look_alikes(admin, person_id)

    assert (run.changed, run.decision_id) == (0, "")
    assert all(one.person_id == other for one in (await store.tracks(faces)).values())


async def test_agreeing_with_every_proposal_where_no_history_is_wired_names_them_all(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    admin = await create_user(temp_db, Role.ADMIN)
    person_id = await make_person(temp_db, "Ada Lovelace")
    faces = await _two_proposals_for(
        service, store, detector, recognizer, clip, other_clip, person_id
    )

    run = await service.confirm_look_alikes(admin, person_id)

    assert (run.changed, run.decision_id) == (2, "")
    assert all(
        one.attribution is Attribution.CONFIRMED for one in (await store.tracks(faces)).values()
    )


async def test_undoing_an_agreement_or_a_no_leaves_a_face_answered_differently_since(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Each undo works from its receipt and never over a newer decision: an agreement's Undo leaves
    a face that is no longer confirmed, and a No's Undo leaves a face somebody has named since."""
    person_id = await make_person(temp_db, "Ada Lovelace")
    other = await make_person(temp_db, "Bryn Calloway")
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    (agreed,) = await store.tracks_of(clip.asset.id)
    (refused,) = await store.tracks_of(other_clip.asset.id)
    await store.attribute(agreed.id, person_id, confidence=0.7, attribution=Attribution.MATCHED)
    await store.attribute(refused.id, other, confidence=1.0, attribution=Attribution.CONFIRMED)

    assert await service.unconfirm_matches(person_id, {agreed.id: 0.7, new_id(): 0.5}) == 0
    assert await service.unreject(person_id, {refused.id: 0.6}, {refused.id: "suggested"}) == 0

    now = await store.tracks([agreed.id, refused.id])
    assert (now[agreed.id].person_id, now[agreed.id].attribution) == (
        person_id,
        Attribution.MATCHED,
    )
    assert (now[refused.id].person_id, now[refused.id].attribution) == (
        other,
        Attribution.CONFIRMED,
    )


async def test_a_scans_own_match_gives_the_whole_file_and_its_undo_takes_it_back(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    """A scan that recognizes somebody with no picture gives them the whole file as a cover, never
    the face, and its receipt names no cover; the Undo makes the face a question again, the file
    leaves them, and its picture with it."""
    written = WorkbenchStore(temp_db)
    service._recorder = written
    cover = "SELECT cover_asset_id, cover_track_id FROM people WHERE id = ?"
    await _one_face_named_by_a_scan(service, store, clip, detector, recognizer, person)
    row = await temp_db.fetch_one(cover, (person,))
    assert row is not None and (row["cover_asset_id"], row["cover_track_id"]) == (
        clip.asset.id,
        None,
    )
    (receipt,), _total = await written.recent(limit=5, offset=0)
    assert "cover" not in json.loads(receipt.payload)
    admin = await create_user(temp_db, Role.ADMIN)

    assert await IdentifiedRecords(service).reverse(admin, receipt.id, receipt.payload)

    row = await temp_db.fetch_one(cover, (person,))
    assert row is not None and (row["cover_asset_id"], row["cover_track_id"]) == (None, None)
