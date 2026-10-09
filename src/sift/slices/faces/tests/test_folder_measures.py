# SPDX-License-Identifier: AGPL-3.0-or-later
"""A folder of people is measured as a scan measures: in the file's own pixels, against the
library's quality preset, and a face turned past the preset's angle is kept for its person and
never compared with anybody."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.slices.faces import frames
from sift.slices.faces import schema as faces_schema
from sift.slices.faces.folder_import import Tally
from sift.slices.faces.models import Detection, Finding
from sift.slices.faces.pipeline import Bar
from sift.slices.faces.references import Auditor, Candidate, PersonReport, mark_odd_ones_out
from sift.slices.faces.service import FaceService
from sift.slices.faces.store import Store
from sift.slices.faces.tests.conftest import (
    FakeDetector,
    FakeRecognizer,
    draw_face,
    noisy_frame,
    person_vector,
)
from sift.slices.faces.tests.test_schema_baseline import _parents

pytestmark = pytest.mark.integration

#: The middle preset's bar, the one a library is set to unless somebody changed it.
BALANCED = Bar(min_pixels=96, min_sharpness=40.0, min_frontality=0.30)


class TurnedDetector(FakeDetector):
    """The closer look finds the face turned part of the way: large, sharp, whole, past the angle."""

    def refine(self, frame: np.ndarray, detection: Detection, *, margin: float = 0.8) -> Detection:
        self.refine_calls += 1
        box = detection.box
        nose = (box.x + box.width * 0.6665, box.y + box.height * 0.55)
        points = list(detection.landmarks)
        points[2] = nose
        return replace(detection, landmarks=tuple(points))


def _decoding(
    monkeypatch: pytest.MonkeyPatch, reduced: np.ndarray, own: np.ndarray | None
) -> list[object]:
    """The reduced decode, and `own` for any piece asked of the file at its own size."""
    pieces: list[object] = []

    async def decode(path: Path, settings: Settings, **asked: object) -> np.ndarray | None:
        if asked.get("piece") is None:
            return reduced
        pieces.append(asked["piece"])
        return own

    monkeypatch.setattr("sift.slices.faces.frames.decode_image", decode)
    return pieces


async def test_a_face_small_in_the_reduced_decode_is_measured_at_the_files_own_size(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, recognizer: FakeRecognizer
) -> None:
    """A 90-pixel face in a 2,048-pixel decode of a 6,000-pixel photograph is 270 there."""
    reduced = noisy_frame(frames.REFERENCE_LONG_SIDE, 1200, seed=4)
    small = draw_face(reduced, x=900, y=500, size=90)
    left, top, right, bottom = frames.reach(small, reduced.shape[1], reduced.shape[0])
    own = noisy_frame((right - left) * 3, (bottom - top) * 3, seed=5)
    draw_face(own, x=(small.x - left) * 3, y=(small.y - top) * 3, size=270)
    pieces = _decoding(monkeypatch, reduced, own)
    detector = FakeDetector(placed={0: [(small, 0.9)]})

    candidate = await Auditor(settings, detector, recognizer, bar=BALANCED).examine(Path("a.jpg"))  # type: ignore[arg-type]

    assert len(pieces) == 1
    assert candidate.usable
    assert candidate.pixels == 270


async def test_a_picture_no_larger_than_the_decode_is_not_read_again(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, recognizer: FakeRecognizer
) -> None:
    reduced = noisy_frame(800, 600, seed=4)
    small = draw_face(reduced, x=300, y=200, size=90)
    pieces = _decoding(monkeypatch, reduced, None)
    detector = FakeDetector(placed={0: [(small, 0.9)]})

    candidate = await Auditor(settings, detector, recognizer, bar=BALANCED).examine(Path("a.jpg"))  # type: ignore[arg-type]

    assert pieces == []
    assert candidate.findings == (Finding.TOO_SMALL,)


async def test_a_piece_that_cannot_be_read_is_measured_in_the_decode(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, recognizer: FakeRecognizer
) -> None:
    reduced = noisy_frame(frames.REFERENCE_LONG_SIDE, 1200, seed=4)
    small = draw_face(reduced, x=900, y=500, size=90)
    _decoding(monkeypatch, reduced, None)
    detector = FakeDetector(placed={0: [(small, 0.9)]})

    candidate = await Auditor(settings, detector, recognizer, bar=BALANCED).examine(Path("a.jpg"))  # type: ignore[arg-type]

    assert candidate.findings == (Finding.TOO_SMALL,)


async def test_the_size_floor_is_the_presets(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, recognizer: FakeRecognizer
) -> None:
    """100 pixels clears the middle preset's 96 and not the strict 112 the import used to read."""
    frame = noisy_frame(400, 400, seed=2)
    box = draw_face(frame, x=100, y=100, size=100)
    _decoding(monkeypatch, frame, None)
    detector = FakeDetector(placed={0: [(box, 0.9)]})

    preset = await Auditor(settings, detector, recognizer, bar=BALANCED).examine(Path("a.jpg"))  # type: ignore[arg-type]
    strict = await Auditor(settings, detector, recognizer).examine(Path("a.jpg"))  # type: ignore[arg-type]

    assert preset.usable
    assert strict.findings == (Finding.TOO_SMALL,)


async def test_a_turned_face_is_kept_where_asked_and_refused_where_not(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, recognizer: FakeRecognizer
) -> None:
    frame = noisy_frame(400, 400, seed=2)
    box = draw_face(frame, x=80, y=80, size=240)
    _decoding(monkeypatch, frame, None)
    detector = TurnedDetector(placed={0: [(box, 0.9)]})

    kept = await Auditor(
        settings,
        detector,  # type: ignore[arg-type]
        recognizer,  # type: ignore[arg-type]
        bar=BALANCED,
        keep_turned=True,
    ).examine(Path("a.jpg"))
    refused = await Auditor(settings, detector, recognizer, bar=BALANCED).examine(Path("a.jpg"))  # type: ignore[arg-type]

    assert (kept.turned, kept.usable, kept.findings) == (True, True, ())
    assert kept.vector is not None
    assert refused.findings == (Finding.TURNED_AWAY,)


def test_a_turned_face_is_never_the_odd_one_out_nor_counted_toward_the_set() -> None:
    """Compared with nobody: a turned face does not drag the others' likeness down, nor is it
    called somebody else for being turned."""
    same = [
        Candidate(path=Path(f"{i}.jpg"), findings=(), vector=person_vector(1)) for i in range(4)
    ]
    turned = Candidate(path=Path("t.jpg"), findings=(), vector=person_vector(9), turned=True)
    report = PersonReport(name="Bryn Calloway", candidates=[*same, turned])

    mark_odd_ones_out(report)

    assert report.left_out == {}
    assert report.turned == 1


@pytest.fixture
def detector() -> FakeDetector:
    return TurnedDetector()


async def test_an_import_holds_a_turned_face_for_its_person_and_never_compares_it(
    service: FaceService,
    store: Store,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frame = noisy_frame(400, 400, seed=2)
    detector.placed = {0: [(draw_face(frame, x=80, y=80, size=240), 0.9)]}
    _decoding(monkeypatch, frame, None)

    async def encode(chips: list[np.ndarray], settings: Settings) -> list[bytes]:
        return [f"picture-{index}".encode() for index in range(len(chips))]

    monkeypatch.setattr("sift.slices.faces.crop.encode", encode)
    folder = tmp_path / "Bryn Calloway"
    folder.mkdir()
    (folder / "one.jpg").write_bytes(b"stand-in")

    report = await service.import_person_folder(folder, source=None)
    (entry,) = await store.unclaimed_entries()

    assert (report.added, report.turned, report.left_out) == (1, 1, {})
    assert await store.entry_faces(str(entry["id"])) == []
    assert await store.fingerprints_held(recognizer.revision) == []


def test_the_report_says_the_turned_faces_kept() -> None:
    tally = Tally()
    turned = Candidate(path=Path("t.jpg"), findings=(), vector=person_vector(1), turned=True)
    tally.add(PersonReport(name="Bryn Calloway", candidates=[turned], added=1))

    assert "1 photo facing away kept for their person, never matched." in (tally.said())


async def test_a_library_before_44_gains_the_turned_mark_and_twice_is_once(
    temp_db: Database,
) -> None:
    columns = "SELECT name FROM pragma_table_info('pack_entry_faces') WHERE name = 'turned'"
    async with temp_db.write() as connection:
        await _parents(connection)
        await faces_schema.initialize(connection, on_disk=0)
        await connection.execute("ALTER TABLE pack_entry_faces DROP COLUMN turned")

        await faces_schema.initialize(connection, on_disk=43)
        await faces_schema.initialize(connection, on_disk=43)

        assert len(list(await connection.execute_fetchall(columns))) == 1
