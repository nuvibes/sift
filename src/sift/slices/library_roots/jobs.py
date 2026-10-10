# SPDX-License-Identifier: AGPL-3.0-or-later
"""Walking a library, and watching one.
`scan` reads and hashes without touching a file, refuses without moving one, and enqueues only
`probe`. It is resumable: identity is the content, so a second walk adds nothing."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from sift.kernel import lanes
from sift.kernel.config import Settings
from sift.kernel.content import (
    ROOT_REL_PATH,
    FolderRow,
    check_rel_path,
    subtree_prefix,
)
from sift.kernel.jobs import (
    BACKGROUND_PRIORITY,
    DEFAULT_PRIORITY,
    MAX_PAGE_SIZE,
    STOP_TO_CANCEL,
    WAITED_ON_PRIORITY,
    JobContext,
    JobQueue,
    JobState,
    JobSwitchedOff,
    register_handler,
)
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.queue_plans import PLAN_WRITE_BATCH, PlanStep
from sift.kernel.jobs.quiet_hours import AT_NOW
from sift.kernel.log import get_logger, timing_hook
from sift.kernel.seams import ReindexSeam, SettingsSeam
from sift.slices.library_roots import quarantine
from sift.slices.library_roots.catch_up import _differences, _folders_that_moved
from sift.slices.library_roots.moved_folders import _reconcile_folders
from sift.slices.library_roots.scan_plan import count_to_read, kind_by_name, size_said
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.sweeping import (
    _folder_for,
    _forget_gone_refusals,
    _record_what_was_seen,
    _recorded_in,
    _refusals_by_folder,
    _settle_folders,
    _sweep,
)
from sift.slices.library_roots.taking_in import (
    PROBE,
    Heartbeat,
    UnansweredFolder,
    Verdict,
    _decide,
    _probe_payload,
    _probe_unless_already_coming,
    _take_in,
)
from sift.slices.library_roots.walking import (
    WORTH_OPENING as WORTH_OPENING,
)
from sift.slices.library_roots.walking import (
    FolderIsGone,
    FolderStoppedAnswering,
    RootIsGone,
    RootUnreachable,
    _quiet_from,
    _root_answer,
    _walk_confined,
    look_at,
)

if TYPE_CHECKING:
    from sift.slices.library_roots.walking import Walk, Walked

log = get_logger(__name__)


SCAN = "scan"

#: Walking a folder and counting what its scan will read, while the scan waits. See `count_scan`.
SCAN_COUNT = "scan_count"

#: One pass over EVERY library folder, with a scan of each hanging off it. See `scan_everything`.
LIBRARY_SCAN = "library_scan"


#: Catching a library up with what changed while Sift was not running. See `reconcile`.
RECONCILE = "library_reconcile"


def scan_shape(root_id: str, folder: FolderRow | None = None) -> dict[str, object]:
    """What identifies one scan: a root, and the folder inside it, unless that folder IS the root.

    A whole-root walk is ONE job whoever asked: the queue dedupes on the payload exactly, so the
    root's own top folder is dropped and `{root_id}` is the only spelling. The key ORDER is
    load-bearing for the same reason: `root_id`, then `folder_id`, then whatever a caller adds.
    """
    shape: dict[str, object] = {"root_id": root_id}
    if folder is not None and folder.rel_path != ROOT_REL_PATH:
        shape["folder_id"] = folder.id
    return shape


async def queue_scan(
    queue: JobQueue,
    shape: Mapping[str, object],
    *,
    requested_by: str | None,
    priority: int = DEFAULT_PRIORITY,
) -> str:
    """Queue a walk, deduped, with the count that runs ahead of it. Returns the walk's id."""
    scan_id = await queue.enqueue(
        SCAN, shape, dedupe=True, priority=priority, requested_by=requested_by
    )
    await count_ahead(queue, scan_id, shape, at=AT_NOW if requested_by is not None else None)
    return scan_id


async def count_ahead(
    queue: JobQueue, scan_id: str, shape: Mapping[str, object], *, at: str | None
) -> None:
    """Queue the count of a walk, outside the scans' share so no other folder's read holds it."""
    # A named-path scan is a few hundred files at most, and nobody waits on its total.
    if "paths" not in shape:
        await queue.enqueue(
            SCAN_COUNT, {"scan_id": scan_id}, dedupe=True, priority=WAITED_ON_PRIORITY, at=at
        )


async def count_scan(context: JobContext, *, service: LibraryService) -> None:
    """Walk the folder of a scan still waiting and write how many files it will read onto its row.

    The scan lists the folder again when it runs, so this only ever sets the number sooner."""
    await context.set_units(0)
    scan_id = context.require_str("scan_id", "a count needs the id of the scan it is for")
    waiting = await context.queue.get(scan_id)
    if waiting is None or waiting.state is not JobState.QUEUED:
        log.info("library.count_not_needed", scan_id=scan_id)
        return
    root = await context.library.get_root(str(waiting.payload.get("root_id")))
    try:
        under = None if root is None else await _scope(context, root.id, waiting.payload)
    except (FolderIsGone, ValueError):
        under = None
    if root is None or under is None:
        # The scan says why when it runs.
        log.info("library.count_not_needed", scan_id=scan_id)
        return
    counted = await count_to_read(context, service, root, under=under)
    if counted is None:
        await context.set_note("The library folder gave no answer, so nothing was counted.")
        return
    written = await context.queue.set_waiting_units(scan_id, counted.files)
    await context.queue.set_to_read(scan_id, counted.kinds)
    await context.set_note(
        f"{counted.files:,} file{'' if counted.files == 1 else 's'},"
        f" {size_said(counted.size)} to import."
    )
    log.info(
        "library.scan_counted",
        root_id=root.id,
        to_read=counted.files,
        ahead=True,
        written=written,
        waited_seconds=max(0, int(time.time()) - waiting.created_at),
    )


#: A recurring job, so a server up for months still ages out refused bytes kept in the clear.
QUARANTINE_PRUNE = "quarantine_prune"


#: Daily: the rule is measured in days.
PRUNE_EVERY_SECONDS = 86_400


#: Told about each folder the walk SAW once the scan is done with it; injected at boot.
FolderSettled = Callable[[str, str], Awaitable[None]]


#: Told about an archive once every picture in it is taken in, with ids in the archive's order.
#: `(root_id, rel_path, name, asset_ids)`: the path alone does not identify an archive.
ArchiveSettled = Callable[[str, str, str, list[str]], Awaitable[None]]


#: Small enough that a stopped pass strands little; large enough not to write the queue per file.
PROBE_HANDOUT_BATCH = 100

#: A walk of fewer files is cheaper to decide again after a restart than to write down.
PLAN_FROM = 500

#: The steps past a checkpoint that its claim may have opened before it stopped: a checkpoint is
#: written every `PLAN_WRITE_BATCH` settled steps, and the reads take their places in the walk's
#: order, a lane's width at a time. Only these are decided again on a restart.
RECHECKED = 2 * PLAN_WRITE_BATCH

STOPPED_ANSWERING = (
    "The folder stopped answering partway through the scan, so nothing in it was marked missing"
    " or unreadable. Scan it again once it's back."
)


def _went_quiet(folders: int) -> str:
    return (
        f"{folders:,} folder{'' if folders == 1 else 's'} stopped answering partway through, so"
        f" nothing in {'it' if folders == 1 else 'them'} was marked missing or unreadable."
    )


async def _walk_for_scan(
    context: JobContext, root_id: str, root_abs: Path, base: Path, named: list[str] | None
) -> Walk:
    # The root is asked first, once, so an unplugged drive stops the pass with a sentence.
    refused = await asyncio.to_thread(_root_answer, root_abs)
    if refused is not None:
        log.warning("library.root_unreachable", root_id=root_id, error=refused.strerror)
        raise RootUnreachable(
            "the library folder did not answer"
            + (f" ({refused.strerror})" if refused.strerror else "")
            + ". Nothing was changed. Scan it again once it's back."
        )
    with timing_hook("library.scan.walk", root_id=root_id):
        # In a thread and handed over as a list; in the root's storage lane, since a walk lists
        # every folder.
        async with lanes.reading(root_abs / "walk"):
            return (
                await asyncio.to_thread(look_at, root_abs, named)
                if named is not None
                else await asyncio.to_thread(_walk_confined, root_abs, base)
            )


class _ScanPass:
    """One scan's decisions, reads and sweep, and what it remembers across its files."""

    def __init__(
        self,
        context: JobContext,
        *,
        settings: Settings,
        service: LibraryService,
        archive_settled: ArchiveSettled | None,
        root_id: str,
        root_abs: Path,
        under: str,
        named: list[str] | None,
        walk: Walk,
        reindexer: ReindexSeam,
    ) -> None:
        self.context = context
        self.reindexer = reindexer
        self.settings = settings
        self.service = service
        self.archive_settled = archive_settled
        self.root_id = root_id
        self.root_abs = root_abs
        self.under = under
        self.named = named
        self.walk = walk
        self.seen: set[str] = set()
        # Named paths are already root-relative; a wrong prefix makes the sweep mark a folder
        # missing.
        self.prefix = "" if named is not None else subtree_prefix(under)
        self.found = walk.files
        self.taken_in: list[str] = []
        self.refusals: dict[str, tuple[int, int]] = {}
        self.folders: dict[str, str] = {}
        self.decided: list[tuple[Walked, str, Verdict]] = []
        self.to_probe: list[str] = []
        self.to_check: list[str] = []
        self.finished = 0
        self.indexed = 0
        self.to_read = 0
        self.unread: Counter[str] = Counter()
        self.opened = 0
        # A scan ends for the folder somebody pressed it on; any other only for its library folder.
        self.answers_for = under if named is None and context.job.requested_by else ROOT_REL_PATH
        # Prefixes of the folders that would not answer: nothing under one is read or judged.
        self.unjudged = {subtree_prefix(self.prefix + one) for one in walk.unlisted}
        self.went_quiet: set[str] = set()
        self.beat = Heartbeat(context)
        # The plan: the steps a claim before a restart settled are `base`; this claim's follow.
        self.base = 0
        self.planned = False
        self.stale_steps = 0
        self.settled_before: list[tuple[str, Verdict]] = []
        self.taken_before: list[str] = []
        self.started: set[int] = set()
        self.done: list[bool] = []
        self.mark = 0
        self.checkpointed = 0
        self.settling = asyncio.Lock()

    @property
    def ended(self) -> bool:
        return subtree_prefix(self.answers_for) in self.unjudged

    async def stopped_answering(self, directory: Path) -> None:
        base = self.root_abs / self.answers_for
        quiet = (await asyncio.to_thread(_quiet_from, base, directory)).relative_to(self.root_abs)
        here = ROOT_REL_PATH if quiet == Path() else quiet.as_posix()
        self.went_quiet.add(here)
        self.unjudged.add(subtree_prefix(here))
        log.warning("library.folder_stopped_answering", root_id=self.root_id, whole=self.ended)

    def walked_dirs(self) -> set[str]:
        # Turned into folder rows once at the end, not per file.
        walked = {self.prefix + one for one in self.walk.directories}
        # The starting folder, which `walk.directories` leaves out; the library's own is ".".
        walked.add("." if self.under == ROOT_REL_PATH else self.under)
        return walked

    async def settle_moved_folders(self) -> None:
        """Settle the folder rows BEFORE anything is taken in, so a renamed folder costs one
        listing.
        Skipped for a named-path scan, which would read untold folders as vanished."""
        with timing_hook("library.scan.folders", root_id=self.root_id):
            await _reconcile_folders(
                self.context,
                root_id=self.root_id,
                under=self.under,
                walk=self.walk,
                prefix=self.prefix,
                service=self.service,
            )

    async def decide(self) -> None:
        # Every file is decided from the rows before any is opened, so the job can say how many it
        # will read.
        context = self.context
        self.refusals = await self.service.rejections_of_root(self.root_id)
        earlier = await self.earlier_plan()
        reading = 0
        for item in self.found:
            # The count lands as it grows, a beat at a time, never below one counted ahead.
            if await self.beat() and reading > context.units:
                await context.set_units(reading)
            rel_path = self.prefix + item.rel_path
            step = earlier.get(rel_path)
            if step is not None and (step.size, step.mtime_ns) != (item.size, item.mtime_ns):
                step = None
            if step is not None and step.seq < self.base:
                # Settled by the claim a restart cut short: neither decided nor opened again.
                self.settled_before.append((rel_path, Verdict(step.verdict)))
                reading += Verdict(step.verdict).reads
                continue
            if step is not None and step.seq >= self.base + RECHECKED:
                # Past anything that claim could have opened: its verdict stands.
                verdict = Verdict(step.verdict)
            else:
                verdict = await _decide(
                    item,
                    rel_path=rel_path,
                    root_id=self.root_id,
                    service=self.service,
                    context=context,
                    refused=self.refusals,
                )
            if step is not None and step.verdict == Verdict.READ and not verdict.reads:
                # Taken in by that claim after its last checkpoint, so owed its index entry, and
                # read by this walk.
                self.taken_before.append(rel_path)
                reading += 1
            self.decided.append((item, rel_path, verdict))
            reading += verdict.reads
        self.unread = Counter(
            kind_by_name(item, verdict) for item, _rel, verdict in self.decided if verdict.reads
        )
        # Files read of files to read: what an earlier claim settled counts as read.
        self.to_read = to_read = reading
        await context.set_units(to_read)
        await self.write_unread()
        await self.claim_settled()
        await self.write_plan()
        # Resolved once each before the concurrent reads, so two files of one directory do not race.
        for _item, rel_path, verdict in self.decided:
            if verdict is Verdict.READ:
                await _folder_for(context, self.root_id, rel_path, self.folders)
        log.info(
            "library.scan_counted", root_id=self.root_id, files=len(self.found), to_read=to_read
        )

    async def earlier_plan(self) -> dict[str, PlanStep]:
        """The plan an earlier claim of this walk wrote, by path; `base` is how much it settled."""
        if self.named is not None:
            return {}
        steps, self.base = await self.context.queue.plan_of(self.context.job.id)
        self.stale_steps = steps[-1].seq + 1 if steps else 0
        return {step.rel_path: step for step in steps}

    async def claim_settled(self) -> None:
        """Claim for the sweep what the settled steps claimed: the file, or an archive's pictures
        as the rows record them (one read of the root's rows, only when an archive settled)."""
        archives: set[str] = set()
        for rel_path, verdict in self.settled_before:
            self.opened += verdict.reads
            if verdict is Verdict.ARCHIVE:
                archives.add(rel_path)
            else:
                self.seen.add(rel_path)
        if archives:
            async for location in self.context.library.iter_locations_in_root(
                self.root_id, under=self.under
            ):
                if location.archive_rel_path in archives:
                    self.seen.add(location.rel_path)
        for rel_path in self.taken_before:
            found = await self.context.content.location_at(self.root_id, rel_path)
            # Read as unchanged a moment ago; only a writer racing this pass could make it go.
            if found is not None:  # pragma: no branch (a race)
                self.taken_in.append(found.asset_id)

    async def write_plan(self) -> None:
        """Write down what this claim will work through, after what an earlier one settled, so a
        restart carries on from the last checkpoint; a batch of steps per write."""
        self.done = [False] * len(self.decided)
        if self.named is not None or (not self.stale_steps and len(self.decided) < PLAN_FROM):
            return
        queue, job = self.context.queue, self.context.job
        if self.stale_steps > self.base:
            await queue.drop_plan_from(job.id, self.base, self.stale_steps)
        # A file taken in before the restart is still one this walk read, whatever it reads as now.
        read_before = set(self.taken_before)
        steps = [
            PlanStep(
                seq=self.base + seq,
                rel_path=rel_path,
                size=item.size,
                mtime_ns=item.mtime_ns,
                kind=kind_by_name(item, verdict),
                verdict=(Verdict.READ if rel_path in read_before else verdict).value,
            )
            for seq, (item, rel_path, verdict) in enumerate(self.decided)
        ]
        self.planned = await queue.write_plan(job.id, self.context.worker_id, steps)
        log.info("library.scan_planned", root_id=self.root_id, steps=len(steps), settled=self.base)

    async def settle(self, seq: int) -> None:
        """Mark one step done, and checkpoint the plan once a batch below the mark is done."""
        self.done[seq] = True
        while self.mark < len(self.done) and self.done[self.mark]:
            self.mark += 1
        if self.mark - self.checkpointed >= PLAN_WRITE_BATCH:
            await self.checkpoint()

    async def checkpoint(self) -> None:
        """Every file below the mark has its probe handed out and its index entry before the mark
        is written, so a restart from it owes nothing for them."""
        if not self.planned:
            return
        async with self.settling:
            through = self.mark
            if through <= self.checkpointed:
                return
            await self.hand_out()
            await self.index_arrivals()
            context = self.context
            await context.queue.settle_plan(context.job.id, context.worker_id, self.base + through)
            self.checkpointed = through

    async def forget_plan(self) -> None:
        if self.planned or self.stale_steps:
            await self.context.queue.forget_plan(
                self.context.job.id, max(self.stale_steps, self.base + len(self.decided))
            )

    async def if_canceled(self) -> None:
        """A cancel takes the walk's waiting probes with it. What it took in keeps its read and its
        place in the search: the probes are asked again outside the stopped family, at the floor
        a stopped scan gets (`read_unread`'s), and the search is told now."""
        context = self.context
        if context.stopping() != STOP_TO_CANCEL:
            return
        owed = set(self.to_probe) | set(self.to_check)
        queue, job = context.queue, context.job
        offset = 0
        while True:
            page = await queue.list(
                parent_id=job.id,
                job_type=PROBE,
                state=JobState.CANCELED,
                limit=MAX_PAGE_SIZE,
                offset=offset,
            )
            owed.update(str(one.payload["asset_id"]) for one in page.jobs)
            if len(page.jobs) < MAX_PAGE_SIZE:
                break
            offset += MAX_PAGE_SIZE
        # A read the cancel cut off after its rows were written.
        for seq in self.started:
            if not self.done[seq] and self.decided[seq][2] is Verdict.READ:
                location = await context.content.location_at(self.root_id, self.decided[seq][1])
                if location is not None:
                    owed.add(location.asset_id)
                    self.taken_in.append(location.asset_id)
        await self.index_arrivals()
        payloads: list[dict[str, object]] = []
        for asset_id in sorted(owed):
            asset = await context.content.get(asset_id)
            if asset is not None and asset.probed_at is None:
                payloads.append({"asset_id": asset_id, "scan_only": True})
        for at in range(0, len(payloads), PLAN_WRITE_BATCH):
            await queue.enqueue_many(PROBE, payloads[at : at + PLAN_WRITE_BATCH], dedupe=True)
        await self.forget_plan()
        log.info("library.scan_canceled", root_id=self.root_id, probes_asked=len(payloads))

    async def write_unread(self) -> None:
        context = self.context
        await context.queue.set_to_read(context.job.id, self.unread, worker_id=context.worker_id)

    async def hand_out(self) -> None:
        # Children, so a cancel takes them too; a batch at a time.
        batch = self.to_probe[:]
        del self.to_probe[:]
        await self.write_unread()
        for asset_id in batch:
            await self.context.enqueue_child(
                PROBE, _probe_payload(self.context, asset_id), priority=self.context.job.priority
            )

    async def index_arrivals(self) -> None:
        """Tell the search index about the files taken in since it was last told."""
        batch = self.taken_in[self.indexed :]
        self.indexed = len(self.taken_in)
        await self.reindexer.touched_many(batch)

    async def take(
        self, gate: asyncio.Semaphore, seq: int, item: Walked, rel_path: str, verdict: Verdict
    ) -> None:
        async with gate:
            await self.beat()
            self.started.add(seq)
            claimed = {rel_path}
            try:
                if not rel_path.startswith(tuple(self.unjudged)):
                    claimed = await _take_in(
                        self.context,
                        item,
                        rel_path=rel_path,
                        root_id=self.root_id,
                        settings=self.settings,
                        service=self.service,
                        taken_in=self.taken_in,
                        archive_settled=self.archive_settled,
                        beat=self.beat,
                        decided=verdict,
                        to_probe=self.to_probe,
                        to_check=self.to_check,
                        folders=self.folders,
                        refused=self.refusals,
                    )
                    self.opened += verdict.reads
            except UnansweredFolder as quiet:
                await self.stopped_answering(quiet.directory)
            self.seen.update(claimed)
            self.finished += 1
            if verdict.reads:
                self.unread[kind_by_name(item, verdict)] -= 1
            if len(self.to_probe) >= PROBE_HANDOUT_BATCH:
                await self.hand_out()
            if len(self.taken_in) - self.indexed >= PROBE_HANDOUT_BATCH:
                await self.index_arrivals()
            # Files read of files to read, so what is left is what the read still has to open.
            await self.context.report_progress(1 - self.unread.total() / max(1, self.to_read))
            await self.settle(seq)

    async def read(self) -> None:
        # The reads, a few at a time and first in the lane. The set is what each take-in CLAIMED.
        gate = asyncio.Semaphore(max(1, lanes.reads_at_once(self.root_abs)))
        # `gather`, so a take-in's refusal reaches the caller unwrapped.
        async with lanes.first():
            pending = [
                asyncio.ensure_future(self.take(gate, seq, *one))
                for seq, one in enumerate(self.decided)
            ]
            try:
                await asyncio.gather(*pending)
            except BaseException:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                raise
        await self.hand_out()
        for asset_id in self.to_check:
            await _probe_unless_already_coming(self.context, asset_id)
        # The whole read settled: a restart in the sweep re-reads nothing.
        await self.checkpoint()

    async def sweep(self) -> None:
        if not self.walk.looked:
            # A walk that did not happen learned nothing; nothing may be concluded from it.
            log.info("library.sweep_not_done", root_id=self.root_id)
            return
        with timing_hook("library.scan.sweep", root_id=self.root_id):
            # The sweep narrows with the read, to the paths this pass looked at.
            await _sweep(
                self.context,
                root_id=self.root_id,
                root_abs=self.root_abs,
                under=self.under,
                seen=self.seen,
                only=self.named,
                unjudged=self.unjudged,
            )
            await _forget_gone_refusals(
                self.service,
                root_id=self.root_id,
                root_abs=self.root_abs,
                under=self.under,
                refused=self.refusals,
                walked={self.prefix + item.rel_path for item in self.found},
                only=self.named,
                unjudged=self.unjudged,
            )


async def _ask_for_what_settles(context: JobContext, settles_into: Sequence[str]) -> None:
    """Ask for the whole-library passes this walk made worth running again, deduped with `probe`'s."""
    for job_type in settles_into:
        try:
            # Background priority: catch-up nobody watches must not hold the pool. See
            # `BACKGROUND_PRIORITY`.
            await context.queue.enqueue_when_settled(job_type, priority=BACKGROUND_PRIORITY)
        # Switched off; not the scan's decision to argue with.
        except JobSwitchedOff:
            log.info("library.settling_skipped", job_type=job_type, reason="switched off")


async def _decide_and_read(one: _ScanPass, root_id: str) -> None:
    """`scan`'s decide and read; a cancel cleans up, and what was read is logged either way."""
    try:
        await one.decide()
        await one.read()
    except BaseException:
        await one.if_canceled()
        raise
    finally:
        log.info("library.scan_read", root_id=root_id, read=one.finished)


async def scan(
    context: JobContext,
    *,
    settings: Settings,
    service: LibraryService,
    reindexer: ReindexSeam,
    folder_settled: FolderSettled | None = None,
    archive_settled: ArchiveSettled | None = None,
    settles_into: Sequence[str] = (),
) -> None:
    """Walk a root, or one folder of it (`folder_id`), index what is new, and mark what has gone.

    The read and the sweep narrow together (see `_sweep`); without the sweep a deleted file would
    stay on the grid for ever."""
    root_id = _root_id(context)
    root = await context.library.get_root(root_id)
    if root is None:
        raise RootIsGone(f"library root {root_id} was removed before its scan ran")
    under = await _scope(context, root_id, context.payload)
    root_abs = Path(root.abs_path)
    # The files a change notification named, relative to the root, or None for a whole walk.
    named = _named_paths(context)
    lanes.ahead_of_passes(named is not None or "folder_id" in context.payload)
    walk = await _walk_for_scan(context, root_id, root_abs, root_abs / under, named)
    one = _ScanPass(
        context,
        settings=settings,
        service=service,
        archive_settled=archive_settled,
        root_id=root_id,
        root_abs=root_abs,
        under=under,
        named=named,
        walk=walk,
        reindexer=reindexer,
    )
    walked_dirs = one.walked_dirs()
    if named is None:
        await one.settle_moved_folders()
    await _decide_and_read(one, root_id)
    # Before the sweep, so a pass that dies on the way out still leaves its files findable.
    await one.index_arrivals()
    # Counted for History: files new to the library, archive members included.
    context.arrived(len(one.taken_in))
    if one.went_quiet:
        # What its History line counts: the files read before a folder stopped answering.
        await context.set_units(one.opened)
    if one.ended:
        raise FolderStoppedAnswering(STOPPED_ANSWERING)
    try:
        await one.sweep()
        # After the sweep, so a folder is judged on what is really still in it.
        await _settle_folders(
            context, root_id=root_id, dirs=walked_dirs, folder_settled=folder_settled
        )
        # Recorded for the catch-up at start, and only after a real listing.
        if named is None:
            await _record_what_was_seen(
                context, root_id=root_id, under=under, walk=walk, unjudged=one.unjudged
            )
    except BaseException:
        await one.if_canceled()
        raise
    await context.set_progress(1.0)
    if one.went_quiet:
        await context.set_note(_went_quiet(len(one.went_quiet)))
    await _ask_for_what_settles(context, settles_into)
    await one.forget_plan()
    log.info(
        "library.scan_finished",
        root_id=root_id,
        files=len(one.found),
        whole_root=named is None and under == "",
        named=len(named) if named is not None else None,
    )


#: The watcher asks for the whole folder past this many (`watcher.MOST_NAMED_PATHS` imports it).
MOST_NAMED_PATHS = 200


def _named_paths(context: JobContext) -> list[str] | None:
    """The files this scan was told about, or None for an ordinary walk; over the cap is refused
    whole."""
    raw = context.payload.get("paths")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("paths must be a list of library paths")
    if len(raw) > MOST_NAMED_PATHS:
        raise ValueError(f"a scan may not name more than {MOST_NAMED_PATHS} paths")
    named: list[str] = []
    for one in raw:
        if not isinstance(one, str):
            raise ValueError("paths must be a list of library paths")
        named.append(check_rel_path(one))
    # Ordered and de-duplicated, so a failing scan reproduces from its payload.
    return list(dict.fromkeys(named))


async def scan_everything(context: JobContext) -> None:
    """Scan every library folder, as ONE job with a scan of each hanging off it.

    The roots are read here, now, so a folder added since the screen loaded is not missed. The parts
    are separate jobs so a dozen folders walk side by side and one cancel stops them all. A folder
    already being walked (`is_live`, not `dedupe`, which would claim another pass's row) is skipped,
    and the note says so: `7 of 12 folders`.
    """
    roots = await context.library.roots()
    # Left out rather than false, so ordinary scans keep their dedupe identity.
    narrowed = {"scan_only": True} if context.payload.get("scan_only") else {}
    handed = 0
    for root in roots:
        await context.raise_if_canceled()
        shape = {**scan_shape(root.id), **narrowed}
        if await context.queue.is_live(SCAN, shape):
            continue
        # The parts inherit this pass's own priority, so a press is not first in name only.
        scan_id = await context.enqueue_child(SCAN, shape, priority=context.job.priority)
        await count_ahead(context.queue, scan_id, shape, at=context.job.timing)
        handed += 1
    await context.set_progress(1.0)
    # A pass that handed out nothing says so; see `JobContext.set_note`.
    await context.set_note(
        f"{handed} of {len(roots)} folders" if roots else "no library folders to scan"
    )
    log.info("library.scan_everything", folders=len(roots), handed=handed)


async def _whole_walk_coming(context: JobContext, root_id: str) -> bool:
    """Whether a walk of this whole library is waiting or under way: one that names neither a
    folder inside it nor a list of files (`scan_shape`). Reading only, or reading and building,
    it takes in every file there is."""
    for payload in await context.queue.live_payloads(SCAN):
        if (
            payload.get("root_id") == root_id
            and "folder_id" not in payload
            and "paths" not in payload
        ):
            return True
    return False


async def _root_to_catch_up(context: JobContext, root_id: str) -> Path | None:
    """The root's folder, or None where there is nothing for a catch-up to do."""
    root = await context.library.get_root(root_id)
    if root is None:
        raise RootIsGone(f"library root {root_id} was removed before its catch-up ran")

    # A walk of the whole library already coming takes in every file this would name.
    if await _whole_walk_coming(context, root_id):
        log.info("library.catch_up_covered", root_id=root_id)
        return None

    base = Path(root.abs_path)
    # An unreachable root is left as it is; every folder would read as moved.
    refused = await asyncio.to_thread(_root_answer, base)
    if refused is not None:
        log.warning("library.root_unreachable", root_id=root_id, error=refused.strerror)
        return None
    return base


async def reconcile(context: JobContext, *, service: LibraryService) -> None:
    """Find what changed in a library while Sift was not running, from one stat per folder.
    A same-size rewrite is not caught; a rescan finds it."""
    root_id = _root_id(context)
    base = await _root_to_catch_up(context, root_id)
    if base is None:
        return
    folders = await context.library.folders_in_root(root_id)
    known = {folder.rel_path for folder in folders}

    moved, walk = await asyncio.to_thread(_folders_that_moved, base, folders)

    named = await _what_moved_folders_hold(context, service, root_id, base, moved, known, walk)
    await _ask_for_catch_up_scans(context, root_id, folders, walk, named)

    # Recorded last, after the scans are durable in the queue, for every folder looked at, so the
    # next
    # start does not redo it.
    for folder, seen_at in moved:
        await context.library.record_folder_mtime(folder.id, seen_at)

    log.info(
        "library.caught_up",
        root_id=root_id,
        # A log key containing `folders` is redacted.
        considered=len(folders),
        changed=len(moved),
        walking=len(walk),
        naming=sum(len(one) for one in named.values()),
    )


async def _what_moved_folders_hold(
    context: JobContext,
    service: LibraryService,
    root_id: str,
    base: Path,
    moved: list[tuple[FolderRow, float]],
    known: set[str],
    walk: set[str],
) -> dict[str, set[str]]:
    """The files each moved folder holds that Sift has not recorded, by folder id; a folder whose
    listing cannot be compared is added to `walk`."""
    named: dict[str, set[str]] = {}
    # Read once, only when a folder moved. See `_recorded_in`.
    refusals = _refusals_by_folder(await service.rejections_of_root(root_id)) if moved else {}
    for folder, _seen_at in moved:
        await context.raise_if_canceled()
        # Only for a folder that really changed.
        recorded = await _recorded_in(context, folder, refusals)
        differences, structural = await asyncio.to_thread(
            _differences, base, folder, recorded, known
        )
        if structural:
            walk.add(folder.id)
        if differences:
            named[folder.id] = differences
    return named


async def _ask_for_catch_up_scans(
    context: JobContext,
    root_id: str,
    folders: Sequence[FolderRow],
    walk: set[str],
    named: dict[str, set[str]],
) -> None:
    # Through `scan_shape`, so the root's own folder becomes the whole-root walk.
    by_id = {folder.id: folder for folder in folders}
    for folder_id in sorted(walk):
        await context.queue.enqueue(SCAN, scan_shape(root_id, by_id.get(folder_id)), dedupe=True)
    for folder_id, paths in sorted(named.items()):
        if folder_id in walk:
            # Its subtree is already being walked.
            continue
        ordered = sorted(paths)
        # In batches, since the cap is a cap.
        for at in range(0, len(ordered), MOST_NAMED_PATHS):
            await context.queue.enqueue(
                SCAN,
                {
                    **scan_shape(root_id, by_id.get(folder_id)),
                    "paths": ordered[at : at + MOST_NAMED_PATHS],
                },
                dedupe=True,
            )


async def _scope(context: JobContext, root_id: str, payload: Mapping[str, object]) -> str:
    """Which folder this scan is of, from the payload's folder id, never a path."""
    folder_id = payload.get("folder_id")
    if folder_id is None:
        return ROOT_REL_PATH
    if not isinstance(folder_id, str):
        raise ValueError("folder_id must be the id of a folder")

    folder = await context.library.get_folder(folder_id)
    if folder is None:
        raise FolderIsGone(f"folder {folder_id} was removed before its scan ran")
    if folder.root_id != root_id:
        # Unchecked, the sweep would mark every file in this root missing.
        raise FolderIsGone(f"folder {folder_id} is not in library root {root_id}")
    return folder.rel_path


def _root_id(context: JobContext) -> str:
    return context.require_str("root_id", "a scan needs the id of the root to walk")


async def prune_quarantine(
    context: JobContext,
    *,
    settings: Settings,
    preferences: SettingsSeam,
    queue: JobQueue,
) -> None:
    """Remove quarantined files older than the retention rule, read fresh on every run.

    Its next run is placed by the one scheduler (`kernel.jobs.clock`), never queued here."""
    del queue
    keep_days = quarantine.keep_days_from(await preferences.get_app(quarantine.KEEP_DAYS_KEY))
    removed = await asyncio.to_thread(quarantine.prune, settings, keep_days=keep_days)
    await context.set_progress(1.0)
    await context.set_note(
        "Nothing was old enough to delete."
        if removed == 0
        else f"Deleted {removed:,} quarantined file{'' if removed == 1 else 's'}."
    )
    log.info("quarantine.prune.finished", removed=removed, keep_days=keep_days)


def register_handlers(
    *,
    settings: Settings,
    service: LibraryService,
    reindexer: ReindexSeam,
    queue: JobQueue,
    preferences: SettingsSeam,
    folder_settled: FolderSettled | None = None,
    archive_settled: ArchiveSettled | None = None,
    settles_into: Sequence[str] = (),
) -> None:
    """Claim the job types this slice owns, once at boot, with what each handler needs bound in.

    `settles_into` names other features' whole-library passes a walk makes worth running, handed
    down by the composition root because a slice never imports another."""
    register_handler(
        SCAN,
        partial(
            scan,
            settings=settings,
            service=service,
            reindexer=reindexer,
            folder_settled=folder_settled,
            archive_settled=archive_settled,
            settles_into=settles_into,
        ),
        name="Scanning folder",
        family=Family.SCAN,
    )
    register_handler(
        SCAN_COUNT,
        partial(count_scan, service=service),
        name="Counting folder",
        family=Family.SCAN,
        # One at a time: a walk holds a place on its share for as long as it lists.
        alone=True,
    )
    register_handler(
        LIBRARY_SCAN,
        scan_everything,
        name="Scanning every folder",
        family=Family.SCAN,
    )
    register_handler(
        RECONCILE,
        partial(reconcile, service=service),
        name="Checking folders for changes",
    )
    register_handler(
        QUARANTINE_PRUNE,
        partial(prune_quarantine, settings=settings, preferences=preferences, queue=queue),
        name="Deleting old quarantined files",
        # Upkeep nobody watches: shown on Tasks and History, not Activity.
        unlisted=True,
    )
