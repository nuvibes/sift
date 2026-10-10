# SPDX-License-Identifier: AGPL-3.0-or-later
"""What an ended walk still owes the files it took in: their probes and their search entries."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sift.kernel.jobs import MAX_PAGE_SIZE, STOP_TO_CANCEL, JobQueue, JobState
from sift.kernel.jobs.queue_plans import PLAN_WRITE_BATCH
from sift.kernel.log import get_logger
from sift.slices.library_roots.taking_in import PROBE, Verdict

if TYPE_CHECKING:
    from sift.kernel.content import ContentStore
    from sift.kernel.jobs import JobContext
    from sift.kernel.seams import ReindexSeam
    from sift.slices.library_roots.jobs import _ScanPass

log = get_logger(__name__)

#: The re-asks a cancel started, held until they end.
_ASKING: set[asyncio.Task[None]] = set()


async def ask_again_after_cancel(one: _ScanPass, error: BaseException) -> None:
    """What only the running claim knows when it stops: the probes it had not handed out, the read
    a cancel cut off after its rows were written, the files the search was not told of. What the
    rows hold is asked again where the walk's row is canceled (`owed_after_a_cancel`). A walk held
    or failed hands out what it took in since its last checkpoint, as the checkpoint would have."""
    context = one.context
    if context.stopping() != STOP_TO_CANCEL:
        if isinstance(error, Exception):
            await one.hand_out()
            await one.index_arrivals()
        return
    owed = set(one.to_probe) | set(one.to_check)
    for seq in one.started:
        if not one.done[seq] and one.decided[seq][2] is Verdict.READ:
            location = await context.content.location_at(one.root_id, one.decided[seq][1])
            if location is not None:
                owed.add(location.asset_id)
                one.taken_in.append(location.asset_id)
    await one.index_arrivals()
    asked = await _probes_asked_again(context.queue, context.content, sorted(owed))
    log.info("library.scan_canceled", root_id=one.root_id, probes_asked=asked)


async def owed_after_a_cancel(
    queue: JobQueue, content: ContentStore, reindexer: ReindexSeam, job_id: str
) -> None:
    """A settle listener for walks: a walk canceled wherever it was (running, or waiting after a
    restart) has every probe it handed out, and the files a cut claim took in past its mark, asked
    again from its rows, off the call that canceled it."""
    task = asyncio.ensure_future(_owed_from_the_rows(queue, content, reindexer, job_id))
    _ASKING.add(task)
    task.add_done_callback(_ASKING.discard)


async def asked() -> None:
    """Wait for every re-ask a cancel started."""
    await asyncio.gather(*_ASKING)


async def _owed_from_the_rows(
    queue: JobQueue, content: ContentStore, reindexer: ReindexSeam, job_id: str
) -> None:
    job = await queue.get(job_id)
    if job is None or job.state is not JobState.CANCELED:
        return
    root_id = str(job.payload.get("root_id"))
    past_the_mark = await _taken_past_the_mark(queue, content, job_id, root_id)
    await reindexer.touched_many(past_the_mark)
    owed = set(past_the_mark)
    offset = 0
    # Every probe the walk handed out, whatever its state: the cancel lands in chunks, so a page of
    # canceled ones read now would miss the chunks still to come. A read one is let go below.
    while True:
        page = await queue.list(
            parent_id=job_id, job_type=PROBE, limit=MAX_PAGE_SIZE, offset=offset
        )
        owed.update(str(one.payload["asset_id"]) for one in page.jobs)
        if len(page.jobs) < MAX_PAGE_SIZE:
            break
        offset += MAX_PAGE_SIZE
    asked = await _probes_asked_again(queue, content, sorted(owed))
    steps, _ = await queue.plan_of(job_id)
    await queue.forget_plan(job_id, steps[-1].seq + 1 if steps else 0)
    log.info("library.scan_cancel_settled", root_id=root_id, probes_asked=asked)


async def owed_by_an_earlier_claim(
    context: JobContext, root_id: str, reindexer: ReindexSeam
) -> None:
    """What the claim a restart cut short took in past its last checkpoint, for a walk that ends
    before it can decide again: their probes asked for and the search told."""
    owed = await _taken_past_the_mark(context.queue, context.content, context.job.id, root_id)
    await reindexer.touched_many(owed)
    asked = await _probes_asked_again(context.queue, context.content, sorted(set(owed)))
    log.info("library.scan_owed_handed_out", root_id=root_id, probes_asked=asked)


async def _taken_past_the_mark(
    queue: JobQueue, content: ContentStore, job_id: str, root_id: str
) -> list[str]:
    """The files a cut claim may have taken in past the plan's mark: its read steps within
    `RECHECKED` of it, as the resume decides again, those with a place now."""
    from sift.slices.library_roots.jobs import RECHECKED

    steps, settled = await queue.plan_of(job_id)
    taken: list[str] = []
    for step in steps:
        if settled <= step.seq < settled + RECHECKED and step.verdict == Verdict.READ:
            location = await content.location_at(root_id, step.rel_path)
            if location is not None:
                taken.append(location.asset_id)
    return taken


async def _probes_asked_again(queue: JobQueue, content: ContentStore, owed: list[str]) -> int:
    """A scan-only probe for each of these never read, a page to a read; how many were asked."""
    payloads: list[dict[str, object]] = []
    for at in range(0, len(owed), PLAN_WRITE_BATCH):
        page_of = owed[at : at + PLAN_WRITE_BATCH]
        unread = await content.unread_among(page_of)
        payloads += [{"asset_id": one, "scan_only": True} for one in page_of if one in unread]
    for at in range(0, len(payloads), PLAN_WRITE_BATCH):
        await queue.enqueue_many(PROBE, payloads[at : at + PLAN_WRITE_BATCH], dedupe=True)
    return len(payloads)
