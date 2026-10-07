# SPDX-License-Identifier: AGPL-3.0-or-later
"""The paths that only run when something has gone wrong, or is unusual.

Kept together rather than scattered because they share a shape: each one is a decision about what
to do when the ordinary case does not apply, and each was a deliberate choice rather than a
fallthrough.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

# Registered for its tables, not for a name: forgetting everything records a ledger event, and the
# ledger lives in the workbench component (`test_store.py` does the same).
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.hardware import HardwareReport
from sift.kernel.ids import new_id
from sift.kernel.ingress import Origin, verify_ingress
from sift.kernel.ledger import Actor
from sift.kernel.media import FFmpegError
from sift.kernel.subprocess import SubprocessError
from sift.slices.faces import clustering, tracking, weights
from sift.slices.faces import frames as frames_module
from sift.slices.faces import references as references_module
from sift.slices.faces import settings as face_settings
from sift.slices.faces.frames import Frame, Reader
from sift.slices.faces.models import Box, Depth, Detection, ScanStatus
from sift.slices.faces.models import Origin as FaceOrigin
from sift.slices.faces.pipeline import Bar, Outcome, Pipeline
from sift.slices.faces.references import PersonReport, folders_in
from sift.slices.faces.runner import Runner
from sift.slices.faces.service import FacesDisabled, FaceService, _with_depth
from sift.slices.faces.store import PassRecord, Store
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

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


async def import_folder(service: FaceService, root: Path) -> list[PersonReport]:
    """A folder of folders taken in one person at a time, as the folder import task reads one."""
    folders = await asyncio.to_thread(folders_in, root)
    return [await service.import_person_folder(folder, source=root.name) for folder in folders]


@pytest.fixture
async def library(library_store: LibraryStore, tmp_path: Path) -> Root:
    directory = tmp_path / "library"
    directory.mkdir()
    return await library_store.create_root(name="Clips", abs_path=directory)


@pytest.fixture
async def picture(content_store: ContentStore, library: Root, settings: Settings) -> Ingested:
    target = Path(str(library.abs_path)) / "still.jpg"
    target.write_bytes((CORPUS / "accepted.jpg").read_bytes())
    checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
    return await content_store.ingest(checked, root_id=library.id, rel_path="still.jpg")


# --- loading a model -----------------------------------------------------------------------------------


class StubSession:
    """Stands in for the inference runtime, which needs a real model file and there is none."""

    def __init__(self, path: str, options: Any, providers: list[str], **kwargs: Any) -> None:
        self.path = path
        self.options = options
        self.providers = providers
        self.kwargs = kwargs

    def get_providers(self) -> list[str]:
        return self.providers

    def get_inputs(self) -> list[Any]:
        return [type("Input", (), {"name": "input"})()]

    def get_outputs(self) -> list[Any]:
        return [type("Output", (), {"name": "output"})()]

    def run(self, names: list[str], feed: dict[str, Any]) -> list[np.ndarray]:
        return [np.zeros((1, 2), dtype=np.float32)]


@pytest.fixture
def stub_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import onnxruntime

    monkeypatch.setattr(onnxruntime, "InferenceSession", StubSession)
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: ["CPUExecutionProvider"])


async def test_a_model_is_verified_before_it_is_loaded_and_loaded_only_once(
    settings: Settings, hardware: HardwareReport, tmp_path: Path, stub_runtime: None
) -> None:
    payload = b"a model, of sorts" * 100
    source = tmp_path / "model.onnx"
    source.write_bytes(payload)
    from sift.slices.faces.crop import digest as digest_of
    from sift.slices.faces.weights import Weight

    weight = Weight(
        id="test.detector",
        role="detector",
        family="accurate",
        revision="test-1",
        url="https://example.test/m.onnx",
        digest=digest_of(payload),
        size_bytes=len(payload),
        archive_member=None,
        licence="MIT",
    )
    await weights.install_from_file(settings, weight, source)
    runner = Runner(settings, hardware)

    first = runner.load(weight)
    again = runner.load(weight)

    assert first is again
    assert first.inputs == ("input",)
    assert first.device == "cpu"
    assert runner.run(first, np.zeros((1, 3, 4, 4), dtype=np.float32))[0].shape == (1, 2)


async def test_inference_is_kept_to_one_thread_per_model(
    settings: Settings, hardware: HardwareReport, tmp_path: Path, stub_runtime: None
) -> None:
    """The work arrives as a queue already running several files together, so a model that spawned a
    thread per core would have every worker fighting every other worker for the same processor."""
    payload = b"a model" * 100
    source = tmp_path / "model.onnx"
    source.write_bytes(payload)
    from sift.slices.faces.crop import digest as digest_of
    from sift.slices.faces.weights import Weight

    weight = Weight(
        id="test.recognizer",
        role="recognizer",
        family="accurate",
        revision="test-1",
        url="https://example.test/m.onnx",
        digest=digest_of(payload),
        size_bytes=len(payload),
        archive_member=None,
        licence="MIT",
        dimension=2,
    )
    await weights.install_from_file(settings, weight, source)

    loaded = Runner(settings, hardware).load(weight)

    assert loaded.session.options.intra_op_num_threads == 1
    assert loaded.session.options.inter_op_num_threads == 1


# --- the service's unusual paths -------------------------------------------------------------------------


async def test_a_file_that_cannot_contain_a_face_is_not_looked_at(
    service: FaceService, content_store: ContentStore, library: Root, settings: Settings
) -> None:
    """An audio-only file has no pictures in it. Reported as having no faces rather than treated as
    a failure, because nothing is wrong with it."""
    target = Path(str(library.abs_path)) / "sound.mp4"
    target.write_bytes((CORPUS / "audio_only.mp4").read_bytes())
    checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(checked, root_id=library.id, rel_path="sound.mp4")

    assert await service.scan(ingested.asset.id) is ScanStatus.NO_FACES


async def test_asking_whether_the_models_are_ready_says_no_until_they_are_obtained(
    service: FaceService,
) -> None:
    """No model ships with Sift, so this is the ordinary state of a fresh install with the feature
    just switched on."""
    assert await service.ready() is False


async def test_switching_the_feature_off_gives_the_memory_back(service: FaceService) -> None:
    service.release()

    assert service._detector is None
    assert service._recognizer is None


async def test_matching_again_with_nobody_to_match_against_does_nothing(
    service: FaceService,
) -> None:
    assert await service.rematch() == 0


async def test_grouping_with_no_unnamed_faces_leaves_no_piles(
    service: FaceService, store: Store
) -> None:
    assert await service.regroup() == 0
    assert await store.piles() == []


async def test_setting_aside_a_pile_that_does_not_exist_says_so(service: FaceService) -> None:
    # None rather than False: setting one aside answers with the record it wrote, and there is no
    # record when there was nothing to set aside.
    assert await service.ignore("no-such-pile") is None
    assert await service.restore("no-such-pile") is False


async def test_confirming_a_face_whose_picture_has_gone_does_not_invent_a_reference(
    service: FaceService, store: Store, picture: Ingested, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")

    await service.confirm("no-such-face", person)

    assert await store.references(person) == []


async def test_rejecting_a_face_that_does_not_exist_is_not_an_error(
    service: FaceService, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")

    await service.reject("no-such-face", person)


async def test_deleting_everything_goes_through_the_service_too(
    service: FaceService, store: Store
) -> None:
    await service.forget_everything(actor=Actor.sift("faces"))

    assert await store.references() == []


async def test_a_deeper_look_samples_more_densely_and_leaves_the_quality_bar_alone(
    service: FaceService,
) -> None:
    """Depth is about how many moments get looked at, and about nothing else.

    Lowering the bar as well would make a deeper look also believe more: faces at half the size
    the recognizer reads, upscaled, described from detail that was not there.
    """
    shallow = await service.configuration()
    deeper = _with_depth(shallow, Depth.DEEP)

    assert deeper.density > shallow.density
    assert deeper.bar == shallow.bar


async def test_asking_for_a_deep_look_on_a_deep_install_is_not_twice_as_deep(
    service: FaceService, preferences: FakePreferences
) -> None:
    """Asking for what is already happening changes nothing.

    The settings say how hard to look; a request about one file says the same thing in a different
    place. Deriving the bar from the setting rather than from the last bar is what keeps the two
    from stacking: stacked, a second deep request would halve an already-halved bar and accept
    faces nothing can be recognized from.
    """
    preferences.set(face_settings.EFFORT_KEY, "deep")
    already_deep = await service.configuration()

    asked_again = _with_depth(already_deep, Depth.DEEP)

    assert asked_again.bar == already_deep.bar
    assert asked_again.density == already_deep.density


async def test_asking_for_a_fast_look_on_a_deep_install_gets_the_fast_bar_back(
    service: FaceService, preferences: FakePreferences
) -> None:
    """The other direction, which the same mistake breaks the other way round: a value derived
    from an already-relaxed one can be loosened but never tightened again."""
    preferences.set(face_settings.EFFORT_KEY, "deep")
    deep = await service.configuration()
    preferences.set(face_settings.EFFORT_KEY, "balanced")
    fast = await service.configuration()

    asked_shallower = _with_depth(deep, Depth.FAST)

    assert asked_shallower.bar == fast.bar
    assert asked_shallower.density == fast.density
    assert deep.density > fast.density


async def test_the_tuning_that_produced_a_result_is_recorded_beside_it(
    service: FaceService,
) -> None:
    """Two files scanned under different quality bars are not comparable, and without this there
    would be no way to tell which of them is stale after a change."""
    configured = await service.configuration()
    other = _with_depth(configured, Depth.DEEP)

    assert configured.digest != other.digest
    assert len(configured.digest) == 8


# --- reference import --------------------------------------------------------------------------------------


async def test_a_folder_of_folders_is_held_as_fingerprints_whoever_its_names_are(
    service: FaceService,
    store: Store,
    temp_db: Database,
    detector: FakeDetector,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")
    frame = noisy_frame(400, 400, seed=2)
    # Comfortably above the smallest usable face and well short of the size at which a face stops
    # scoring better for being bigger, so what the audit measures is a real number rather than the
    # top of the scale, which is what makes the quality assertion below say anything.
    box = draw_face(frame, x=80, y=80, size=120)
    detector.placed = {0: [(box, 0.9)]}

    root = tmp_path / "references"
    for name in ("Ada Lovelace", "Nobody At All"):
        folder = root / name
        folder.mkdir(parents=True)
        for index in range(2):
            (folder / f"{index}.jpg").write_bytes(b"stand-in")

    async def decode(path: Path, settings: Settings, **_: object) -> np.ndarray:
        return frame

    monkeypatch.setattr("sift.slices.faces.frames.decode_image", decode)

    async def encode(chips: list[np.ndarray], settings: Settings) -> list[bytes]:
        return [f"picture-{index}".encode() for index in range(len(chips))]

    monkeypatch.setattr("sift.slices.faces.crop.encode", encode)

    reports = await import_folder(service, root)

    assert [report.name for report in reports] == ["Ada Lovelace", "Nobody At All"]
    # A folder's name gives nobody anything: Ada here has the name and none of its faces.
    assert [report.person_id for report in reports] == [None, None]
    assert await store.references(person) == []
    held = {str(row["name"]): str(row["id"]) for row in await store.unclaimed_entries()}
    assert set(held) == {"Ada Lovelace", "Nobody At All"}
    kept = await store.entry_faces(held["Ada Lovelace"])
    assert len(kept) == 2
    # What the audit measured about each picture, kept. A column that reads the same on every row
    # is a column that tells whoever exports these nothing about the faces in them.
    assert sorted(float(face["quality"]) for face in kept) == sorted(
        candidate.quality for candidate in reports[0].usable
    )
    assert all(0.0 < float(face["quality"]) < 1.0 for face in kept)


async def test_importing_the_same_folder_twice_adds_nothing_the_second_time(
    service: FaceService,
    store: Store,
    temp_db: Database,
    detector: FakeDetector,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Somebody who imports six hundred folders WILL do it again. It must cost nothing.

    Keyed on the identity of each picture, so the second run finds every reference already held,
    and the count it reports is what happened rather than what was attempted.
    """
    person = await make_person(temp_db, "Ada Lovelace")
    frame = noisy_frame(400, 400, seed=2)
    box = draw_face(frame, x=80, y=80, size=120)
    detector.placed = {0: [(box, 0.9)]}

    folder = tmp_path / "references" / "Ada Lovelace"
    folder.mkdir(parents=True)
    for index in range(2):
        (folder / f"{index}.jpg").write_bytes(b"stand-in")

    async def decode(path: Path, settings: Settings, **_: object) -> np.ndarray:
        return frame

    monkeypatch.setattr("sift.slices.faces.frames.decode_image", decode)

    async def encode(chips: list[np.ndarray], settings: Settings) -> list[bytes]:
        return [f"picture-{index}".encode() for index in range(len(chips))]

    monkeypatch.setattr("sift.slices.faces.crop.encode", encode)

    first = await import_folder(service, tmp_path / "references")
    again = await import_folder(service, tmp_path / "references")

    assert first[0].added == 2
    assert again[0].added == 0, "the second run must add nothing"
    (entry,) = await store.unclaimed_entries()
    assert len(await store.entry_faces(str(entry["id"]))) == 2
    assert await store.references(person) == []


async def test_a_folder_naming_nobody_is_reported_and_the_rest_still_import(
    service: FaceService,
    store: Store,
    temp_db: Database,
    detector: FakeDetector,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """One bad folder out of six hundred must never sink the run, and must be NAMED.

    "Something went wrong with one of your folders" is not a sentence anybody can act on. Every
    folder comes back, whether it worked or not, with which one it was.
    """
    person = await make_person(temp_db, "Ada Lovelace")
    frame = noisy_frame(400, 400, seed=2)
    box = draw_face(frame, x=80, y=80, size=120)
    detector.placed = {0: [(box, 0.9)]}

    root = tmp_path / "references"
    for name in ("Ada Lovelace", "Nobody At All"):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "one.jpg").write_bytes(b"stand-in")

    async def decode(path: Path, settings: Settings, **_: object) -> np.ndarray:
        return frame

    monkeypatch.setattr("sift.slices.faces.frames.decode_image", decode)

    async def encode(chips: list[np.ndarray], settings: Settings) -> list[bytes]:
        return [b"picture" for _ in chips]

    monkeypatch.setattr("sift.slices.faces.crop.encode", encode)

    reports = await import_folder(service, root)

    assert [one.name for one in reports] == ["Ada Lovelace", "Nobody At All"]
    assert [one.added for one in reports] == [1, 1], "every folder still imported"
    assert await store.references(person) == []


async def test_a_folder_with_nothing_usable_holds_nothing(
    service: FaceService,
    store: Store,
    detector: FakeDetector,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A name with no face to recognize it by is no entry: the pass could never place it."""
    detector.placed = {}

    folder = tmp_path / "references" / "Nothing Here"
    folder.mkdir(parents=True)
    (folder / "one.jpg").write_bytes(b"stand-in")

    async def decode(path: Path, settings: Settings, **_: object) -> np.ndarray | None:
        return None

    monkeypatch.setattr("sift.slices.faces.frames.decode_image", decode)

    reports = await import_folder(service, tmp_path / "references")

    assert (reports[0].added, reports[0].person_id) == (0, None)
    assert await store.unclaimed_entries() == []


# --- reading pictures ---------------------------------------------------------------------------------------


async def test_a_decoder_that_will_not_start_is_reported_as_a_media_failure(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(*_: object, **__: object) -> bytes:
        raise SubprocessError("could not run it")

    monkeypatch.setattr(frames_module, "subprocess_capture", refuse)

    with pytest.raises(FFmpegError):
        [
            frame
            async for frame in Reader(settings).stream(
                Path("f.gif"), media_type="gif", width=16, height=16, timestamps=(0,)
            )
        ]


async def test_a_gif_with_nothing_readable_in_it_yields_nothing(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def empty(*_: object, **__: object) -> bytes:
        return b""

    monkeypatch.setattr(frames_module, "subprocess_capture", empty)

    got = [
        frame
        async for frame in Reader(settings).stream(
            Path("f.gif"), media_type="gif", width=16, height=16, timestamps=(0,)
        )
    ]

    assert got == []


async def test_an_empty_picture_file_comes_back_as_nothing(
    settings: Settings, tmp_path: Path
) -> None:
    """Nothing to decode is an answer of nothing, not an error that sinks the import."""
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")

    assert await frames_module.decode_image(empty, settings) is None


async def test_a_picture_the_decoder_refuses_comes_back_as_nothing(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def will_not_decode(*_: object, **__: object) -> bytes:
        raise FFmpegError("no")

    monkeypatch.setattr("sift.kernel.media.run", will_not_decode)
    target = tmp_path / "odd.jpg"
    target.write_bytes(b"something")

    assert await frames_module.decode_image(target, settings) is None


# --- following faces -----------------------------------------------------------------------------------------


def test_two_boxes_with_no_size_between_them_are_not_the_same_face() -> None:
    nothing = Detection(
        box=Box(x=0, y=0, width=0, height=0), score=0.9, landmarks=(), timestamp_ms=0
    )

    assert tracking.continues(nothing, nothing) is False


def test_a_face_that_moved_out_of_its_own_box_starts_a_second_run() -> None:
    """Not abandoned: the two runs are joined afterwards by what the faces look like, which is
    evidence about who they are rather than about where they were."""
    first = Detection(
        box=Box(x=0, y=0, width=60, height=60), score=0.9, landmarks=(), timestamp_ms=0
    )
    moved = Detection(
        box=Box(x=200, y=0, width=60, height=60), score=0.9, landmarks=(), timestamp_ms=1000
    )

    linker = tracking.Linker()
    linker.add([first])
    linker.add([moved])

    assert tracking.continues(first, moved) is False
    assert len(linker.runs) == 2


def test_a_face_that_stayed_put_carries_one_run_across_both_moments() -> None:
    first = Detection(
        box=Box(x=10, y=10, width=50, height=50), score=0.9, landmarks=(), timestamp_ms=0
    )
    second = Detection(
        box=Box(x=12, y=12, width=50, height=50), score=0.9, landmarks=(), timestamp_ms=1000
    )

    linker = tracking.Linker()
    started = [linker.add([first]), linker.add([second])]

    assert started == [1, 0], "the second moment is the same face carrying on, not somebody new"
    assert len(linker.runs) == 1
    assert linker.runs[0].started_ms == 0
    assert linker.runs[0].ended_ms == 1000


def test_joining_runs_that_have_nothing_described_yet_leaves_nothing() -> None:
    assert tracking.merge([]) == []


# --- a pass over a file --------------------------------------------------------------------------------------


def test_a_pass_that_planned_to_look_at_nothing_reports_full_coverage() -> None:
    outcome = Outcome(appearances=(), frames_examined=0, frames_planned=0, stopped_early=False)

    assert outcome.coverage == 1.0


async def test_a_pass_stops_when_it_has_spent_its_budget(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """A limit stops one very long or very large file taking a share of the queue out of all
    proportion to the others."""

    class Slow:
        async def stream(self, path, **_: object):  # type: ignore[no-untyped-def]
            for index in range(30):
                frame = noisy_frame(200, 200, seed=index)
                box = draw_face(frame, x=20 + index, y=20, size=140)
                detector.placed[index * 1000] = [(box, 0.9)]
                yield Frame(pixels=frame, timestamp_ms=index * 1000)

    pipeline = Pipeline(
        Slow(),  # type: ignore[arg-type]
        detector,  # type: ignore[arg-type]
        recognizer,  # type: ignore[arg-type]
        bar=Bar(min_pixels=48, min_sharpness=1.0, min_frontality=0.05),
        budget_seconds=0.0,
    )

    outcome = await pipeline.run(
        Path("long.mp4"), media_type="video", width=200, height=200, duration_ms=60_000
    )

    assert outcome.stopped_early
    assert outcome.frames_examined == 1


# --- the last few corners ---------------------------------------------------------------------------------


def test_grouping_stops_when_every_pair_has_been_joined() -> None:
    """One description cannot be grouped with anything, so there is nothing left to try."""
    assert clustering.agglomerate([person_vector(0)], join_above=0.5) == [[0]]


def test_a_pack_read_from_a_file_on_disk_is_the_same_as_one_read_from_bytes(
    tmp_path: Path,
) -> None:
    from sift.slices.faces import packs

    raw = packs.build(
        name="Sample",
        version="1",
        recognizer="test-recognizer",
        dimension=8,
        people=[],
        include_pictures=False,
    )
    target = tmp_path / "sample.pack"
    target.write_bytes(raw)

    assert packs.read_file(target, expect_recognizer="test-recognizer").name == "Sample"


def test_a_pack_with_no_manifest_in_it_is_refused() -> None:
    import zipfile
    from io import BytesIO

    from sift.slices.faces.packs import PackError, read

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("something-else.txt", "nope")

    with pytest.raises(PackError, match="isn't a file of facial fingerprints"):
        read(buffer.getvalue(), expect_recognizer="test-recognizer")


def test_a_spreadsheet_row_with_no_name_in_it_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "people.csv").write_text("Name,Aliases\n,orphaned alias\nAda,ada l\n")

    sheet = references_module.read_sheet(tmp_path)

    assert list(sheet.aliases) == ["Ada"]


def test_a_spreadsheet_without_the_optional_columns_still_reads(tmp_path: Path) -> None:
    (tmp_path / "people.csv").write_text("Name\nAda Lovelace\n")

    sheet = references_module.read_sheet(tmp_path)

    assert sheet.aliases["Ada Lovelace"] == ()
    assert sheet.links["Ada Lovelace"] == ()


def test_a_picture_that_was_refused_is_not_compared_with_the_ones_that_passed() -> None:
    """The near-duplicate check looks only at what could actually become a reference."""
    from sift.slices.faces.models import Finding
    from sift.slices.faces.references import Candidate, PersonReport

    report = PersonReport(name="Ada")
    report.candidates = [
        Candidate(path=Path("no-face.jpg"), findings=(Finding.NO_FACE,)),
        Candidate(path=Path("a.jpg"), findings=(), vector=person_vector(0)),
    ]

    references_module.mark_near_duplicates(report)

    assert report.candidates[0].findings == (Finding.NO_FACE,)
    assert report.candidates[1].findings == ()


async def test_a_run_whose_frame_has_been_let_go_is_skipped_rather_than_crashing(
    detector: FakeDetector, recognizer: FakeRecognizer
) -> None:
    """Frames are held only while a pass is reading them; a run naming one that is gone contributes
    nothing rather than taking the file down with it."""
    from sift.slices.faces.pipeline import Bar, Pipeline, _Tally

    pipeline = Pipeline(
        object(),  # type: ignore[arg-type]
        detector,  # type: ignore[arg-type]
        recognizer,  # type: ignore[arg-type]
        bar=Bar(min_pixels=48, min_sharpness=1.0, min_frontality=0.05),
    )
    run = tracking.Run(
        detections=[
            Detection(
                box=Box(x=0, y=0, width=100, height=100),
                score=0.9,
                landmarks=((1.0, 1.0),) * 5,
                timestamp_ms=999,
            )
        ]
    )

    assert pipeline._choose(run, {}, (1.0, 1.0), _Tally()) == []


def test_two_faces_in_one_frame_each_continue_their_own_run() -> None:
    """Each face may continue at most one run and each run may be continued by at most one face, so
    two people crossing produce two runs rather than a tangle."""

    def at(x: int, stamp: int) -> Detection:
        return Detection(
            box=Box(x=x, y=0, width=60, height=60),
            score=0.9,
            landmarks=(),
            timestamp_ms=stamp,
        )

    linker = tracking.Linker()
    assert linker.add([at(0, 0), at(300, 0)]) == 2
    assert linker.add([at(310, 1000), at(5, 1000)]) == 0
    assert len(linker.runs) == 2
    assert [len(run.detections) for run in linker.runs] == [2, 2]


async def test_a_face_in_a_pile_somebody_set_aside_is_not_matched_again(
    service: FaceService, store: Store, temp_db: Database, picture: Ingested
) -> None:
    """Setting a pile aside means what it says: those faces stop being offered, including when
    somebody new is added afterwards."""
    from sift.slices.faces.models import PileStatus

    person = await make_person(temp_db, "Ada Lovelace")
    track_ids = await _record_two(store, picture.asset.id)
    await store.replace_piles([(person_vector(0), track_ids)])
    pile_id = str((await store.piles(PileStatus.OPEN))[0]["id"])
    await service.ignore(pile_id)
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=1.0,
        crop=b"a picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )

    assert await service.rematch() == 0


async def test_a_face_whose_picture_has_been_lost_does_not_become_a_reference(
    service: FaceService, store: Store, temp_db: Database, picture: Ingested
) -> None:
    """The picture is what a reference is; without it there is nothing to add."""
    person = await make_person(temp_db, "Ada Lovelace")
    track_ids = await _record_two(store, picture.asset.id)
    for face in await store.faces_of(track_ids[0]):
        store.resolve(face.crop_path).unlink()

    await service.confirm(track_ids[0], person)

    assert await store.references(person) == []


async def test_importing_a_pack_matches_nothing_inside_the_request(
    service: FaceService,
    preferences: FakePreferences,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route asks the queue for one re-match afterwards, and the service does not also match
    the whole library inside the request while the person who pressed the button waits."""
    await make_person(temp_db, "Ada Lovelace")

    async def not_here() -> int:
        raise AssertionError("the import matched inside the request")

    monkeypatch.setattr(service, "rematch", not_here)

    outcome = await service.import_pack(_a_small_pack())

    assert outcome.added == 2


def _a_small_pack() -> bytes:
    from sift.slices.faces import packs

    return packs.build(
        name="Sample",
        version="1",
        recognizer="test-recognizer",
        dimension=8,
        people=[
            packs.PackedPerson(
                name="Ada Lovelace",
                aliases=(),
                links=(),
                faces=tuple(
                    packs.PackedFace(
                        digest=f"face-{variant}",
                        quality=0.8,
                        vector=person_vector(0, variant),
                        picture=None,
                    )
                    for variant in range(2)
                ),
            )
        ],
        include_pictures=False,
    )


async def _record_two(store: Store, asset_id: str) -> list[str]:
    from sift.slices.faces.models import Appearance, Described, Quality

    appearances = []
    for index in range(2):
        detection = Detection(
            box=Box(x=10, y=10, width=100, height=100),
            score=0.9,
            landmarks=((1.0, 1.0),) * 5,
            timestamp_ms=index * 1000,
        )
        appearances.append(
            Appearance(
                started_ms=index * 1000,
                ended_ms=index * 1000,
                seen_in=1,
                quality=0.8,
                faces=(
                    Described(
                        detection=detection,
                        quality=Quality(
                            pixels=100, sharpness=500.0, frontality=0.9, score=0.8, accepted=True
                        ),
                        vector=person_vector(0, index),
                        chip=np.zeros((1, 1, 3), dtype=np.uint8),
                    ),
                ),
            )
        )
    return await store.replace_pass(
        asset_id,
        appearances,
        [[b"\xff\xd8\xff picture"] for _ in appearances],
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


async def test_a_deeper_look_at_one_file_can_be_asked_for(
    service: FaceService, picture: Ingested, detector: FakeDetector, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read from the density, which is what depth now changes. See `Bar.of`."""
    seen: list[float] = []
    original = Pipeline.run

    async def record(self, path, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(self._density)
        return await original(self, path, **kwargs)

    monkeypatch.setattr(Pipeline, "run", record)

    await service.scan(picture.asset.id, depth=Depth.DEEP)
    await service.scan(picture.asset.id, depth=Depth.FAST)

    assert seen[0] > seen[1]


async def test_the_pair_of_models_is_built_once_and_reused(
    store: Store,
    content_store: ContentStore,
    access: Any,
    preferences: FakePreferences,
    settings: Settings,
    hardware: HardwareReport,
    reindexer: RecordingReindexer,
    tmp_path: Path,
    stub_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The device is resolved when the models are loaded rather than decided when the image was
    built, which is what makes adding a second kind of accelerator one more implementation rather
    than a second way of installing Sift."""
    for weight in weights.pairing("accurate"):
        payload = f"model {weight.id}".encode() * 100
        source = tmp_path / f"{weight.id}.onnx"
        source.write_bytes(payload)
        from sift.slices.faces.crop import digest as digest_of

        monkeypatch.setitem(
            weights.CATALOG,
            weight.id,
            type(weight)(
                **{
                    **{field: getattr(weight, field) for field in weight.__slots__},
                    "digest": digest_of(payload),
                    "archive_member": None,
                }
            ),
        )
        await weights.install_from_file(settings, weights.CATALOG[weight.id], source)

    built = FaceService(
        store=store,
        content=content_store,
        repository=access,
        preferences=preferences,
        settings=settings,
        hardware=hardware,
        reindexer=reindexer,
    )
    configured = await built.configuration()

    detector, recognizer = await built._models(configured)
    again = await built._models(configured)

    assert (detector, recognizer) == again
    assert await built.ready() is True

    built.release()
    assert built._runner is None


async def test_loading_the_models_leaves_the_loop_free(
    store: Store,
    content_store: ContentStore,
    access: Any,
    preferences: FakePreferences,
    settings: Settings,
    hardware: HardwareReport,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting the model process and loading the pair is a wait on another process. Waited on
    the loop, every page and video stopped for as long, once per scan that loaded them."""
    built = FaceService(
        store=store,
        content=content_store,
        repository=access,
        preferences=preferences,
        settings=settings,
        hardware=hardware,
        reindexer=reindexer,
    )
    configured = await built.configuration()
    pair = (object(), object())

    def slow_load(_configured: object) -> tuple[object, object]:
        time.sleep(0.3)
        return pair

    monkeypatch.setattr(built, "_load_models", slow_load)
    ticks: list[float] = []

    async def tick() -> None:
        while True:
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.005)

    ticker = asyncio.create_task(tick())
    await asyncio.sleep(0.02)
    try:
        assert await built._models(configured) == pair
        await asyncio.sleep(0.02)
    finally:
        ticker.cancel()
    longest = max(later - earlier for earlier, later in itertools.pairwise(ticks))
    assert longest < 0.15, f"the loop was held for {longest:.3f} s while the models loaded"


def test_a_face_already_claimed_by_one_run_does_not_claim_it_again() -> None:
    """Two faces close together, each continuing its own run rather than both taking the first."""

    def at(x: int, stamp: int) -> Detection:
        return Detection(
            box=Box(x=x, y=0, width=40, height=40), score=0.9, landmarks=(), timestamp_ms=stamp
        )

    linker = tracking.Linker()
    linker.add([at(0, 0), at(20, 0)])
    started = linker.add([at(2, 1000), at(22, 1000)])

    assert started == 0
    assert [len(run.detections) for run in linker.runs] == [2, 2]


def test_a_face_that_matched_nobody_is_left_alone_rather_than_attached_to_the_best_guess() -> None:
    """Below the offering line, Sift says nothing at all: a suggestion list that includes
    everything half-noticed is a list nobody reads to the end."""
    from sift.slices.faces import matching
    from sift.slices.faces.models import Reference

    gallery = matching.build_gallery(
        {
            "ada": [
                Reference(
                    id="a", person_id="ada", vector=person_vector(0), quality=1.0, crop_digest="a"
                )
            ]
        }
    )

    assert matching.best_match(person_vector(4), gallery) is None


def test_a_persons_description_that_cancels_out_is_left_as_it_is() -> None:
    from sift.slices.faces import matching
    from sift.slices.faces.models import Reference

    opposite = tuple(-value for value in person_vector(0))
    references = [
        Reference(id="a", person_id="ada", vector=person_vector(0), quality=1.0, crop_digest="a"),
        Reference(id="b", person_id="ada", vector=opposite, quality=1.0, crop_digest="b"),
    ]

    assert matching.describe(references) == [(0.0,) * 8]


def test_a_pack_with_pictures_asked_for_but_none_to_include_writes_none() -> None:
    """A person whose references are numbers only, exported by somebody who asked for pictures."""
    import zipfile
    from io import BytesIO

    from sift.slices.faces import packs

    raw = packs.build(
        name="Sample",
        version="1",
        recognizer="test-recognizer",
        dimension=8,
        people=[
            packs.PackedPerson(
                name="Ada",
                aliases=(),
                links=(),
                faces=(
                    packs.PackedFace(
                        digest="a", quality=1.0, vector=person_vector(0), picture=None
                    ),
                ),
            )
        ],
        include_pictures=True,
    )

    with zipfile.ZipFile(BytesIO(raw)) as bundle:
        assert not [name for name in bundle.namelist() if name.startswith(packs.PICTURES)]


async def test_a_person_with_enough_good_pictures_is_not_reported_as_short(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    tmp_path: Path,
) -> None:
    from sift.slices.faces import tuning
    from sift.slices.faces.models import Finding
    from sift.slices.faces.references import Auditor

    frame = noisy_frame(400, 400, seed=6)
    box = draw_face(frame, x=80, y=80, size=240)
    detector.placed = {0: [(box, 0.9)]}
    folder = tmp_path / "Ada Lovelace"
    folder.mkdir()
    for index in range(tuning.MIN_REFERENCES):
        (folder / f"{index}.jpg").write_bytes(b"stand-in")

    async def decode(path: Path, _settings: Settings, **__: object) -> np.ndarray:
        return frame

    monkeypatch.setattr("sift.slices.faces.frames.decode_image", decode)
    # Each picture describes to its own direction, so none is a near-duplicate of another.
    order: list[int] = []

    def rule(chip: np.ndarray) -> list[float]:
        order.append(len(order))
        return list(person_vector(0, len(order) % 4))

    recognizer.rule = rule

    report = await Auditor(settings, detector, recognizer).person(folder)  # type: ignore[arg-type]

    assert Finding.BELOW_MINIMUM not in report.findings


async def test_bringing_the_schema_up_to_date_twice_changes_nothing(temp_db: Database) -> None:
    """A component records the version it reached, so a second boot does not run the first one's
    statements again."""
    from sift.slices.faces import schema

    await temp_db.initialize_schema()
    async with temp_db.write() as connection:
        await schema.initialize(connection, schema.VERSION)

    rows = await temp_db.fetch_all("SELECT id FROM face_tracks", ())
    assert rows == []


async def _columns(temp_db: Database, table: str) -> str:
    """How one of the three tables spells the column, as one word. Three literal statements against
    three literal names, because no SQL here is assembled from a value."""
    statement = {
        "scans": "PRAGMA table_info(face_scans)",
        "packs": "PRAGMA table_info(face_packs)",
        "references": "PRAGMA table_info(face_references)",
    }[table]
    names = {str(row["name"]) for row in await temp_db.fetch_all(statement, ())}
    if "recogniser" in names:
        return "recogniser"
    return "recognizer" if "recognizer" in names else "neither"


async def test_a_reference_of_numbers_alone_leaves_nothing_to_delete(
    store: Store, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")
    await store.add_reference(
        person,
        vector=person_vector(0),
        quality=1.0,
        crop=None,
        digest="a-stated-identity",
        origin=FaceOrigin.PACK,
        recognizer="test-recognizer",
    )

    assert await store.remove_references(person_id=person) == 1


async def test_a_file_with_no_unnamed_faces_contributes_nothing_to_match_against(
    store: Store, picture: Ingested, temp_db: Database
) -> None:
    person = await make_person(temp_db, "Ada Lovelace")
    track_ids = await _record_two(store, picture.asset.id)
    for track_id in track_ids:
        await store.attribute(track_id, person, confidence=0.9, attribution=None)

    assert await store.unattributed("test-recognizer") == []


def test_boxes_that_all_overlap_are_reduced_to_one_without_running_out_of_candidates() -> None:
    """The loop ends because nothing is left rather than because the last one was reached."""
    from sift.slices.faces.detect import suppress

    boxes = np.array([[0.0, 0.0, 50.0, 50.0], [1.0, 1.0, 51.0, 51.0], [2.0, 2.0, 52.0, 52.0]])
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)

    assert suppress(boxes, scores, 0.4) == [0]


def test_a_scale_looked_at_twice_reuses_the_positions_worked_out_the_first_time() -> None:
    """They never change, and working them out is a grid the size of the input."""
    from sift.slices.faces.detect import AnchorDetector
    from sift.slices.faces.runner import Loaded

    loaded = Loaded(
        weight=weights.CATALOG["accurate.detector"],
        session=None,
        inputs=("input",),
        outputs=(),
        device="cpu",
    )

    class Nothing:
        def run(self, *_: object) -> list[np.ndarray]:
            return []

    detector = AnchorDetector(Nothing(), loaded)  # type: ignore[arg-type]

    first = detector._grid(8, 2)
    again = detector._grid(8, 2)

    assert first is again


async def test_a_face_that_matches_nobody_leaves_no_attribution_behind(
    service: FaceService, store: Store, temp_db: Database, picture: Ingested
) -> None:
    """A gallery with somebody in it, and a face that is not them."""
    person = await make_person(temp_db, "Ada Lovelace")
    await store.add_reference(
        person,
        vector=person_vector(4),
        quality=1.0,
        crop=b"a picture",
        origin=FaceOrigin.ADDED,
        recognizer="test-recognizer",
    )
    track_ids = await _record_two(store, picture.asset.id)

    await service._attribute(track_ids, await service.configuration())

    assert all(track.person_id is None for track in await store.tracks_of(picture.asset.id))


async def test_several_pictures_of_one_appearance_yield_one_description_to_match_against(
    store: Store, picture: Ingested
) -> None:
    """The clearest view of somebody is the one most likely to be recognized, so one per appearance
    rather than all of them."""
    from sift.slices.faces.models import Appearance, Described, Quality

    faces = tuple(
        Described(
            detection=Detection(
                box=Box(x=10, y=10, width=100, height=100),
                score=0.9,
                landmarks=((1.0, 1.0),) * 5,
                timestamp_ms=index * 100,
            ),
            quality=Quality(
                pixels=100, sharpness=500.0, frontality=0.9, score=0.5 + index * 0.1, accepted=True
            ),
            vector=person_vector(0, index),
            chip=np.zeros((1, 1, 3), dtype=np.uint8),
        )
        for index in range(2)
    )
    await store.replace_pass(
        picture.asset.id,
        [Appearance(started_ms=0, ended_ms=100, seen_in=2, quality=0.6, faces=faces)],
        [[b"\xff\xd8\xff one", b"\xff\xd8\xff two"]],
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

    assert len(await store.unattributed("test-recognizer")) == 1


async def test_importing_a_folder_matches_nothing_inside_the_request(
    service: FaceService,
    store: Store,
    temp_db: Database,
    detector: FakeDetector,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The route queues the one pass afterwards, and the service neither places the folder nor
    matches inline, so a folder of pictures is matched once."""
    person = await make_person(temp_db, "Ada Lovelace")
    frame = noisy_frame(400, 400, seed=8)
    box = draw_face(frame, x=80, y=80, size=240)
    detector.placed = {0: [(box, 0.9)]}

    folder = tmp_path / "references" / "Ada Lovelace"
    folder.mkdir(parents=True)
    (folder / "one.jpg").write_bytes(b"stand-in")

    async def decode(path: Path, _settings: Settings, **__: object) -> np.ndarray:
        return frame

    monkeypatch.setattr("sift.slices.faces.frames.decode_image", decode)

    async def encode(chips: list[np.ndarray], _settings: Settings) -> list[bytes]:
        return [b"picture" for _ in chips]

    monkeypatch.setattr("sift.slices.faces.crop.encode", encode)

    async def not_here() -> int:
        raise AssertionError("the import matched inside the request")

    monkeypatch.setattr(service, "rematch", not_here)
    monkeypatch.setattr(service, "recognize_from_fingerprints", not_here)

    reports = await import_folder(service, tmp_path / "references")

    assert reports[0].added == 1
    assert await store.references(person) == []


async def test_the_recognition_seam_is_quiet_when_the_feature_is_off(temp_db: Database) -> None:
    """Not having turned recognition on is not an error.

    The slice that creates People asks this on every create. With the feature off the service
    refuses (correctly, it refuses everything), and a refusal here would stop somebody being
    added at all, on an install that has nothing to do with faces.
    """
    from sift.slices.faces.service import Recognition

    class Off:
        async def claim_for(self, person_id: str, name: str) -> int:
            raise FacesDisabled()

        async def waiting_for(self, name: str) -> list[str]:
            raise FacesDisabled()

        async def held_for(self, person_id: str, name: str) -> int:
            raise FacesDisabled()

    seam = Recognition(Off())  # type: ignore[arg-type]

    assert await seam.claim_for("p1", "Ada") == 0
    assert await seam.waiting_for("Ada") == []
    assert await seam.held_for("p1", "Ada") == 0


async def test_the_recognition_seam_passes_the_answer_through_when_it_is_on(
    temp_db: Database,
) -> None:
    """The other half. A seam that always answered zero would pass the test above."""
    from sift.slices.faces.service import Recognition

    class On:
        async def claim_for(self, person_id: str, name: str) -> int:
            return 3

        async def waiting_for(self, name: str) -> list[str]:
            return ["Ada"]

        async def held_for(self, person_id: str, name: str) -> int:
            return 4

    seam = Recognition(On())  # type: ignore[arg-type]

    assert await seam.claim_for("p1", "Ada") == 3
    assert await seam.waiting_for("Ada") == ["Ada"]
    assert await seam.held_for("p1", "Ada") == 4


async def test_a_claim_that_gave_faces_asks_for_the_library_to_be_matched_again() -> None:
    """New references change who the unnamed faces look like, so a claim that gave any asks for
    the re-match, once, and a claim that gave none asks for nothing."""
    from sift.slices.faces.jobs import FACE_REMATCH
    from sift.slices.faces.service import Recognition

    class Gives:
        def __init__(self, count: int) -> None:
            self.count = count

        async def claim_for(self, person_id: str, name: str) -> int:
            return self.count

    class Queue:
        def __init__(self) -> None:
            self.asked: list[str] = []

        async def enqueue_when_settled(self, kind: str, *_: object, **__: object) -> None:
            self.asked.append(kind)

    queue = Queue()
    assert await Recognition(Gives(2), queue=queue).claim_for("p1", "Ada") == 2  # type: ignore[arg-type]
    assert queue.asked == [FACE_REMATCH]
    assert await Recognition(Gives(0), queue=queue).claim_for("p1", "Ada") == 0  # type: ignore[arg-type]
    assert queue.asked == [FACE_REMATCH]


async def test_a_folder_naming_nobody_is_held_rather_than_thrown_away(
    service: FaceService,
    store: Store,
    temp_db: Database,
    detector: FakeDetector,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The same rule the pack import follows, and for the same reason.

    Reading six hundred folders is the expensive part. Discarding what came out of one because
    nobody by that name exists yet means doing that work again the day somebody adds them, so it
    is held, and adding them later claims it. Asserted through `claim_for`, which is the route the
    application takes when a person is created, rather than by reading the holding table.
    """
    frame = noisy_frame(400, 400, seed=2)
    box = draw_face(frame, x=80, y=80, size=120)
    detector.placed = {0: [(box, 0.9)]}

    folder = tmp_path / "references" / "Nobody Yet"
    folder.mkdir(parents=True)
    (folder / "one.jpg").write_bytes(b"stand-in")

    async def decode(path: Path, settings: Settings, **_: object) -> np.ndarray:
        return frame

    monkeypatch.setattr("sift.slices.faces.frames.decode_image", decode)

    async def encode(chips: list[np.ndarray], settings: Settings) -> list[bytes]:
        return [b"picture" for _ in chips]

    monkeypatch.setattr("sift.slices.faces.crop.encode", encode)

    reports = await import_folder(service, tmp_path / "references")
    assert reports[0].person_id is None, "nobody was invented"

    # Running it again writes nothing: the entry is matched on the name and each face on the
    # identity of its picture. Without this, re-importing a folder of 600 doubles what is held.
    await import_folder(service, tmp_path / "references")

    person = await make_person(temp_db, "Nobody Yet")
    arrived = await service.claim_for(person, "Nobody Yet")
    assert arrived == 1, "adding the person did not claim what the folder was holding"
    assert len(await store.references(person)) == 1
    # A FOLDER's face, and it says so, though the folder's faces are held in the same place a
    # pack's are: the claim is the last moment the difference exists.
    (origin,) = await temp_db.fetch_all(
        "SELECT origin FROM face_references WHERE person_id = ?", (person,)
    )
    assert origin["origin"] == "added"


async def test_a_face_with_no_stored_picture_still_has_its_cover_collected(
    store: Store, picture: Ingested, temp_db: Database
) -> None:
    """A track whose detections have gone, which is what a half-finished pass leaves behind.

    Its cover is named after the track rather than recorded anywhere, so the only thing that knows
    to collect it is the track, and a rescan that skipped it would leak a picture per track.
    """
    track_id = new_id()
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, "
        "created_at) VALUES (?, ?, 0, 0, 1, 0.5, 0)",
        (track_id, picture.asset.id),
    )
    cover = store.cover_path(track_id)
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"a cover")

    assert cover in await store._pictures_of(picture.asset.id)
