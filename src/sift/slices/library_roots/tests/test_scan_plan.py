# SPDX-License-Identifier: AGPL-3.0-or-later
"""A plan of a scan says what the scan would do, and writes nothing while it works it out.

A scan moves folder rows before it decides anything about the files in them, so a plan that asked
the rows the scan's questions without those moves would call every file in a renamed folder new
and every old row gone. These hold the plan to the scan: the same disk, planned and then scanned,
comes to the same folders moved, the same files read and the same files marked missing.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from sift.kernel.config import Settings
from sift.kernel.content import ROOT_REL_PATH, LocationStatus, Root
from sift.kernel.db import Database
from sift.kernel.jobs import SystemCapabilities
from sift.slices.library_roots import jobs, moved_folders, scan_plan, walking
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import RecordingReindexer, draw
from sift.testing.logs import uncached_log

from .test_jobs import Context

_TABLES = ("folders", "asset_locations", "assets", "scan_rejections", "photo_sets")


async def _state(database: Database) -> dict[str, list[tuple[object, ...]]]:
    """Every row a scan could write, to prove a plan wrote none of them."""
    return {
        table: [
            tuple(row)
            # nosemgrep: sift-no-string-built-sql
            for row in await database.fetch_all(f"SELECT * FROM {table} ORDER BY 1")  # noqa: S608 (a fixed list)
        ]
        for table in _TABLES
    }


async def _scan(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)


def _picture(path: Path, width: int) -> Path:
    return draw(path, f"testsrc2=size={width}x48:rate=1")


# --- the moves, worked out without making them ----------------------------------------------------


def test_a_folder_carried_along_under_its_parent_is_not_moved_again() -> None:
    moves, recorded = moved_folders._carried_out((("a", "x"), ("a/b", "x/b")), {"a", "a/b"})

    assert moves.pairs == (("a", "x"),)
    assert recorded == {"x", "x/b"}


def test_a_move_onto_a_path_made_by_an_earlier_one_is_refused_as_the_store_refuses_it() -> None:
    """`p` lands at `q/r`, which makes the chain `q` first, so `s/t` cannot then become `q`."""
    moves, recorded = moved_folders._carried_out((("p", "q/r"), ("s/t", "q")), {"p", "s", "s/t"})

    assert moves.pairs == (("p", "q/r"),)
    assert recorded == {"q", "q/r", "s", "s/t"}


def test_a_path_read_through_the_moves_comes_back_where_the_rows_hold_it() -> None:
    moves = walking.FolderMoves((("shoot", "archive/shoot"), ("archive/shoot/inner", "kept")))

    assert moves.after("shoot/inner/a.png") == "kept/a.png"
    assert moves.before("kept/a.png") == "shoot/inner/a.png"
    assert moves.before("elsewhere/b.png") == "elsewhere/b.png"
    assert moves.before("shooting/c.png") == "shooting/c.png"


# --- the plan against the scan --------------------------------------------------------------------


async def test_a_plan_writes_nothing_and_the_scan_then_does_what_it_said(
    context_for: Context,
    capabilities: SystemCapabilities,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    temp_db: Database,
) -> None:
    """A renamed folder with a file changed and a file added in it, and a file deleted beside it.

    Read without the moves, the four files that only moved would be four new files and the four
    old rows four files gone; the plan says one new, one changed, one gone, and the scan agrees.
    """
    for index, name in enumerate(("a", "b", "c", "d")):
        _picture(root_path / "holidays" / f"{name}.png", 64 + index * 8)
    _picture(root_path / "gone.png", 128)
    await _scan(context_for, root, settings, service, reindexer)
    before = await capabilities.library.folder_at(root.id, "holidays")
    assert before is not None
    gone = await capabilities.content.location_at(root.id, "gone.png")
    assert gone is not None

    (root_path / "holidays").rename(root_path / "trips")
    _picture(root_path / "trips" / "d.png", 200)
    _picture(root_path / "trips" / "e.png", 208)
    (root_path / "gone.png").unlink()
    rows = await _state(temp_db)

    planned = await scan_plan.plan_scan(capabilities, service, root)

    assert await _state(temp_db) == rows
    assert planned.answered
    assert planned.folders is not None
    assert planned.folders.moves.pairs == (("holidays", "trips"),)
    assert (planned.new, planned.changed, planned.returned, planned.archives) == (1, 1, 0, 0)
    assert planned.missing == 1 and planned.going == (gone.asset_id,)
    assert sorted(one.rel_path for one in planned.reading) == ["trips/d.png", "trips/e.png"]
    assert {one.folder for one in planned.reading} == {"holidays"}

    await _scan(context_for, root, settings, service, reindexer)

    after = await capabilities.library.folder_at(root.id, "trips")
    assert after is not None and after.id == before.id
    marked = await capabilities.content.location_at(root.id, "gone.png")
    assert marked is not None and marked.status is LocationStatus.MISSING
    present = await temp_db.fetch_all(
        "SELECT rel_path FROM asset_locations WHERE status = 'present' ORDER BY rel_path"
    )
    assert [row["rel_path"] for row in present] == [f"trips/{one}.png" for one in "abcde"]


async def test_a_plan_marks_missing_only_the_picture_taken_out_of_an_archive(
    context_for: Context,
    capabilities: SystemCapabilities,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    tmp_path: Path,
) -> None:
    """The take-in claims the pictures an archive's index lists, so the plan claims them too, and
    the one picture no longer inside is the one file it would mark missing."""
    pictures = [_picture(tmp_path / "made" / f"{index}.png", 64 + index * 8) for index in range(3)]
    archive = root_path / "gallery.zip"
    with zipfile.ZipFile(archive, "w") as writing:
        for one in pictures:
            writing.write(one, one.name)
    await _scan(context_for, root, settings, service, reindexer)

    with zipfile.ZipFile(archive, "w") as writing:
        for one in pictures[:2]:
            writing.write(one, one.name)

    planned = await scan_plan.plan_scan(capabilities, service, root)

    assert planned.archives == 1 and planned.missing == 1
    dropped = await capabilities.content.location_at(root.id, "gallery.zip/2.png")
    assert dropped is not None and planned.going == (dropped.asset_id,)


async def test_a_library_that_does_not_answer_is_planned_as_nothing(
    capabilities: SystemCapabilities, root: Root, root_path: Path, service: LibraryService
) -> None:
    root_path.rmdir()

    planned = await scan_plan.plan_scan(capabilities, service, root)

    assert not planned.answered and planned.folders is None and planned.missing == 0


async def test_an_archive_that_cannot_be_read_claims_nothing_and_once_refused_is_not_read_again(
    context_for: Context,
    capabilities: SystemCapabilities,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An archive whose index cannot be read is planned as the take-in treats it, claiming none of
    its pictures, and the plan and the scan agree on what goes. A refused archive the same size and
    age as when it was refused is not opened again by a plan, as the take-in does not open it."""
    pictures = [_picture(tmp_path / "made" / f"{index}.png", 64 + index * 8) for index in range(3)]
    archive = root_path / "gallery.zip"
    with zipfile.ZipFile(archive, "w") as writing:
        for one in pictures:
            writing.write(one, one.name)
    await _scan(context_for, root, settings, service, reindexer)
    archive.write_bytes(b"not an archive at all, though it is named like one")

    planned = await scan_plan.plan_scan(capabilities, service, root)

    assert planned.archives == 1
    await _scan(context_for, root, settings, service, reindexer)
    marked = [
        await capabilities.content.location_at(root.id, f"gallery.zip/{index}.png")
        for index in range(3)
    ]
    gone = [one for one in marked if one is not None and one.status is LocationStatus.MISSING]
    assert planned.missing == len(gone)

    def _never(*_: object, **__: object) -> object:
        raise AssertionError("a refused archive's index was read again")

    monkeypatch.setattr(scan_plan, "inspect_archive", _never)
    again = await scan_plan.plan_scan(capabilities, service, root)

    assert again.archives == 1 and again.missing == 0


async def test_a_file_put_back_as_it_was_is_planned_as_returned_and_not_read(
    context_for: Context,
    capabilities: SystemCapabilities,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    tmp_path: Path,
) -> None:
    picture = _picture(root_path / "kept.png", 96)
    await _scan(context_for, root, settings, service, reindexer)
    aside = tmp_path / "aside.png"
    shutil.move(picture, aside)
    await _scan(context_for, root, settings, service, reindexer)
    shutil.move(aside, picture)

    planned = await scan_plan.plan_scan(capabilities, service, root)

    assert (planned.new, planned.changed, planned.returned) == (0, 0, 1)
    assert planned.reading == ()
    await _scan(context_for, root, settings, service, reindexer)
    back = await capabilities.content.location_at(root.id, "kept.png")
    assert back is not None and back.status is LocationStatus.PRESENT


async def test_a_plan_counts_every_file_and_names_only_the_first_and_a_new_folder_reads_as_its_parent(
    context_for: Context,
    capabilities: SystemCapabilities,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """The counts are whole and the lists stop at `first`. A file in a folder no row records yet is
    seen by whoever may see the nearest recorded folder above it."""
    for index in range(3):
        _picture(root_path / f"{index}.png", 64 + index * 8)
    await _scan(context_for, root, settings, service, reindexer)
    for index in range(3):
        (root_path / f"{index}.png").unlink()
    _picture(root_path / "fresh" / "deeper" / "new.png", 200)

    planned = await scan_plan.plan_scan(capabilities, service, root, first=1)

    assert planned.missing == 3 and len(planned.going) == 1
    assert planned.new == 1
    (read,) = planned.reading
    assert (read.rel_path, read.folder) == ("fresh/deeper/new.png", ROOT_REL_PATH)


async def test_a_walk_that_did_not_list_the_library_plans_nothing_missing(
    context_for: Context,
    capabilities: SystemCapabilities,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The folder answered and then its listing did not (a drive unplugged in between): a walk that
    saw nothing is no evidence of what is gone, so nothing is planned missing, not even a file a
    look at its own path would not find, and no folder moves."""
    _picture(root_path / "here.png", 64)
    await _scan(context_for, root, settings, service, reindexer)
    (root_path / "here.png").unlink()
    monkeypatch.setattr(
        scan_plan, "_walk_confined", lambda *_: walking.Walk(files=(), directories=(), looked=False)
    )

    planned = await scan_plan.plan_scan(capabilities, service, root)

    assert planned.answered and planned.folders is None
    assert planned.missing == 0 and planned.going == ()


async def test_a_folder_another_writer_lands_on_first_is_not_moved_and_is_said_in_the_log(
    context_for: Context,
    capabilities: SystemCapabilities,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Between the plan and its moves something records a folder where a renamed one would land.
    The move is not made, the scan goes on as it would without recognising the rename, and the
    difference from the plan is logged rather than silent."""
    for index in range(3):
        _picture(root_path / "holidays" / f"{index}.png", 64 + index * 8)
    await _scan(context_for, root, settings, service, reindexer)
    before = await capabilities.library.folder_at(root.id, "holidays")
    assert before is not None
    (root_path / "holidays").rename(root_path / "trips")
    planning = moved_folders._plan_folders

    async def _planned_then_raced(*args: object, **kwargs: object) -> object:
        plan = await planning(*args, **kwargs)  # type: ignore[arg-type]
        await capabilities.library.upsert_folder(root.id, "trips")
        return plan

    monkeypatch.setattr(moved_folders, "_plan_folders", _planned_then_raced)
    uncached_log(monkeypatch, moved_folders)

    with capture_logs() as logs:
        await _scan(context_for, root, settings, service, reindexer)

    assert any(line["event"] == "library.folder_moves_unplanned" for line in logs)
    after = await capabilities.library.folder_at(root.id, "trips")
    assert after is not None and after.id != before.id
