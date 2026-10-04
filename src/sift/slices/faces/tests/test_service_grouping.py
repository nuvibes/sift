# SPDX-License-Identifier: AGPL-3.0-or-later
"""Groups arranged by hand, adding a person, and a file's own list of People."""

from __future__ import annotations

import json

import numpy as np
import pytest

from sift.kernel.access import Role
from sift.kernel.audience import EVERY_ADMIN, Audience
from sift.kernel.changes import About
from sift.kernel.content import Ingested
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.slices.faces import (
    service_matching,
    tuning,
)
from sift.slices.faces.frames import Frame
from sift.slices.faces.models import (
    Appearance,
    Attribution,
    Box,
    Described,
    Detection,
    PileStatus,
    Quality,
    ScanStatus,
)
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.queue import IdentifiedRecords
from sift.slices.faces.service import (
    FaceService,
)
from sift.slices.faces.store import PassRecord, Store
from sift.slices.faces.tests import test_service
from sift.slices.faces.tests.conftest import (
    FakeDetector,
    FakePreferences,
    FakeRecognizer,
    RecordingReindexer,
    draw_face,
    make_person,
    noisy_frame,
    person_vector,
)
from sift.slices.faces.tests.test_service import (
    Scripted,
    _two_files_of_one_stranger,
    _two_files_of_two_strangers,
    install_reader,
)
from sift.slices.workbench.store import Store as WorkbenchStore
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.integration

#: The fixtures this file shares with the files they are defined in, found here by name.
clip = test_service.clip
library = test_service.library
other_clip = test_service.other_clip
restore_pipeline = test_service.restore_pipeline


# --- merging and splitting a group ----------------------------------------------------------------
#
# The grouping deliberately over-splits, so it has two standing mistakes: one pile that is really
# two people, and two piles that are really one. Naming is the wrong tool for either: it creates a
# Person to hold a decision that is not about a name, and two people who happen to share one are
# merged by collision.
#
# Both are guarded against the two things that would otherwise undo anything arranged by hand:
# re-grouping, which deletes every open pile and rebuilds it, and a rescan, which deletes the rows
# the pile points at.


async def test_two_groups_of_one_person_can_be_merged_into_one(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The mistake the tuning makes on purpose, and the answer that is not "invent a Person"."""
    await _two_files_of_two_strangers(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    piles = await store.piles(PileStatus.OPEN)
    assert len(piles) == 2, "two strangers should be two piles"

    keeping, folding = str(piles[0]["id"]), str(piles[1]["id"])
    moving = [track.id for track in await store.pile_tracks(folding)]
    assert await service.move_faces(moving, keeping) == keeping

    left = await store.piles(PileStatus.OPEN)
    assert [str(row["id"]) for row in left] == [keeping], "the emptied pile was not tidied away"
    assert len(await store.pile_tracks(keeping)) == 2
    assert int(left[0]["size"]) == 2, "the count on the pile was left saying what it used to hold"


async def test_a_merge_survives_the_next_regrouping(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Without this the button would be a lie: re-grouping deletes every open pile and rebuilds it.

    The two faces are deliberately far apart in description (that is what made them two piles),
    so a rebuild would separate them again, which is exactly the decision being overridden.
    """
    await _two_files_of_two_strangers(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    piles = await store.piles(PileStatus.OPEN)
    keeping, folding = str(piles[0]["id"]), str(piles[1]["id"])
    await service.move_faces([track.id for track in await store.pile_tracks(folding)], keeping)

    await service.regroup()

    left = await store.piles(PileStatus.OPEN)
    assert [str(row["id"]) for row in left] == [keeping], "the merge was undone by regrouping"
    assert len(await store.pile_tracks(keeping)) == 2


async def test_a_merge_survives_its_files_being_scanned_again(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The harder half, and the one every decision of this shape has to survive.

    A rescan deletes every track a file had. A pile is a row pointing at tracks, so the merge would
    be left holding nothing while the freshly found faces came back among the clustering's own
    piles, undone silently, at a moment nobody is watching.
    """
    await _two_files_of_two_strangers(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    piles = await store.piles(PileStatus.OPEN)
    keeping, folding = str(piles[0]["id"]), str(piles[1]["id"])
    await service.move_faces([track.id for track in await store.pile_tracks(folding)], keeping)

    # Each file describes the same person it did the first time. The fake recognizer answers from
    # one rule for every chip, so the rule has to move with the file being scanned. Otherwise the
    # rescan invents a different person and the test measures that instead.
    recognizer.rule = lambda chip: person_vector(0)
    await service.scan(clip.asset.id)
    recognizer.rule = lambda chip: person_vector(1)
    await service.scan(other_clip.asset.id)

    assert [str(row["id"]) for row in await store.piles(PileStatus.OPEN)] == [keeping]
    assert len(await store.pile_tracks(keeping)) == 2, "the faces did not come back together"


async def test_faces_split_out_of_a_group_stay_out_of_it(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The other direction, which is the same operation with a different destination.

    One pile of two faces that resemble each other closely, so re-grouping would put them back
    together, and a rescan would too. Both have to leave the split alone.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)
    assert len(tracks) == 2

    made = await service.move_faces([tracks[0].id], None)
    assert made is not None and made != pile_id

    await service.regroup()
    await service.scan(clip.asset.id)
    await service.scan(other_clip.asset.id)

    piles = {str(row["id"]) for row in await store.piles(PileStatus.OPEN)}
    assert made in piles, "the group split out was thrown away"
    assert len(await store.pile_tracks(made)) == 1, "the face did not stay on its own"


async def test_moving_a_face_on_forgets_where_it_was_moved_to_before(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The trap every decision of this shape has, in its fourth form.

    A memory keyed on the face's description outlives the row it was written against, which is the
    point of it, and means the old one has to go when the decision changes. Left behind, moving a
    face somewhere else would work until the next scan of its file and then quietly put it back.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)

    split = await service.move_faces([tracks[0].id], None)
    assert split is not None
    moved_back = [track.id for track in await store.pile_tracks(split)]
    assert await service.move_faces(moved_back, pile_id) == pile_id

    await service.scan(clip.asset.id)
    await service.scan(other_clip.asset.id)

    piles = {str(row["id"]) for row in await store.piles(PileStatus.OPEN)}
    assert split not in piles, "the face went back to the group it had been moved out of"
    assert len(await store.pile_tracks(pile_id)) == 2


async def test_a_name_is_matched_to_somebody_who_exists_before_one_is_created(
    service: FaceService, temp_db: Database
) -> None:
    """Pressing "add them" twice must not produce two of somebody. Matched the same way, ignoring
    case, that a pack import matches a folder name."""
    existing = await make_person(temp_db, "Ada Lovelace")

    assert await service.find_or_create_person("ada lovelace", by="who-typed-it") == existing
    made = await service.find_or_create_person("Grace Hopper", by="who-typed-it")
    assert made is not None and made != existing
    assert await service.find_or_create_person("GRACE HOPPER", by="who-typed-it") == made
    assert await service.find_or_create_person("   ", by="who-typed-it") is None


# --- adding a person opens nothing ------------------------------------------------------------------


async def test_adding_a_person_matches_the_library_without_opening_a_file(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    temp_db: Database,
) -> None:
    """The single largest improvement over doing this the usual way.

    Because every face's description was stored when it was found, somebody added afterwards is
    matched by arithmetic. Nothing is decoded, nothing is queued, and the reader is never touched.
    """
    frame = noisy_frame(400, 300, seed=7)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(3)
    reader = Scripted([Frame(pixels=frame, timestamp_ms=0)])
    await install_reader(service, reader)

    await service.scan(clip.asset.id)
    assert reader.opens == 1
    opens_after_scan = reader.opens
    describes_after_scan = recognizer.calls

    later = await make_person(temp_db, "Katherine Johnson")
    await store.add_reference(
        later,
        vector=person_vector(3),
        quality=1.0,
        crop=b"reference-picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )

    attributed = await service.rematch()

    assert attributed == 1
    assert reader.opens == opens_after_scan
    assert recognizer.calls == describes_after_scan
    assert (await store.tracks_of(clip.asset.id))[0].person_id == later


async def test_a_rematch_that_attaches_anybody_says_so_once(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every screen drawing those faces is told, once for the whole pass.

    Ringing only when a file's PEOPLE move answers a different question: a face gaining a name on a
    file that already carries that person would change nothing anybody is told about, and the strip
    under the file would go on saying "Not named yet" until somebody reloaded the page by hand.

    Once rather than per face, because a pass attaches hundreds and the bus coalesces: announcing
    per face would have every open browser re-reading its lists for the length of the pass.
    """
    said: list[tuple[Audience, About]] = []
    monkeypatch.setattr(
        service_matching,
        "announce_now",
        lambda audience, about: said.append((audience, about)),
    )
    frame = noisy_frame(400, 300, seed=11)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(3)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)

    later = await make_person(temp_db, "Marit Halvorsen")
    await store.add_reference(
        later,
        vector=person_vector(3),
        quality=1.0,
        crop=b"reference-picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )

    assert await service.rematch() == 1

    assert said == [(EVERY_ADMIN, About.LIBRARY)]


async def test_a_rematch_that_attaches_anybody_says_how_sure_it_was(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    temp_db: Database,
) -> None:
    """The receipt carries the range, and it is written at the moment of the match.

    Read off `face_tracks` wherever a record is drawn instead, the figure would describe the state
    those appearances are in NOW (one agreed to, another taken back, a third matched to somebody
    else) rather than the run it claims to be about. One face is one figure; the sentence says
    "between" only where the two ends differ.
    """
    written = WorkbenchStore(temp_db)
    service._recorder = written
    frame = noisy_frame(400, 300, seed=11)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(3)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)

    later = await make_person(temp_db, "Marit Halvorsen")
    await store.add_reference(
        later,
        vector=person_vector(3),
        quality=1.0,
        crop=b"reference-picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )

    assert await service.rematch() == 1

    (receipts, _total) = await written.recent(limit=1, offset=0)
    assert receipts[0].title == "Sift recognized 1 more face as Marit Halvorsen, 100% sure"


async def test_a_match_made_during_a_scan_is_written_down_too(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    """A match made while a file is scanned writes an undo record, as a re-match does.

    Without it the History line saying Sift decided has no Undo beside it: most files with a
    matched face would have no way to take the attach back.

    The title is the file's own history sentence, word for word, which is what makes the pane fold
    the two into one line carrying this receipt's Undo. See `history.matched_sentence`: if the two
    ever drift the fold stops and the pane grows a second line saying nearly the same thing.
    """
    written = WorkbenchStore(temp_db)
    service._recorder = written
    frame = noisy_frame(400, 300, seed=13)
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

    receipts, total = await written.recent(limit=5, offset=0)
    assert total == 1
    assert receipts[0].queue == "identified"
    # "named", not "matched": the sentence table uses the word a person would use for what the
    # pass DID: it put a name on a file. `matched_sentence` is the source.
    assert receipts[0].title == "Sift recognized Ada Lovelace here, 100% sure"
    # The verb agrees with the count: one face "was" attached, never "were".
    # And what the match was based on: her reference pictures, counted as they were at the time.
    assert receipts[0].detail.startswith(
        "1 face was attached by comparing it with 1 reference picture of Ada"
    )
    stored = await temp_db.fetch_one(
        "SELECT payload FROM workbench_decisions WHERE id = ?", (receipts[0].id,)
    )
    assert stored is not None and json.loads(stored["payload"])["reference_pictures"] == 1
    # The FILE is a subject, which is what puts the record in that file's history for the fold to
    # find. Without it the pane would draw the plain line again, with no way back on it.
    named = await temp_db.fetch_all(
        "SELECT kind, subject_id FROM workbench_decision_subjects WHERE decision_id = ?"
        " ORDER BY kind",
        (receipts[0].id,),
    )
    assert [(str(row["kind"]), str(row["subject_id"])) for row in named] == [
        ("asset", clip.asset.id),
        ("person", person),
    ]


async def test_the_receipt_a_scan_writes_is_that_files_share_and_nobody_elses(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
    temp_db: Database,
) -> None:
    """THE PER-FILE UNDO, and it is the receipt rather than a door of its own.

    A scan settles one file at a time, so the record it writes is about that one file and the whole
    of it IS that file's share, which means taking it back through the ordinary undo detaches the
    faces on that file and leaves the same person's faces on every other one. That is the answer
    this application already gives the same question: the filename pass writes a decision per file
    it files, the watermark pass takes one filing off the file it was put on. A reverser narrowed by
    a subject would be a second mechanism for a question already answered.
    """
    written = WorkbenchStore(temp_db)
    service._recorder = written
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
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(other_clip.asset.id)

    receipts, total = await written.recent(limit=5, offset=0)
    assert total == 2
    here = {track.id for track in await store.tracks_of(clip.asset.id)}
    for_clip = [one for one in receipts if set(json.loads(one.payload)["track_ids"]) == here]
    assert len(for_clip) == 1
    payload = json.loads(for_clip[0].payload)
    assert payload["person_id"] == person

    admin = await create_user(temp_db, Role.ADMIN)
    taken = await IdentifiedRecords(service).reverse(admin, for_clip[0].id, for_clip[0].payload)

    assert taken is True

    # The name comes off this file's face, which is asked about instead (`FaceService.unmatch`),
    # and the other file's stays exactly as it was.
    assert (await store.tracks_of(clip.asset.id))[0].attribution is Attribution.SUGGESTED
    assert (await store.tracks_of(other_clip.asset.id))[0].attribution is Attribution.MATCHED
    assert (await store.tracks_of(other_clip.asset.id))[0].person_id == person


async def test_a_rematch_that_only_proposes_says_nothing(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    preferences: FakePreferences,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proposal decided nothing, so nothing on any screen moved.

    The pass changed a row (the face now carries a name waiting to be agreed to), and no surface
    drawing that file says anything different: a proposal is not a name on a file. Ringing for it
    would be a page re-read in every open browser for a pass that settled nothing, which is the
    same rule `telling` states one level down.
    """
    said: list[tuple[Audience, About]] = []
    monkeypatch.setattr(
        service_matching,
        "announce_now",
        lambda audience, about: said.append((audience, about)),
    )
    frame = noisy_frame(400, 300, seed=12)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    # Alike enough to be offered, not identical, which is the gap this test lives in.
    recognizer.rule = lambda chip: person_vector(0, variant=2)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)

    later = await make_person(temp_db, "Marit Halvorsen")
    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.99)
    await store.add_reference(
        later,
        vector=person_vector(0),
        quality=1.0,
        crop=b"reference-picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )

    assert await service.rematch() == 1

    assert (await store.tracks_of(clip.asset.id))[0].attribution is Attribution.SUGGESTED
    assert said == []


async def test_a_confirmed_face_becomes_one_of_that_persons_references(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """The flywheel: matching improves from this library's own pictures."""
    frame = noisy_frame(400, 300, seed=9)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(1)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))

    await service.scan(clip.asset.id)
    track = (await store.tracks_of(clip.asset.id))[0]

    await service.confirm(track.id, person)

    references = await store.references(person)
    assert len(references) == 1
    # The quality measured when the face was found, not a flat "somebody said so": it is what an
    # exported pack carries about this face.
    assert 0.0 < references[0].quality < 1.0
    assert (await store.track(track.id)).attribution is Attribution.CONFIRMED  # type: ignore[union-attr]


async def test_a_face_refused_as_her_is_not_offered_as_her_groups_question(
    service: FaceService,
    store: Store,
    temp_db: Database,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    clip: Ingested,
    other_clip: Ingested,
) -> None:
    """Naming a group from its card offers the rest of the group as her questions, except a face
    somebody already answered "not her" about: the No stands."""
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)
    assert len(tracks) == 2, "the fixture is meant to produce one group of two"
    person_id = await make_person(temp_db, "Ada")
    await store.reject(tracks[1].id, person_id)

    named, offered = await service.name_with_their_group([tracks[0].id], person_id)

    assert (named, offered) == (1, 0)
    refused = await store.track(tracks[1].id)
    assert refused is not None and refused.person_id is None


async def test_a_match_below_the_attaching_line_is_offered_rather_than_applied(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    preferences: FakePreferences,
    person: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gap between offering and attaching is where a person's judgement goes."""
    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.99)
    frame = noisy_frame(400, 300, seed=11)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    # Alike enough to be offered, not identical, which is the situation the gap exists for.
    recognizer.rule = lambda chip: person_vector(0, variant=2)
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

    track = (await store.tracks_of(clip.asset.id))[0]
    assert track.attribution is Attribution.SUGGESTED
    assert track.person_id == person
    # And the arithmetic is written down as who asked, never left to be read off the number: a
    # re-match recognizes a question the arithmetic put, and only that kind.
    row = await store.database.fetch_one(
        "SELECT asked_by FROM face_tracks WHERE id = ?", (track.id,)
    )
    assert row is not None and row["asked_by"] == "match"


async def test_a_suggestion_from_a_rematch_does_not_take_a_persons_cover(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    preferences: FakePreferences,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A suggestion is a question, and a cover is what everybody sees.

    Answering the question with somebody's portrait puts a face on a person on the strength of a
    guess nobody has agreed to, and it happens silently, on a screen nobody was looking at.
    """
    frame = noisy_frame(400, 300, seed=7)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    # Alike enough to be offered, not identical, which is the gap this test lives in.
    recognizer.rule = lambda chip: person_vector(0, variant=2)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)

    # Added AFTERWARDS, so it is the re-match that finds them rather than the scan.
    later = await make_person(temp_db, "Katherine Johnson")
    monkeypatch.setattr(tuning, "AUTO_APPLY_CONFIDENCE", 0.99)
    await store.add_reference(
        later,
        vector=person_vector(0),
        quality=1.0,
        crop=b"reference-picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )

    await service.rematch()

    assert (await store.tracks_of(clip.asset.id))[0].attribution is Attribution.SUGGESTED
    row = await temp_db.fetch_one("SELECT cover_track_id FROM people WHERE id = ?", (later,))
    assert row is not None and row["cover_track_id"] is None


async def test_an_appearance_is_matched_by_its_clearest_face_not_its_last_one(
    service: FaceService, store: Store, clip: Ingested, person: str
) -> None:
    """The same choice a re-match makes, made at the same appearance.

    An appearance keeps its best couple of frames, and they are not equally good. Matching on
    whichever happened to be last would mean an appearance attributed when it was found and the
    same appearance attributed differently when the gallery next changed, with nothing anywhere
    to say which answer was which, because both are simply "matched".
    """
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=1.0,
        crop=b"reference-picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )
    # The clearest view is of this person; the later, poorer one is of nobody in the gallery.
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
                        _described(0, quality=0.9, vector=person_vector(0)),
                        _described(1000, quality=0.1, vector=person_vector(4)),
                    ),
                )
            ],
            [[b"\xff\xd8\xff clear", b"\xff\xd8\xff poor"]],
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

    await service._attribute([track_id], await service.configuration())

    attributed = await store.track(track_id)
    assert attributed is not None
    assert attributed.person_id == person

    # And a re-match over the same appearance reaches the same answer, which is the point.
    await store.attribute(track_id, None, confidence=None, attribution=None)
    await service.rematch()
    rematched = await store.track(track_id)
    assert rematched is not None
    assert rematched.person_id == person


def _described(timestamp_ms: int, *, quality: float, vector: tuple[float, ...]) -> Described:
    return Described(
        detection=Detection(
            box=Box(x=10, y=10, width=100, height=100),
            score=0.9,
            landmarks=((1.0, 1.0),) * 5,
            timestamp_ms=timestamp_ms,
        ),
        quality=Quality(pixels=100, sharpness=500.0, frontality=0.9, score=quality, accepted=True),
        vector=vector,
        chip=np.zeros((1, 1, 3), dtype=np.uint8),
    )


# --- the file's own list of People ---------------------------------------------------------------


async def people_on(database: Database, asset_id: str) -> list[str]:
    rows = await database.fetch_all(
        "SELECT person_id FROM asset_people WHERE asset_id = ?", (asset_id,)
    )
    return [str(row["person_id"]) for row in rows]


async def test_confirming_a_face_puts_that_person_on_the_file_and_tells_the_search_box(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    reindexer: RecordingReindexer,
    temp_db: Database,
    person: str,
) -> None:
    """What recognizing a face is FOR.

    A name only this feature could see would be no use to the search box, to the person's own page
    or to the grid. So an attribution writes into the shared table the rest of Sift reads, and
    the index is told, because a file that now says somebody is in it has changed what it matches.
    """
    frame = noisy_frame(400, 300, seed=11)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(1)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    track = (await store.tracks_of(clip.asset.id))[0]
    reindexer.touched_ids.clear()

    await service.confirm(track.id, person)

    assert await people_on(temp_db, clip.asset.id) == [person]
    assert reindexer.touched_ids == [clip.asset.id]


async def test_rejecting_the_only_face_takes_the_person_back_off_the_file(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    reindexer: RecordingReindexer,
    temp_db: Database,
    person: str,
) -> None:
    """A withdrawn attribution has to reach the file, or the name stays there saying something
    nobody believes any more."""
    frame = noisy_frame(400, 300, seed=12)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(1)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    track = (await store.tracks_of(clip.asset.id))[0]
    await service.confirm(track.id, person)
    assert await people_on(temp_db, clip.asset.id) == [person]
    reindexer.touched_ids.clear()

    await service.reject(track.id, person)

    assert await people_on(temp_db, clip.asset.id) == []
    assert reindexer.touched_ids == [clip.asset.id]


async def test_a_file_whose_people_did_not_change_is_not_reindexed(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    reindexer: RecordingReindexer,
) -> None:
    """A re-match sweeps the whole library and settles every file it touched. Telling the index
    about the ones whose list of People came out identical is a write per file for no change."""
    frame = noisy_frame(400, 300, seed=13)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(2)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))

    await service.scan(clip.asset.id)

    assert reindexer.touched_ids == [], "nobody was attributed, so nothing about the file changed"


async def test_deleting_everything_tells_the_index_about_the_files_it_renamed(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    reindexer: RecordingReindexer,
    temp_db: Database,
    person: str,
) -> None:
    """The files that stopped saying somebody is in them are exactly what a search would still
    find otherwise, and they are named in one call rather than one each."""
    frame = noisy_frame(400, 300, seed=14)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(1)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    track = (await store.tracks_of(clip.asset.id))[0]
    await service.confirm(track.id, person)
    reindexer.touched_ids.clear()
    reindexer.bulk_calls.clear()

    await service.forget_everything(actor=Actor.sift("faces"))

    assert await people_on(temp_db, clip.asset.id) == []
    assert reindexer.bulk_calls == [(clip.asset.id,)]
