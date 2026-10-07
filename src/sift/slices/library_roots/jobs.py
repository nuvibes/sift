# SPDX-License-Identifier: AGPL-3.0-or-later
"""Walking a library, and watching one.

`scan` reads a directory tree and tells Sift what is in it. It is the only thing in the app that
turns a file somebody already had into an asset, and it does so without touching the file: it
reads, it hashes, it writes rows. The directory it walked is byte-for-byte what it was.

Three things about it are worth reading before changing any of it.

**It refuses without moving anything.** A file that fails the ingress gate stays exactly where it
is: it is the user's file, in the user's folder, and Sift disliking it is not a reason to
relocate it. That is what makes the refusal memory necessary: the file is still there next time,
and a scanner that forgot would refuse and log it again on every pass, forever.

**It enqueues `probe` and nothing else.** The thumbnail, the preview and the sprite each need what
only `probe` knows (the duration, the dimensions, and whether the file decodes at all), so
`probe` starts them itself, as its own children. Enqueued here they would race it, and all three
would separately rediscover that a truncated file is truncated. One file is one job with its work
hanging off it, which is also what makes cancelling a scan cancel everything it started.

**It is resumable, not restartable.** A killed scan comes back and walks the tree again; every
file it already took is found by digest and adds nothing. The pass is idempotent because identity
is the content, not the path.

`watch` is the same pipeline reached a different way. The only thing it adds is patience: a file
that is still being written hashes perfectly happily, and the digest is of the half.
"""

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
    WAITED_ON_PRIORITY,
    JobContext,
    JobQueue,
    JobState,
    JobSwitchedOff,
    register_handler,
)
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.quiet_hours import AT_NOW
from sift.kernel.log import get_logger, timing_hook
from sift.kernel.seams import ReindexSeam, SettingsSeam
from sift.slices.library_roots import quarantine
from sift.slices.library_roots.catch_up import _differences, _folders_that_moved
from sift.slices.library_roots.moved_folders import _reconcile_folders
from sift.slices.library_roots.scan_plan import count_to_read, kind_by_name
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
    await context.set_note(f"{counted.files:,} file{'' if counted.files == 1 else 's'} to read.")
    log.info(
        "library.scan_counted",
        root_id=root.id,
        to_read=counted.files,
        ahead=True,
        written=written,
        waited_seconds=max(0, int(time.time()) - waiting.created_at),
    )


#: Ageing the quarantine directory out. A recurring job rather than a sweep at boot, because a
#: server that stays up for months would otherwise never do it, and this is the one pile in Sift
#: that holds refused bytes in the clear, so "eventually" is not good enough.
QUARANTINE_PRUNE = "quarantine_prune"


#: How often that runs. Daily: the rule it applies is measured in days, so asking more often would
#: be the same answer several times, and asking less often would let the rule overshoot by longer
#: than its own unit.
PRUNE_EVERY_SECONDS = 86_400


#: Told about a folder the walk went through, once the scan has finished with it.
#:
#: Injected at boot and absent by default. What Sift does with the news belongs to whoever is
#: listening (today that is the photo-set rule, which asks whether the folder holds pictures and
#: nothing else), and this slice must not learn that photo sets exist.
#:
#: Every folder the walk SAW, not only the ones that took a file in. A library that was already
#: indexed before the rule existed has nothing new in it, so "folders that changed" would mean the
#: rule never fired on anything anybody already owned, which is the whole case it is for.
FolderSettled = Callable[[str, str], Awaitable[None]]


#: Told about an archive the walk opened, once every picture inside it has been taken in.
#:
#: Injected at boot and absent by default, exactly like `FolderSettled` and for the same reason:
#: what happens next is a photo set, and this slice must not learn that photo sets exist.
#:
#: The asset ids arrive in the order the archive lists them, which for a gallery is the order
#: somebody arranged the pictures in, and a shoot shown shuffled is a different shoot.
#:
#: `(root_id, rel_path, name, asset_ids)`. The root travels with the path because the path alone
#: does not identify an archive: two libraries can each hold a `galleries/482615.zip`, and with the
#: path alone the listener behind it would have nothing to key a set on.
ArchiveSettled = Callable[[str, str, str, list[str]], Awaitable[None]]


#: How many files a scan takes in before it hands out their probes and tells the search index.
#: Small enough that a pass stopped part way strands little; large enough that the queue is not
#: written per file.
PROBE_HANDOUT_BATCH = 100

#: A read of this many files or more on a network share holds back its own per-file work until its
#: reads end, so the share's places go to the read: about seven minutes of reading on a share.
SCAN_FIRST_FILES = 2_000

#: Off, a big read shares its share with its own per-file work.
HOLD_WHILE_READING = True

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
    # THE ROOT IS ASKED FIRST, ONCE. An unplugged drive or a share that is off answers the walk
    # with nothing, and it answers every one of the sweep's stats with the same error one at a
    # time, on a share, each after the network's own timeout. Asked here, the pass stops before
    # it has read a row, with a sentence that says what happened.
    refused = await asyncio.to_thread(_root_answer, root_abs)
    if refused is not None:
        log.warning("library.root_unreachable", root_id=root_id, error=refused.strerror)
        raise RootUnreachable(
            "the library folder did not answer"
            + (f" ({refused.strerror})" if refused.strerror else "")
            + ". Nothing was changed. Scan it again once it's back."
        )
    with timing_hook("library.scan.walk", root_id=root_id):
        # The walk is blocking and can run for minutes on a large library, so it is done in a
        # thread and handed over as a list rather than as a generator: a generator would step the
        # blocking walk from inside the event loop, one directory per `next`.
        # In the root's storage lane: a walk is a directory listing per folder, which over a share
        # is a round trip per folder, and a dozen roots walked at the same time are a dozen readers.
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
        # `look_at` was handed paths that are already relative to the root; a walk reports them
        # relative to wherever it started. Getting this wrong does not fail loudly: it prefixes a
        # folder onto a path that already has it, and the sweep then marks every file in that
        # folder missing.
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
        self.holding = False

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
        # The directories the walk went through, as paths relative to the root. Gathered as strings
        # and turned into folder rows once at the end: resolving each inside the loop would be one
        # lookup per FILE for an answer that is the same for every file in a directory.
        walked = {self.prefix + one for one in self.walk.directories}
        # The folder the walk STARTED in, which is not in `walk.directories`: a walk is always inside
        # something. `_settle_folders` spells the library's own folder as "." because an empty path
        # is not a path anything can be looked up by.
        walked.add("." if self.under == ROOT_REL_PATH else self.under)
        return walked

    async def settle_moved_folders(self) -> None:
        """BEFORE anything is taken in, and that ordering is the whole point of it.

        A folder somebody renamed on their disk looks to a walk like one folder that vanished and a
        different one that appeared. Settled here, the rows move first and the take-in below then
        finds every file exactly where it is recorded, so a renamed folder of five thousand files
        costs one directory listing. Settled afterwards, every one of those files would be read and
        hashed end to end first, to conclude what a listing already said.

        Skipped entirely for a named-path scan, and that is a correctness decision rather than a
        saving. This reads a walk's directory listing as "what is in this folder now" and compares it
        with what Sift recorded, so handed the two or three directories a notification happened to
        touch, it would conclude that every folder it was NOT told about had vanished. A folder that
        was renamed or moved is exactly the case named paths must not be used for; the watcher sends
        a folder scan for those. See `_named_paths`.
        """
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
        # THE COUNT BEFORE THE FIRST READ. Every file the walk found is decided (an archive, a file
        # refused before, a file unchanged since the last pass, or a file to read) from the rows
        # alone, before any file is opened. What that costs is one lookup per file; what it buys is
        # a number: the job says how many files it is about to read, so the Activity screen's
        # estimate weighs this job by its files rather than as one row.
        # The pass's memory across its files: the root's refusals, read once, and the folder rows it
        # resolves, by directory. See `_take_in`.
        context = self.context
        self.refusals = await self.service.rejections_of_root(self.root_id)
        for item in self.found:
            await context.raise_if_canceled()
            rel_path = self.prefix + item.rel_path
            verdict = await _decide(
                item,
                rel_path=rel_path,
                root_id=self.root_id,
                service=self.service,
                context=context,
                refused=self.refusals,
            )
            self.decided.append((item, rel_path, verdict))
        self.unread = Counter(
            kind_by_name(item, verdict) for item, _rel, verdict in self.decided if verdict.reads
        )
        self.to_read = to_read = self.unread.total()
        await context.set_units(to_read)
        await self.write_unread()
        await self.hold_own_work(to_read)
        # The folder rows of every directory about to receive a file, resolved once each BEFORE the
        # reads: the reads run several at a time, and two files of one directory arriving together
        # would each resolve and write the chain before either had remembered it.
        for _item, rel_path, verdict in self.decided:
            if verdict is Verdict.READ:
                await _folder_for(context, self.root_id, rel_path, self.folders)
        log.info(
            "library.scan_counted", root_id=self.root_id, files=len(self.found), to_read=to_read
        )

    async def write_unread(self) -> None:
        context = self.context
        await context.queue.set_to_read(context.job.id, self.unread, worker_id=context.worker_id)

    async def hand_out(self) -> None:
        # Children, so a cancel takes them too; a batch at a time, so a pass stopped part way loses
        # one batch of probes. The lane still puts this pass's reads first.
        batch = self.to_probe[:]
        del self.to_probe[:]
        await self.write_unread()
        for asset_id in batch:
            # At this walk's own urgency, read from its row, as `scan_everything` hands down its.
            await self.context.enqueue_child(
                PROBE, _probe_payload(self.context, asset_id), priority=self.context.job.priority
            )

    async def index_arrivals(self) -> None:
        """Tell the search index about the files taken in since it was last told."""
        batch = self.taken_in[self.indexed :]
        self.indexed = len(self.taken_in)
        await self.reindexer.touched_many(batch)

    async def hold_own_work(self, to_read: int) -> None:
        """A big read on a share keeps its own per-file work waiting until its reads end."""
        # A named-path scan is a few hundred files, and a local disk has no places to share.
        if not HOLD_WHILE_READING or self.named is not None or to_read < SCAN_FIRST_FILES:
            return
        if not lanes.storage_for(self.root_abs / "walk").remote:
            return
        # Its probes are the read, and the walk goes first in the lane: they pass.
        await self.context.hold_own_family(spared=(SCAN, SCAN_COUNT, LIBRARY_SCAN, PROBE))
        self.holding = True
        log.info("library.scan_holding", root_id=self.root_id, to_read=to_read)

    async def ahead_of_what_it_held(self) -> None:
        """Ask again, a step ahead, for this read's probes still waiting as its hold lifts: the
        per-file work it releases is older than they are, and would otherwise go first."""
        queue, job = self.context.queue, self.context.job
        more, offset = self.holding, 0
        while more:
            page = await queue.list(
                parent_id=job.id,
                job_type=PROBE,
                state=JobState.QUEUED,
                limit=MAX_PAGE_SIZE,
                offset=offset,
            )
            for one in page.jobs:
                await queue.enqueue(PROBE, one.payload, dedupe=True, priority=job.priority - 1)
            more, offset = len(page.jobs) == MAX_PAGE_SIZE, offset + MAX_PAGE_SIZE

    async def take(
        self, gate: asyncio.Semaphore, item: Walked, rel_path: str, verdict: Verdict
    ) -> None:
        async with gate:
            await self.context.raise_if_canceled()
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

    async def read(self) -> None:
        # THE READS, A FEW AT A TIME AND FIRST IN THE LANE, as many open as the storage is capped
        # to, so a share's places are not left to the probes of the files this pass took in.
        #
        # What a take-in CLAIMED, not the path the walk found: an archive claims a location per
        # picture inside it and none for itself, and the sweep compares against this set.
        gate = asyncio.Semaphore(max(1, lanes.reads_at_once(self.root_abs)))
        # `gather`, not a task group, so a take-in's refusal (the root's folder row gone) reaches the
        # caller as itself rather than wrapped in a group; the others are cancelled on the way out.
        async with lanes.first():
            pending = [asyncio.ensure_future(self.take(gate, *one)) for one in self.decided]
            try:
                await asyncio.gather(*pending)
            except BaseException:
                for task in pending:
                    task.cancel()
                raise
        # The last batch of probes, and the checks for files the walk found already on the books.
        await self.hand_out()
        for asset_id in self.to_check:
            await _probe_unless_already_coming(self.context, asset_id)

    async def sweep(self) -> None:
        if not self.walk.looked:
            # The same rule the folder reconciliation follows: a walk that did not happen learned
            # nothing, and nothing may be concluded from it: `_still_there` would reach the same
            # answer one refused stat at a time, which over a share is the slow way to say so.
            log.info("library.sweep_not_done", root_id=self.root_id)
            return
        with timing_hook("library.scan.sweep", root_id=self.root_id):
            # BOTH HALVES NARROW TOGETHER. A sweep reads what Sift believes is present and marks
            # what is not there any more, so a pass that looked at three named files and then
            # swept a whole root would ask about every row in the library to conclude what it
            # never examined. `only` holds it to the paths this pass actually looked at.
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
    """The whole-library work this walk has made worth doing again, asked for here as well as by
    `probe`: a scan of a library already indexed hands out almost no `probe`, and the passes that
    read folder and file names would never run. The same `enqueue_when_settled`, so the two collapse
    onto one waiting row. Asked whether or not the walk found anything new.
    """
    for job_type in settles_into:
        try:
            # AT THE BACKGROUND PRIORITY. These are whole-library catch-up nobody is sitting in
            # front of, and a scan fires one per folder: at the default priority several of them
            # can hold half the pool for an hour while file reads wait. See
            # `BACKGROUND_PRIORITY`, and `register_handler(alone=True)` for the other half.
            await context.queue.enqueue_when_settled(job_type, priority=BACKGROUND_PRIORITY)
        # Switched off. A pass somebody has turned off is not asked for, and asking is not the
        # scan's decision to argue with: the same shape as the switch check in the probe, which
        # skips the follow-on rather than queueing work that will do nothing.
        except JobSwitchedOff:
            log.info("library.settling_skipped", job_type=job_type, reason="switched off")


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
    try:
        await one.decide()
        await one.read()
        await one.ahead_of_what_it_held()
    finally:
        # Before the sweep, and on a cancel, a pause or a failure.
        held = context.lift_own_hold()
        log.info("library.scan_read", root_id=root_id, read=one.finished, held=held)
    # Before the sweep, so a pass that dies on the way out still leaves its files findable.
    await one.index_arrivals()
    # And counted, for the scan's line in History: the files this walk imported that the library
    # did not hold before, archive members included.
    context.arrived(len(one.taken_in))
    if one.went_quiet:
        # What its History line counts: the files read before a folder stopped answering.
        await context.set_units(one.opened)
    if one.ended:
        raise FolderStoppedAnswering(STOPPED_ANSWERING)
    await one.sweep()
    # After the sweep, so a folder is judged on what is really still in it rather than on what the
    # walk happened to find before the missing files were marked.
    await _settle_folders(context, root_id=root_id, dirs=walked_dirs, folder_settled=folder_settled)
    # What each folder's directory looked like, so the catch-up at start knows which of them to look
    # at again (see `reconcile`). Only after a real LISTING: a named-path scan stats the files it
    # was told about and never reads the directory, so it has no business saying it has seen one.
    if named is None:
        await _record_what_was_seen(
            context, root_id=root_id, under=under, walk=walk, unjudged=one.unjudged
        )
    await context.set_progress(1.0)
    if one.went_quiet:
        await context.set_note(_went_quiet(len(one.went_quiet)))
    await _ask_for_what_settles(context, settles_into)
    log.info(
        "library.scan_finished",
        root_id=root_id,
        files=len(one.found),
        whole_root=named is None and under == "",
        named=len(named) if named is not None else None,
    )


#: The most paths one named scan may carry.
#:
#: A cap rather than a promise about how many arrive: a copy of ten thousand files into a watched
#: folder is an ordinary thing, and a job payload holding ten thousand strings is a row nobody wants
#: in the queue and a message nobody wants to parse. The watcher stops collecting at this point and
#: asks for the folder instead (see `watcher.MOST_NAMED_PATHS`, which is deliberately the same
#: number and imported from here so the two cannot drift).
MOST_NAMED_PATHS = 200


def _named_paths(context: JobContext) -> list[str] | None:
    """The files this scan was told about, or None for an ordinary walk.

    A path is allowed here because it is relative to the root the payload names, goes through
    `check_rel_path`, and is confined to that root by `look_at` before anything opens it. A list
    over the cap is refused whole, never truncated. An empty list stays empty, so a burst holding
    no media does not widen into a walk of the whole root.
    """
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
    # Ordered and de-duplicated: the same file named twice in one burst is one file, and a stable
    # order makes a failing scan reproducible from its payload.
    return list(dict.fromkeys(named))


# --- one pass over the whole library --------------------------------------------------------------


async def scan_everything(context: JobContext) -> None:
    """Scan every library folder, as ONE job with a scan of each hanging off it.

    The roots are read here, now, so a folder added since the screen loaded is not missed. The parts
    are separate jobs so a dozen folders walk side by side and one cancel stops them all. A folder
    already being walked (`is_live`, not `dedupe`, which would claim another pass's row) is skipped,
    and the note says so: `7 of 12 folders`.
    """
    roots = await context.library.roots()
    # `scan_only` travels down to each part, and is left OUT of the payload rather than set false.
    # The queue matches a payload exactly to decide whether work is already coming, so a field that
    # is always present would change the identity of every scan in the library (see `rescan`).
    narrowed = {"scan_only": True} if context.payload.get("scan_only") else {}
    handed = 0
    for root in roots:
        await context.raise_if_canceled()
        shape = {**scan_shape(root.id), **narrowed}
        if await context.queue.is_live(SCAN, shape):
            continue
        # THE PARTS INHERIT THIS PASS'S OWN URGENCY, and that is what makes pressing Scan on the
        # Importing pane mean anything. This job only reads the list of roots and hands them out
        # (it finishes in milliseconds), so giving it the waited-on priority and leaving the walks
        # at the default would put the press at the front of the queue and the work it asked for at
        # the back, minutes behind a thousand-odd file reads.
        #
        # Read from the row rather than named here, so a pass the machine started hands its parts
        # the machine's priority and a pass a person started hands them the person's.
        scan_id = await context.enqueue_child(SCAN, shape, priority=context.job.priority)
        await count_ahead(context.queue, scan_id, shape, at=context.job.timing)
        handed += 1
    await context.set_progress(1.0)
    # A pass that handed out nothing at all finished correctly having found nothing to do, and a job
    # that returns instantly and says nothing reads as broken. See `JobContext.set_note`.
    await context.set_note(
        f"{handed} of {len(roots)} folders" if roots else "no library folders to scan"
    )
    log.info("library.scan_everything", folders=len(roots), handed=handed)


# --- catching up with what happened while Sift was not running ------------------------------------


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
    # A root that is not there to ask (a drive not plugged in yet, a share still asleep at
    # start) is left exactly as it is: every folder under it would read as moved, and every
    # one of them would be walked for nothing. The Library screen says it is unreachable.
    refused = await asyncio.to_thread(_root_answer, base)
    if refused is not None:
        log.warning("library.root_unreachable", root_id=root_id, error=refused.strerror)
        return None
    return base


async def reconcile(context: JobContext, *, service: LibraryService) -> None:
    """Find what changed in a library while Sift was not running, without walking it again.

    One `stat` per folder says which folders' timestamps moved; only those are listed, and compared
    with the rows by `(name, size)`. A file rewritten in place with the same size while Sift was
    closed is not caught: that needs the walk this exists to avoid, and a rescan finds it.
    """
    root_id = _root_id(context)
    base = await _root_to_catch_up(context, root_id)
    if base is None:
        return
    folders = await context.library.folders_in_root(root_id)
    known = {folder.rel_path for folder in folders}

    # ONE `stat` PER FOLDER, and nothing else until something has moved.
    moved, walk = await asyncio.to_thread(_folders_that_moved, base, folders)

    named = await _what_moved_folders_hold(context, service, root_id, base, moved, known, walk)
    await _ask_for_catch_up_scans(context, root_id, folders, walk, named)

    # WRITTEN DOWN, or this pass does the same work at every start for ever.
    #
    # Recorded last, after the scans are enqueued, because the queue is durable: a job that is in it
    # survives a restart, so a folder marked as seen has its work booked even if Sift stops here.
    # Recorded before, a stop between the two would lose the difference and record that there was
    # none.
    #
    # And recorded for every folder that was LOOKED AT, including the ones that turned out to hold
    # no differences: a folder whose timestamp moved because something Sift does not index landed
    # in it has been examined just as thoroughly as one that gained a video, and re-listing it at
    # every start is exactly the cost this pass exists to avoid: without it every such folder reads
    # as "changed" on every boot, for ever.
    for folder, seen_at in moved:
        await context.library.record_folder_mtime(folder.id, seen_at)

    log.info(
        "library.caught_up",
        root_id=root_id,
        # `considered` and not `folders`: any log key holding that word is treated as naming
        # somebody's filesystem and is redacted.
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
    # The root's refusals, read once and only when a folder moved: the same one read a scan
    # makes. See `_recorded_in` for why a refused file is part of what a folder is known to hold.
    refusals = _refusals_by_folder(await service.rejections_of_root(root_id)) if moved else {}
    for folder, _seen_at in moved:
        await context.raise_if_canceled()
        # Read only for a folder that really changed. A library where nothing moved reads no rows
        # at all, which is the ordinary case at every start.
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
    # Through `scan_shape`, so a folder that IS the root comes out as the whole-root walk rather
    # than as a second spelling of it. A root's own top folder reaches this list like any other:
    # `_folders_that_moved` returns a parent to walk when a child will not answer.
    by_id = {folder.id: folder for folder in folders}
    for folder_id in sorted(walk):
        await context.queue.enqueue(SCAN, scan_shape(root_id, by_id.get(folder_id)), dedupe=True)
    for folder_id, paths in sorted(named.items()):
        if folder_id in walk:
            # Already getting a full walk of its subtree, which covers these. Naming them as well
            # would be the same files taken in twice.
            continue
        ordered = sorted(paths)
        # In batches, because the cap is a cap: a folder that gained a thousand files while Sift
        # was closed is several named scans rather than one refused payload or one walk.
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
    """Which folder this scan is of: the root itself, or one folder inside it.

    The payload carries a folder id, never a path, so the path comes from the row, which is the
    only thing that could authorise it. A made-up id cannot point the walk anywhere: it resolves to
    a folder or it resolves to nothing.
    """
    folder_id = payload.get("folder_id")
    if folder_id is None:
        return ROOT_REL_PATH
    if not isinstance(folder_id, str):
        raise ValueError("folder_id must be the id of a folder")

    folder = await context.library.get_folder(folder_id)
    if folder is None:
        raise FolderIsGone(f"folder {folder_id} was removed before its scan ran")
    if folder.root_id != root_id:
        # A folder of another library. Left unchecked the walk would start outside this root and
        # the sweep would then mark every file in this one missing, because none of them was seen.
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
        # Upkeep nobody watches: its row on Tasks and its line in History say what it did, and
        # Activity's list of what is happening now leaves it off.
        unlisted=True,
    )
