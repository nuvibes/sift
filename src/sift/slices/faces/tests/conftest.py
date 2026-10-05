# SPDX-License-Identifier: AGPL-3.0-or-later
"""Standing in for the models, so the feature can be tested without any.

**No model file ships with Sift**, so there is nothing to load in a test and nothing to download in
CI. What is tested here is everything around the models: which frames are read, which faces survive
the quality bar, how many are described, how runs are joined, who gets attributed, what a pack
refuses. The models themselves are two functions (picture in, boxes out; face in, numbers out),
and standing in for them is what makes the rest observable.

The stand-ins **count what they were asked to do**, which is the point. Several of the guarantees
this feature makes are about work NOT happening: a poor face is never described, adding a person
opens no file, nothing at all runs while the feature is off. Those cannot be tested by looking at
the result: only by asking the expensive thing whether it was called.

Faces are drawn into a picture as plain grey squares with darker marks for eyes, nose and mouth.
Nothing here is trying to be a face: the detector is a stand-in, and what a test needs is a picture
where a known thing is at a known place.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import sift.slices.workbench.schema  # noqa: F401  (a scan records a ledger event; the table must exist)
from sift.kernel.access import Repository
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore
from sift.kernel.content.identity_models import Ingested
from sift.kernel.content.library import LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.hardware import HardwareReport
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.faces import runner as faces_runner
from sift.slices.faces import service_weights, weights
from sift.slices.faces import settings as face_settings
from sift.slices.faces.crop import CHIP_SIZE
from sift.slices.faces.models import Box, Description, Detection, Vector
from sift.slices.faces.recognize import recognisability
from sift.slices.faces.service import FaceService
from sift.slices.faces.store import Store

#: How many numbers a stand-in recognizer produces. Small on purpose: the arithmetic is identical
#: at any width and a test that has to read one is easier at eight.
DIMENSION = 8


# --- pictures ------------------------------------------------------------------------------------


def draw_face(frame: np.ndarray, *, x: int, y: int, size: int, shade: int = 200) -> Box:
    """Put a recognizable arrangement into a frame and say where it went."""
    frame[y : y + size, x : x + size] = shade
    eye = max(2, size // 8)
    frame[y + size // 3 : y + size // 3 + eye, x + size // 4 : x + size // 4 + eye] = 20
    frame[y + size // 3 : y + size // 3 + eye, x + 5 * size // 8 : x + 5 * size // 8 + eye] = 20
    frame[y + size // 2 : y + size // 2 + eye, x + size // 2 - eye // 2 : x + size // 2 + eye] = 60
    frame[y + 3 * size // 4 : y + 3 * size // 4 + eye, x + size // 3 : x + 2 * size // 3] = 40
    return Box(x=x, y=y, width=size, height=size)


def landmarks_for(box: Box) -> tuple[tuple[float, float], ...]:
    """The five points, arranged as a front-on face inside a box."""
    return (
        (box.x + box.width * 0.3, box.y + box.height * 0.38),
        (box.x + box.width * 0.7, box.y + box.height * 0.38),
        (box.x + box.width * 0.5, box.y + box.height * 0.55),
        (box.x + box.width * 0.35, box.y + box.height * 0.75),
        (box.x + box.width * 0.65, box.y + box.height * 0.75),
    )


def turned_away(box: Box) -> tuple[tuple[float, float], ...]:
    """The same five points with the nose swung right out to where a full profile puts it.

    `frontality` reads this as 0.0: the nose's offset from the midpoint of the eyes reaches the
    whole of what the measure's scale allows, which is half the eye-to-mouth drop.
    """
    points = list(landmarks_for(box))
    drop = box.height * (0.75 - 0.38)
    nose_x, nose_y = points[2]
    points[2] = (nose_x + 0.5 * drop, nose_y)
    return tuple(points)


def noisy_frame(width: int, height: int, seed: int = 0) -> np.ndarray:
    """A frame with texture in it, so a sharpness measurement is not zero everywhere."""
    rng = np.random.default_rng(seed)
    return rng.integers(40, 210, (height, width, 3), dtype=np.uint8)


# --- the stand-ins -------------------------------------------------------------------------------


@dataclass
class FakeDetector:
    """Reports whatever the test placed, and counts how often it was asked.

    `refine` is separate from `detect` and counted separately, because one of the design claims is
    that the second, closer look happens only for faces about to be described, never for every
    detection.
    """

    placed: dict[int, list[tuple[Box, float]]] = field(default_factory=dict)
    detect_calls: int = 0
    refine_calls: int = 0

    def detect(
        self,
        frame: np.ndarray,
        *,
        timestamp_ms: int = 0,
        threshold: float = 0.5,
        retry: bool = True,
    ) -> list[Detection]:
        self.detect_calls += 1
        points = turned_away if self.coarse_first_look else landmarks_for
        return [
            Detection(
                box=box,
                score=score,
                landmarks=points(box),
                timestamp_ms=timestamp_ms,
            )
            for box, score in self.placed.get(timestamp_ms, [])
        ]

    #: Make the closer look hand back landmarks that are wrong rather than absent: the eyes still
    #: apart, the nose out past one of them, which is what `frontality` reads as zero and what a
    #: failed refinement produces on a real file. Fully collapsed landmarks are a different case
    #: and `align` already refuses those outright.
    ruin_refine: bool = False

    #: Make the FIRST look report landmarks that are wrong and the closer one correct them, which
    #: is the ordinary case rather than a fault. The coarse points are pinned to the grid of a frame
    #: reduced to a 640-pixel square, and `refine` exists precisely because they are not good enough
    #: to judge a face by. A face is placed as though turned right away from the camera and comes
    #: back front-on.
    coarse_first_look: bool = False

    def refine(self, frame: np.ndarray, detection: Detection, *, margin: float = 0.8) -> Detection:
        self.refine_calls += 1
        if self.ruin_refine:
            askew = ((10.0, 10.0), (50.0, 10.0), (50.0, 10.0), (20.0, 40.0), (40.0, 40.0))
            return replace(detection, landmarks=askew)
        if self.coarse_first_look:
            return replace(detection, landmarks=landmarks_for(detection.box))
        return detection


@dataclass
class FakeRecognizer:
    """Turns a face into numbers by where it is, and counts every single call.

    The count is what several tests are actually about: describing a face is the expensive step,
    and the guarantees are about it happening a small fixed number of times rather than once per
    frame.

    Which numbers come back is decided by a rule the test sets, so two faces can be made to look
    like the same person or like different ones deliberately.
    """

    rule: Any = None
    calls: int = 0
    batches: int = 0
    many_calls: int = 0
    """How many times a whole file's faces were described in one ask, where `calls` is how many
    faces were described at all. The two part company on purpose: one is about the shape of the
    call and the other about the work."""
    revision: str = "test-recognizer"
    dimension: int = DIMENSION
    strength_rule: Any = None
    """How firmly this fake answers, as a function of the picture. `None` answers the same every
    time, and that answer is above any ramp, so a test that is not about strength is not quietly
    affected by it."""
    ramp: tuple[float, float] | None = None
    """Left uncalibrated by default, which is a real state a real family can be in: no entry, no
    term, scores exactly as before. A test that is about the ramp sets one."""

    def embed_many(self, chips: Any) -> list[Description]:
        """A file's faces in one run, which is what the real one does.

        `many_calls` counts the runs that went through this door and `calls` counts the faces, so a
        test can hold the per-file path to one ask however many faces the file happens to have:
        a fixture with one face each would make the two counts equal and prove nothing.
        """
        self.many_calls += 1
        return [self._describe(chip) for chip in chips]

    def embed(self, chip: np.ndarray) -> Description:
        return self._describe(chip)

    def _describe(self, chip: np.ndarray) -> Description:
        self.calls += 1
        strength = 1.0 if self.strength_rule is None else float(self.strength_rule(chip))
        if self.rule is not None:
            return Description(vector=unit(self.rule(chip)), strength=strength)
        # Without a rule, describe by the average brightness of the picture, so two crops of the
        # same drawn face agree and two of different shades do not.
        shade = float(chip.mean()) / 255.0
        return Description(
            vector=unit([shade, 1 - shade, *([0.0] * (DIMENSION - 2))]), strength=strength
        )

    def recognisability(self, strength: float) -> float:
        # The real rule, not a second one written here. See `recognize.recognisability`.
        return recognisability(strength, self.ramp)


def unit(values: list[float] | tuple[float, ...]) -> Vector:
    array = np.asarray(values, dtype=np.float32)
    length = float(np.linalg.norm(array))
    return tuple((array / length).tolist()) if length else tuple(array.tolist())


def person_vector(index: int, variant: int = 0) -> Vector:
    """A description that is close to itself and far from every other index.

    One strong direction per person plus a small nudge per variant, so two faces of one person sit
    near each other and two of different people sit nearly at right angles, which is how real
    descriptions behave, and what the thresholds are set against.
    """
    values = [0.0] * DIMENSION
    values[index % DIMENSION] = 1.0
    values[(index + 1) % DIMENSION] = 0.12 * variant
    return unit(values)


@dataclass
class FakePreferences:
    """The settings, in memory, starting at what a fresh install would have."""

    values: dict[str, Any] = field(default_factory=dict)

    async def get_app(self, key: str) -> Any:
        if key in self.values:
            return self.values[key]
        from sift.kernel.settings_registry import get_registered

        declared = get_registered(key)
        if declared is None:
            raise KeyError(key)
        return declared.default

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value


class RecordingReindexer:
    """The search-index seam, standing still and writing down what it was told.

    A stand-in rather than the real one for the reason every other seam here is stood in for: this
    slice may not import the one that owns the index, and what is worth asserting is that it was
    told at all. `touched` names one file whose text changed; `touched_many` names a set. The
    distinction is kept rather than flattened, because the real one is two different amounts of
    work and a test that could not tell them apart would not notice a library-wide sweep being run
    one file at a time.
    """

    def __init__(self) -> None:
        self.touched_ids: list[str] = []
        self.bulk_calls: list[tuple[str, ...]] = []

    async def touched(self, asset_id: str) -> None:
        self.touched_ids.append(asset_id)

    async def touched_many(self, asset_ids: Sequence[str]) -> None:
        self.bulk_calls.append(tuple(asset_ids))
        self.touched_ids.extend(asset_ids)

    async def renamed(self) -> None:  # pragma: no cover (nothing here renames a person)
        raise AssertionError("the face feature never renames anybody")


# --- fixtures ------------------------------------------------------------------------------------


@pytest.fixture
def reindexer() -> RecordingReindexer:
    return RecordingReindexer()


@pytest.fixture
def preferences() -> FakePreferences:
    """Switched ON. Every test that is about the switch turns it back off itself, so the rest do
    not each have to remember to turn it on."""
    return FakePreferences({face_settings.ENABLED_KEY: True})


@pytest.fixture
def detector() -> FakeDetector:
    return FakeDetector()


@pytest.fixture
def recognizer() -> FakeRecognizer:
    return FakeRecognizer()


@pytest.fixture
def hardware() -> HardwareReport:
    return HardwareReport(
        cpu_count=4,
        total_ram_bytes=8 << 30,
        worker_concurrency=3,
        cuda=False,
        rocm=False,
        transcode_encoders=(),
        warnings=(),
    )


@pytest.fixture
async def store(temp_db: Database, settings: Settings) -> Store:
    await temp_db.initialize_schema()
    return Store(temp_db, data_dir=settings.data_dir)


@pytest.fixture
async def service(
    store: Store,
    content_store: ContentStore,
    access: Repository,
    preferences: FakePreferences,
    settings: Settings,
    hardware: HardwareReport,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> FaceService:
    """The real service, with the two models stood in for."""
    # The catalog says the accurate family's recognizer IS the fake standing in for it. Every
    # description a scan stores is stamped with the loaded model's revision, and every read that
    # compares descriptions keeps to the CONFIGURED family's: one value on a real install, and
    # this is what keeps them one value here. Left apart, every scanned file would read as
    # described by another model and matching and grouping would see nothing at all.
    real_pairing = weights.pairing
    accurate_detector, accurate_recognizer = real_pairing("accurate")
    as_the_fake = (
        accurate_detector,
        replace(accurate_recognizer, revision=recognizer.revision, dimension=recognizer.dimension),
    )
    monkeypatch.setattr(
        weights,
        "pairing",
        lambda family: as_the_fake if family == "accurate" else real_pairing(family),
    )
    built = FaceService(
        store=store,
        content=content_store,
        repository=access,
        preferences=preferences,
        settings=settings,
        hardware=hardware,
        reindexer=reindexer,
    )
    # Put in place directly: the loader's job is to find a model file, and there is no model
    # file. What the loader does is tested on its own, next door.
    built._detector = detector  # type: ignore[assignment]
    built._recognizer = recognizer  # type: ignore[assignment]
    built._loaded_family = "accurate"
    built._loaded_device = "cpu"
    return built


@pytest.fixture
async def person(temp_db: Database) -> str:
    """One person who already exists. This feature never creates one."""
    return await make_person(temp_db, "Ada Lovelace")


async def make_person(database: Database, name: str) -> str:
    from sift.kernel.ids import new_id

    person_id = new_id()
    await database.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)",
        (person_id, name),
    )
    return person_id


@pytest.fixture
def chip() -> np.ndarray:
    """One aligned square, with structure in it so a sharpness measurement is not zero."""
    frame = noisy_frame(CHIP_SIZE, CHIP_SIZE, seed=3)
    draw_face(frame, x=8, y=8, size=CHIP_SIZE - 16)
    return frame


@pytest.fixture
def face_root(settings: Settings) -> Iterator[Path]:
    yield settings.data_dir / "faces"


@pytest.fixture(autouse=True)
def models_in_this_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runtime in this process, where the stubs these tests install can reach it.

    The service runs models in a child process; a stub `InferenceSession` set on this process's
    `onnxruntime` never reaches one. The child itself is proved by the kernel's own test, against
    a real model, once.
    """
    monkeypatch.setattr(service_weights, "ChildRunner", faces_runner.Runner)


#: The ingress corpus the kernel's tests keep: real small files a scan would accept.
CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


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
async def other_clip(content_store: ContentStore, library: Root, settings: Settings) -> Ingested:
    """A second, different file. What it takes to ask a question about a BATCH of them."""
    target = Path(str(library.abs_path)) / "other.webm"
    target.write_bytes((CORPUS / "accepted.webm").read_bytes())
    checked = verify_ingress(target, origin=Origin.SCAN, settings=settings)
    return await content_store.ingest(checked, root_id=library.id, rel_path="other.webm")
