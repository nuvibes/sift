# SPDX-License-Identifier: AGPL-3.0-or-later
"""The walk itself: what it reports, what the machine refuses it, and a root that is not there."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from sift.kernel import lanes
from sift.kernel.config import Settings
from sift.kernel.content import LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.jobs import (
    JobFailedPermanently,
)
from sift.kernel.ledger import Actor
from sift.slices.library_roots import jobs, taking_in, walking
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import (
    POSIX_ONLY,
    VIDEO_SECONDS,
    WINDOWS_ONLY,
    RecordingReindexer,
    draw,
    junction,
)
from sift.slices.library_roots.tests.test_jobs import (
    Context,
    assets_in,
)

# --- the walk --------------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "a named pipe cannot be put in a folder on Windows: its named pipes live under \\\\.\\pipe\\ and are not reachable as a path inside a media folder, so the hazard this guards against does not exist there. See kernel/paths.O_NONBLOCK."
    ),
)
def test_the_walk_skips_a_pipe(tmp_path: Path) -> None:
    """A FIFO named `.mp4` would hang a worker forever, and no timeout would save it.

    Opening one blocks until somebody writes, which is never: the read has not failed, it is
    waiting, and the worker that opened it is gone for good.
    """
    os.mkfifo(tmp_path / "trap.mp4")  # type: ignore[attr-defined, unused-ignore]
    draw(tmp_path / "real.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    found = [item.rel_path for item in walking.walk_media(tmp_path).files]

    assert found == ["real.mp4"]


@POSIX_ONLY
def test_the_walk_does_not_follow_a_directory_symlink(tmp_path: Path) -> None:
    """A symlinked directory pointing at an ancestor is a loop, and a walk that follows it does
    not terminate."""
    (tmp_path / "clips").mkdir()
    draw(tmp_path / "clips" / "real.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    (tmp_path / "clips" / "loop").symlink_to(tmp_path, target_is_directory=True)

    found = [item.rel_path for item in walking.walk_media(tmp_path).files]

    assert found == ["clips/real.mp4"]


@WINDOWS_ONLY
def test_the_walk_does_not_follow_a_junction(tmp_path: Path) -> None:
    """The same loop, by the mechanism a Windows user has.

    Worth having rather than skipping the platform: a junction reports `is_symlink() == False`, so
    whatever refuses it is not the link check, and a walk that followed one back to an ancestor
    would not terminate, on the platform Sift ships on.
    """
    (tmp_path / "clips").mkdir()
    draw(tmp_path / "clips" / "real.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    junction(tmp_path / "clips" / "loop", tmp_path)

    found = [item.rel_path for item in walking.walk_media(tmp_path).files]

    assert found == ["clips/real.mp4"]


@POSIX_ONLY
def test_the_walk_does_not_follow_a_file_symlink_out_of_the_root(tmp_path: Path) -> None:
    """A symlinked file is a file living somewhere the root does not reach.

    Indexed, it would be recorded as sitting in a library it is not in, and the root's promise,
    that everything under it is under it, would not be true.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    real = draw(outside / "elsewhere.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    library = tmp_path / "library"
    library.mkdir()
    (library / "shortcut.mp4").symlink_to(real)

    found = [item.rel_path for item in walking.walk_media(library).files]

    assert found == []


@POSIX_ONLY
def test_the_walk_reads_a_folder_it_cannot_open_as_empty_rather_than_failing(
    tmp_path: Path,
) -> None:
    """One unreadable folder is a permissions problem to be told about, not a reason to index
    nothing."""
    draw(tmp_path / "fine.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    shut = tmp_path / "shut"
    shut.mkdir()
    draw(shut / "hidden.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    os.chmod(shut, 0o000)

    try:
        found = [item.rel_path for item in walking.walk_media(tmp_path).files]
    finally:
        os.chmod(shut, 0o755)  # noqa: S103

    assert found == ["fine.mp4"]


def test_a_root_that_refuses_its_listing_is_a_walk_that_learned_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = os.scandir

    def refused(path: Any) -> Any:
        if Path(path) == tmp_path:
            raise PermissionError(13, "Access is denied")
        return real(path)

    monkeypatch.setattr(os, "scandir", refused)
    walk = walking.walk_media(tmp_path)

    assert not walk.looked
    assert (walk.directories, walk.unlisted, walk.mtimes) == ((), (), {})


async def test_a_scan_for_a_root_that_is_gone_says_so(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    library_store: LibraryStore,
    reindexer: RecordingReindexer,
) -> None:
    await library_store.delete_root(root.id, actor=Actor.sift("folder"))
    context = await context_for(jobs.SCAN, {"root_id": root.id})

    with pytest.raises(walking.RootIsGone):
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)


# --- when the machine refuses --------------------------------------------------------------------
#
# The CALL is made to fail here rather than a filesystem arranged that makes it fail, and the two
# tests above that arrange one are kept exactly as they are. A folder nobody can read is
# `chmod 0o000` on one operating system and cannot be expressed on the other (the bits are accepted there
# and then ignored), so the folder stays readable and the refusal never happens. A test that
# arranges the state proves more where it can run and proves NOTHING where it cannot, which would
# leave the sweep's whole "I did not see it is not it is not there" rule unchecked on the
# platform Sift ships on.


class _NeitherFileNorFolder:
    """A directory entry that is neither. A socket, a device node or a pipe on POSIX; on Windows
    the equivalents live somewhere that is not reachable as a path inside a media folder at all.

    Refused either way, and the check is what stands between a worker and a handle that never
    answers: opening a pipe named `.mp4` blocks until somebody writes, which is never.
    """

    def __init__(self, path: Path) -> None:
        self.name = path.name
        self.path = str(path)

    def is_junction(self) -> bool:
        return False

    def is_dir(self, follow_symlinks: bool = True) -> bool:
        return False

    def is_file(self, follow_symlinks: bool = True) -> bool:
        return False


def test_the_walk_skips_something_that_is_neither_a_folder_nor_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    draw(tmp_path / "real.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    real_scandir = os.scandir

    def with_a_trap(directory: str | os.PathLike[str]) -> list[object]:
        return [_NeitherFileNorFolder(tmp_path / "trap.mp4"), *real_scandir(directory)]

    monkeypatch.setattr(os, "scandir", with_a_trap)

    found = [item.rel_path for item in walking.walk_media(tmp_path).files]

    assert found == ["real.mp4"]


async def test_a_folder_the_machine_refuses_mid_pass_takes_nothing_off_the_wall(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both halves of the rule, on either operating system: the scan carries on past a folder it cannot list,
    and nothing inside that folder is declared missing on the strength of not having been seen.

    Refused for that one folder and answered normally everywhere else, so what is being measured is
    the handling rather than a machine with nothing working on it.
    """
    draw(root_path / "open" / "seen.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    draw(root_path / "shut" / "unseen.mp4", "testsrc2=size=32x32:rate=10", VIDEO_SECONDS)

    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    assert await assets_in(temp_db) == 2

    real_scandir, real_stat = os.scandir, os.stat

    def refuse_that_folder(directory: str | os.PathLike[str]) -> object:
        if Path(directory).name == "shut":
            raise PermissionError(13, "Permission denied")
        return real_scandir(directory)

    def refuse_what_is_in_it(path: str | os.PathLike[str], **_kwargs: object) -> os.stat_result:
        if "shut" in Path(path).parts:
            raise PermissionError(13, "Permission denied")
        return real_stat(path)

    monkeypatch.setattr(os, "scandir", refuse_that_folder)
    monkeypatch.setattr(os, "stat", refuse_what_is_in_it)

    again = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)

    rows = await temp_db.fetch_all("SELECT rel_path, status FROM asset_locations ORDER BY rel_path")
    assert [(row["rel_path"], row["status"]) for row in rows] == [
        ("open/seen.mp4", "present"),
        ("shut/unseen.mp4", "present"),
    ], "not being allowed to look is not evidence that the file has gone"


# --- a root that is not there to ask ------------------------------------------------------------


async def test_a_root_that_does_not_answer_changes_nothing(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unplugged drive or a share that is off is asked once, before a row is touched, and the
    pass stops with a sentence, not with every file marked missing one refused stat at a time."""
    draw(root_path / "kept.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    assert await assets_in(temp_db) == 1
    jobs_before = await temp_db.fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE type = 'probe'")

    def off(path: Path) -> OSError | None:
        return OSError(53, "The network path was not found")

    monkeypatch.setattr(jobs, "_root_answer", off)
    again = await context_for(jobs.SCAN, {"root_id": root.id})
    with pytest.raises(walking.RootUnreachable, match="did not answer"):
        await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)

    rows = await temp_db.fetch_all("SELECT status FROM asset_locations")
    assert [row["status"] for row in rows] == ["present"], "nothing was marked missing"
    jobs_after = await temp_db.fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE type = 'probe'")
    assert jobs_before is not None and jobs_after is not None
    assert jobs_after["n"] == jobs_before["n"], "no probe was handed out"
    assert issubclass(walking.RootUnreachable, JobFailedPermanently), (
        "the queue must not try the same unplugged drive twice more"
    )


@WINDOWS_ONLY
async def test_a_copy_on_a_share_that_stopped_answering_is_not_marked_missing(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows a share that has gone quiet raises the same exception class as a missing file.
    The code on the error is the difference, and the sweep reads it."""
    clip = draw(root_path / "shared.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    clip.unlink()

    real = os.stat

    def share_is_off(path: Any, *args: Any, **kwargs: Any) -> Any:
        if str(path).endswith("shared.mp4"):
            raise FileNotFoundError(2, "The network path was not found", str(path), 53)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", share_is_off)
    again = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)
    rows = await temp_db.fetch_all("SELECT status FROM asset_locations")
    assert [row["status"] for row in rows] == ["present"], "a share that is off is not a deletion"

    monkeypatch.undo()
    third = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(third, settings=settings, service=service, reindexer=reindexer)
    rows = await temp_db.fetch_all("SELECT status FROM asset_locations")
    assert [row["status"] for row in rows] == ["missing"], "a file that is really gone still is"


async def test_a_copy_that_comes_back_unchanged_is_marked_present_without_being_read(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
    tmp_path: Path,
) -> None:
    """A drive plugged back in is not a root's worth of new files. A copy marked missing that
    comes back with the size and age it had is marked present, and the pass reads nothing."""
    clip = draw(root_path / "back.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    before = await temp_db.fetch_one("SELECT id, identity FROM assets")
    assert before is not None

    parked = tmp_path / "parked.mp4"
    clip.rename(parked)
    gone = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(gone, settings=settings, service=service, reindexer=reindexer)
    rows = await temp_db.fetch_all("SELECT status FROM asset_locations")
    assert [row["status"] for row in rows] == ["missing"]

    parked.rename(clip)
    back = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(back, settings=settings, service=service, reindexer=reindexer)
    rows = await temp_db.fetch_all("SELECT status FROM asset_locations")
    assert [row["status"] for row in rows] == ["present"], "the copy is back"
    assert back.units == 0, "nothing was read: the row already described the file"
    after = await temp_db.fetch_all("SELECT id, identity FROM assets")
    assert [(row["id"], row["identity"]) for row in after] == [(before["id"], before["identity"])]


async def test_probes_are_handed_out_a_batch_at_a_time_as_the_pass_goes(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pass stopped part way strands the probes of one batch, not of everything it had read.
    Three files, a batch of two, one read at a time, and the third read fails: the first two
    files' probes are already in the queue."""
    # Three different files: two identical ones would be one asset with a second copy, and a
    # copy goes on the check list rather than the `probe` list.
    for name, size in (("a.mp4", "32x32"), ("b.mp4", "48x48"), ("c.mp4", "64x48")):
        draw(root_path / name, "testsrc2=size=" + size + ":rate=5", VIDEO_SECONDS)
    monkeypatch.setattr(jobs, "PROBE_HANDOUT_BATCH", 2)
    monkeypatch.setattr(lanes, "reads_at_once", lambda path: 1)
    taken = 0
    original = taking_in._take_in

    async def cut_off_after_two(*args: Any, **kwargs: Any) -> set[str]:
        nonlocal taken
        if taken >= 2:
            raise RuntimeError("the pass stopped here")
        result = await original(*args, **kwargs)
        taken += 1
        return result

    monkeypatch.setattr(jobs, "_take_in", cut_off_after_two)
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    with pytest.raises(RuntimeError, match="stopped here"):
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    probes = await temp_db.fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE type = 'probe'")
    assert probes is not None and probes["n"] == 2, "the first batch's probes were handed out"
