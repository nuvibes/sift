# SPDX-License-Identifier: AGPL-3.0-or-later
"""A walk's plan: written before the reads, checkpointed a batch at a time, and what a restart or a
cancel leaves owed."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from sift.kernel import lanes
from sift.kernel.config import Settings
from sift.kernel.content import Root
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import (
    STOP_TO_CANCEL,
    JobCanceled,
    JobContext,
    JobHeld,
    JobQueue,
    JobState,
    SystemCapabilities,
    queue_controls,
    queue_plans,
    recover,
)
from sift.slices.library_roots import canceling, jobs, sweeping, taking_in, walking
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.tests.conftest import RecordingReindexer, draw
from sift.slices.library_roots.tests.test_archive_scan import gallery

Context = Callable[..., Awaitable[JobContext]]


class Halt(Exception):
    """Stands in for the process going away mid-read."""


def pictures(root_path: Path, count: int) -> list[str]:
    names = [f"p{number}.png" for number in range(count)]
    for number, name in enumerate(names):
        draw(root_path / name, f"testsrc2=size={32 + 2 * number}x32")
    return names


@pytest.fixture
def small_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plan for six files, checkpointed every three, read one at a time in the walk's order."""
    monkeypatch.setattr(jobs, "PLAN_FROM", 1)
    monkeypatch.setattr(jobs, "PLAN_WRITE_BATCH", 3)
    monkeypatch.setattr(queue_plans, "PLAN_WRITE_BATCH", 2)
    monkeypatch.setattr(lanes, "reads_at_once", lambda _root: 1)


def taking(monkeypatch: pytest.MonkeyPatch, *, halt_at: int | None = None) -> list[str]:
    """The paths handed to the take-in, in order; `halt_at` stops the walk at that call."""
    taken: list[str] = []
    real = taking_in._take_in

    async def counted(context: JobContext, item: Any, **options: Any) -> set[str]:
        if halt_at is not None and len(taken) + 1 == halt_at:
            raise Halt
        taken.append(options["rel_path"])
        return await real(context, item, **options)

    monkeypatch.setattr(jobs, "_take_in", counted)
    return taken


async def rows(database: Database, sql: str, *params: object) -> list[Any]:
    return [tuple(row) for row in await database.fetch_all(sql, params)]


async def claim_again(
    job_queue: JobQueue, capabilities: SystemCapabilities, job_id: str
) -> JobContext:
    worker_id = new_id()
    job = await job_queue.claim(worker_id)
    assert job is not None and job.id == job_id
    return JobContext(job=job, worker_id=worker_id, queue=job_queue, capabilities=capabilities)


async def test_a_restart_carries_on_from_the_last_checkpoint_and_owes_nothing(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    temp_db: Database,
    reindexer: RecordingReindexer,
    small_plan: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three files settled, a fourth taken in, then the process goes: the next claim opens neither
    the settled three nor decides them, tells the search about the fourth and asks for its probe,
    and the restart is not charged to the walk."""
    names = pictures(root_path, 6)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    taking(monkeypatch, halt_at=5)
    with pytest.raises(Halt):
        await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    assert await rows(
        temp_db, "SELECT settled FROM job_plan_marks WHERE job_id = ?", first.job.id
    ) == [(3,)]
    assert len(await rows(temp_db, "SELECT seq FROM job_plan WHERE job_id = ?", first.job.id)) == 6

    requeued, failed = await recover(job_queue)
    assert (requeued, failed) == ([first.job.id], [])
    again = await claim_again(job_queue, capabilities, first.job.id)
    assert again.job.attempts == 1, "the restart did not spend the walk's attempt"
    taken = taking(monkeypatch)
    await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)

    assert taken == names[3:]
    assets = {row[0] for row in await rows(temp_db, "SELECT id FROM assets")}
    assert len(assets) == 6
    assert assets <= set(reindexer.told)
    probed = await rows(temp_db, "SELECT payload FROM jobs WHERE type = ?", taking_in.PROBE)
    assert {json.loads(row[0])["asset_id"] for row in probed} == assets
    assert await rows(temp_db, "SELECT job_id FROM job_plan") == []
    assert await rows(temp_db, "SELECT job_id FROM job_plan_marks") == []


async def test_a_walk_resumed_onto_a_folder_that_does_not_answer_waits_and_owes_nothing(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    temp_db: Database,
    reindexer: RecordingReindexer,
    small_plan: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The process goes past a checkpoint, and at the restart the library folder is away: the walk
    is held with its attempt handed back, and the file taken in since the checkpoint has its
    probe asked for and its index entry, as the checkpoint would have given it."""
    pictures(root_path, 6)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    taking(monkeypatch, halt_at=5)
    with pytest.raises(Halt):
        await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    await temp_db.execute("DELETE FROM jobs WHERE type = ?", (taking_in.PROBE,))
    reindexer.told.clear()

    await recover(job_queue)
    again = await claim_again(job_queue, capabilities, first.job.id)
    monkeypatch.setattr(jobs, "_root_answer", lambda _path: OSError(2, "gone"))
    with pytest.raises(walking.RootUnreachable) as held:
        await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)

    assert isinstance(held.value, JobHeld)
    taken = {row[0] for row in await rows(temp_db, "SELECT id FROM assets")}
    assert len(taken) == 4
    probed = await rows(temp_db, "SELECT payload FROM jobs WHERE type = ?", taking_in.PROBE)
    owed = {json.loads(row[0])["asset_id"] for row in probed}
    assert len(owed) == 1 and owed <= taken, "the one taken in past the checkpoint"
    assert owed <= set(reindexer.told)


async def test_a_cancel_still_landing_in_chunks_leaves_no_probe_unasked(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    temp_db: Database,
    reindexer: RecordingReindexer,
    small_plan: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The walk hears its stop after the cancel's first chunk: its probes still waiting for the
    later chunks are asked again as surely as the ones already canceled."""
    pictures(root_path, 3)
    monkeypatch.setattr(jobs, "PROBE_HANDOUT_BATCH", 1)
    context = await context_for(jobs.SCAN, {"root_id": root.id})

    monkeypatch.setattr(queue_controls, "_STOP_CHUNK", 1)

    async def canceled_a_row_at_a_time(*_args: Any, **_options: Any) -> None:
        await job_queue.cancel(context.job.id)
        context.told_to_stop(STOP_TO_CANCEL)
        raise JobCanceled(context.job.id)

    monkeypatch.setattr(jobs, "_sweep", canceled_a_row_at_a_time)
    with pytest.raises(JobCanceled):
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)
    await canceling.asked()

    asked = await rows(
        temp_db,
        "SELECT payload FROM jobs WHERE type = ? AND parent_id IS NULL",
        taking_in.PROBE,
    )
    assets = {row[0] for row in await rows(temp_db, "SELECT id FROM assets")}
    assert len(assets) == 3
    assert {json.loads(row[0])["asset_id"] for row in asked} == assets


async def test_a_resumed_walk_canceled_while_it_decides_owes_nothing_past_the_mark(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    temp_db: Database,
    reindexer: RecordingReindexer,
    small_plan: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file the cut claim took in past its checkpoint has its probe and its index entry even when
    the resumed claim is canceled before its decide finds it."""
    pictures(root_path, 6)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    taking(monkeypatch, halt_at=5)
    with pytest.raises(Halt):
        await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    await temp_db.execute("DELETE FROM jobs WHERE type = ?", (taking_in.PROBE,))
    reindexer.told.clear()
    await recover(job_queue)
    again = await claim_again(job_queue, capabilities, first.job.id)

    async def canceled_while_deciding(*_args: Any, **_options: Any) -> Any:
        await job_queue.cancel(again.job.id)
        again.told_to_stop(STOP_TO_CANCEL)
        raise JobCanceled(again.job.id)

    monkeypatch.setattr(jobs, "_decide", canceled_while_deciding)
    with pytest.raises(JobCanceled):
        await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)
    await canceling.asked()

    probed = await rows(temp_db, "SELECT payload FROM jobs WHERE type = ?", taking_in.PROBE)
    owed = {json.loads(row[0])["asset_id"] for row in probed}
    assert len(owed) == 1, "the one taken in past the checkpoint"
    assert owed <= set(reindexer.told)


async def test_a_walk_canceled_while_it_waits_after_a_restart_owes_nothing(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    temp_db: Database,
    reindexer: RecordingReindexer,
    small_plan: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A walk requeued by a restart and canceled before its next claim: no claim runs to clean up,
    so its row's cancel asks again for every probe it handed out and for what it took in past its
    checkpoint, and tells the search of those."""
    pictures(root_path, 6)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    taking(monkeypatch, halt_at=5)
    with pytest.raises(Halt):
        await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    reindexer.told.clear()
    await recover(job_queue)

    await job_queue.cancel(first.job.id)
    await canceling.asked()

    taken = {row[0] for row in await rows(temp_db, "SELECT id FROM assets")}
    assert len(taken) == 4
    asked = await rows(
        temp_db,
        "SELECT payload FROM jobs WHERE type = ? AND state = ? AND parent_id IS NULL",
        taking_in.PROBE,
        JobState.QUEUED.value,
    )
    assert {json.loads(row[0])["asset_id"] for row in asked} == taken
    assert len(reindexer.told) == 1, "the one taken in past the checkpoint"
    assert await rows(temp_db, "SELECT job_id FROM job_plan") == []


async def test_a_claim_that_settled_nothing_is_charged_for_the_restart(
    job_queue: JobQueue, handlers: None
) -> None:
    """A job that takes the process down before it settles anything still runs out of attempts."""
    job_id = await job_queue.enqueue(jobs.SCAN, {"root_id": "r"})
    first = new_id()
    assert (await job_queue.claim(first)) is not None
    assert await job_queue.settle_plan(job_id, first, 1)
    assert await job_queue.refund_progressed() == [job_id]
    await job_queue.reclaim(stale_after=None, error="restarted")
    second = new_id()
    job = await job_queue.claim(second)
    assert job is not None and job.attempts == 1

    assert await job_queue.refund_progressed() == [], "its mark is the last claim's"
    assert not await job_queue.settle_plan(job_id, first, 2), "fenced on the claim"


async def test_a_cancel_mid_read_leaves_every_file_with_its_probe_and_its_index_entry(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    temp_db: Database,
    reindexer: RecordingReindexer,
    small_plan: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cancel takes the walk's probes with it; the files it took in are asked again outside the
    stopped family, at the read and the picture, and the search is told."""
    pictures(root_path, 5)
    monkeypatch.setattr(jobs, "PROBE_HANDOUT_BATCH", 1)
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    real = taking_in._take_in
    calls: list[str] = []

    monkeypatch.setattr(canceling, "MAX_PAGE_SIZE", 1)

    async def cancel_at_three(context: JobContext, item: Any, **options: Any) -> set[str]:
        calls.append(options["rel_path"])
        claimed = await real(context, item, **options)
        if len(calls) == 3:
            # The first file was read meanwhile: it is owed nothing.
            async with temp_db.write() as connection:
                await connection.execute(
                    "UPDATE assets SET probed_at = 1 WHERE id IN"
                    " (SELECT asset_id FROM asset_locations WHERE rel_path = 'p0.png')"
                )
            await job_queue.cancel(context.job.id)
            context.told_to_stop(STOP_TO_CANCEL)
            # Cut off after its rows were written, before the walk heard of it.
            raise JobCanceled(context.job.id)
        return claimed

    monkeypatch.setattr(jobs, "_take_in", cancel_at_three)
    with pytest.raises(JobCanceled):
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)
    await canceling.asked()

    assets = {row[0] for row in await rows(temp_db, "SELECT id FROM assets")}
    assert len(assets) == 3
    assert assets <= set(reindexer.told)
    read = await rows(temp_db, "SELECT id FROM assets WHERE probed_at IS NOT NULL")
    waiting = await rows(
        temp_db,
        "SELECT payload, parent_id FROM jobs WHERE type = ? AND state = ?",
        taking_in.PROBE,
        JobState.QUEUED.value,
    )
    assert {json.loads(row[0])["asset_id"] for row in waiting} == assets - {read[0][0]}
    assert all(json.loads(row[0])["scan_only"] and row[1] is None for row in waiting)
    assert await rows(temp_db, "SELECT job_id FROM job_plan") == []


async def test_a_cancel_in_the_sweep_asks_again_for_every_probe_it_took(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    temp_db: Database,
    reindexer: RecordingReindexer,
    small_plan: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every probe was handed out by then, so every one went with the cancel."""
    pictures(root_path, 2)
    monkeypatch.setattr(jobs, "PROBE_HANDOUT_BATCH", 1)
    context = await context_for(jobs.SCAN, {"root_id": root.id})

    async def canceled(*_args: Any, **_options: Any) -> None:
        await job_queue.cancel(context.job.id)
        context.told_to_stop(STOP_TO_CANCEL)
        raise JobCanceled(context.job.id)

    monkeypatch.setattr(jobs, "_sweep", canceled)
    with pytest.raises(JobCanceled):
        await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)
    await canceling.asked()

    waiting = await rows(
        temp_db,
        "SELECT payload FROM jobs WHERE type = ? AND state = ? AND parent_id IS NULL",
        taking_in.PROBE,
        JobState.QUEUED.value,
    )
    assets = {row[0] for row in await rows(temp_db, "SELECT id FROM assets")}
    assert len(assets) == 2
    assert {json.loads(row[0])["asset_id"] for row in waiting} == assets


async def test_a_settled_archive_keeps_its_pictures_and_a_changed_file_is_decided_again(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    temp_db: Database,
    reindexer: RecordingReindexer,
    small_plan: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The settled archive is not opened again, yet the sweep still counts its pictures as seen;
    a file changed since the plan was written is decided afresh."""
    gallery(root_path / "a.zip", ["01.png", "02.png"], tmp_path)
    names = pictures(root_path, 4)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    taking(monkeypatch, halt_at=4)
    with pytest.raises(Halt):
        await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    draw(root_path / names[3], "testsrc2=size=96x64")
    await recover(job_queue)
    again = await claim_again(job_queue, capabilities, first.job.id)
    taken = taking(monkeypatch)
    # Asked of the archive one picture at a time: on a share, an index read over the network each.
    asked: list[str] = []
    real = sweeping._member_still_there

    async def looked(context: Any, location: Any, root_abs: Path) -> bool:
        asked.append(location.rel_path)
        return await real(context, location, root_abs)

    monkeypatch.setattr(sweeping, "_member_still_there", looked)
    await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)

    assert taken == names[2:]
    assert asked == [], "the settled archive's pictures were looked for one at a time"
    members = await rows(
        temp_db,
        "SELECT status FROM asset_locations WHERE archive_rel_path = ?",
        "a.zip",
    )
    assert [row[0] for row in members] == ["present", "present"]


async def test_a_walk_too_small_to_plan_writes_no_plan(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    temp_db: Database,
    reindexer: RecordingReindexer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under `PLAN_FROM` files a restart costs less than the plan's writes."""
    pictures(root_path, 2)
    written: list[str] = []
    monkeypatch.setattr(
        JobQueue,
        "write_plan",
        lambda *args: written.append("plan"),  # pragma: no cover
    )
    context = await context_for(jobs.SCAN, {"root_id": root.id})
    await jobs.scan(context, settings=settings, service=service, reindexer=reindexer)
    assert written == []
    assert await rows(temp_db, "SELECT job_id FROM job_plan_marks") == []


async def test_a_restart_decides_again_only_what_the_cut_claim_could_have_opened(
    context_for: Context,
    root: Root,
    root_path: Path,
    settings: Settings,
    service: LibraryService,
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    reindexer: RecordingReindexer,
    small_plan: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the checkpoint's window the plan's own verdict stands: no read of the rows per file."""
    pictures(root_path, 6)
    monkeypatch.setattr(jobs, "RECHECKED", 1)
    first = await context_for(jobs.SCAN, {"root_id": root.id})
    taking(monkeypatch, halt_at=5)
    with pytest.raises(Halt):
        await jobs.scan(first, settings=settings, service=service, reindexer=reindexer)
    await recover(job_queue)
    again = await claim_again(job_queue, capabilities, first.job.id)
    taking(monkeypatch, halt_at=3)
    decided: list[str] = []
    real = taking_in._decide

    async def counted(item: Any, **options: Any) -> taking_in.Verdict:
        decided.append(options["rel_path"])
        return await real(item, **options)

    monkeypatch.setattr(jobs, "_decide", counted)
    with pytest.raises(Halt):
        await jobs.scan(again, settings=settings, service=service, reindexer=reindexer)

    assert decided == ["p3.png"]
    assert again.units == 6
    # The file the first restart found taken in stays a read for the next, not an unchanged file.
    steps, settled = await job_queue.plan_of(again.job.id)
    assert settled == 3
    assert [(step.rel_path, step.verdict) for step in steps[3:]] == [
        ("p3.png", "read"),
        ("p4.png", "read"),
        ("p5.png", "read"),
    ]
