# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a scan does to a library, and what it must never do to one.

The first test in this file is the one the whole slice exists to keep true: a scan reads somebody's
folder and the folder is byte-for-byte what it was, mtimes included. Everything else Sift promises
about not touching your files rests on that being checked rather than believed.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

from sift.kernel.config import Settings
from sift.kernel.content import ROOT_REL_PATH, ContentStore, LibraryStore, Root
from sift.kernel.content.library import FolderRow
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ingress import verify_ingress
from sift.kernel.jobs import (
    JobContext,
    JobQueue,
    JobState,
    register_handler,
)
from sift.kernel.jobs.tuning import DEFAULT_PRIORITY, WAITED_ON_PRIORITY
from sift.slices.library_roots import jobs, quarantine, sweeping, taking_in, walking
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import (
    POSIX_ONLY,
    VIDEO_SECONDS,
    WINDOWS_ONLY,
    RecordingReindexer,
    draw,
    junction,
    refused_file,
)
from sift.testing.logs import uncached_log

#: `...` rather than the two arguments, because the helper takes a keyword-only `priority` as well:
#: `scan_everything` reads its own row's urgency and hands it to the parts, so a test of that
#: needs a context that is not always at the default. A protocol would spell all three out; this
#: alias is used only to name the fixture in a signature.
Context = Callable[..., Awaitable[JobContext]]

#: A whole-library pass belonging to some other feature, named here rather than imported. This
#: slice may not import another, and what is asserted is that the scan ASKED. What the pass then
#: does is proven where that pass lives. The same reasoning the `probe` no-op in `conftest` gives.
_SETTLES_INTO = "settling_pass"


async def _nothing(context: JobContext) -> None:
    return None


def tree_state(root: Path) -> dict[str, tuple[int, int, bytes]]:
    """Everything about a directory tree that a scan must not change."""
    state = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            state[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns, path.read_bytes())
    return state


async def assets_in(database: Database) -> int:
    rows = await database.fetch_all("SELECT COUNT(*) AS c FROM assets")
    return int(rows[0]["c"])


async def asset_ids_in(database: Database) -> set[str]:
    return {str(row["id"]) for row in await database.fetch_all("SELECT id FROM assets")}


async def probes_queued(queue: JobQueue) -> list[str]:
    """Every probe the queue has ever held, whatever became of it.

    For asking whether a pass ASKED for work: a probe claimed by a worker in between is still a
    probe this scan queued, and comparing only what is still waiting would report it as having
    vanished.
    """
    page = await queue.list(job_type=taking_in.PROBE, limit=100)
    return [job.id for job in page.jobs]


async def probes_waiting(queue: JobQueue) -> list[str]:
    """The probes still waiting to run.

    For asking whether work is OUTSTANDING, which is a different question and the one the recovery
    tests ask. They stage a lost probe by cancelling it, and against the list above they would count
    the corpse they had just made.
    """
    page = await queue.list(job_type=taking_in.PROBE, limit=100)
    return [job.id for job in page.jobs if job.state is JobState.QUEUED]


# --- the promise -----------------------------------------------------------------------------


async def test_a_scan_changes_nothing_in_the_library(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """Sift indexes in place. This is that promise, asserted rather than assumed.

    Bytes and modification times both: reading a file is allowed to be invisible, and an
    implementation that opened one for writing, or that rewrote a mtime, would be caught here and
    nowhere else in the suite.
    """
    draw(root_path / "clips" / "holiday.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    draw(root_path / "photo.png", "testsrc2=size=64x48:rate=1")
    refused_file(root_path / "clips" / "not-really.mp4")
    before = tree_state(root_path)

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert tree_state(root_path) == before


async def test_a_file_the_gate_refuses_is_left_exactly_where_it_is(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
) -> None:
    """Refused, not quarantined. It is the user's file, in the user's folder.

    Sift moving something a person put there themselves, because Sift did not like it, is not a
    safety measure, and the file is not being executed by anything. Only bytes Sift wrote itself
    are ever quarantined.
    """
    disguised = refused_file(root_path / "not-really.mp4")
    original = disguised.read_bytes()

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert disguised.exists(), "a refused file must stay where the person put it"
    assert disguised.read_bytes() == original
    assert await assets_in(temp_db) == 0, "and it must not be indexed"

    quarantined = (
        list(settings.quarantine_dir.glob("*")) if settings.quarantine_dir.exists() else []
    )
    assert quarantined == [], "nothing found in a library is ever quarantined"


# --- what it indexes -------------------------------------------------------------------------


async def test_a_scan_indexes_what_it_finds_and_builds_the_folders(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    library_store: LibraryStore,
    content_store: ContentStore,
    reindexer: RecordingReindexer,
) -> None:
    draw(root_path / "clips" / "holiday" / "beach.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    draw(root_path / "photo.png", "testsrc2=size=64x48:rate=1")

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    top = await library_store.root_folder(root.id)
    assert top is not None
    paths = [folder.rel_path for folder in await library_store.folders_under(top)]
    assert paths == ["clips", "clips/holiday"]


async def test_a_scan_writes_a_directorys_folder_chain_once_and_forgets_no_refusal_it_never_had(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    library_store: LibraryStore,
    temp_db: Database,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The directory is the unit. Resolving and writing a folder chain per FILE, and looking up and
    then deleting a refusal per file that never had one, would be three write transactions for every
    new file on a first import. The chain is written once per directory and the root's refusals are
    read once."""
    settled = time.time() - 60
    for name, size in (("a", "64x48"), ("b", "64x64"), ("c", "48x48")):
        made = draw(
            root_path / "clips" / f"{name}.mp4", f"testsrc2=size={size}:rate=5", VIDEO_SECONDS
        )
        # Old enough that the pass reads them rather than leaving them as still being written.
        os.utime(made, (settled, settled))
    upserts: list[str] = []
    real_upsert = library_store.upsert_folder

    async def counting_upsert(root_id: str, rel_path: str) -> Any:
        upserts.append(rel_path)
        return await real_upsert(root_id, rel_path)

    monkeypatch.setattr(library_store, "upsert_folder", counting_upsert)
    forgets: list[str] = []

    async def counting_forget(*, root_id: str, rel_path: str) -> None:
        forgets.append(rel_path)

    monkeypatch.setattr(service, "forget_rejection", counting_forget)
    assert service.forget_rejection is counting_forget

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert await assets_in(temp_db) == 3, "all three were taken in"
    assert upserts == ["clips"], "one folder chain for three files in one directory"
    assert forgets == [], "no refusal existed, so none was forgotten"


async def test_a_scan_ignores_what_is_not_media(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
) -> None:
    (root_path / "notes.txt").write_text("not media")
    (root_path / "sheet.csv").write_text("a,b")
    draw(root_path / "real.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert await assets_in(temp_db) == 1


async def test_a_scan_asks_for_a_probe_and_nothing_else(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """One file, one `probe`, hanging off the scan.

    The thumbnail, the preview and the sprite are `probe`'s children, not the scan's: each needs
    the duration, the dimensions and whether the file decodes at all, which only `probe` knows.
    Enqueued here they would race it, and all three would separately rediscover a truncated file.

    The two files are drawn differently on purpose. Drawn the same they are the same bytes, and the
    same bytes are one asset with two locations and one `probe`, which is correct, and would make
    this test pass while proving half of what it says.
    """
    draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    draw(root_path / "two.mp4", "testsrc2=size=32x32:rate=10", VIDEO_SECONDS)

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    page = await job_queue.list(limit=100, parent_id=context.job.id)
    assert sorted(job.type for job in page.jobs) == [taking_in.PROBE, taking_in.PROBE]


async def test_a_scan_asks_for_the_whole_library_passes_although_it_read_nothing(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """An EMPTY walk, deliberately, because the empty walk is the case that matters.

    A press of Scan on a library that is already indexed reads almost no file and hands out almost
    no `probe`. Asked for by `probe` alone, the passes that read folder and file names would never
    run on the one press a person makes to have their library read again.

    A second scan is run for the other half of the promise: the request is deduped, so a dozen
    folders make one pass and not a dozen.
    """
    register_handler(_SETTLES_INTO, _nothing, name="Test job")

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(
        context,
        settings=settings,
        service=service,
        reindexer=reindexer,
        settles_into=(_SETTLES_INTO,),
    )
    assert (await job_queue.list(job_type=_SETTLES_INTO, limit=10)).total == 1

    again = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(
        again,
        settings=settings,
        service=service,
        reindexer=reindexer,
        settles_into=(_SETTLES_INTO,),
    )
    assert (await job_queue.list(job_type=_SETTLES_INTO, limit=10)).total == 1


async def test_a_scan_says_how_many_files_it_will_read_before_it_reads_them(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """The count is fixed from the rows before the first file is opened, so the estimate weighs
    the scan by its files; a second pass over unchanged files is about none."""
    draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    draw(root_path / "two.mp4", "testsrc2=size=32x32:rate=10", VIDEO_SECONDS)
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)
    first = await job_queue.get(context.job.id)
    assert first is not None and first.units == 2

    again = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)
    second = await job_queue.get(again.job.id)
    assert second is not None and second.units == 0


async def test_a_scan_only_pass_marks_every_probe_it_hands_out(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """The flag has to reach EVERY probe, and this is the half that could silently go wrong.

    A probe hands out the pictures, the faces and the descriptions, so a scan is otherwise the whole
    pipeline whatever the button that began it said. `scan_only` is how a pass asks for the reading
    alone, and if it reached some probes and not others the pass would be scan-only for some files
    and not for others, which is worse than not having it: the difference is invisible until the
    queue fills.

    EVERY file, not any file. One file drawn twice would be one asset with two locations and one
    probe, so the second is drawn differently, for the same reason the test above says so.
    """
    draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    draw(root_path / "two.mp4", "testsrc2=size=32x32:rate=10", VIDEO_SECONDS)

    context = await context_for(jobs.SCAN, {"root_id": root.id, "scan_only": True})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    page = await job_queue.list(limit=100, parent_id=context.job.id)
    probes = [job for job in page.jobs if job.type == taking_in.PROBE]
    assert len(probes) == 2
    assert all(job.payload.get("scan_only") is True for job in probes)


async def test_an_ordinary_scan_leaves_the_probe_payload_as_it_was(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """And an ordinary scan carries no flag at all, which is what keeps the queue's identity stable.

    The queue decides whether work is already coming by matching a payload EXACTLY. A field present
    on every probe (even set to false) would be a different payload from the one every existing
    row carries, so nothing would collapse onto anything and a rescan would queue a second read of
    every file in the library.
    """
    draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    page = await job_queue.list(limit=100, parent_id=context.job.id)
    probes = [job for job in page.jobs if job.type == taking_in.PROBE]
    assert len(probes) == 1
    assert "scan_only" not in probes[0].payload


async def test_a_scan_tells_the_search_index_what_it_took_in(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
) -> None:
    """A filename is indexed text, so a scan that says nothing leaves a file unfindable by name.

    It is the failure that looks like nothing: the file is on the grid, it opens, it plays, and
    typing its name returns an empty screen, for as long as the application stays up. Every other
    writer in Sift tells the index when text it owns changes, and a scan is the main way files enter
    a running library.

    Said ONCE for the whole walk rather than once per file. Per file it is a write transaction and
    two whole-table sweeps each, which on a first scan of a real library is that work tens of
    thousands of times over, on the lock the workers want.
    """
    draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    draw(root_path / "two.mp4", "testsrc2=size=32x32:rate=10", VIDEO_SECONDS)

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert len(reindexer.told) == 2, "the index was not told about everything the scan took in"
    assert set(reindexer.told) == await asset_ids_in(temp_db)


async def test_a_scan_makes_a_file_findable_by_name_through_the_real_index(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    job_queue: JobQueue,
) -> None:
    """The whole path, with the real index rather than the stand-in above it.

    The two tests either side of this one prove what the scan SAYS, which is the part that belongs
    to this slice. They prove it against a recorder, so between them they would still pass if the
    thing on the other end could not accept the call at all, and "the writer reports, the index
    does not take it" is exactly the shape of bug a recorder cannot see. So this one runs the
    real reindexer, against a real database, and asks the index itself.

    It is also the deadlock check. Indexing opens a write, the scan opens writes of its own per
    file, and this slice's write guard is not reentrant, so a call placed one level too deep
    would hang here rather than in production.

    A slice never imports another slice; a test may, and this file's own contract is that what the
    scan hands over is real.
    """
    from sift.slices.search import Reindexer

    draw(root_path / "holiday.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(
        context,
        settings=settings,
        service=service,
        reindexer=Reindexer(database=temp_db, queue=job_queue),
    )

    indexed = await temp_db.fetch_all("SELECT filename FROM assets_fts")
    assert [str(row["filename"]) for row in indexed] == ["holiday.mp4"]


async def test_a_scan_that_finds_nothing_new_tells_the_index_nothing(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """The other half, and the reason the first is not just "call it always".

    A scan runs on a timer, on a watch, and every time somebody presses the button. Re-indexing a
    library that has not changed would be the whole text index rewritten for nothing, repeatedly,
    so only what was actually taken in is named, and a pass that took nothing in says nothing.
    """
    draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    assert reindexer.told, "the first pass should have taken the file in"

    reindexer.told.clear()
    again = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)

    assert reindexer.told == []


async def test_a_scan_that_runs_again_adds_nothing(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """A killed scan resumes; it does not restart.

    Identity is the content, so the second pass finds every file already taken and asks for no
    work. This is what makes a scan safe to run at any time, including over and over.
    """
    draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    after_first = await probes_queued(job_queue)

    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    assert await assets_in(temp_db) == 1
    assert await probes_queued(job_queue) == after_first, "the second pass must queue no more work"


async def test_a_file_that_has_not_changed_is_not_read_again(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    monkeypatch: pytest.MonkeyPatch,
    reindexer: RecordingReindexer,
) -> None:
    """The cost of a rescan, and the reason a watched library would never settle.

    Putting every file back through the gate (which decodes it) and then hashing it end to end,
    only to conclude it was the file already at that path, costs tens of seconds a pass on an
    ordinary library, and a watched folder is rescanned often. The library would be permanently
    busy re-learning what it already knows.

    The gate is counted rather than the rows, for the same reason the refusal memory's test counts
    it: rows prove the upsert is an upsert and say nothing about whether the file was opened.
    """
    draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    opened = 0
    real_gate = verify_ingress

    def counting_gate(*args: object, **kwargs: object) -> object:
        nonlocal opened
        opened += 1
        return real_gate(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(taking_in, "verify_ingress", counting_gate)

    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    assert opened == 1

    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    assert opened == 1, "the second pass must not read a file it has already indexed"


async def test_a_file_that_has_not_changed_is_still_counted_as_seen(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
) -> None:
    """The dangerous half of skipping a file, and the reason the ordering in `scan` matters.

    The sweep marks a location missing when the walk did not see it. Skipping the work for a file
    must not skip recording that it is there: if it did, the second pass would take every
    unchanged file out of the library, which is the entire library, and the grid would empty itself
    while the files sat untouched on disk.
    """
    draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)

    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    present = await temp_db.fetch_all("SELECT status FROM asset_locations")
    assert [row["status"] for row in present] == ["present"]


async def test_a_file_touched_at_the_same_size_is_read_again(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    monkeypatch: pytest.MonkeyPatch,
    reindexer: RecordingReindexer,
) -> None:
    """Unchanged means the same size AND the same mtime, and the mtime is the half that matters.

    Size alone is a weak test: a video edited in place is very often the same length to the byte.
    This changes only the mtime (same path, same size, same bytes), and the file must be read
    again, because from the outside that is indistinguishable from a file that was rewritten.
    """
    target = root_path / "one.mp4"
    draw(target, "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    opened = 0
    real_gate = verify_ingress

    def counting_gate(*args: object, **kwargs: object) -> object:
        nonlocal opened
        opened += 1
        return real_gate(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(taking_in, "verify_ingress", counting_gate)

    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    assert opened == 1

    # Forward an hour. Nothing else about the file changes.
    touched = target.stat().st_mtime + 3600
    os.utime(target, (touched, touched))

    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    assert opened == 2, "a file whose mtime moved has to be read again"


async def test_an_asset_whose_probe_was_lost_is_probed_again(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """A file whose `probe` never ran must not sit unread forever.

    `probe` is a separate job and it can be lost: it failed, or it was cancelled, or the
    machine went down between the scan queuing it and a worker taking it. Nothing about the FILE
    changes when that happens, so the shortcut that skips an unchanged file skips it for exactly
    the reason it needs looking at. What it leaves behind is an asset with no duration, no codec
    and no thumbnail, which the player reports as a video the browser cannot play.

    Cancelling the first `probe` is how the loss is staged: a cancelled job is not waiting, which is
    precisely the state a failed one leaves behind.
    """
    draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)

    queued = await probes_waiting(job_queue)
    assert len(queued) == 1
    await job_queue.cancel(queued[0])

    # Nothing about the file has changed. It is the same path, the same size and the same mtime.
    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    again = await job_queue.list(job_type=taking_in.PROBE, limit=100, parent_id=second.job.id)
    assert [job.type for job in again.jobs] == [taking_in.PROBE], (
        "an asset that was never read has to be read, whatever the file did or did not do"
    )


async def test_a_scan_does_not_queue_a_second_probe_over_one_already_waiting(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """The other side of the recovery above, and the reason it is safe to ask every time.

    A library being imported has a probe waiting for every file in it, and none of them has run
    yet. Without the collapse, every pass of the scanner would add a second probe per file, then a
    third: work whose only outcome is to discover what the job in front of it is already about to
    discover, in a queue everything else is behind.

    One context, run twice, rather than two contexts. Building a context CLAIMS a job, and the
    claim loop takes whatever is at the front of the queue on its way to the scan, including the
    probe this test is about, which stops it waiting and so stops it being collapsed onto. That is
    the fixture moving the queue, not the scanner, and asking the same context to walk again keeps
    the queue still enough to see what the scanner does to it.
    """
    draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert len(await probes_waiting(job_queue)) == 1


async def test_a_file_that_changed_length_is_read_again_even_with_its_old_mtime(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
) -> None:
    """The other half of the pair: mtime alone is not the test either.

    A file copied over another with its timestamps preserved (which is what every archiver and
    `cp -p` does) keeps the mtime it had and is a completely different file. The size is what
    catches it.
    """
    target = root_path / "one.mp4"
    draw(target, "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    was = target.stat().st_mtime

    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    assert await assets_in(temp_db) == 1

    target.unlink()
    draw(target, "testsrc2=size=32x32:rate=5", VIDEO_SECONDS + 1)
    os.utime(target, (was, was))

    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    assert await assets_in(temp_db) == 2, "a file of a different length is a different file"


async def test_the_same_file_in_two_places_is_one_asset_in_two_places(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    content_store: ContentStore,
    reindexer: RecordingReindexer,
) -> None:
    """Identity is the bytes. A copy is a location, not a second asset."""
    original = draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    (root_path / "backup").mkdir()
    (root_path / "backup" / "one-copy.mp4").write_bytes(original.read_bytes())

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    assert await assets_in(temp_db) == 1
    rows = await temp_db.fetch_all("SELECT COUNT(*) AS c FROM asset_locations")
    assert rows[0]["c"] == 2


# --- what goes away --------------------------------------------------------------------------


async def test_a_file_that_is_gone_goes_missing_and_keeps_its_asset(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    content_store: ContentStore,
    reindexer: RecordingReindexer,
) -> None:
    """A deleted file is not a deleted asset.

    An asset with no location left is content Sift knows about and cannot currently see: the same
    state as an unplugged drive. Everything anybody recorded about it stays attached, which is what
    makes the next test possible.
    """
    clip = draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)
    clip.unlink()

    again = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)

    assert await assets_in(temp_db) == 1
    rows = await temp_db.fetch_all("SELECT status FROM asset_locations")
    assert [row["status"] for row in rows] == ["missing"]


async def test_a_moved_file_is_the_same_asset_with_its_tags(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
) -> None:
    """Rename it, move it, and it is the file you tagged. This is the point of hashing.

    A library keyed on paths would have lost the tag the moment somebody tidied their folders,
    which is exactly the thing people do to libraries.
    """
    clip = draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    rows = await temp_db.fetch_all("SELECT id FROM assets")
    asset_id = str(rows[0]["id"])
    tag_id = new_id()
    await temp_db.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, 'holiday', 1)", (tag_id,)
    )
    await temp_db.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (asset_id, tag_id)
    )

    (root_path / "sorted").mkdir()
    clip.rename(root_path / "sorted" / "renamed.mp4")

    again = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)

    assert await assets_in(temp_db) == 1, "the same bytes are the same asset"
    kept = await temp_db.fetch_all("SELECT tag_id FROM asset_tags WHERE asset_id = ?", (asset_id,))
    assert [row["tag_id"] for row in kept] == [tag_id]

    present = await temp_db.fetch_all(
        "SELECT rel_path FROM asset_locations WHERE status = 'present'"
    )
    assert [row["rel_path"] for row in present] == ["sorted/renamed.mp4"]


async def test_a_file_that_comes_back_is_present_again(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
) -> None:
    clip = draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    kept = clip.read_bytes()
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    clip.unlink()
    gone = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(gone, settings=settings, service=service, reindexer=reindexer)

    clip.write_bytes(kept)
    back = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(back, settings=settings, service=service, reindexer=reindexer)

    rows = await temp_db.fetch_all("SELECT status FROM asset_locations")
    assert [row["status"] for row in rows] == ["present"]
    assert await assets_in(temp_db) == 1


# --- scanning one folder, which is what the watcher asks for ---------------------------------


async def test_a_scan_of_one_folder_leaves_the_rest_of_the_library_alone(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    library_store: LibraryStore,
    reindexer: RecordingReindexer,
) -> None:
    """The test this whole design turns on.

    A folder scan marks missing whatever it was shown and did not see. Shown the whole root after
    walking one folder, it sees one folder's files and marks every other file in the library
    missing: somebody drops one holiday clip into one directory and their entire library goes off
    the grid. The walk and the sweep have to narrow together or not at all.
    """
    draw(root_path / "inbox" / "new.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    draw(root_path / "archive" / "old.mp4", "testsrc2=size=32x32:rate=10", VIDEO_SECONDS)

    whole = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(whole, settings=settings, service=service, reindexer=reindexer)
    assert await assets_in(temp_db) == 2

    inbox = await library_store.upsert_folder(root.id, "inbox")
    draw(root_path / "inbox" / "another.mp4", "testsrc2=size=16x16:rate=5", VIDEO_SECONDS)

    one = await context_for(jobs.SCAN, {"root_id": root.id, "folder_id": inbox.id})
    await jobs.scan(one, settings=settings, service=service, reindexer=reindexer)

    assert await assets_in(temp_db) == 3, "the new file in the scanned folder is indexed"
    rows = await temp_db.fetch_all("SELECT rel_path, status FROM asset_locations ORDER BY rel_path")
    assert [(row["rel_path"], row["status"]) for row in rows] == [
        ("archive/old.mp4", "present"),
        ("inbox/another.mp4", "present"),
        ("inbox/new.mp4", "present"),
    ], "nothing outside the scanned folder may be touched"


async def test_a_scan_of_one_folder_still_sweeps_that_folder(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    library_store: LibraryStore,
    reindexer: RecordingReindexer,
) -> None:
    """Filtered, not switched off. A file deleted from the watched folder still goes missing."""
    gone = draw(root_path / "inbox" / "leaving.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    draw(root_path / "archive" / "staying.mp4", "testsrc2=size=32x32:rate=10", VIDEO_SECONDS)
    whole = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(whole, settings=settings, service=service, reindexer=reindexer)

    gone.unlink()
    inbox = await library_store.upsert_folder(root.id, "inbox")
    one = await context_for(jobs.SCAN, {"root_id": root.id, "folder_id": inbox.id})
    await jobs.scan(one, settings=settings, service=service, reindexer=reindexer)

    rows = await temp_db.fetch_all("SELECT rel_path, status FROM asset_locations ORDER BY rel_path")
    assert [(row["rel_path"], row["status"]) for row in rows] == [
        ("archive/staying.mp4", "present"),
        ("inbox/leaving.mp4", "missing"),
    ]


async def test_a_scan_of_a_folder_stores_paths_relative_to_the_root(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    library_store: LibraryStore,
    reindexer: RecordingReindexer,
) -> None:
    """The walk reports paths from where it started; everything is stored from the root.

    Stored as the walk reports them, a file in `inbox` would be recorded at `new.mp4`, and the
    next whole-root scan would find nothing at that path, mark it missing, and index the real file
    again as a second location.
    """
    draw(root_path / "inbox" / "deep" / "new.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    inbox = await library_store.upsert_folder(root.id, "inbox")

    one = await context_for(jobs.SCAN, {"root_id": root.id, "folder_id": inbox.id})
    await jobs.scan(one, settings=settings, service=service, reindexer=reindexer)

    rows = await temp_db.fetch_all("SELECT rel_path FROM asset_locations")
    assert [row["rel_path"] for row in rows] == ["inbox/deep/new.mp4"]


async def test_a_scan_of_a_folder_in_a_different_library_is_refused(
    context_for: Context,
    root: Root,
    tmp_path: Path,
    settings: Settings,
    service: LibraryService,
    library_store: LibraryStore,
    reindexer: RecordingReindexer,
) -> None:
    """Otherwise the walk starts outside this root and the sweep marks all of it missing."""
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    other = await library_store.create_root(name="Other", abs_path=elsewhere)
    theirs = await library_store.upsert_folder(other.id, "clips")

    context = await context_for(jobs.SCAN, {"root_id": root.id, "folder_id": theirs.id})

    with pytest.raises(walking.FolderIsGone):
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)


async def test_a_scan_of_a_folder_that_is_gone_says_so(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    context = await context_for(jobs.SCAN, {"root_id": root.id, "folder_id": new_id()})

    with pytest.raises(walking.FolderIsGone):
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)


async def test_a_file_the_walk_could_not_see_is_not_declared_missing(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
) -> None:
    """ "I did not see it" is not "it is not there", and the sweep must not confuse the two.

    A folder that becomes unreadable half way through a pass is the ordinary way this happens: the
    walk skips it and says nothing, and every file in it would be marked missing on the strength of
    a silence. So the sweep looks for the file before recording it as gone, and here it finds it.

    This is the test that makes that check load-bearing. Without it the check can be removed as a
    redundant stat. The scope filter looks like it already covers this, and it does not.
    """
    draw(root_path / "open" / "seen.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    shut = root_path / "shut"
    draw(shut / "unseen.mp4", "testsrc2=size=32x32:rate=10", VIDEO_SECONDS)

    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    assert await assets_in(temp_db) == 2

    os.chmod(shut, 0o000)
    try:
        again = await context_for(jobs.SCAN, {"root_id": root.id})
        await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)
    finally:
        os.chmod(shut, 0o755)  # noqa: S103

    rows = await temp_db.fetch_all("SELECT rel_path, status FROM asset_locations ORDER BY rel_path")
    assert [(row["rel_path"], row["status"]) for row in rows] == [
        ("open/seen.mp4", "present"),
        ("shut/unseen.mp4", "present"),
    ], "a file the walk could not reach is still on the disk, and still in the library"


@POSIX_ONLY
def test_walk_confined_skips_a_base_that_leaves_the_root(tmp_path: Path) -> None:
    """A folder row can name an in-library symlink that leads out of the root. os.scandir would
    follow the starting directory even though the walk refuses a symlink at every step below it, so
    a base that resolves outside the root is skipped rather than walked into someone else's files."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.mp4").write_bytes(b"x")
    link = root / "link"
    os.symlink(outside, link)

    refused = walking._walk_confined(root, link)
    assert refused.files == ()
    # And it says it did not LOOK, which is the half that matters: a walk that found nothing and a
    # walk that never happened are the same empty answer, and the folder reconciliation deletes on
    # the strength of one of them.
    assert refused.looked is False

    (root / "real.mp4").write_bytes(b"y")
    assert [walked.rel_path for walked in walking._walk_confined(root, root).files] == ["real.mp4"]


@WINDOWS_ONLY
def test_walk_confined_skips_a_junction_that_leaves_the_root(tmp_path: Path) -> None:
    """The same refusal, on the platform Sift ships on, by the escape somebody there can make.

    This is the case where the difference matters most: the walk refuses a LINK at every step
    below the base, and a junction is not a link, so the only thing standing between the scan and
    a stranger's files is the base resolving outside the root.
    """
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.mp4").write_bytes(b"x")
    link = root / "link"
    junction(link, outside)

    refused = walking._walk_confined(root, link)
    assert refused.files == ()
    # And it says it did not LOOK, which is the half that matters: a walk that found nothing and a
    # walk that never happened are the same empty answer, and the folder reconciliation deletes on
    # the strength of one of them.
    assert refused.looked is False

    (root / "real.mp4").write_bytes(b"y")
    assert [walked.rel_path for walked in walking._walk_confined(root, root).files] == ["real.mp4"]


# --- the awkward corners --------------------------------------------------------------------
#
# Each of these is a real thing a library does to a scanner, and each was reachable only by
# arranging it deliberately: a file that goes away mid-walk, a folder row that has lost its root,
# a payload carrying the wrong sort of thing. They are here because a scanner that gets one of them
# wrong fails on somebody's library at three in the morning, not in a test.


def test_a_file_that_vanishes_between_being_listed_and_being_asked_about_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A walk is not atomic. Something else is allowed to be deleting files while it runs.

    `scandir` hands back a name, and the stat that follows is a second question, by which time
    the file can be gone. It is skipped rather than raised: the next pass will find it, or it was
    never really there.
    """
    root = tmp_path / "root"
    root.mkdir()
    (root / "going.mp4").write_bytes(b"x")
    (root / "staying.mp4").write_bytes(b"y")

    real_stat = os.DirEntry.stat

    def stat_that_loses_one(self: os.DirEntry[str], **kwargs: Any) -> os.stat_result:
        if self.name == "going.mp4":
            raise OSError(2, "No such file or directory")
        return real_stat(self, **kwargs)

    monkeypatch.setattr(os.DirEntry, "stat", stat_that_loses_one)

    assert [walked.rel_path for walked in walking._walk_confined(root, root).files] == [
        "staying.mp4"
    ]


async def test_a_scan_payload_naming_something_that_is_not_a_folder_id_is_refused(
    context_for: Context,
    root: Root,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """`folder_id` comes off a queue payload, which is data. A number where an id should be is not
    a folder that cannot be found: it is a payload nobody should act on."""
    context = await context_for(jobs.SCAN, {"root_id": root.id, "folder_id": 12})

    with pytest.raises(ValueError, match="folder_id"):
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)


async def test_a_scan_of_a_root_whose_folder_row_is_gone_says_so(
    context_for: Context,
    root: Root,
    root_path: Path,
    temp_db: Database,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """The row standing for the root itself is an invariant every scan leans on. If it is not
    there, the scan cannot say where anything it finds belongs, so it stops rather than
    inventing somewhere.

    A file has to be there for the scan to get far enough to need the answer: a walk that finds
    nothing never asks which folder anything is in."""
    draw(root_path / "orphan.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    await temp_db.execute("DELETE FROM folders WHERE root_id = ?", (root.id,))
    context = await context_for(jobs.SCAN, {"root_id": root.id})

    with pytest.raises(walking.RootIsGone):
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)


async def test_a_file_that_could_not_be_opened_just_now_is_looked_at_again_next_pass(
    context_for: Context,
    root: Root,
    root_path: Path,
    temp_db: Database,
    settings: Settings,
    service: LibraryService,
    monkeypatch: pytest.MonkeyPatch,
    reindexer: RecordingReindexer,
) -> None:
    """A refusal is remembered against the bytes at a path. A file another program held open, or
    a share that was away, is not a fact about the bytes: remembered, it would be walked past on
    every pass until "look again" was pressed. Not remembered, it is read the next time."""
    from sift.kernel.ingress import IngressRejected, Reason

    draw(root_path / "held.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    opened = 0
    real_gate = verify_ingress

    def gate_that_fails_once(*args: object, **kwargs: object) -> object:
        nonlocal opened
        opened += 1
        if opened == 1:
            raise IngressRejected(Reason.UNREADABLE)
        return real_gate(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(taking_in, "verify_ingress", gate_that_fails_once)

    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    refused = await temp_db.fetch_all("SELECT * FROM scan_rejections WHERE root_id = ?", (root.id,))
    assert refused == [], "a file that could not be opened just now is not a refused file"

    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    assert opened == 2, "the second pass reads it again"
    assert await assets_in(temp_db) == 1


async def test_a_scan_that_sees_a_file_again_clears_what_a_moment_said_about_it(
    context_for: Context,
    root: Root,
    root_path: Path,
    temp_db: Database,
    settings: Settings,
    service: LibraryService,
    content_store: ContentStore,
    reindexer: RecordingReindexer,
) -> None:
    """A transient verdict (the share was away, the file was held open) is about a moment. The
    next pass that finds the file present and unchanged is the moment that has passed; a standing
    verdict, about the bytes, stays."""
    from sift.kernel.content import VerdictProduct

    draw(root_path / "seen.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    rows = await temp_db.fetch_all("SELECT id FROM assets", ())
    asset_id = str(rows[0]["id"])
    await content_store.record_verdict(
        asset_id, VerdictProduct.FACES, code="no_copy", reason="away", transient=True
    )
    await content_store.record_verdict(
        asset_id, VerdictProduct.THUMBNAILS, code="no_frame", reason="none", transient=False
    )

    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    assert [v.product for v in await content_store.verdicts_of(asset_id)] == ["thumbnails"]


async def test_a_file_that_has_stayed_empty_is_remembered_and_one_just_made_is_not(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """A zero-byte file a moment old may be about to be written, so it is looked at again. One that
    has sat empty for longer than a writer takes to start is an empty file: read again on every pass
    it would never be shown to anybody, so it is remembered like any other refusal, against its size
    and time, and a byte written to it brings it back."""
    old = root_path / "old.mp4"
    old.write_bytes(b"")
    long_ago = time.time_ns() - (taking_in.EMPTY_SETTLED_SECONDS + 60) * 1_000_000_000
    os.utime(old, ns=(long_ago, long_ago))
    (root_path / "new.mp4").write_bytes(b"")

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    remembered = await service._db.fetch_all(
        "SELECT rel_path, reason FROM scan_rejections WHERE root_id = ? ORDER BY rel_path",
        (root.id,),
    )
    assert [(row["rel_path"], row["reason"]) for row in remembered] == [("old.mp4", "empty")]


async def test_a_file_still_being_written_is_left_for_the_next_pass_not_recorded_as_refused(
    context_for: Context,
    root: Root,
    root_path: Path,
    temp_db: Database,
    settings: Settings,
    service: LibraryService,
    content_store: ContentStore,
    monkeypatch: pytest.MonkeyPatch,
    reindexer: RecordingReindexer,
) -> None:
    """Half-written is not the same as unreadable, and writing it down as refused would outlive
    the write: the file would finish, be perfectly good, and go on being skipped."""
    draw(root_path / "growing.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)

    from sift.kernel.content import FileStillChanging

    async def still_growing(*args: object, **kwargs: object) -> None:
        raise FileStillChanging("the file is still being written")

    monkeypatch.setattr(content_store, "ingest", still_growing)

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    refused = await temp_db.fetch_all("SELECT * FROM scan_rejections WHERE root_id = ?", (root.id,))
    assert refused == [], "a file that was still being written is not a file that was refused"


async def test_a_file_the_walk_missed_but_which_is_really_there_is_left_alone(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
    reindexer: RecordingReindexer,
) -> None:
    """The sweep marks what it did not see as missing. It checks first, and this is why.

    A folder that became unreadable half way through a pass, or a file the hint list stopped
    matching, is a file the walk did not report and which is sitting on the disk the whole time.
    Marking it missing takes it off the grid for somebody who has not lost anything.
    """
    draw(root_path / "seen.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    draw(root_path / "hidden.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    # A second pass that cannot see one of them, though it is still on the disk.
    real_walk = walking._walk_confined
    monkeypatch.setattr(
        jobs,
        "_walk_confined",
        lambda base, start: _without(real_walk(base, start), "hidden.mp4"),
    )
    again = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)

    rows = await temp_db.fetch_all("SELECT rel_path, status FROM asset_locations ORDER BY rel_path")
    assert [(row["rel_path"], row["status"]) for row in rows] == [
        ("hidden.mp4", "present"),
        ("seen.mp4", "present"),
    ]


def _without(walk: walking.Walk, name: str) -> walking.Walk:
    """The same walk with one file taken out of it, as though the walk had not seen that file.

    A walk that did not report a file it can still see is the case the sweep's own check exists for.
    Built by hand rather than by filtering the walk into a list, because a walk is not a list any
    more: it carries the directories it went through and whether it managed to look at all, and a
    test that threw those away would be testing a different pass from the one that runs.
    """
    return walking.Walk(
        files=tuple(one for one in walk.files if one.rel_path != name),
        directories=walk.directories,
        looked=walk.looked,
    )


async def test_a_file_already_known_to_be_missing_is_not_swept_again(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
) -> None:
    """A file deleted a fortnight ago is swept past, not marked missing a second time.

    The sweep asks the disk about anything the walk did not report, and asking about every file
    that has ever been deleted is a scan that gets slower for the rest of the library's life.
    """
    draw(root_path / "gone.mp4", "testsrc=size=64x64:rate=10", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)

    (root_path / "gone.mp4").unlink()
    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    # A third pass, with the location already recorded as missing.
    third = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(third, settings=settings, service=service, reindexer=reindexer)

    rows = await temp_db.fetch_all("SELECT rel_path, status FROM asset_locations")
    assert [(row["rel_path"], row["status"]) for row in rows] == [("gone.mp4", "missing")]


async def test_the_scan_memory_is_left_alone_when_it_is_already_current(
    temp_db: Database, service: LibraryService, root: Root
) -> None:
    """An initializer told there is no gap must do nothing at all.

    "Nothing" is the difference between a quiet boot and one that rewrites a table it was only
    meant to read the version of.
    """
    from sift.slices.library_roots.schema import SCAN_MEMORY_VERSION, initialize_scan_memory

    await temp_db.execute(
        "INSERT INTO scan_rejections"
        " (id, root_id, rel_path, size_bytes, mtime_ns, reason, detected, first_seen_at,"
        " last_seen_at) VALUES ('r', ?, 'a.mp4', 1, 1, 'why', 'what', 0, 0)",
        (root.id,),
    )

    async with temp_db.write() as connection:
        await initialize_scan_memory(connection, on_disk=SCAN_MEMORY_VERSION)

    survivors = await temp_db.fetch_all("SELECT id FROM scan_rejections")
    assert [row["id"] for row in survivors] == ["r"]


async def test_a_refusal_under_the_retired_name_rule_is_forgotten_once_so_a_scan_reads_it_again(
    temp_db: Database, service: LibraryService, root: Root
) -> None:
    """A picture refused because its name named another kind is read again by the next scan.

    A remembered refusal is walked past while the file is unchanged, and a PNG named `.jpg` does
    not change on its own. The gate takes such a file by its bytes, so the step forgets exactly
    those refusals and leaves every other one where it is.
    """
    from sift.slices.library_roots.schema import initialize_scan_memory

    for row_id, reason in (
        ("named", "extension_contradicts_signature"),
        ("noise", "signature_not_allowed"),
    ):
        await temp_db.execute(
            "INSERT INTO scan_rejections"
            " (id, root_id, rel_path, size_bytes, mtime_ns, reason, detected, first_seen_at,"
            " last_seen_at) VALUES (?, ?, ?, 1, 1, ?, NULL, 0, 0)",
            (row_id, root.id, f"{row_id}.jpg", reason),
        )

    async with temp_db.write() as connection:
        await initialize_scan_memory(connection, on_disk=1)

    survivors = await temp_db.fetch_all("SELECT id FROM scan_rejections")
    assert [row["id"] for row in survivors] == ["noise"]
    assert await service.rejections_of_root(root.id) == {"noise.jpg": (1, 1)}


async def test_a_picture_refused_under_the_retired_rule_is_taken_in_by_the_next_scan(
    context_for: Context,
    temp_db: Database,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """The whole way: a refusal forgotten by the step, then an ordinary scan takes the file in."""
    from sift.slices.library_roots.schema import initialize_scan_memory

    # A PNG under a `.jpg` name, the way an image host serves one.
    drawn = root_path / "poster.png"
    draw(drawn, "testsrc2=size=64x48:rate=1")
    picture = drawn.rename(root_path / "poster.jpg")
    held = picture.stat()
    await temp_db.execute(
        "INSERT INTO scan_rejections"
        " (id, root_id, rel_path, size_bytes, mtime_ns, reason, detected, first_seen_at,"
        " last_seen_at) VALUES ('r', ?, 'poster.jpg', ?, ?, 'extension_contradicts_signature',"
        " 'image/png', 0, 0)",
        (root.id, held.st_size, held.st_mtime_ns),
    )
    async with temp_db.write() as connection:
        await initialize_scan_memory(connection, on_disk=1)

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)

    rows = await temp_db.fetch_all(
        "SELECT a.mime FROM asset_locations l JOIN assets a ON a.id = l.asset_id"
        " WHERE l.rel_path = 'poster.jpg'"
    )
    assert [row["mime"] for row in rows] == ["image/png"]
    assert await service.rejection_count() == 0


async def test_a_second_copy_of_an_unprobed_file_asks_for_the_probe_that_was_lost(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """The path the renamed file really took, and the one the recovery was written for.

    A file whose probe is lost and which then appears somewhere else (renamed, or copied) comes
    back as a second location of an asset that is no longer new. Nothing about it is new, so the
    "probe what is new" rule says nothing, and the file sits in the library with no duration, no
    codec and no thumbnail for ever.
    """
    draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)

    queued = await probes_waiting(job_queue)
    assert len(queued) == 1
    await job_queue.cancel(queued[0])

    # The same bytes, at a second path. Same asset, new location, and so far no `probe`.
    (root_path / "one.mp4").rename(root_path / "two.mp4")

    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    again = await job_queue.list(job_type=taking_in.PROBE, limit=100, parent_id=second.job.id)
    assert [job.type for job in again.jobs] == [taking_in.PROBE]


async def test_the_recovery_says_nothing_about_a_path_that_is_not_indexed(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
) -> None:
    """The guards in the recovery, which are about a race rather than about a rule.

    It is reached from the shortcut that skips an unchanged file, so by then there is normally a
    location and an asset behind it. Normally: a file removed from the library between the walk and
    this lookup leaves neither, and the recovery has to be able to say nothing rather than raise:
    the scan is most of the way through a pass and there is nothing wrong with the pass.
    """
    context = await context_for(jobs.SCAN, {"root_id": root.id})

    await taking_in._probe_if_it_never_was(context, root_id=root.id, rel_path="never-indexed.mp4")

    assert await probes_waiting(job_queue) == []


async def test_a_file_that_has_already_been_read_is_not_read_again(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    content_store: ContentStore,
    temp_db: Database,
    job_queue: JobQueue,
    reindexer: RecordingReindexer,
) -> None:
    """The recovery is for an asset that was never probed, and only for that one.

    Both halves of it are guarded on `probed_at`, and without the guard a scan would queue a probe
    for every file it walked past on every pass: the exact runaway the shortcut above exists to
    prevent, arriving through the door beside it.

    So the asset is marked as read the way a probe marks it, and then met twice: once at the path it
    is already indexed at, and once as a second copy, which is the route that skips the shortcut
    entirely. Neither should ask for anything.
    """
    draw(root_path / "one.mp4", "testsrc2=size=64x48:rate=5", VIDEO_SECONDS)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)

    for job_id in await probes_waiting(job_queue):
        await job_queue.cancel(job_id)

    # What a probe writes, without running one: this is about the scanner's reaction to a file that
    # has been read, and a real probe here would be testing the prober.
    rows = await temp_db.fetch_all("SELECT id FROM assets")
    await content_store.record_probe(str(rows[0]["id"]), width=64, height=48)

    # The same bytes at a second path, so the walk meets a known, probed asset as a new location.
    (root_path / "two.mp4").write_bytes((root_path / "one.mp4").read_bytes())

    second = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(second, settings=settings, service=service, reindexer=reindexer)

    assert await probes_waiting(job_queue) == []


# --- telling whoever is listening which folders a pass went through ------------------------------
#
# One call per DIRECTORY, after the sweep, so a folder is judged on what is really still in it. What
# is listening is the rule that turns a folder of pictures into a shoot; nothing here knows that.


async def test_every_folder_a_pass_walked_is_offered_once(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """Per directory rather than per file: three pictures in one folder are one offer, not three."""
    for name in ("a.png", "b.png", "c.png"):
        draw(root_path / "shoot" / name, "testsrc2=size=64x48:rate=1")
    offered: list[tuple[str, str]] = []

    async def settled(folder_id: str, name: str) -> None:
        offered.append((folder_id, name))

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(
        context,
        settings=settings,
        service=service,
        reindexer=reindexer,
        folder_settled=settled,
    )

    # Once each, and the library's own folder is one of them: the walk started there, so it saw it.
    assert [name for _, name in offered].count("shoot") == 1
    assert len({folder_id for folder_id, _ in offered}) == len(offered)


async def test_a_folder_that_indexed_nothing_is_still_a_folder_sift_knows(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
) -> None:
    """A folder Sift took no file from is still a folder on the disk, and is offered like any other.

    A folder is not a by-product of indexing a file: a folder somebody has just made, or one
    holding only files Sift will not open, has to exist in the application, or it could not be
    shared, downloaded into, or browsed to. What decides whether such a folder becomes anything is
    the listener, which asks how many pictures are in it and answers none.
    """
    refused_file(root_path / "rejects" / "not-really.mp4")
    draw(root_path / "shoot" / "a.png", "testsrc2=size=64x48:rate=1")
    offered: list[str] = []

    async def settled(_folder_id: str, name: str) -> None:
        offered.append(name)

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(
        context,
        settings=settings,
        service=service,
        reindexer=reindexer,
        folder_settled=settled,
    )

    assert sorted(offered).count("rejects") == 1
    assert sorted(offered).count("shoot") == 1


async def test_a_listener_that_falls_over_does_not_take_the_scan_down(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    temp_db: Database,
) -> None:
    """A scan that indexed a library and then failed on the way out would be re-run, re-walking
    everything, for the sake of a grouping that is a convenience. The files stay indexed."""
    draw(root_path / "shoot" / "a.png", "testsrc2=size=64x48:rate=1")

    async def settled(_folder_id: str, _name: str) -> None:
        raise RuntimeError("no")

    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(
        context,
        settings=settings,
        service=service,
        reindexer=reindexer,
        folder_settled=settled,
    )

    assert await assets_in(temp_db) == 1


# --- ageing the quarantine directory out, on a schedule ----------------------------------------


class _Retention:
    """A stored retention rule, as the job reads it."""

    def __init__(self, days: object) -> None:
        self._days = days

    async def get_app(self, key: str) -> object:
        return self._days if key == quarantine.KEEP_DAYS_KEY else None

    async def get_user(self, user_id: str, key: str) -> object:
        return None


def _quarantine(settings: Settings, name: str, *, at: int) -> Path:
    """One file in the quarantine directory, with the note the ingress gate writes beside it."""
    import json

    from sift.kernel.ingress import NOTE_SUFFIX

    settings.quarantine_dir.mkdir(parents=True, exist_ok=True)
    target = settings.quarantine_dir / name
    target.write_bytes(b"refused bytes")
    target.with_name(target.name + NOTE_SUFFIX).write_text(
        json.dumps({"reason": "not_decodable", "quarantined_at": at}), encoding="utf-8"
    )
    return target


async def test_the_sweep_removes_what_is_past_the_rule_and_leaves_the_next_run_to_the_scheduler(
    settings: Settings, job_queue: JobQueue, context_for: Context
) -> None:
    """The sweep deletes and says what it did; it does NOT queue its own next run.

    Every timed task's next run is placed by the one scheduler (`kernel.jobs.clock`) as its run
    settles. A sweep queuing itself with its own interval would ignore quiet hours and be a second
    writer of the row the scheduler places (see `kernel/tests/test_task_clock.py` for the rules
    there: nothing while retention is off, one run and never a second).
    """
    import time as clock

    now = int(clock.time())
    old = _quarantine(settings, "old.bin", at=now - 40 * 86_400)
    recent = _quarantine(settings, "recent.bin", at=now - 3 * 86_400)
    context = await context_for(jobs.QUARANTINE_PRUNE, {})

    await jobs.prune_quarantine(
        context, settings=settings, preferences=_Retention(30), queue=job_queue
    )

    assert not old.exists()
    assert recent.exists(), "and what is inside the rule is left alone"
    waiting = await job_queue.list(job_type=jobs.QUARANTINE_PRUNE, state=JobState.QUEUED, limit=5)
    assert waiting.total == 0, "the next run is the scheduler's to place, not the sweep's"


async def test_the_sweep_deletes_nothing_when_retention_is_off(
    settings: Settings, job_queue: JobQueue, context_for: Context
) -> None:
    """Zero days is keep everything, which is also what a PRESS of Run now on a rule of zero does.

    A press always runs (the task model's one promise), so the sweep itself has to be safe to run
    with the rule off: "keep for zero days" is an off switch, never an instruction to empty the
    folder.
    """
    kept = _quarantine(settings, "old.bin", at=0)
    context = await context_for(jobs.QUARANTINE_PRUNE, {})

    await jobs.prune_quarantine(
        context, settings=settings, preferences=_Retention(0), queue=job_queue
    )

    assert kept.exists(), "nothing is removed with the rule switched off"


# --- one walk, one job, and a press that is waited on --------------------------------------------


@pytest.mark.unit
def test_a_walk_of_the_roots_own_top_folder_is_the_whole_root(root: Root) -> None:
    """Two spellings of one walk, which the queue alone cannot see are one.

    A press names no folder; the watcher names the root's own top folder, because that is the row
    it has when something changes in the root's own directory. `enqueue(dedupe=True)` matches the
    serialized payload exactly, so the two would never collide and the same folder would be walked
    twice at the same time, for one answer.

    They MEAN the same walk: `_scope` resolves a missing folder id to `ROOT_REL_PATH`, and
    the top folder's own `rel_path` is `ROOT_REL_PATH`.
    """
    top = FolderRow(id="01HQTOP", root_id=root.id, parent_id=None, rel_path=ROOT_REL_PATH, name="m")

    assert jobs.scan_shape(root.id, top) == jobs.scan_shape(root.id) == {"root_id": root.id}


@pytest.mark.unit
def test_a_walk_of_a_folder_inside_the_root_stays_a_job_of_its_own(root: Root) -> None:
    """The known negative, and it is the whole point of the watcher naming a folder: two different
    sub-folders are two walks and both are needed."""
    one = FolderRow(id="01HQA", root_id=root.id, parent_id="01HQTOP", rel_path="a", name="a")
    two = FolderRow(id="01HQB", root_id=root.id, parent_id="01HQTOP", rel_path="b", name="b")

    assert jobs.scan_shape(root.id, one) == {"root_id": root.id, "folder_id": "01HQA"}
    assert jobs.scan_shape(root.id, one) != jobs.scan_shape(root.id, two)
    assert jobs.scan_shape(root.id, one) != jobs.scan_shape(root.id)


async def test_a_press_and_a_watchers_whole_root_walk_are_one_run(
    root: Root, job_queue: JobQueue, handlers: None
) -> None:
    """The two shapes at the same second collapse to one job."""
    _ = handlers
    top = FolderRow(id="01HQTOP", root_id=root.id, parent_id=None, rel_path=ROOT_REL_PATH, name="m")

    pressed = await job_queue.enqueue(jobs.SCAN, jobs.scan_shape(root.id), dedupe=True)
    watched = await job_queue.enqueue(jobs.SCAN, jobs.scan_shape(root.id, top), dedupe=True)

    assert pressed == watched
    assert (await job_queue.list(job_type=jobs.SCAN)).total == 1


async def test_two_different_sub_folders_are_two_runs(
    root: Root, job_queue: JobQueue, handlers: None
) -> None:
    """The known positive beside it: the dedupe must still tell real work apart."""
    _ = handlers
    one = FolderRow(id="01HQA", root_id=root.id, parent_id="01HQTOP", rel_path="a", name="a")
    two = FolderRow(id="01HQB", root_id=root.id, parent_id="01HQTOP", rel_path="b", name="b")

    await job_queue.enqueue(jobs.SCAN, jobs.scan_shape(root.id, one), dedupe=True)
    await job_queue.enqueue(jobs.SCAN, jobs.scan_shape(root.id, two), dedupe=True)

    assert (await job_queue.list(job_type=jobs.SCAN)).total == 2


async def test_a_pressed_library_scan_is_claimed_before_a_queued_machine_job(
    context_for: Context,
    root: Root,
    job_queue: JobQueue,
) -> None:
    """The parts inherit the press's own urgency, and that is what makes pressing Scan mean
    anything.

    With every job in the queue at one priority, the order is arrival alone, and a press can wait
    minutes behind a thousand file reads.

    This pass only reads the list of folders and hands them out (it finishes in milliseconds), so
    the urgency has to travel to the parts or the press is at the front of the queue and the work it
    asked for is at the back. Read off the row rather than named here, so a pass the machine started
    hands its parts the machine's priority.
    """
    _ = root
    behind = await job_queue.enqueue("probe", {"asset_id": "01HQ00000000000000000000AA"})

    context = await context_for(jobs.LIBRARY_SCAN, {}, priority=WAITED_ON_PRIORITY)
    await jobs.scan_everything(context)

    claimed = await job_queue.claim("worker-one")
    assert claimed is not None
    assert claimed.type == jobs.SCAN, "the pressed walk waited behind a file read"
    assert claimed.priority == WAITED_ON_PRIORITY
    assert claimed.id != behind


async def test_a_machine_started_library_scan_leaves_its_parts_at_the_ordinary_priority(
    context_for: Context,
    root: Root,
    job_queue: JobQueue,
) -> None:
    """The known negative: the urgency is the press's, not the pass's. A scheduled sweep must not
    put itself in front of the reads somebody is watching."""
    _ = root
    context = await context_for(jobs.LIBRARY_SCAN, {})
    await jobs.scan_everything(context)

    page = await job_queue.list(limit=10, parent_id=context.job.id)
    assert [one.priority for one in page.jobs] == [DEFAULT_PRIORITY]


async def _sweep_one_gone_copy(tmp_path: Path, *, marked: bool) -> None:
    """Sweep a root holding one copy that is not on the disk, with `mark_missing` answering `marked`.

    Fakes rather than a scan, because what is under test is the arithmetic after the write: two
    real passes racing over one root cannot be ordered on demand, and the one fact that matters
    (the write found the row already missing) is exactly what `mark_missing` returns.
    """
    location = SimpleNamespace(id="a-copy", rel_path="gone.mp4", inside_an_archive=False)

    async def locations(root_id: str, *, under: str) -> Any:
        yield location

    async def path_of(copy: Any) -> Path:
        return tmp_path / str(copy.rel_path)

    async def mark_missing(location_id: str) -> bool:
        return marked

    context = SimpleNamespace(
        library=SimpleNamespace(iter_locations_in_root=locations),
        content=SimpleNamespace(path_of=path_of, mark_missing=mark_missing),
    )
    await sweeping._sweep(context, root_id="a-root", root_abs=tmp_path, under="", seen=set())  # type: ignore[arg-type]


def _marked_missing_events(
    logs: list[MutableMapping[str, Any]],
) -> list[MutableMapping[str, Any]]:
    """The sweep's count line, read as the event it is rather than as rendered text.

    Read off the log call itself because the rendered form depends on which renderer and which
    stream the process configured first: under a capture of stdout the line can go to a stream
    bound before the capture began, and the test then reads an empty string and fails on a sweep
    that logged exactly what it should.
    """
    return [entry for entry in logs if entry["event"] == "library.scan_marked_missing"]


async def test_a_sweep_counts_the_copies_it_marked_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The known positive: without it the test below passes on a sweep that never logs at all.
    uncached_log(monkeypatch, sweeping)
    with capture_logs() as logs:
        await _sweep_one_gone_copy(tmp_path, marked=True)

    assert [entry["files"] for entry in _marked_missing_events(logs)] == [1]


async def test_a_sweep_does_not_count_a_copy_another_pass_already_marked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder deleted, and the watcher's scan and a Scan press running together over the root,
    must not each log `files: 2` for the same two copies. The row is written once; the count has to
    be of what THIS pass wrote."""
    uncached_log(monkeypatch, sweeping)
    with capture_logs() as logs:
        await _sweep_one_gone_copy(tmp_path, marked=False)

    assert _marked_missing_events(logs) == []


async def finished(context: JobContext) -> None:
    """Finish a job a test ran by hand, as the pool would once its handler returned. `context_for`
    claims a job and leaves it running; a catch-up then sees a walk of the whole library still
    under way and leaves the library to it."""
    assert await context.queue.complete(context.job.id, context.worker_id)


async def scans_asked_for(queue: JobQueue) -> list[dict[str, object]]:
    page = await queue.list(job_type=jobs.SCAN, limit=200)
    return [job.payload for job in page.jobs]
