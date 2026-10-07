# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scanning a file, attributing who is in it, and everything a person can decide about that.

Two of the properties here are the ones the whole review loop rests on and they get named tests:
**a file with several people in it splits into several faces that are decided independently**, and
**a decision somebody made is remembered**. Both are easy to get right on the day and easy to break
later, because neither shows up as an error: they show up as a queue that keeps asking the same
question, or as one person's rejection quietly changing another person's attribution.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest

from sift.kernel import media
from sift.kernel.access import Role
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root, VerdictProduct
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ingress import Origin, verify_ingress
from sift.kernel.ledger import Actor
from sift.slices.faces import frames as frames_module
from sift.slices.faces import (
    recognize,
    service_pictures,
    service_scanning,
    tuning,
)
from sift.slices.faces.frames import Frame
from sift.slices.faces.models import (
    Attribution,
    Box,
    PileStatus,
    ScanStatus,
)
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.service import (
    FacesDisabled,
    FaceService,
    status_of,
)
from sift.slices.faces.store import Store
from sift.slices.faces.tests.conftest import (
    FakeDetector,
    FakePreferences,
    FakeRecognizer,
    draw_face,
    make_person,
    noisy_frame,
    person_vector,
    unit,
)
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


@pytest.fixture
async def library(library_store: LibraryStore, tmp_path: Path) -> Root:
    directory = tmp_path / "library"
    directory.mkdir()
    return await library_store.create_root(name="Clips", abs_path=directory)


@pytest.fixture
async def clip(content_store: ContentStore, library: Root, settings: Settings) -> Ingested:
    """A real file in a real library, so nothing here is testing a stub of the content model."""
    return await _ingest(content_store, library, settings, "clip.mp4", "accepted.mp4")


@pytest.fixture
async def other_clip(content_store: ContentStore, library: Root, settings: Settings) -> Ingested:
    """A second, different file. Two files are what it takes to make a pile: the same face found
    twice in ONE file is one appearance, by design."""
    return await _ingest(content_store, library, settings, "other.webm", "accepted.webm")


@pytest.fixture
async def third_clip(content_store: ContentStore, library: Root, settings: Settings) -> Ingested:
    """A third file, so a group can be BIGGER than another one.

    Two files make two groups of one, which cannot tell an order by size from an order by id.
    """
    return await _ingest(content_store, library, settings, "third.mov", "accepted.mov")


async def _ingest(
    content_store: ContentStore, library: Root, settings: Settings, name: str, source: str
) -> Ingested:
    target = Path(str(library.abs_path)) / name
    target.write_bytes((CORPUS / source).read_bytes())
    checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
    return await content_store.ingest(checked, root_id=library.id, rel_path=name)


class Scripted:
    """Frames a test wrote out, and a count of how many times a file was opened.

    The count is the point of it. One of this feature's headline claims is that adding a person
    re-matches the whole library **without opening anything**, and the only way to check that is to
    ask whether anything was opened.
    """

    def __init__(self, frames: list[Frame]) -> None:
        self.frames = frames
        self.opens = 0

    async def stream(self, path, **_: object):  # type: ignore[no-untyped-def]
        self.opens += 1
        for frame in self.frames:
            yield frame


def three_people_frame() -> tuple[Frame, list[Box]]:
    frame = noisy_frame(720, 300, seed=5)
    boxes = [
        draw_face(frame, x=20, y=80, size=140, shade=210),
        draw_face(frame, x=280, y=80, size=140, shade=170),
        draw_face(frame, x=540, y=80, size=140, shade=130),
    ]
    return Frame(pixels=frame, timestamp_ms=0), boxes


def by_shade(mapping: dict[int, int]) -> Callable[[np.ndarray], list[float]]:
    """Describe a face by which of the drawn shades it is, so a test can say who is who."""

    def rule(chip: np.ndarray) -> list[float]:
        shade = int(round(float(np.percentile(chip, 90)) / 10) * 10)
        nearest = min(mapping, key=lambda key: abs(key - shade))
        return list(person_vector(mapping[nearest]))

    return rule


async def install_reader(service: FaceService, reader: Scripted) -> None:
    """Give the service frames instead of a decoder.

    Everything about reading a file has its own tests; what these are about is what happens to the
    faces afterwards.
    """
    from sift.slices.faces import pipeline as pipeline_module

    original = pipeline_module.Pipeline.run

    async def run(self, path, **kwargs):  # type: ignore[no-untyped-def]
        self._reader = reader
        return await original(self, path, **kwargs)

    pipeline_module.Pipeline.run = run  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def restore_pipeline() -> Iterator[None]:
    from sift.slices.faces import pipeline as pipeline_module

    original = pipeline_module.Pipeline.run
    yield
    pipeline_module.Pipeline.run = original  # type: ignore[method-assign]


# --- the switch -----------------------------------------------------------------------------------


async def test_with_the_feature_off_nothing_runs_nothing_is_written_and_no_model_is_loaded(
    service: FaceService,
    store: Store,
    preferences: FakePreferences,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    temp_db: Database,
) -> None:
    """Off means off, on every path, and it is a consent gate rather than a tidy default.

    Break any one of the guards and one of these raises stops being raised, or a row appears.
    """
    preferences.set("faces.enabled", False)

    assert await service.enabled() is False
    assert await service.ready() is False
    for call in (
        service.scan(clip.asset.id),
        service.rematch(),
        service.regroup(),
        service.confirm("t", "p"),
        service.reject("t", "p"),
        service.ignore("pile"),
        service.restore("pile"),
        service.import_person_folder(Path("/nowhere"), source=None),
        service.import_pack(b""),
        service.export_pack(name="x", version="1", person_ids=[], include_pictures=False),
    ):
        with pytest.raises(FacesDisabled):
            await call

    assert detector.detect_calls == 0
    assert recognizer.calls == 0
    assert await store.scan_of(clip.asset.id) is None
    rows = await temp_db.fetch_all("SELECT id FROM face_tracks", ())
    assert rows == []


# --- the five states, counted by appearance ---------------------------------------------------------


def test_the_five_states_are_counted_by_appearance_not_by_face() -> None:
    """The rule this feature counts by, named.

    Counted the other way (faces seen against distinct people matched), a thirty-frame clip of
    one person records thirty faces and one person, and reads as partly identified for ever. There
    is nothing anybody can do about it from a screen, because nothing is actually unfinished.
    """
    assert status_of(0, 0) is ScanStatus.NO_FACES
    assert status_of(1, 0) is ScanStatus.NONE_IDENTIFIED
    assert status_of(1, 1) is ScanStatus.ALL_IDENTIFIED
    assert status_of(3, 1) is ScanStatus.SOME_IDENTIFIED
    assert status_of(3, 3) is ScanStatus.ALL_IDENTIFIED


async def test_a_thirty_frame_clip_of_one_person_reads_as_fully_identified(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    temp_db: Database,
    person: str,
) -> None:
    """The same rule, end to end.

    One person, thirty sampled moments, one appearance, one person attributed. Anything that counts
    faces instead of appearances makes this read as one of thirty.
    """
    frames = []
    placed = {}
    for index in range(30):
        stamp = index * 100
        frame = noisy_frame(640, 360, seed=index)
        box = draw_face(frame, x=100 + index, y=60, size=150)
        frames.append(Frame(pixels=frame, timestamp_ms=stamp))
        placed[stamp] = [(box, 0.9)]

    detector.placed = placed
    recognizer.rule = lambda chip: person_vector(0)
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=1.0,
        crop=b"reference-picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )
    await install_reader(service, Scripted(frames))

    status = await service.scan(clip.asset.id)

    tracks = await store.tracks_of(clip.asset.id)
    assert len(tracks) == 1
    assert status is ScanStatus.ALL_IDENTIFIED
    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert scan.track_count == 1
    assert scan.identified_count == 1


async def test_a_file_of_faces_too_small_to_read_keeps_the_reason_it_found_nobody(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
) -> None:
    """The pass counts what it refused, and the scan row keeps the count.

    Five moments of somebody far from the camera settle as "no faces" (which is right, nothing
    could be described), and the count of what was refused for size is what lets the file's History
    say that the people were there and too small, rather than that nobody was.
    """
    frames = []
    placed = {}
    for index in range(5):
        stamp = index * 100
        frame = noisy_frame(640, 360, seed=index)
        box = draw_face(frame, x=100 + index, y=60, size=40)
        frames.append(Frame(pixels=frame, timestamp_ms=stamp))
        placed[stamp] = [(box, 0.9)]
    detector.placed = placed
    await install_reader(service, Scripted(frames))

    status = await service.scan(clip.asset.id)

    assert status is ScanStatus.NO_FACES
    scan = await store.scan_of(clip.asset.id)
    assert scan is not None
    assert (scan.refused_small, scan.refused_closer) == (5, 0)


# --- several people in one file, decided independently -----------------------------------------------


async def test_a_file_with_three_people_splits_into_three_faces_decided_separately(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    temp_db: Database,
) -> None:
    """The property the whole review loop rests on.

    Confirm one, set another aside, leave the third alone, and the other two are untouched by
    either decision. Anything that operates on a file rather than on a face breaks this, and it
    breaks silently.
    """
    frame, boxes = three_people_frame()
    detector.placed = {0: [(box, 0.9) for box in boxes]}
    recognizer.rule = by_shade({210: 0, 170: 2, 130: 4})
    await install_reader(service, Scripted([frame]))

    await service.scan(clip.asset.id)
    tracks = await store.tracks_of(clip.asset.id)
    assert len(tracks) == 3

    first, second, third = tracks
    known = await make_person(temp_db, "Grace Hopper")
    await service.confirm(first.id, known)
    await service.regroup()

    piles = await store.piles(PileStatus.OPEN)
    # The two nobody named are still their own faces; whether they piled together depends on how
    # alike they are, and here they are deliberately different people.
    assert all(pile["size"] >= tuning.MIN_PILE_SIZE for pile in piles)

    after = {track.id: track for track in await store.tracks_of(clip.asset.id)}
    assert after[first.id].person_id == known
    assert after[first.id].attribution is Attribution.CONFIRMED
    assert after[second.id].person_id is None
    assert after[third.id].person_id is None


# --- decisions are remembered -------------------------------------------------------------------------


async def test_a_rejected_suggestion_does_not_come_back(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """A queue that re-asks a question somebody answered is a queue nobody reads."""
    frame = noisy_frame(400, 300, seed=2)
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
    track = (await store.tracks_of(clip.asset.id))[0]
    assert track.person_id == person

    await service.reject(track.id, person)
    assert (await store.track(track.id)).person_id is None  # type: ignore[union-attr]

    attributed = await service.rematch()

    assert attributed == 0
    assert (await store.track(track.id)).person_id is None  # type: ignore[union-attr]


async def test_the_batched_pile_read_answers_what_asking_one_at_a_time_answers(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The property the batched read has to have: the same faces, in the same order, per pile.

    It stands in for a read per pile on the page that draws them, so what matters is that it is
    the SAME answer: a second way of deciding which faces are still in a pile would show a card one size
    and its own screen another. Asserted against `pile_tracks` rather than against a list written
    here, so the two cannot both be wrong the same way.

    A pile that was never asked about is absent, and one asked about with nothing left is present
    and empty. That distinction is the whole reason the answer is a dictionary of every asked-for
    pile rather than only the ones with something in them.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    piles = [str(row["id"]) for row in await store.piles(PileStatus.OPEN)]
    assert piles, "needs at least one pile"

    # The best face first, and given qualities that DISAGREE with the order of the ids, or the
    # ordering is proved by the tie-break rather than by the order it claims to be in. The card on
    # the wall draws `faces[0]`, so which one that is decides what somebody sees.
    inside = sorted(await store.pile_tracks(piles[0]), key=lambda one: one.id)
    assert len(inside) >= 2, "needs two faces to have an order at all"
    for rank, track in enumerate(inside):
        await temp_db.execute(
            "UPDATE face_tracks SET quality = ? WHERE id = ?", (0.1 * (rank + 1), track.id)
        )

    batched = await store.tracks_in_piles([*piles, "no-such-pile"])

    for pile_id in piles:
        one_at_a_time = await store.pile_tracks(pile_id)
        assert one_at_a_time, "the pile has to have faces, or the comparison is vacuous"
        assert batched[pile_id] == one_at_a_time
    assert [one.id for one in batched[piles[0]]] == [one.id for one in reversed(inside)], (
        "the best face is first, and that is not the order the ids fall in"
    )
    assert batched["no-such-pile"] == [], "an empty pile is present and empty, not missing"

    # And a face somebody has been attached to has LEFT the pile, the rule the two reads have to
    # share, so a pile whose faces have all been claimed empties instead of going on asking a
    # question that has been answered. Written out because without it this test would pass with
    # the rule deleted from the batched read.
    person = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Someone', 0)", (person,)
    )
    await temp_db.execute(
        "UPDATE face_tracks SET person_id = ? WHERE id = ?", (person, inside[0].id)
    )

    after = await store.tracks_in_piles(piles)
    assert inside[0].id not in {one.id for one in after[piles[0]]}
    assert after[piles[0]] == await store.pile_tracks(piles[0])


async def _two_files_of_one_stranger(
    service: FaceService,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    first: Ingested,
    second: Ingested,
) -> None:
    """The same unknown person, found in two different files. That is a pile of two."""
    frame = noisy_frame(400, 300, seed=4)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(0)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(first.asset.id)
    await service.scan(second.asset.id)


async def test_a_file_with_no_readable_copy_is_a_transient_verdict(
    service: FaceService, store: Store, content_store: ContentStore, clip: Ingested, library: Root
) -> None:
    """A drive unplugged or a share away is written on the file, transiently, rather than failed
    three times: the next scan that sees the file clears it."""
    for one in Path(library.abs_path).rglob("*"):
        if one.is_file():
            one.unlink()

    await service.scan(clip.asset.id)

    verdict = await content_store.verdict_of(clip.asset.id, VerdictProduct.FACES)
    assert verdict is not None and verdict.code == "no_copy" and verdict.transient
    assert await store.tracks_of(clip.asset.id) == []


async def test_a_pass_asks_for_frames_at_the_density_the_settings_say(
    service: FaceService, tmp_path: Path
) -> None:
    """What a Build reads for this feature is what a pass of its own would have read."""
    facts = media.FileFacts(
        asset_id="a",
        path=tmp_path / "clip.mp4",
        media_type="video",
        duration_ms=4000,
        width=320,
        height=240,
        fps=10.0,
        size_bytes=1,
    )
    configured = await service.configuration()

    requests = await service.frame_requests(facts)

    assert requests == frames_module.frame_requests(facts, density=configured.density)
    assert requests, "a four-second video is read at least once"


async def test_the_lines_a_match_is_judged_against_are_the_measured_ones(
    service: FaceService, preferences: FakePreferences
) -> None:
    """A row an older version stored for a removed line changes nothing: the lines are fixed."""
    preferences.set("faces.match_confidence", 90)
    preferences.set("faces.attach_confidence", 100)
    preferences.set("faces.reference_groups", 4)

    configured = await service.configuration()

    assert configured.suggest_above == tuning.SUGGEST_CONFIDENCE
    assert configured.attach_above == tuning.AUTO_APPLY_CONFIDENCE
    assert configured.groups == tuning.MATCH_GROUPS


async def test_what_needs_scanning_among_files_is_what_no_pass_has_covered(
    service: FaceService,
    preferences: FakePreferences,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """And nothing at all while the feature is off: nothing is going to be looked at."""
    ids = [clip.asset.id, other_clip.asset.id]
    assert await service.needs_scanning_among(ids) == set(ids)

    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    assert await service.needs_scanning_among(ids) == set()

    preferences.set("faces.enabled", False)
    assert await service.needs_scanning_among([new_id()]) == set()


async def test_grouping_from_scratch_with_nobody_unnamed_leaves_no_piles(
    service: FaceService,
) -> None:
    assert await service.regroup(full=True) == 0


async def test_measuring_again_forgets_the_pictures_that_have_gone(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    settings: Settings,
) -> None:
    """Measured again from the squares Sift kept, and a square that is no longer on the disk
    cannot be measured. With nobody named there is no reference to measure; with the pictures
    gone the faces are dropped and the reference forgotten rather than carried as noise."""
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    recognizer.revision = "test-recognizer-2"

    done = await service.remeasure()
    assert (done.files, done.references, done.remaining) == (2, 0, 0)

    person_id = await make_person(temp_db, "Ada Lovelace")
    named = (await store.tracks_of(clip.asset.id))[0].id
    await service.confirm(named, person_id)
    assert await service.references_without_pictures() == 0
    shutil.rmtree(settings.data_dir / "faces")
    recognizer.revision = "test-recognizer-3"

    done = await service.remeasure()

    assert (done.files, done.references, done.remaining) == (2, 0, 0)
    assert await store.faces_of(named) == []
    assert await service.references_without_pictures() == 0, "the reference was forgotten"


async def test_a_person_this_viewer_may_not_be_told_about_has_no_page_of_faces(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The page is filtered off the sighting rather than off the stored row: a person whose name
    is withheld from this viewer comes back with no id at all, and that is what keeps a page from
    being served for somebody the viewer may not be told exists, even with the files shared."""
    admin = await create_user(temp_db, Role.ADMIN)
    guest = await create_user(temp_db, Role.GUEST)
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    person_id = await make_person(temp_db, "Ada Lovelace")
    for asset in (clip, other_clip):
        (track,) = await store.tracks_of(asset.asset.id)
        await store.attribute(
            track.id, person_id, confidence=0.5, attribution=Attribution.SUGGESTED
        )
        await temp_db.execute(
            "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect,"
            " created_at) VALUES (?, 'item', ?, ?, 'share', 0)",
            (new_id(), asset.asset.id, guest.id),
        )

    told = await service.identified_for(admin, person_id, limit=10)
    withheld = await service.identified_for(guest, person_id, limit=10)

    assert told.total == 2, "the admin is told, so the shut case below is evidence"
    assert withheld.total == 0


async def test_a_pile_set_aside_is_listed_and_can_be_brought_back(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Setting a pile aside must not be a trapdoor: it stays visible and it is reversible."""
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)

    await service.regroup()
    piles = await store.piles(PileStatus.OPEN)
    assert piles

    pile_id = str(piles[0]["id"])
    assert await service.ignore(pile_id) is not None

    dismissed = await store.piles(PileStatus.IGNORED)
    assert [str(row["id"]) for row in dismissed] == [pile_id]
    assert await store.pile_tracks(pile_id)

    assert await service.restore(pile_id) is True
    assert [str(row["id"]) for row in await store.piles(PileStatus.OPEN)] == [pile_id]


async def test_the_ordinary_grouping_places_new_faces_into_the_piles_that_exist_and_keeps_them(
    service: FaceService,
    store: Store,
    content_store: ContentStore,
    library: Root,
    settings: Settings,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A batch does not re-cluster the whole library and mint every pile afresh: the pile a screen
    is showing keeps its identity, a third file of the same stranger joins it, and a new stranger
    makes a pile of their own beside it."""
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    (pile,) = [str(row["id"]) for row in await store.piles(PileStatus.OPEN)]

    # Two more files with bytes of their own: a copy of a file already in the library IS that
    # file to the store, and a scan of it would replace the appearance the pile already holds.
    third = Path(str(library.abs_path)) / "third.mov"
    third.write_bytes((CORPUS / "accepted.mov").read_bytes())
    checked = verify_ingress(third, origin=Origin.SCAN, settings=settings)
    same = await content_store.ingest(checked, root_id=library.id, rel_path="third.mov")
    assert same.asset.id not in (clip.asset.id, other_clip.asset.id)
    await service.scan(same.asset.id)
    fourth = Path(str(library.abs_path)) / "fourth.mkv"
    fourth.write_bytes((CORPUS / "accepted.mkv").read_bytes())
    checked = verify_ingress(fourth, origin=Origin.SCAN, settings=settings)
    stranger = await content_store.ingest(checked, root_id=library.id, rel_path="fourth.mkv")
    assert stranger.asset.id not in (clip.asset.id, other_clip.asset.id, same.asset.id)
    recognizer.rule = lambda chip: person_vector(3)
    await service.scan(stranger.asset.id)

    touched = await service.regroup()

    open_piles = {str(row["id"]): int(row["size"]) for row in await store.piles(PileStatus.OPEN)}
    assert pile in open_piles, "the pile on the screen is still the pile on the screen"
    assert open_piles[pile] == 3
    assert len(open_piles) == 2
    assert touched == 2
    joined = await store.pile_of(pile)
    assert joined is not None and int(joined["size"]) == 3


async def test_the_ordinary_grouping_places_a_face_by_the_piles_middle_not_by_grouping_from_scratch(
    service: FaceService,
    store: Store,
    content_store: ContentStore,
    library: Root,
    settings: Settings,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The difference between the two ways, made visible. A face like a pile's middle but not
    quite like any one face in it joins the pile when placed; grouped from scratch it would be a
    pile of its own, because from scratch only pairs above the bar are joined."""
    frame = noisy_frame(400, 300, seed=4)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    # Two faces 0.6 alike, whose middle points straight along the first axis.
    recognizer.rule = lambda chip: unit([1.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    await service.scan(clip.asset.id)
    recognizer.rule = lambda chip: unit([1.0, -0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    await service.scan(other_clip.asset.id)
    await service.regroup()
    (pile,) = [str(row["id"]) for row in await store.piles(PileStatus.OPEN)]

    third = Path(str(library.abs_path)) / "third.mov"
    third.write_bytes((CORPUS / "accepted.mov").read_bytes())
    checked = verify_ingress(third, origin=Origin.SCAN, settings=settings)
    newcomer = await content_store.ingest(checked, root_id=library.id, rel_path="third.mov")
    # 0.52 like the middle, 0.47 like either face: above the bar for the one, below for the other.
    recognizer.rule = lambda chip: unit([0.52, 0.0, 0.854, 0.0, 0.0, 0.0, 0.0, 0.0])
    await service.scan(newcomer.asset.id)

    await service.regroup()

    assert {str(row["id"]): int(row["size"]) for row in await store.piles(PileStatus.OPEN)} == {
        pile: 3
    }


async def test_a_full_regrouping_keeps_each_piles_identity_where_it_can(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    await _two_files_of_two_strangers(service, detector, recognizer, clip, other_clip)
    await service.regroup(full=True)
    before = {str(row["id"]) for row in await store.piles(PileStatus.OPEN)}
    assert len(before) == 2

    await service.regroup(full=True)

    assert {str(row["id"]) for row in await store.piles(PileStatus.OPEN)} == before


async def test_regrouping_leaves_a_pile_somebody_set_aside_alone(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Otherwise every re-group would resurrect what somebody deliberately put away."""
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)

    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    await service.ignore(pile_id)

    await service.regroup()

    assert [str(row["id"]) for row in await store.piles(PileStatus.IGNORED)] == [pile_id]


# --- answering for part of a pile -----------------------------------------------------------------
#
# The grouping is deliberately set to split rather than merge, so a pile is usually one person and
# occasionally one person plus a stranger. An answer only about all of it would be, for the case
# the tuning is built around, exactly the wrong shape.


async def test_a_piles_own_page_shows_every_face_in_it(
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
    view, total = found
    assert total == 2
    assert len(view.faces) == 2


async def test_a_pile_that_does_not_exist_has_no_page(
    service: FaceService, temp_db: Database
) -> None:
    admin = await create_user(temp_db, Role.ADMIN)

    assert await service.pile(admin, "no-such-pile") is None


async def test_naming_part_of_a_pile_leaves_the_rest_of_it_a_pile(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The whole point of the screen. All-or-nothing is the wrong shape for a grouping that is
    tuned to occasionally put a stranger in."""
    admin = await create_user(temp_db, Role.ADMIN)
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)
    person_id = await make_person(temp_db, "Ada")

    assert await service.confirm_many([tracks[0].id], person_id) == 1

    left = await service.pile(admin, pile_id)
    assert left is not None
    view, total = left
    assert total == 1, "the face that was named has left the pile; the other one has not"
    assert view.faces[0].track_id == tracks[1].id


async def test_naming_one_face_offers_the_name_to_the_rest_of_its_group(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Naming a face from the file it is in answers the question the whole GROUP was asking.

    A group is already a claim that these faces are one person: the clustering makes it at import
    and it deliberately over-splits, so a group is high-precision rather than complete. Naming one
    face out of twenty and leaving nineteen in the pile ignores work that has already been done.

    The two halves are deliberately not the same strength, and that is the whole design. What was
    NAMED is confirmed: somebody looked at that crop and said who it was, which is the only evidence
    in this feature that comes from a person. The rest are SUGGESTED, because nobody has looked at
    them, so they land on that person's own wall to be agreed with, exactly as a match below the
    attach line does.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)
    assert len(tracks) == 2, "the fixture is meant to produce one group of two"
    person_id = await make_person(temp_db, "Ada")

    named, offered = await service.name_with_their_group([tracks[0].id], person_id)

    assert (named, offered) == (1, 1)
    decided = await store.track(tracks[0].id)
    followed = await store.track(tracks[1].id)
    assert decided is not None and followed is not None
    assert decided.attribution is Attribution.CONFIRMED, "the one somebody named"
    assert followed.person_id == person_id
    assert followed.attribution is Attribution.SUGGESTED, "the one nobody has looked at"
    assert followed.confidence is None, (
        "a confidence is a match score, and this did not come from a match"
    )


async def test_a_naming_that_writes_nothing_offers_nothing(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A face that already carries somebody is skipped, and the group must not follow anyway.

    Offering a name to twenty faces off the back of a call that wrote nothing would attach a person
    to a whole group on the strength of a press that did nothing at all.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)
    person_id = await make_person(temp_db, "Ada")
    someone_else = await make_person(temp_db, "Bea")
    await service.confirm_many([tracks[0].id], person_id)

    named, offered = await service.name_with_their_group([tracks[0].id], someone_else)

    assert (named, offered) == (0, 0)
    unchanged = await store.track(tracks[1].id)
    assert unchanged is not None
    assert unchanged.person_id is None


async def test_a_face_id_that_names_nothing_is_stepped_over_rather_than_followed(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A track id that resolves to nothing contributes no GROUP, and the real ones still do.

    The call gathers the piles of everything it was handed before it names anything, and a screen
    can hand it an id that has just stopped existing: a face removed in another tab, a pile swept
    while the bar was open. Stepping over it is what keeps that from being an error; following it
    would be reading `pile_id` off nothing.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)
    person_id = await make_person(temp_db, "Ada")

    named, offered = await service.name_with_their_group(
        [tracks[0].id, "01HX0000000000000000000099"], person_id
    )

    assert (named, offered) == (1, 1), "the real one was named and its group still followed"


async def test_a_pile_whose_faces_have_all_been_named_stops_being_a_pile(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A question that has been answered. Left behind, it is a count that never comes down."""
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)
    person_id = await make_person(temp_db, "Ada")

    await service.confirm_many([track.id for track in tracks], person_id)

    assert await store.piles(PileStatus.OPEN) == []


async def test_naming_a_face_that_is_already_somebody_changes_nothing(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """So that pressing it twice, or two screens racing, does not write a second set of references
    for the same face under a second person."""
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)
    first = await make_person(temp_db, "Ada")
    second = await make_person(temp_db, "Grace")

    assert await service.confirm_many([tracks[0].id], first) == 1
    assert await service.confirm_many([tracks[0].id], second) == 0

    track = await store.track(tracks[0].id)
    assert track is not None
    assert track.person_id == first


async def test_setting_part_of_a_pile_aside_moves_only_that_part(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Split into a pile of its own rather than flagged face by face, so that everything setting a
    WHOLE pile aside already does (listed, reversible, kept out of regrouping) applies without a
    second mechanism to keep in step."""
    admin = await create_user(temp_db, Role.ADMIN)
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)

    moved = await service.set_aside([tracks[0].id])

    assert moved is not None
    assert [str(row["id"]) for row in await store.piles(PileStatus.IGNORED)] == [moved]
    left = await service.pile(admin, pile_id)
    assert left is not None
    assert left[1] == 1, "the face left behind is still a pile of its own"


async def test_faces_set_aside_stay_out_of_the_next_regrouping(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Otherwise every regrouping resurrects what somebody deliberately put away, which is the
    same promise a whole pile set aside already makes, and it has to hold for part of one."""
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)
    moved = await service.set_aside([tracks[0].id])

    await service.regroup()

    assert [str(row["id"]) for row in await store.piles(PileStatus.IGNORED)] == [moved]
    assert [track.id for track in await store.pile_tracks(str(moved))] == [tracks[0].id]


async def test_a_piles_count_comes_down_when_one_of_its_faces_goes(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A stored count beside the thing it counts, with nothing keeping the two in step.

    Written only when a pile is made, `size` would still claim five after one face of five is
    removed. Nothing on the group screens would show it, because they recount per viewer and never
    read the column, which is exactly how it could drift that far unnoticed, and why the next
    thing to read it would believe it.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    tracks = await store.pile_tracks(pile_id)
    assert len(tracks) == 2

    await service.remove_faces([tracks[0].id])

    rows = await store.piles(PileStatus.OPEN)
    assert [int(row["size"]) for row in rows] == [1]


async def test_agreeing_with_a_suggestion_settles_it_and_makes_it_a_reference(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Agreeing to a face Sift proposed somebody for.

    Agreeing has to do more than flip the label. A confirmation is what makes the face one of that
    person's reference photos, and references are what every later match is measured against, so
    an agreement that only changed a word would leave matching exactly as unsure as it was.
    """
    person_id = await make_person(temp_db, "Ada Lovelace")
    frame = noisy_frame(400, 300, seed=7)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(0)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    track = (await store.tracks_of(clip.asset.id))[0]
    await store.attribute(track.id, person_id, confidence=0.5, attribution=Attribution.SUGGESTED)
    before = await store.reference_count(person_id)

    agreed = await service.accept_suggestions([track.id])

    assert agreed == 1
    settled = await store.track(track.id)
    assert settled is not None
    assert settled.attribution is Attribution.CONFIRMED
    assert settled.person_id == person_id
    assert await store.reference_count(person_id) > before, (
        "agreeing changed the label and did not improve what future matching is measured against"
    )


async def test_refusing_every_proposal_for_somebody_settles_all_of_them_in_one_press(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The no behind the card's Yes, and it writes rather than opening a screen.

    Both answers on that card are one press: the yes writes against exactly the same faces, from
    the same press, on a card showing the whole question, so the no needs no screen of its own.

    What a refusal must do is more than take the name off: it is REMEMBERED, so the next scan of
    that file does not put the same proposal back. That is the property worth pinning here, because
    the label flipping is the visible half and the memory is the half that makes it stick.

    A face already CONFIRMED is left alone. This refuses proposals, and somebody's own answer is
    not one.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    person_id = await make_person(temp_db, "Ada Lovelace")
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    tracks = [
        track for asset in (clip, other_clip) for track in await store.tracks_of(asset.asset.id)
    ]
    await store.attribute(
        tracks[0].id, person_id, confidence=0.5, attribution=Attribution.SUGGESTED
    )
    await store.attribute(
        tracks[1].id, person_id, confidence=1.0, attribution=Attribution.CONFIRMED
    )

    refused = await service.reject_look_alikes(admin, person_id)

    assert refused.changed == 1
    taken_off = await store.track(tracks[0].id)
    assert taken_off is not None
    assert taken_off.person_id is None, "the proposal was refused, so the name comes off"
    assert (await store.rejections()).get(tracks[0].id) == {person_id}, (
        "a refusal that is not remembered comes back at the next scan of the file"
    )
    kept = await store.track(tracks[1].id)
    assert kept is not None
    assert kept.person_id == person_id, "somebody's own answer is not a proposal"


async def test_refusing_the_proposals_of_a_person_this_account_may_not_be_told_about_writes_nothing(
    service: FaceService,
    temp_db: Database,
) -> None:
    """Nothing changed, rather than a refusal: the same answer agreeing gives.

    A 404 here would say whether somebody exists, which is precisely what withholding a person is
    for.
    """
    admin = await create_user(temp_db, Role.ADMIN)

    assert (await service.reject_look_alikes(admin, "01M0NOSUCHPERSON000000000")).changed == 0


async def test_the_proposals_for_somebody_are_narrowed_before_the_page_is_taken(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Asking for a page of everything and keeping the proposals reads the WRONG page.

    Somebody with more settled faces than a page holds has their proposals past the end of it, so
    the screen would draw nothing under a heading counting them: two dozen proposals, none in the
    first hundred rows, and a page that asked for a hundred. The narrowing
    has to happen where the page is taken, not after it arrives.

    A page of one, with the settled face ahead of the proposal, is that in miniature.
    """
    admin = await create_user(temp_db, Role.ADMIN)
    person_id = await make_person(temp_db, "Ada Lovelace")
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    tracks = [
        track for asset in (clip, other_clip) for track in await store.tracks_of(asset.asset.id)
    ]
    assert len(tracks) == 2
    await store.attribute(
        tracks[0].id, person_id, confidence=0.5, attribution=Attribution.SUGGESTED
    )
    await store.attribute(
        tracks[1].id, person_id, confidence=1.0, attribution=Attribution.CONFIRMED
    )

    waiting = await service.identified_for(
        admin, person_id, limit=1, attribution=Attribution.SUGGESTED
    )

    assert waiting.total == 1, (
        "the count describes the proposals, not everything decided about them"
    )
    assert [face.track_id for face in waiting.items] == [tracks[0].id]
    assert all(face.attribution is Attribution.SUGGESTED for face in waiting.items)

    # The other two tabs are counted on the SAME answer, which is what stops the row filling in one
    # tab at a time as somebody presses them.
    assert (waiting.waiting, waiting.confirmed, waiting.matched) == (1, 1, 0)

    everything = await service.identified_for(admin, person_id, limit=10)
    assert everything.total == 2, "unnarrowed, the page is still everything decided about them"
    assert len(everything.items) == 2
    # Counted over everything, whatever the page narrows to: a page of none still says one waits.
    assert (everything.waiting, everything.confirmed, everything.matched) == (1, 1, 0)
    matched = await service.identified_for(
        admin, person_id, limit=1, attribution=Attribution.MATCHED
    )
    assert (matched.total, matched.waiting, matched.confirmed) == (0, 1, 1)


async def test_deleting_somebody_puts_their_faces_back_among_the_questions(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Cut loose is not the same as back in the pile, and the key alone only cuts loose.

    The key clears `person_id` when a person is deleted and leaves the two columns beside it
    describing a decision about nobody. Worse, a named face is in no pile (naming took it out of
    one), so cutting it loose puts it nowhere: neither identified nor waiting, on no screen at all,
    until somebody happened to press "Group them again".
    """
    person_id = await make_person(temp_db, "Ada Lovelace")
    frame = noisy_frame(400, 300, seed=11)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(0)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    track = (await store.tracks_of(clip.asset.id))[0]
    await service.confirm(track.id, person_id)
    assert (await store.track(track.id)).person_id == person_id  # type: ignore[union-attr]

    # What deleting a person does to the face tables, and nothing more: the key, on its own.
    await temp_db.execute("DELETE FROM people WHERE id = ?", (person_id,))
    stranded = await store.track(track.id)
    assert stranded is not None
    assert stranded.person_id is None
    assert stranded.attribution is not None, "the fixture no longer reproduces the stranded state"

    released = await service.release_deleted()

    assert released == 1
    settled = await store.track(track.id)
    assert settled is not None
    assert settled.attribution is None, "a decision about nobody was left on the face"
    assert settled.confidence is None
    # And it is a question again rather than sitting in a state no screen shows.
    piles = await store.piles(PileStatus.OPEN)
    assert [t.id for pile in piles for t in await store.pile_tracks(str(pile["id"]))] == [track.id]


async def test_agreeing_over_a_selection_skips_what_was_already_settled(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """A selection dragged across a page picks up faces that were already decided.

    Refusing the whole call over one of those would make the obvious gesture on the screen fail for
    a reason nobody could see. Agreeing with a decision already made is not an error.
    """
    person_id = await make_person(temp_db, "Ada Lovelace")
    frame = noisy_frame(400, 300, seed=8)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(0)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    track = (await store.tracks_of(clip.asset.id))[0]
    await store.attribute(track.id, person_id, confidence=1.0, attribution=Attribution.CONFIRMED)

    assert await service.accept_suggestions([track.id, "nothing-by-that-name"]) == 0


async def _one_face_recognized_as(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person_id: str,
) -> str:
    """One face in `clip`, recognized as `person_id` by a reference, so that a rescan with no
    memory of a "no" would put the name straight back. Returns the appearance."""
    frame = noisy_frame(400, 300, seed=2)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(0)
    await store.add_reference(
        person_id,
        vector=person_vector(0),
        quality=1.0,
        crop=b"reference-picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    track = (await store.tracks_of(clip.asset.id))[0]
    assert track.person_id == person_id, "the reference has to recognize the face to begin with"
    return track.id


async def test_a_no_stays_a_no_when_its_file_is_scanned_again(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """The fifth decision of the shape the other four have.

    A refusal is keyed by the appearance and a rescan deletes every appearance a file has, so
    without a memory each "not her" would be wiped the next time the file was looked at, and here,
    with a reference that recognizes the face, her name would go straight back on. The refusal has to be on the FRESH face,
    and the moment it carries has to be the moment it was said, or a person's history counts the
    old "no" again on the day of the rescan.
    """
    refused = await _one_face_recognized_as(service, store, clip, detector, recognizer, person)
    await service.reject(refused, person)
    said = await temp_db.fetch_one("SELECT created_at FROM face_rejections", ())
    assert said is not None

    await service.scan(clip.asset.id)

    fresh = (await store.tracks_of(clip.asset.id))[0]
    assert fresh.id != refused, "the rescan made a new appearance, which is the whole difficulty"
    assert fresh.person_id is None, "the person the face was refused as was put back on it"
    assert (await store.rejections()).get(fresh.id) == {person}
    carried = await temp_db.fetch_one(
        "SELECT created_at FROM face_rejections WHERE track_id = ?", (fresh.id,)
    )
    assert carried is not None and carried["created_at"] == said["created_at"]
    assert await service.rematch() == 0, "and a re-match does not propose them either"


async def test_a_face_refused_as_two_people_is_refused_as_both_after_a_rescan(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """Refusals add up, where a name or a pile is one place. Matched across every refusal together,
    the rule gives each fresh face to the FIRST remembered decision it agrees with, and the
    other "no" is lost."""
    other = await make_person(temp_db, "Nadia Vance")
    refused = await _one_face_recognized_as(service, store, clip, detector, recognizer, person)
    await service.reject(refused, person)
    await service.reject(refused, other)

    await service.scan(clip.asset.id)

    fresh = (await store.tracks_of(clip.asset.id))[0]
    assert (await store.rejections()).get(fresh.id) == {person, other}


async def test_a_no_that_was_undone_does_not_come_back_at_the_next_scan(
    service: FaceService,
    store: Store,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """Undo takes the memory off with the refusal. Left behind, the "no" an undo removed would be
    put back by the next scan of the file: the trap a name taken off has, from the other side."""
    refused = await _one_face_recognized_as(service, store, clip, detector, recognizer, person)
    await service.reject(refused, person)
    await store.forget_rejection(refused, person)

    await service.scan(clip.asset.id)

    fresh = (await store.tracks_of(clip.asset.id))[0]
    assert fresh.person_id == person
    assert fresh.id not in await store.rejections()


async def test_a_remembered_no_moves_with_the_numbers_when_the_model_changes(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """A memory is read only against descriptions the file's current model made. One the swap did
    not measure again would be passed over at every later scan, the refusal lost, silently."""
    refused = await _one_face_recognized_as(service, store, clip, detector, recognizer, person)
    await service.reject(refused, person)
    # The stand-in reference has no real picture to measure again, and it is not what this is about.
    await temp_db.execute("DELETE FROM face_references", ())
    recognizer.revision = "test-recognizer-2"
    recognizer.rule = lambda chip: person_vector(1)

    await service.remeasure()

    remembered = await store.rejections_for(clip.asset.id)
    assert [who for who, _, _ in remembered] == [person]
    assert recognize.similarity(remembered[0][2], person_vector(1)) > 0.99


async def test_forgetting_every_face_forgets_every_no(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    person: str,
) -> None:
    """A library told to forget every face must not refuse somebody at the next scan of a file."""
    refused = await _one_face_recognized_as(service, store, clip, detector, recognizer, person)
    await service.reject(refused, person)

    await store.forget_everything(actor=Actor.sift("faces"))

    left = await temp_db.fetch_one("SELECT COUNT(*) AS n FROM face_rejected", ())
    assert left is not None and left["n"] == 0


async def test_a_name_you_gave_stays_yours_when_its_file_is_scanned_again(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The third decision of the same shape, and the one whose fallback could hide it.

    A rescan deletes every track a file has, so the attribution goes with them. Naming looks
    survivable because confirming also files the face as a reference photo, and the reference
    re-attaches the NAME by itself, so nothing appears to be lost. What would be lost is that
    somebody decided it: every "you said so" would come back as "Sift recognized them", and
    anything landing between the two confidence thresholds as a question already answered.

    So the assertion is on the attribution, not on the person. Checking only the name would pass
    with none of this built, because the reference photo would put it back.
    """
    person_id = await make_person(temp_db, "Ada Lovelace")
    frame = noisy_frame(400, 300, seed=11)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(0)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    await service.confirm((await store.tracks_of(clip.asset.id))[0].id, person_id)

    await service.scan(clip.asset.id)

    after = await store.tracks_of(clip.asset.id)
    assert len(after) == 1
    assert after[0].person_id == person_id
    assert after[0].attribution is Attribution.CONFIRMED, (
        "the name came back as something Sift worked out rather than something a person decided"
    )


async def test_changing_the_model_measures_every_face_again_from_the_pictures_and_opens_no_file(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole of what the setting's disclosure promises. Every face found so far is described
    again by the new model from the square Sift kept; no media file is opened; a name somebody
    gave stays and its remembered copy moves with the numbers; a match Sift made by arithmetic
    is undone for the re-match to make again; the references the name filed are measured again
    too, so the gallery is the new model's as well."""
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    person_id = await make_person(temp_db, "Ada Lovelace")
    named = (await store.tracks_of(clip.asset.id))[0].id
    await service.confirm(named, person_id)
    matched = (await store.tracks_of(other_clip.asset.id))[0].id
    await store.attribute(matched, person_id, confidence=0.9, attribution=Attribution.MATCHED)

    async def no_file_is_opened(*_: object, **__: object) -> object:
        raise AssertionError("measuring again read a media file")

    # Both parts that open a media file: the scan and the cover cut.
    for opener in (service_scanning, service_pictures):
        monkeypatch.setattr(opener, "resolve_decodable", no_file_is_opened)
    recognizer.revision = "test-recognizer-2"
    recognizer.rule = lambda chip: person_vector(1)
    recognizer.batches = 0
    recognizer.calls = 0

    done = await service.remeasure()

    assert (done.files, done.remaining) == (2, 0)
    # ONE RUN OF THE MODEL PER FILE, and one more for the whole page of references, never one
    # per face: the model reads a batch, with the same answers several times quicker on a card.
    assert recognizer.many_calls == done.files + 1
    assert done.references >= 1
    for asset in (clip, other_clip):
        scan = await store.scan_of(asset.asset.id)
        assert scan is not None and scan.recognizer == "test-recognizer-2"
        for track in await store.tracks_of(asset.asset.id):
            for face in await store.faces_of(track.id):
                assert recognize.similarity(face.vector, person_vector(1)) > 0.99
    kept = await store.track(named)
    assert kept is not None and kept.attribution is Attribution.CONFIRMED
    remembered = await store.confirmations_for(clip.asset.id)
    assert recognize.similarity(remembered[0][1], person_vector(1)) > 0.99
    undone = await store.track(matched)
    assert undone is not None and undone.person_id is None
    rows = await temp_db.fetch_all("SELECT recognizer FROM face_references", ())
    assert rows and {str(row["recognizer"]) for row in rows} == {"test-recognizer-2"}


async def test_a_file_the_previous_model_described_is_measured_again_before_it_is_read_again(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """Everything a rescan puts back (here, a name somebody gave) is found by comparing
    descriptions, and this file's remembered ones are the other model's numbers until it is
    measured again. Measured first, the name comes back; read first, it would not have."""
    person_id = await make_person(temp_db, "Ada Lovelace")
    frame = noisy_frame(400, 300, seed=12)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(0)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    await service.confirm((await store.tracks_of(clip.asset.id))[0].id, person_id)
    recognizer.revision = "test-recognizer-2"
    recognizer.rule = lambda chip: person_vector(1)

    await service.scan(clip.asset.id)

    after = await store.tracks_of(clip.asset.id)
    assert len(after) == 1
    assert after[0].attribution is Attribution.CONFIRMED and after[0].person_id == person_id
    scan = await store.scan_of(clip.asset.id)
    assert scan is not None and scan.recognizer == "test-recognizer-2"


async def test_taking_a_name_off_stops_it_coming_back_at_the_next_scan(
    service: FaceService,
    store: Store,
    temp_db: Database,
    clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The half that would be worse than not remembering at all.

    Remembering a naming by description means taking one off has to forget it. Left behind, the name
    would come off and then quietly reappear the next time that file was scanned: the person did
    the one thing the screen offers and it undid itself later, with nothing to say why. The same
    trap as restoring a pile somebody set aside.
    """
    person_id = await make_person(temp_db, "Ada Lovelace")
    frame = noisy_frame(400, 300, seed=12)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    recognizer.rule = lambda chip: person_vector(0)
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    await service.scan(clip.asset.id)
    track_id = (await store.tracks_of(clip.asset.id))[0].id
    await service.confirm(track_id, person_id)
    await service.reject(track_id, person_id)

    await service.scan(clip.asset.id)

    after = await store.tracks_of(clip.asset.id)
    assert len(after) == 1
    assert after[0].attribution is not Attribution.CONFIRMED


async def test_a_face_set_aside_stays_set_aside_when_its_file_is_scanned_again(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The promise the screen makes, against a rescan.

    Regrouping leaves an ignored pile alone. A **rescan** is different in kind: it deletes every
    track the file has and writes new ones, so a decision living on the track, as the pile it
    pointed at, would go with them: the pile surviving as a row claiming faces it no longer holds,
    and the faces themselves coming back among the open piles.

    So the decision is kept against the face's own description, exactly as a removal is, and put
    back when the file is scanned again.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    await service.ignore(pile_id)
    assert len(await store.pile_tracks(pile_id)) == 2

    await service.scan(clip.asset.id)
    await service.scan(other_clip.asset.id)

    ignored = await store.piles(PileStatus.IGNORED)
    assert [str(row["id"]) for row in ignored] == [pile_id], "the pile came back under its own id"
    held = await store.pile_tracks(pile_id)
    assert len(held) == 2, "the freshly found faces were not put back where they were set aside"
    assert all(track.person_id is None for track in held)


async def test_bringing_a_pile_back_stops_it_setting_itself_aside_at_the_next_scan(
    service: FaceService,
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> None:
    """The other half, and the one that would have been worse than not remembering at all.

    Remembering the decision by description means restoring has to forget it. Left behind, the pile
    would come back when somebody pressed the button and then quietly set itself aside again at the
    next scan of those files: the person did the one thing the screen offers and it came undone
    later, with nothing to say why.
    """
    await _two_files_of_one_stranger(service, detector, recognizer, clip, other_clip)
    await service.regroup()
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    await service.ignore(pile_id)

    await service.restore(pile_id)
    await service.scan(clip.asset.id)
    await service.scan(other_clip.asset.id)

    assert await store.piles(PileStatus.IGNORED) == []


async def _two_files_of_two_strangers(
    service: FaceService,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    first: Ingested,
    second: Ingested,
) -> None:
    """Two different unknown people, one per file. That is two piles of one."""
    frame = noisy_frame(400, 300, seed=4)
    box = draw_face(frame, x=60, y=40, size=180)
    detector.placed = {0: [(box, 0.9)]}
    await install_reader(service, Scripted([Frame(pixels=frame, timestamp_ms=0)]))
    recognizer.rule = lambda chip: person_vector(0)
    await service.scan(first.asset.id)
    recognizer.rule = lambda chip: person_vector(1)
    await service.scan(second.asset.id)
