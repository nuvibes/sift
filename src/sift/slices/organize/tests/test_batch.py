# SPDX-License-Identifier: AGPL-3.0-or-later
"""Renaming a batch of files from one template: the plan, carrying it out, and taking it back.

The plan is pure and is tested as a function. Carrying it out is tested against real files in a
real library, because what matters is where the bytes are afterwards and that nothing was written
over.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from sift.kernel import naming as kernel_naming
from sift.kernel.access import Role, Viewer
from sift.kernel.content import ContentStore, Location, LocationStatus
from sift.kernel.db import Database
from sift.kernel.naming import TOKENS, fill
from sift.kernel.workbench import Recorded, Reversal
from sift.slices.organize.batch import (
    KEPT_SINCE,
    QUEUE,
    WORDS,
    BatchRenamer,
    BatchRenameReceipts,
    library_facts,
    plan,
    undone_in_part,
)
from sift.slices.organize.service import Organizer, Place

from .conftest import Library


class _ReadsFacts:
    """The one thing `library_facts` asks of the organizer: the kernel's naming facts."""

    def __init__(self, content: ContentStore) -> None:
        self._content = content

    async def naming_facts(self, asset_ids: Sequence[str]) -> dict[str, dict[str, object]]:
        return await self._content.naming_facts(asset_ids)


pytestmark = pytest.mark.anyio

HERE = Path("/library/folder")


def _place(asset_id: str, filename: str, directory: Path = HERE) -> Place:
    location = Location(
        id=f"loc-{asset_id}",
        asset_id=asset_id,
        root_id="root",
        folder_id=None,
        rel_path=filename,
        filename=filename,
        size_bytes=None,
        mtime=None,
        status=LocationStatus.PRESENT,
        first_seen_at=0,
        last_seen_at=0,
    )
    return Place(location=location, directory=directory, remote=False)


def _states(rows: Sequence[Any]) -> list[tuple[str, str]]:
    return [(row.after, row.state) for row in rows]


# --- the plan --------------------------------------------------------------------------------


def test_the_words_offered_for_library_files_are_the_kernels_words() -> None:
    """One set of words for both features: a word offered here that the kernel does not fill would
    be typed in and left as typed."""
    assert set(WORDS) == set(TOKENS)


def test_a_name_on_disk_takes_the_next_number_when_clashes_are_numbered() -> None:
    places = [("a", _place("a", "one.mp4")), ("b", _place("b", "two.mp4"))]
    stems = {"a": "holiday", "b": "holiday"}
    on_disk = {HERE: {"one.mp4", "two.mp4", "holiday.mp4"}}

    planned = plan(places, stems, on_disk, on_clash="number")

    assert _states(planned.rows) == [("holiday-1.mp4", "numbered"), ("holiday-2.mp4", "numbered")]


def test_a_clash_left_alone_says_whether_the_disk_or_the_batch_holds_the_name() -> None:
    places = [
        ("a", _place("a", "one.mp4")),
        ("b", _place("b", "two.mp4")),
        ("c", _place("c", "three.mp4")),
    ]
    stems = {"a": "beach", "b": "beach", "c": "taken"}
    on_disk = {HERE: {"one.mp4", "two.mp4", "three.mp4", "taken.mp4"}}

    planned = plan(places, stems, on_disk, on_clash="skip")

    assert _states(planned.rows) == [
        ("beach.mp4", "renamed"),
        ("two.mp4", "twice"),
        ("three.mp4", "taken"),
    ]


def test_a_name_given_up_earlier_in_the_batch_is_free_for_a_later_file() -> None:
    """Renumbering a run: the second file takes the name the first has just left."""
    places = [("a", _place("a", "x - 2.mp4")), ("b", _place("b", "x - 3.mp4"))]
    stems = {"a": "x - 1", "b": "x - 2"}
    on_disk = {HERE: {"x - 2.mp4", "x - 3.mp4"}}

    planned = plan(places, stems, on_disk, on_clash="skip")

    assert _states(planned.rows) == [("x - 1.mp4", "renamed"), ("x - 2.mp4", "renamed")]


def test_a_name_that_differs_only_in_case_or_is_empty_keeps_the_file_as_it_is() -> None:
    places = [("a", _place("a", "Beach.mp4")), ("b", _place("b", "two.mp4"))]
    planned = plan(places, {"a": "beach", "b": ""}, {HERE: set()}, on_clash="number")
    assert [row.state for row in planned.rows] == ["same", "same"]
    assert planned.moving == 0


def test_a_file_that_cannot_be_renamed_is_a_row_with_its_reason() -> None:
    places: list[tuple[str, Place | str]] = [("a", "Only an admin can rename or move files.")]
    planned = plan(places, {}, {}, on_clash="number")
    assert planned.rows[0].state == "refused"
    assert planned.rows[0].reason == "Only an admin can rename or move files."


# --- the facts -------------------------------------------------------------------------------


async def test_date_is_the_day_the_file_was_added_and_n_counts_the_batch(
    temp_db: Database,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kernel_naming, "_DEVICE_ZONE", UTC)
    first = await add_file(managed, "first.mp4")
    second = await add_file(managed, "second.mkv", source="accepted.mkv")
    added = datetime(2024, 3, 9, 12, 0, tzinfo=UTC)
    await temp_db.execute(
        "UPDATE assets SET added_at = ?, title = ? WHERE id = ?",
        (int(added.timestamp()), "A title", second.asset.id),
    )
    placed = [
        (second.asset.id, _place(second.asset.id, "second.mkv")),
        (first.asset.id, _place(first.asset.id, "first.mp4")),
    ]

    facts = await library_facts(_ReadsFacts(content_store), placed)

    assert (
        fill("{n} {date} {title} {name}", facts[second.asset.id]) == "1 2024-03-09 A title second"
    )
    assert fill("{title}", facts[second.asset.id]) == "A title"
    assert fill("{n} {name}", facts[first.asset.id]) == "2 first"


# --- carrying it out and taking it back ------------------------------------------------------


class _Recorder:
    """Keeps what a receipt would say. The receipt's own table is the workbench's to test."""

    def __init__(self) -> None:
        self.said: list[dict[str, Any]] = []

    async def record_on(self, connection: Any, **said: Any) -> str:
        self.said.append(said)
        return f"receipt-{len(self.said)}"


async def _nothing(ids: Sequence[str]) -> None:
    return None


async def test_a_batch_renames_every_file_and_writes_one_receipt_that_undoes_it_backwards(
    organizer: Organizer,
    temp_db: Database,
    content_store: ContentStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    one = await add_file(managed, "one.mp4")
    two = await add_file(managed, "two.mkv", source="accepted.mkv")
    three = await add_file(managed, "three.webm", source="accepted.webm")
    recorder = _Recorder()
    renamer = BatchRenamer(organizer, temp_db, recorder, touched=_nothing)
    ids = [one.asset.id, two.asset.id, three.asset.id]

    applied = await renamer.apply(ids, "Trip {n}", on_clash="number", actor=admin)

    assert applied.renamed == 3
    assert sorted(path.name for path in managed.path.iterdir()) == [
        "Trip 1.mp4",
        "Trip 2.mkv",
        "Trip 3.webm",
    ]
    assert len(recorder.said) == 1
    receipt = recorder.said[0]
    assert receipt["queue"] == QUEUE
    assert len(json.loads(receipt["payload"])["moves"]) == 3

    # One of them renamed again since: it keeps its newer name, and the other two go back.
    await organizer.rename(two.asset.id, new_name="kept.mkv", actor=admin)
    receipts = BatchRenameReceipts(organizer, touched=_nothing)
    # The answer says it went back in part: two of three, and why the third stayed.
    assert await receipts.reverse(admin, "receipt-1", receipt["payload"]) == Reversal(
        put_back=2,
        of=3,
        said="Put back 2 of 3 names. The others keep the names they have now, " + KEPT_SINCE + ".",
    )

    assert sorted(path.name for path in managed.path.iterdir()) == [
        "kept.mkv",
        "one.mp4",
        "three.webm",
    ]


async def test_a_name_taken_on_disk_is_never_written_over(
    organizer: Organizer, temp_db: Database, managed: Library, add_file: Any, admin: Viewer
) -> None:
    one = await add_file(managed, "one.mp4")
    (managed.path / "Beach.mp4").write_bytes(b"somebody else's")
    renamer = BatchRenamer(organizer, temp_db, _Recorder(), touched=_nothing)

    skipped = await renamer.apply([one.asset.id], "Beach", on_clash="skip", actor=admin)
    numbered = await renamer.apply([one.asset.id], "Beach", on_clash="number", actor=admin)

    assert skipped.renamed == 0
    assert numbered.renamed == 1
    assert (managed.path / "Beach.mp4").read_bytes() == b"somebody else's"
    assert (managed.path / "Beach-1.mp4").exists()


async def test_a_guest_is_planned_nothing(
    organizer: Organizer, temp_db: Database, managed: Library, add_file: Any, guest: Viewer
) -> None:
    one = await add_file(managed, "one.mp4")
    renamer = BatchRenamer(organizer, temp_db, _Recorder(), touched=_nothing)

    planned = await renamer.plan([one.asset.id], "{n}", on_clash="number", actor=guest)

    assert [row.state for row in planned.rows] == ["refused"]
    assert (managed.path / "one.mp4").exists()


async def test_undo_walks_a_renumbered_run_backwards_so_each_name_is_free_when_it_is_wanted(
    organizer: Organizer, temp_db: Database, managed: Library, add_file: Any, admin: Viewer
) -> None:
    """The second file took the name the first gave up. Put back first-to-last, the first file's
    old name is still the second file's, and the undo would refuse it."""
    one = await add_file(managed, "x - 2.mp4")
    two = await add_file(managed, "x - 3.mp4", source="accepted_png_wearing_mp4.mp4")
    recorder = _Recorder()
    renamer = BatchRenamer(organizer, temp_db, recorder, touched=_nothing)
    places = await organizer.places([one.asset.id, two.asset.id], actor=admin)
    first, second = places[one.asset.id], places[two.asset.id]
    assert isinstance(first, Place) and isinstance(second, Place)
    run = plan(
        [(one.asset.id, first), (two.asset.id, second)],
        {one.asset.id: "x - 1", two.asset.id: "x - 2"},
        {first.directory: {"x - 2.mp4", "x - 3.mp4"}},
        on_clash="skip",
    )
    await renamer.carry_out(run, "{name}", actor=admin)
    assert sorted(path.name for path in managed.path.iterdir()) == ["x - 1.mp4", "x - 2.mp4"]

    receipts = BatchRenameReceipts(organizer, touched=_nothing)
    assert await receipts.reverse(admin, "receipt-1", recorder.said[0]["payload"]) == Reversal(
        put_back=2, of=2
    )

    assert sorted(path.name for path in managed.path.iterdir()) == ["x - 2.mp4", "x - 3.mp4"]


def test_an_undo_says_nothing_extra_when_every_name_went_back() -> None:
    """The line is for an undo that went back in part or not at all; a whole one says nothing."""
    assert undone_in_part(3, 3) is None
    assert undone_in_part(0, 3) == "None of the 3 names went back, " + KEPT_SINCE + "."
    assert undone_in_part(1_200, 1_500) is not None
    assert "1,200 of 1,500 names" in (undone_in_part(1_200, 1_500) or "")


def test_the_line_says_at_once_only_for_more_than_one_file() -> None:
    """One file renamed is not a batch, so its line leaves "together" out."""
    receipts = BatchRenameReceipts(None, touched=_nothing)  # type: ignore[arg-type]

    def line(moves: list[str]) -> str:
        recorded = Recorded(
            id="r",
            queue="batch-rename",
            payload=json.dumps({"moves": moves}),
            title="",
            detail="",
            decided_at=0,
        )
        worded = receipts.worded(recorded)
        assert worded is not None
        return "".join(part for part in worded.said if isinstance(part, str))

    assert line(["m1"]).endswith("renamed 1 file")
    assert line(["m1", "m2", "m3"]).endswith("renamed 3 files together")


# --- the plan's own refusals and clashes ----------------------------------------------------------


def test_a_name_the_template_made_that_no_disk_could_hold_is_refused_with_why() -> None:
    planned = plan(
        [("a", _place("a", "clip.mp4"))], {"a": "bad\x00name"}, {HERE: set()}, on_clash="number"
    )
    assert planned.rows[0].state == "refused"
    assert planned.rows[0].reason
    assert planned.rows[0].after == "clip.mp4"


def test_numbering_that_reaches_the_files_own_name_keeps_it() -> None:
    """`Beach` is taken and the file is already `Beach-1`: the first free number is its own name,
    so it stays as it is rather than moving to `Beach-2`."""
    planned = plan(
        [("a", _place("a", "Beach-1.mp4"))],
        {"a": "Beach"},
        {HERE: {"beach.mp4", "beach-1.mp4"}},
        on_clash="number",
    )
    assert _states(planned.rows) == [("Beach-1.mp4", "same")]


def test_a_name_with_every_number_taken_is_left_as_a_clash() -> None:
    from sift.kernel.naming import MOST_COLLISIONS, numbered

    held = {
        "beach.mp4",
        *(f"{numbered('beach', nth)}.mp4" for nth in range(1, MOST_COLLISIONS + 1)),
    }
    planned = plan(
        [("a", _place("a", "clip.mp4"))], {"a": "Beach"}, {HERE: held}, on_clash="number"
    )
    assert _states(planned.rows) == [("clip.mp4", "taken")]


def test_a_folder_that_cannot_be_listed_holds_no_names(tmp_path: Path) -> None:
    """A folder gone between the plan's reads holds nothing to clash with; the rename itself is
    what then refuses, file by file."""
    from sift.slices.organize.batch import _listing

    assert _listing(tmp_path / "not-here") == set()
    (tmp_path / "Clip.MP4").write_bytes(b"x")
    assert _listing(tmp_path) == {"clip.mp4"}


# --- where each file of a batch sits, and why one cannot move --------------------------------------


async def test_each_file_a_batch_cannot_rename_is_planned_with_its_own_reason(
    organizer: Organizer,
    temp_db: Database,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sift.kernel.library_write import LibraryWriteRefused, check_folder_may_change
    from sift.kernel.reach import OUT_OF_REACH
    from sift.slices.organize import service as organize_service

    twice = await add_file(managed, "twice.mp4")
    await add_file(managed, "copy/twice.mp4")
    packed = await add_file(managed, "packed.mp4", source="accepted.mkv")
    gone = await add_file(managed, "gone.mp4", source="accepted.webm")
    shut = await add_file(managed, "shut/clip.mp4", source="accepted_png_wearing_mp4.mp4")
    await temp_db.execute(
        "UPDATE asset_locations SET archive_rel_path = 'pack.zip', member_path = 'packed.mp4'"
        " WHERE asset_id = ?",
        (packed.asset.id,),
    )
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (gone.asset.id,)
    )
    real = check_folder_may_change

    async def refuse_shut(directory: Path) -> None:
        if directory.name == "shut":
            raise LibraryWriteRefused("Sift is not allowed to write to that folder.")
        await real(directory)

    monkeypatch.setattr(organize_service, "check_folder_may_change", refuse_shut)
    ids = [twice.asset.id, packed.asset.id, gone.asset.id, shut.asset.id, "not-a-file"]

    places = await organizer.places(ids, actor=admin)

    assert places == {
        twice.asset.id: (
            "This file is in more than one folder, so a batch leaves it alone. Rename it from its"
            " own page."
        ),
        packed.asset.id: "This picture is inside an archive, so it keeps its name.",
        gone.asset.id: "Sift can't find that file where it expects it to be.",
        shut.asset.id: "Sift is not allowed to write to that folder.",
        "not-a-file": OUT_OF_REACH,
    }


async def test_a_batch_in_a_library_folder_that_has_left_is_refused_once_for_every_file(
    organizer: Organizer,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one = await add_file(managed, "one.mp4")
    two = await add_file(managed, "two.mkv", source="accepted.mkv")
    asked: list[str] = []

    async def gone(root_id: str) -> None:
        asked.append(root_id)
        return None

    monkeypatch.setattr(organizer._library, "get_root", gone)

    places = await organizer.places([one.asset.id, two.asset.id], actor=admin)

    assert {type(one) for one in places.values()} == {str}
    assert len(set(places.values())) == 1
    said = places[one.asset.id]
    assert isinstance(said, str) and "no longer part of your library" in said
    assert len(asked) == 1


# --- a batch as a task, a file refused on the way, and a batch taken back twice ---------------------


class _Queue:
    def __init__(self) -> None:
        self.queued: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def enqueue(self, job_type: str, payload: dict[str, Any], **options: Any) -> str:
        self.queued.append((job_type, payload, options))
        return "job-1"


class _Task:
    """The job's context as the batch task reads it, stopping after `stop_after` files."""

    def __init__(self, payload: dict[str, Any], *, stop_after: int | None = None) -> None:
        self.payload = payload
        self.notes: list[str] = []
        self.units: list[int] = []
        self.reported: list[float] = []
        self._stop_after = stop_after

    def require_str(self, key: str, why: str) -> str:
        value = self.payload.get(key)
        assert isinstance(value, str), why
        return value

    async def set_note(self, note: str) -> None:
        self.notes.append(note)

    async def set_units(self, units: int) -> None:
        self.units.append(units)

    async def report_progress(self, fraction: float) -> None:
        self.reported.append(fraction)

    def stopping(self) -> str | None:
        stop = self._stop_after is not None and len(self.reported) >= self._stop_after
        return "paused" if stop else None


async def test_a_batch_in_a_folder_served_from_another_machine_is_handed_to_a_task_once(
    organizer: Organizer,
    temp_db: Database,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rename over the network is not waited for. The task carries the template as filled and
    runs once: a second attempt would fill it over names the first already gave."""
    from sift.slices.organize import service as organize_service
    from sift.slices.organize.batch import RENAME_BATCH

    monkeypatch.setattr(organize_service, "is_remote", lambda _directory: True)
    one = await add_file(managed, "one.mp4")
    queue = _Queue()
    renamer = BatchRenamer(
        organizer,
        temp_db,
        _Recorder(),
        touched=_nothing,
        queue=queue,  # type: ignore[arg-type]
    )

    applied = await renamer.apply(
        [one.asset.id, one.asset.id], "Trip {n}", on_clash="skip", actor=admin
    )

    assert applied.job_id == "job-1" and applied.renamed == 0
    ((job_type, payload, options),) = queue.queued
    assert job_type == RENAME_BATCH
    assert payload == {
        "actor_id": admin.id,
        "asset_ids": [one.asset.id],
        "template": "Trip {n}",
        "on_clash": "skip",
    }
    assert options == {"max_attempts": 1, "requested_by": admin.id}
    assert (managed.path / "one.mp4").exists()


async def test_the_task_keeps_what_it_did_when_stopped_and_says_so(
    organizer: Organizer,
    temp_db: Database,
    access: Any,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    from sift.slices.organize.batch import rename_batch

    files = [
        await add_file(managed, name, source=source)
        for name, source in (
            ("one.mp4", "accepted.mp4"),
            ("two.mkv", "accepted.mkv"),
            ("three.webm", "accepted.webm"),
        )
    ]
    recorder = _Recorder()
    renamer = BatchRenamer(organizer, temp_db, recorder, touched=_nothing)
    task = _Task(
        {
            "actor_id": admin.id,
            "asset_ids": [one.asset.id for one in files] + [7],
            "template": "Trip {n}",
            "on_clash": "number",
        },
        stop_after=1,
    )

    await rename_batch(task, renamer=renamer, access=access)  # type: ignore[arg-type]

    assert task.units == [3]
    assert task.notes == ["Renamed 1 file. 2 kept their names."]
    assert sorted(path.name for path in managed.path.iterdir()) == [
        "Trip 1.mp4",
        "three.webm",
        "two.mkv",
    ]
    assert len(json.loads(recorder.said[0]["payload"])["moves"]) == 1


async def test_a_task_for_somebody_no_longer_here_renames_nothing(
    organizer: Organizer, temp_db: Database, access: Any, managed: Library, add_file: Any
) -> None:
    from sift.slices.organize.batch import rename_batch

    one = await add_file(managed, "one.mp4")
    renamer = BatchRenamer(organizer, temp_db, _Recorder(), touched=_nothing)
    task = _Task({"actor_id": "nobody", "asset_ids": one.asset.id, "template": "{n}"})

    await rename_batch(task, renamer=renamer, access=access)  # type: ignore[arg-type]

    assert task.notes == ["Whoever asked for this no longer exists."]
    assert (managed.path / "one.mp4").exists()


async def test_a_file_gone_from_the_disk_since_the_plan_is_kept_out_with_the_reason(
    organizer: Organizer, temp_db: Database, managed: Library, add_file: Any, admin: Viewer
) -> None:
    one = await add_file(managed, "one.mp4")
    two = await add_file(managed, "two.mkv", source="accepted.mkv")
    renamer = BatchRenamer(organizer, temp_db, _Recorder(), touched=_nothing)
    planned = await renamer.plan(
        [one.asset.id, two.asset.id], "Trip {n}", on_clash="number", actor=admin
    )
    (managed.path / "one.mp4").unlink()

    applied = await renamer.carry_out(planned, "Trip {n}", actor=admin)

    assert (applied.renamed, applied.skipped) == (1, 1)
    assert applied.reason is not None and "couldn't move that file" in applied.reason
    assert (managed.path / "Trip 2.mkv").exists()


async def test_a_batch_taken_back_twice_puts_nothing_back_the_second_time(
    organizer: Organizer, temp_db: Database, managed: Library, add_file: Any, admin: Viewer
) -> None:
    one = await add_file(managed, "one.mp4")
    recorder = _Recorder()
    renamer = BatchRenamer(organizer, temp_db, recorder, touched=_nothing)
    await renamer.apply([one.asset.id], "Trip", on_clash="number", actor=admin)
    told: list[Sequence[str]] = []

    async def touched(ids: Sequence[str]) -> None:
        told.append(ids)

    receipts = BatchRenameReceipts(organizer, touched=touched)
    payload = recorder.said[0]["payload"]

    assert (await receipts.reverse(admin, "receipt-1", payload)).put_back == 1
    again = await receipts.reverse(admin, "receipt-1", payload)

    assert (again.put_back, again.of) == (0, 1)
    assert told == [[one.asset.id]]


def test_a_receipt_with_no_moves_has_no_line_of_its_own() -> None:
    receipts = BatchRenameReceipts(None, touched=_nothing)  # type: ignore[arg-type]
    for payload in ("{}", '{"moves": "m1"}', '{"moves": ["", 3]}'):
        recorded = Recorded(
            id="r", queue="batch-rename", payload=payload, title="", detail="", decided_at=0
        )
        assert receipts.worded(recorded) is None


async def test_the_registered_task_renames_the_whole_batch_and_says_how_many(
    organizer: Organizer,
    temp_db: Database,
    access: Any,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    from sift.kernel.jobs import registered_handlers
    from sift.slices.organize.batch import RENAME_BATCH, register_handlers

    one = await add_file(managed, "one.mp4")
    two = await add_file(managed, "two.mkv", source="accepted.mkv")
    register_handlers(
        organizer=organizer,
        database=temp_db,
        recorder=_Recorder(),
        access=access,
        touched=_nothing,
    )
    task = _Task(
        {"actor_id": admin.id, "asset_ids": [one.asset.id, two.asset.id], "template": "Trip {n}"}
    )

    await registered_handlers()[RENAME_BATCH](task)  # type: ignore[arg-type]

    assert task.notes == ["Renamed 2 files."]
    assert sorted(path.name for path in managed.path.iterdir()) == ["Trip 1.mp4", "Trip 2.mkv"]


async def test_a_batch_whose_every_file_went_since_the_plan_writes_no_receipt_and_tells_nobody(
    organizer: Organizer, temp_db: Database, managed: Library, add_file: Any, admin: Viewer
) -> None:
    one = await add_file(managed, "one.mp4")
    told: list[Sequence[str]] = []

    async def touched(ids: Sequence[str]) -> None:
        told.append(ids)

    recorder = _Recorder()
    renamer = BatchRenamer(organizer, temp_db, recorder, touched=touched)
    planned = await renamer.plan([one.asset.id], "Trip", on_clash="number", actor=admin)
    (managed.path / "one.mp4").unlink()

    applied = await renamer.carry_out(planned, "Trip", actor=admin)

    assert (applied.renamed, applied.skipped, applied.receipt_id) == (0, 1, None)
    assert recorder.said == [] and told == []


async def test_a_batchs_line_draws_no_pictures() -> None:
    receipts = BatchRenameReceipts(None, touched=_nothing)  # type: ignore[arg-type]
    viewer = Viewer(id="u", role=Role.ADMIN)
    assert await receipts.pictures_of(viewer, '{"moves": ["m1"]}') == ()


def test_a_release_date_reads_as_its_day_and_anything_else_as_none() -> None:
    from datetime import date

    from sift.slices.organize.batch import _day

    assert _day("2024-03-09T10:00:00Z") == date(2024, 3, 9)
    assert _day("2024-13-45 later") is None
    assert _day("2024") is None
    assert _day(20240309) is None
