# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folder import task: read from its path a person at a time, said on Activity, stopped
without losing what landed."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.content import Grant
from sift.kernel.jobs import JobCanceled, JobFailedPermanently, JobPaused
from sift.slices.faces import folder_import
from sift.slices.faces.folder_import import READING, STAGED_PREFIX, Tally, import_folder
from sift.slices.faces.models import Finding
from sift.slices.faces.references import Candidate, PersonReport
from sift.slices.faces.weights import WeightError

pytestmark = pytest.mark.unit


class Library:
    """The folders Sift has been given."""

    def __init__(self, *given: Path) -> None:
        self.given = [
            Grant(id=f"g{index}", abs_path=str(path.resolve()), granted_at=0)
            for index, path in enumerate(given)
        ]

    async def grants(self) -> list[Grant]:
        return self.given


class Context:
    """The few things the task asks of its job, recorded."""

    def __init__(
        self,
        payload: dict[str, Any],
        *,
        given: Path | None = None,
        cancel_at: int = 0,
        pause: bool = False,
    ):
        self.payload = payload
        self.library = Library(*([given] if given else []))
        self.notes: list[str] = []
        self.progress: list[float] = []
        self.queue = object()
        self._beats = 0
        self._cancel_at = cancel_at
        self._pause = pause
        self.canceled = False

    def require_str(self, key: str, message: str) -> str:
        return str(self.payload[key])

    async def set_note(self, note: str) -> None:
        self.notes.append(note)

    async def set_progress(self, fraction: float) -> None:
        self.progress.append(fraction)

    async def report_progress(self, fraction: float) -> None:
        self.progress.append(fraction)

    async def raise_if_canceled(self) -> None:
        self._beats += 1
        if self._cancel_at and self._beats >= self._cancel_at:
            self._pause = False
            self.canceled = True
            raise JobCanceled("canceled")

    def stopping(self) -> str | None:
        return "pause" if self._pause else "cancel" if self.canceled else None


class Faces:
    """The face service, standing in: each folder is one picture kept, unless named otherwise."""

    def __init__(
        self,
        *,
        on: bool = True,
        reports: dict[str, PersonReport] | None = None,
        scratch: Path | None = None,
        unmodelled: str | None = None,
    ):
        self.on = on
        self.scratch = scratch
        #: The folder whose reading finds the models gone, as a model removed mid-import does.
        self.unmodelled = unmodelled
        self.read: list[str] = []
        self.reports = reports or {}
        #: The folder of people each person's entry was said to come from.
        self.sources: list[str | None] = []

    async def enabled(self) -> bool:
        return self.on

    def scratch_root(self) -> Path:
        assert self.scratch is not None
        return self.scratch

    async def import_person_folder(self, folder: Path, *, source: str | None) -> PersonReport:
        self.read.append(folder.name)
        self.sources.append(source)
        if folder.name == self.unmodelled:
            raise WeightError("the detector model has not been installed yet")
        report = self.reports.get(folder.name)
        if report is None:
            report = PersonReport(name=folder.name, added=1)
            report.candidates = [Candidate(path=folder / "one.jpg", findings=())]
        return report


def gallery(root: Path, *names: str) -> Path:
    for name in names:
        (root / name).mkdir(parents=True)
        (root / name / "one.jpg").write_bytes(b"stand-in")
    return root


def granted(root: Path) -> dict[str, Any]:
    """A payload naming the folder by the grant it is, as the path door queues it."""
    return {"grant": "g0", "within": ""}


def run(context: Context, faces: Faces, asked: list[object] | None = None) -> list[object]:
    asked = [] if asked is None else asked

    async def ask(queue: object) -> None:
        asked.append(queue)

    asyncio.run(import_folder(context, service=faces, ask=ask))  # type: ignore[arg-type]
    return asked


def test_each_person_is_read_in_turn_and_the_end_says_what_was_kept(tmp_path: Path) -> None:
    root = gallery(tmp_path / "Gallery", "Ada Lumen", "Wren Halloway")
    context = Context(granted(root), given=root)
    faces = Faces()

    asked = run(context, faces)

    assert faces.read == ["Ada Lumen", "Wren Halloway"]
    assert context.notes[0] == READING
    assert context.notes[1] == "Importing 1 of 2 people\u2026"
    assert context.notes[-1] == "Kept 2 faces from 2 people."
    assert context.progress[-1] == 1.0
    assert asked == [context.queue], "the pass over facial fingerprints was not asked for"


def test_each_entry_says_the_folder_of_people_it_came_from(tmp_path: Path) -> None:
    """The granted folder's own name, and for an upload the name it was chosen by, never the
    copy's under the cache; an upload of several folders names none."""
    root = gallery(tmp_path / "Gallery", "Ada Lumen", "Wren Halloway")
    faces = Faces()
    run(Context(granted(root), given=root), faces)
    assert faces.sources == ["Gallery", "Gallery"]

    staged = gallery(tmp_path / f"{STAGED_PREFIX}two", "Ada Lumen")
    sent = Faces(scratch=tmp_path)
    run(Context({"staged": staged.name, "named": " Studio Faces "}), sent)
    assert sent.sources == ["Studio Faces"]

    several = gallery(tmp_path / f"{STAGED_PREFIX}three", "Ada Lumen")
    unnamed = Faces(scratch=tmp_path)
    run(Context({"staged": several.name}), unnamed)
    assert unnamed.sources == [None]


def test_nothing_kept_asks_for_no_pass(tmp_path: Path) -> None:
    root = gallery(tmp_path / "Gallery", "Ada Lumen")
    report = PersonReport(name="Ada Lumen", added=0)
    report.candidates = [Candidate(path=Path("one.jpg"), findings=())]

    asked = run(Context(granted(root), given=root), Faces(reports={"Ada Lumen": report}))

    assert asked == []


def test_a_folder_past_the_cap_is_refused_in_words_before_any_face_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(folder_import, "MAX_FOLDER_FILES", 1)
    root = gallery(tmp_path / "Gallery", "Ada Lumen", "Wren Halloway")
    faces = Faces()

    with pytest.raises(JobFailedPermanently, match="Import it in parts"):
        run(Context(granted(root), given=root), faces)

    assert faces.read == []


def test_a_folder_past_the_byte_cap_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(folder_import, "MAX_FOLDER_BYTES", 1)
    root = gallery(tmp_path / "Gallery", "Ada Lumen")

    with pytest.raises(JobFailedPermanently, match="GB of pictures"):
        run(Context(granted(root), given=root), Faces())


def test_a_stopped_import_keeps_what_landed_and_asks_for_the_pass(tmp_path: Path) -> None:
    """Cancelled before the second person: the first is held, and the pass over facial
    fingerprints is still asked for, so what landed is recognized."""
    root = gallery(tmp_path / f"{STAGED_PREFIX}one", "Ada Lumen", "Wren Halloway")
    context = Context({"staged": root.name}, cancel_at=2)
    faces = Faces(scratch=tmp_path)
    asked: list[object] = []

    with pytest.raises(JobCanceled):
        run(context, faces, asked)

    assert faces.read == ["Ada Lumen"]
    assert asked == [context.queue], "what landed before the stop is never recognized"
    assert not root.exists(), "the copy of the upload outlived its task"


def test_a_paused_import_keeps_the_copy_it_reads_from(tmp_path: Path) -> None:
    root = gallery(tmp_path / f"{STAGED_PREFIX}one", "Ada Lumen")

    with pytest.raises(JobPaused):
        run(Context({"staged": root.name}, pause=True), Faces(scratch=tmp_path))

    assert root.exists()


def test_models_gone_part_way_end_the_import_for_good_and_keep_who_landed(tmp_path: Path) -> None:
    """Not a pause: no later run reads faces without the models. The people held still ask for
    the pass, and the upload's copy goes."""
    root = gallery(tmp_path / f"{STAGED_PREFIX}one", "Ada Lumen", "Wren Halloway")
    context = Context({"staged": root.name})
    faces = Faces(scratch=tmp_path, unmodelled="Wren Halloway")
    asked: list[object] = []

    with pytest.raises(JobFailedPermanently, match="detector model has not been installed"):
        run(context, faces, asked)

    assert faces.read == ["Ada Lumen", "Wren Halloway"]
    assert asked == [context.queue], "the person held before the failure is never recognized"
    assert not root.exists(), "the copy of the upload outlived its task"


def test_a_folder_on_the_machine_is_never_removed(tmp_path: Path) -> None:
    """Only the upload's own copy goes: the path door reads somebody's real folder."""
    root = gallery(tmp_path / "Gallery", "Ada Lumen")

    run(Context(granted(root), given=root), Faces())

    assert (root / "Ada Lumen" / "one.jpg").exists()


def test_with_recognition_off_nothing_is_read(tmp_path: Path) -> None:
    root = gallery(tmp_path / "Gallery", "Ada Lumen")
    faces = Faces(on=False)

    with pytest.raises(JobFailedPermanently, match="switched off"):
        run(Context(granted(root), given=root), faces)

    assert faces.read == []


def test_a_folder_given_back_since_the_press_is_not_read(tmp_path: Path) -> None:
    root = gallery(tmp_path / "Gallery", "Ada Lumen")
    faces = Faces()

    with pytest.raises(JobFailedPermanently, match="no longer has the folder"):
        run(Context(granted(root)), faces)

    assert faces.read == []


def test_a_folder_that_leads_out_of_its_grant_is_not_read(tmp_path: Path) -> None:
    """Checked again when the task runs: the payload is ids, and ids are resolved by the task."""
    given = tmp_path / "given"
    given.mkdir()
    gallery(tmp_path / "Gallery", "Ada Lumen")
    faces = Faces()

    with pytest.raises(JobFailedPermanently, match="isn't inside one Sift has been given"):
        run(Context({"grant": "g0", "within": "../Gallery"}, given=given), faces)

    assert faces.read == []


def test_a_staged_name_that_is_not_a_staged_folder_is_refused(tmp_path: Path) -> None:
    with pytest.raises(JobFailedPermanently, match="no folder Sift was sent"):
        run(Context({"staged": "elsewhere"}), Faces(scratch=tmp_path))


def test_a_folder_that_has_gone_is_said_in_words(tmp_path: Path) -> None:
    with pytest.raises(JobFailedPermanently, match="couldn't read the folder"):
        run(Context({"grant": "g0", "within": "gone"}, given=tmp_path), Faces())


def test_the_report_says_the_photos_left_out_and_why_and_the_folders_to_check() -> None:
    """Each picture counted once under its one reason, a near-copy said as kept, and only the
    folders worth going back to named: never one whose photos were already here."""
    tally = Tally()
    thin = PersonReport(name="Ada Lumen", added=1)
    thin.candidates = [
        Candidate(path=Path("a.jpg"), findings=()),
        Candidate(path=Path("b.jpg"), findings=(Finding.TOO_BLURRED,)),
        Candidate(path=Path("c.jpg"), findings=(Finding.NO_FACE,)),
        Candidate(path=Path("d.jpg"), findings=(Finding.NO_FACE,)),
        Candidate(path=Path("e.jpg"), findings=(Finding.NEAR_DUPLICATE,)),
    ]
    tally.add(thin)
    here_already = PersonReport(name="Wren Halloway", added=0)
    here_already.candidates = [Candidate(path=Path("a.jpg"), findings=())]
    tally.add(here_already)
    for index in range(6):
        empty = PersonReport(name=f"Folder {index}")
        empty.candidates = [Candidate(path=Path("x.jpg"), findings=(Finding.UNREADABLE,))]
        tally.add(empty)

    assert tally.said() == (
        "Kept 1 face from 8 people. 9 photos left out: 6 not a readable picture, "
        "2 with no face in it, 1 too blurred. 1 photo kept though almost the same as another. "
        "Check these folders: Ada Lumen, Folder 0, Folder 1, Folder 2, Folder 3 and 2 more."
    )


def test_the_report_groups_the_thousands() -> None:
    tally = Tally(people=1_234, added=12_345)
    assert tally.said() == "Kept 12,345 faces from 1,234 people."
