# SPDX-License-Identifier: AGPL-3.0-or-later
"""The checks a candidate reference has to pass, and the invariant that is not negotiable.

**Exactly one face per reference image.** None, or more than one, is refused and reported for
whoever supplied it to fix, never silently interpreted by picking the biggest. The whole corpus
rests on a reference being unambiguously the person it is filed under, and a picture with two
people in it cannot be.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from sift.kernel.config import Settings
from sift.slices.faces import references as references_module
from sift.slices.faces import tuning
from sift.slices.faces.models import Box, Finding
from sift.slices.faces.references import Auditor, Candidate, PersonReport, read_sheet
from sift.slices.faces.service import Strength
from sift.slices.faces.service_references import band_of
from sift.slices.faces.tests.conftest import (
    FakeDetector,
    FakeRecognizer,
    draw_face,
    noisy_frame,
    person_vector,
)

pytestmark = pytest.mark.unit


class Pictures:
    """Stands in for the decoder: hands back a picture a test drew, by filename."""

    def __init__(self, by_name: dict[str, np.ndarray | None]) -> None:
        self.by_name = by_name

    async def decode(self, path: Path, settings: Settings, **_: object) -> np.ndarray | None:
        return self.by_name.get(path.name)


@pytest.fixture
def portrait() -> tuple[np.ndarray, Box]:
    frame = noisy_frame(400, 400, seed=2)
    box = draw_face(frame, x=80, y=80, size=240)
    return frame, box


@pytest.fixture
def auditor(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
) -> Auditor:
    monkeypatch.setattr("sift.slices.faces.frames.decode_image", Pictures({}).decode)
    return Auditor(settings, detector, recognizer)  # type: ignore[arg-type]


def with_pictures(monkeypatch: pytest.MonkeyPatch, by_name: dict[str, np.ndarray | None]) -> None:
    monkeypatch.setattr("sift.slices.faces.frames.decode_image", Pictures(by_name).decode)


# --- the invariant ----------------------------------------------------------------------------------


async def test_a_reference_with_no_face_is_refused_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
    auditor: Auditor,
    detector: FakeDetector,
    portrait: tuple[np.ndarray, Box],
) -> None:
    frame, _ = portrait
    with_pictures(monkeypatch, {"empty.jpg": frame})
    detector.placed = {}

    candidate = await auditor.examine(Path("empty.jpg"))

    assert candidate.findings == (Finding.NO_FACE,)
    assert candidate.usable is False


async def test_a_reference_with_two_faces_is_refused_and_says_how_many(
    monkeypatch: pytest.MonkeyPatch,
    auditor: Auditor,
    detector: FakeDetector,
) -> None:
    """Refused rather than resolved by picking one.

    Choosing the biggest would accept an ambiguous reference and would hide the mistake, and the
    mistake is somebody else's face being filed under this person, which then matches confidently
    and for ever.
    """
    frame = noisy_frame(400, 400, seed=3)
    first = draw_face(frame, x=20, y=100, size=140)
    second = draw_face(frame, x=220, y=100, size=140)
    with_pictures(monkeypatch, {"pair.jpg": frame})
    detector.placed = {0: [(first, 0.9), (second, 0.9)]}

    candidate = await auditor.examine(Path("pair.jpg"))

    assert candidate.findings == (Finding.SEVERAL_FACES,)
    assert candidate.detail is not None
    assert "2" in candidate.detail
    assert candidate.usable is False


async def test_the_checks_run_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    auditor: Auditor,
    detector: FakeDetector,
    portrait: tuple[np.ndarray, Box],
) -> None:
    """A folder of thousands is model work per picture; on the loop it stalls every request."""
    frame, box = portrait
    with_pictures(monkeypatch, {"one.jpg": frame})
    detector.placed = {0: [(box, 0.9)]}
    looked = detector.detect
    on_loop: list[bool] = []

    def watched(picture: np.ndarray, **kwargs: object) -> object:
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return looked(picture, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(detector, "detect", watched)

    await auditor.examine(Path("one.jpg"))

    assert on_loop == [False]


async def test_a_starter_picture_that_does_not_decode_is_reported_as_unreadable(
    monkeypatch: pytest.MonkeyPatch, auditor: Auditor
) -> None:
    """Bytes from a stash-box that are no picture get the same finding as a folder's file."""

    async def nothing(_blob: bytes, _settings: Settings) -> None:
        return None

    monkeypatch.setattr("sift.slices.faces.frames.decode_picture_bytes", nothing)

    candidate = await auditor.examine_bytes(b"not a picture", Path("starter picture 1"))

    assert candidate.findings == (Finding.UNREADABLE,)
    assert candidate.usable is False


async def test_an_unreadable_file_is_reported_rather_than_crashing(
    monkeypatch: pytest.MonkeyPatch, auditor: Auditor
) -> None:
    with_pictures(monkeypatch, {"broken.jpg": None})

    candidate = await auditor.examine(Path("broken.jpg"))

    # Its own code: a picture that could not be read is not a picture with nobody in it.
    assert candidate.findings == (Finding.UNREADABLE,)
    assert candidate.detail == "not a readable image"


async def test_a_face_too_small_to_describe_is_refused(
    monkeypatch: pytest.MonkeyPatch, auditor: Auditor, detector: FakeDetector
) -> None:
    frame = noisy_frame(400, 400, seed=4)
    box = draw_face(frame, x=10, y=10, size=20)
    with_pictures(monkeypatch, {"tiny.jpg": frame})
    detector.placed = {0: [(box, 0.9)]}

    candidate = await auditor.examine(Path("tiny.jpg"))

    assert candidate.findings == (Finding.TOO_SMALL,)


async def test_a_face_with_no_detail_in_it_is_refused_as_blurred(
    monkeypatch: pytest.MonkeyPatch, auditor: Auditor, detector: FakeDetector
) -> None:
    """A flat picture has no fine structure, which is exactly what blur destroys."""
    flat = np.full((400, 400, 3), 128, dtype=np.uint8)
    box = Box(x=80, y=80, width=240, height=240)
    with_pictures(monkeypatch, {"flat.jpg": flat})
    detector.placed = {0: [(box, 0.9)]}

    candidate = await auditor.examine(Path("flat.jpg"))

    assert candidate.findings == (Finding.TOO_BLURRED,)


async def test_a_reference_whose_face_is_cut_by_the_edge_is_refused_for_running_off_it(
    monkeypatch: pytest.MonkeyPatch, auditor: Auditor, detector: FakeDetector
) -> None:
    """Named from the measurement that actually failed, and that naming is the whole point.

    A portrait whose subject is cut by the side of the picture is sharp, is large enough, and still
    cannot be aligned: the square the recognizer needs runs off the picture and comes back part
    padding. Reported as "too blurred" (the last of the three) it would send somebody looking
    for a better camera when what they need is more of the person.
    """
    frame = noisy_frame(200, 200, seed=6)
    draw_face(frame, x=110, y=0, size=200)
    # Most of the face is past the right-hand edge, so the features themselves are what is missing.
    box = Box(x=110, y=0, width=200, height=200)
    with_pictures(monkeypatch, {"tight.jpg": frame})
    detector.placed = {0: [(box, 0.9)]}

    candidate = await auditor.examine(Path("tight.jpg"))

    assert candidate.findings == (Finding.RUNS_OFF_EDGE,)
    assert candidate.usable is False


async def test_a_reference_cropped_close_but_whole_is_accepted(
    monkeypatch: pytest.MonkeyPatch, auditor: Auditor, detector: FakeDetector
) -> None:
    """The same check from the other side: a tight headshot is accepted.

    A picture cropped right down to somebody's head has no room above the hairline, so the aligned
    square (which carries a margin the face does not fill) reaches past the top. Scored over the
    whole square that would be a refusal; scored over the face it is not, because every feature the
    recognizer reads is present.
    """
    frame = noisy_frame(200, 200, seed=6)
    draw_face(frame, x=0, y=0, size=200)
    box = Box(x=0, y=0, width=200, height=200)
    with_pictures(monkeypatch, {"close.jpg": frame})
    detector.placed = {0: [(box, 0.9)]}

    candidate = await auditor.examine(Path("close.jpg"))

    assert candidate.findings == ()
    assert candidate.usable is True


async def test_a_good_reference_passes_and_comes_back_described(
    monkeypatch: pytest.MonkeyPatch,
    auditor: Auditor,
    detector: FakeDetector,
    portrait: tuple[np.ndarray, Box],
) -> None:
    frame, box = portrait
    with_pictures(monkeypatch, {"good.jpg": frame})
    detector.placed = {0: [(box, 0.9)]}

    candidate = await auditor.examine(Path("good.jpg"))

    assert candidate.findings == ()
    assert candidate.usable is True
    assert candidate.vector is not None
    assert candidate.chip is not None


# --- the checks that need the whole set ----------------------------------------------------------------


def described(name: str, vector: tuple[float, ...], quality: float = 0.5) -> Candidate:
    return Candidate(path=Path(name), findings=(), vector=vector, quality=quality)


def test_a_near_duplicate_is_reported_but_still_usable() -> None:
    """Redundant rather than wrong: it adds nothing and lengthens every comparison."""
    report = PersonReport(name="Ada")
    report.candidates = [
        described("a.jpg", person_vector(0)),
        described("b.jpg", person_vector(0)),
        described("c.jpg", person_vector(0, variant=4)),
    ]

    references_module.mark_near_duplicates(report)

    assert report.candidates[0].findings == ()
    assert report.candidates[1].findings == (Finding.NEAR_DUPLICATE,)
    assert report.candidates[1].usable is True
    assert report.candidates[2].findings == ()


def test_the_odd_one_out_is_the_check_worth_the_most() -> None:
    """A perfectly good picture of the wrong person.

    Every other check looks at the picture; this one is the only thing that can see that the face in
    it is somebody else, and that is the mistake that quietly poisons a person's matching for ever.
    """
    report = PersonReport(name="Ada")
    report.candidates = [
        described("a.jpg", person_vector(0)),
        described("b.jpg", person_vector(0, variant=1)),
        described("c.jpg", person_vector(0, variant=2)),
        described("d.jpg", person_vector(0, variant=1)),
        described("intruder.jpg", person_vector(4)),
    ]

    references_module.mark_odd_ones_out(report)

    assert report.candidates[-1].findings == (Finding.ODD_ONE_OUT,)
    assert all(item.findings == () for item in report.candidates[:-1])


def test_the_odd_one_out_check_says_nothing_when_there_is_too_little_to_compare() -> None:
    """With two pictures, "unlike the others" is a statement about both and names no intruder."""
    report = PersonReport(name="Ada")
    report.candidates = [
        described("a.jpg", person_vector(0)),
        described("b.jpg", person_vector(5)),
    ]

    references_module.mark_odd_ones_out(report)

    assert all(item.findings == () for item in report.candidates)


# --- the tally: every picture in exactly one bucket ------------------------------------------------


async def test_a_near_duplicate_is_counted_as_used_and_never_as_left_out(
    monkeypatch: pytest.MonkeyPatch,
    auditor: Auditor,
    detector: FakeDetector,
    recognizer: FakeRecognizer,
    portrait: tuple[np.ndarray, Box],
    tmp_path: Path,
) -> None:
    """Read = used + left out, with a near-copy among the used.

    Counted with the refusals too, the near-copies (which are used) would make "left out" larger
    than the difference between "read" and "used".
    """
    frame, box = portrait
    folder = tmp_path / "Esme Wrenfield"
    folder.mkdir()
    for name in ("a.jpg", "b.jpg", "broken.jpg", "c.jpg"):
        (folder / name).write_bytes(b"stand-in")
    # `broken.jpg` decodes to nothing, so it is left out as unreadable and never described.
    with_pictures(monkeypatch, {"a.jpg": frame, "b.jpg": frame, "c.jpg": frame})
    detector.placed = {0: [(box, 0.9)]}
    # Described in name order: `b` all but identical to `a`, `c` plainly her but different. Three
    # described pictures, so the odd-one-out check (which needs four) stays out of this count.
    descriptions = iter([person_vector(0), person_vector(0), person_vector(0, variant=4)])
    recognizer.rule = lambda chip: next(descriptions)

    report = await auditor.person(folder)

    assert [item.findings for item in report.candidates] == [
        (),
        (Finding.NEAR_DUPLICATE,),
        (Finding.UNREADABLE,),
        (),
    ]
    assert len(report.usable) == 3
    assert report.near_duplicates == 1
    assert report.left_out == {Finding.UNREADABLE: 1}
    assert len(report.candidates) == len(report.usable) + report.left_out.total()


def test_a_picture_with_two_findings_is_left_out_once_for_the_one_that_refuses_it() -> None:
    """A near-copy is still looked at by the odd-one-out check, and can be marked by it too."""
    report = PersonReport(name="Esme Wrenfield")
    report.candidates = [
        described("a.jpg", person_vector(0)),
        Candidate(
            path=Path("b.jpg"),
            findings=(Finding.NEAR_DUPLICATE, Finding.ODD_ONE_OUT),
            vector=person_vector(4),
        ),
        Candidate(path=Path("c.jpg"), findings=(Finding.TOO_BLURRED,)),
    ]

    assert report.candidates[1].left_out_for is Finding.ODD_ONE_OUT
    assert report.candidates[1].usable is False
    assert report.near_duplicates == 0, "a refused near-copy was not kept, so it is not a note"
    assert report.left_out == {Finding.ODD_ONE_OUT: 1, Finding.TOO_BLURRED: 1}
    assert len(report.candidates) == len(report.usable) + report.left_out.total()


def test_marking_a_near_duplicate_keeps_the_size_the_face_was_measured_at() -> None:
    """A near-copy is still imported, so what was measured about it has to survive the marking."""
    report = PersonReport(name="Esme Wrenfield")
    report.candidates = [
        Candidate(path=Path("a.jpg"), findings=(), vector=person_vector(0), pixels=180),
        Candidate(path=Path("b.jpg"), findings=(), vector=person_vector(0), pixels=150),
    ]

    references_module.mark_near_duplicates(report)

    assert report.candidates[1].findings == (Finding.NEAR_DUPLICATE,)
    assert report.candidates[1].pixels == 150


async def test_a_person_with_too_few_pictures_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    auditor: Auditor,
    detector: FakeDetector,
    portrait: tuple[np.ndarray, Box],
    tmp_path: Path,
) -> None:
    frame, box = portrait
    folder = tmp_path / "Ada Lovelace"
    folder.mkdir()
    names = []
    for index in range(3):
        name = f"{index}.jpg"
        (folder / name).write_bytes(b"stand-in")
        names.append(name)
    with_pictures(monkeypatch, dict.fromkeys(names, frame))
    detector.placed = {0: [(box, 0.9)]}

    report = await auditor.person(folder)

    assert Finding.BELOW_MINIMUM in report.findings
    assert len(report.usable) == 3
    assert len(report.usable) < tuning.MIN_REFERENCES


async def test_files_that_are_not_pictures_are_ignored_rather_than_reported(
    monkeypatch: pytest.MonkeyPatch,
    auditor: Auditor,
    detector: FakeDetector,
    portrait: tuple[np.ndarray, Box],
    tmp_path: Path,
) -> None:
    """People keep notes and stray files beside their pictures; that is not a finding."""
    frame, box = portrait
    folder = tmp_path / "Ada Lovelace"
    folder.mkdir()
    (folder / "one.jpg").write_bytes(b"stand-in")
    (folder / "notes.txt").write_text("where these came from")
    (folder / "subfolder").mkdir()
    with_pictures(monkeypatch, {"one.jpg": frame})
    detector.placed = {0: [(box, 0.9)]}

    report = await auditor.person(folder)

    assert [item.path.name for item in report.candidates] == ["one.jpg"]


async def test_a_parent_folder_is_read_as_one_person_per_folder(
    monkeypatch: pytest.MonkeyPatch,
    auditor: Auditor,
    detector: FakeDetector,
    portrait: tuple[np.ndarray, Box],
    tmp_path: Path,
) -> None:
    frame, box = portrait
    for name in ("Ada Lovelace", "Grace Hopper"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "one.jpg").write_bytes(b"stand-in")
    with_pictures(monkeypatch, {"one.jpg": frame})
    detector.placed = {0: [(box, 0.9)]}

    reports = await auditor.folder(tmp_path)

    assert [report.name for report in reports] == ["Ada Lovelace", "Grace Hopper"]


# --- the optional spreadsheet ---------------------------------------------------------------------------


def test_the_spreadsheet_is_read_by_column_name_and_tolerates_extra_columns(
    tmp_path: Path,
) -> None:
    """Nobody hand-writes a manifest. The only things worth asking a person for are the other names
    somebody is known by and where their content is, and those come from a spreadsheet that already
    exists in exactly this shape."""
    (tmp_path / "people.csv").write_text(
        "Status,Name,Aliases,Channel URL,Social URL,Note\n"
        'Verified,Ada Lovelace,"ada l, countess",https://example.test/ada,https://social.test/ada,ok\n'
        "Draft,Grace Hopper,,,,\n"
    )

    sheet = read_sheet(tmp_path)

    assert sheet.aliases["Ada Lovelace"] == ("ada l", "countess")
    assert sheet.links["Ada Lovelace"] == (
        "https://example.test/ada",
        "https://social.test/ada",
    )
    assert sheet.aliases["Grace Hopper"] == ()


def test_no_spreadsheet_is_perfectly_valid(tmp_path: Path) -> None:
    sheet = read_sheet(tmp_path)

    assert sheet.aliases == {}
    assert sheet.links == {}


def test_a_spreadsheet_with_no_name_column_is_ignored_rather_than_guessed_at(
    tmp_path: Path,
) -> None:
    (tmp_path / "people.csv").write_text("Who,Aliases\nAda,ada l\n")

    sheet = read_sheet(tmp_path)

    assert sheet.aliases == {}


# --- how strong a person's recognition is, and saying so -----------------------------------------
#
# Matching compares a new face against every reference somebody has, so a person with three of them
# under-matches, correctly, quietly, and with nothing anywhere to say why they are never
# recognized. After six hundred People are imported from folders, the ones with two usable photos
# look exactly like the ones with fifty.


def strength(references: int) -> Strength:
    return Strength(
        references=references,
        target=tuning.GOOD_REFERENCES,
        floor=tuning.MIN_REFERENCES,
        strong=tuning.STRONG_REFERENCES,
    )


def test_somebody_with_no_reference_faces_cannot_be_recognized_at_all() -> None:
    assert strength(0).verdict == "none"
    assert strength(0).fraction == 0.0


def test_a_chooser_bands_a_count_of_confirmed_faces_on_the_measured_curve() -> None:
    """A list drawing many people from counts alone bands them where the curve turns: a coin toss
    under five, working to ten, dependable to twenty, flat after."""
    assert band_of(0) == "none"
    assert band_of(tuning.MIN_REFERENCES - 1) == "weak"
    assert band_of(tuning.MIN_REFERENCES) == "fair"
    assert band_of(tuning.STRONG_REFERENCES) == "good"
    assert band_of(tuning.GOOD_REFERENCES) == "strong"
    assert band_of(200) == "strong"


def test_the_three_thresholds_are_the_measured_curve_and_stay_in_order() -> None:
    """Five is where somebody stops being a coin toss, ten is dependable, twenty is flat after.
    Out of order they would produce a band that can never be reached."""
    assert tuning.MIN_REFERENCES == 5
    assert tuning.STRONG_REFERENCES == 10
    assert tuning.GOOD_REFERENCES == 20


def test_the_bar_is_the_rate_however_many_pictures_there_are() -> None:
    """Two hundred references are not two hundred percent: the bar is a share of her faces."""
    assert strength(200).fraction == 0.0
    assert strength(200).verdict == "unseen"
