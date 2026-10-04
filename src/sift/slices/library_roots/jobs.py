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
from collections.abc import Awaitable, Callable, Sequence
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
    JobContext,
    JobQueue,
    JobSwitchedOff,
    register_handler,
)
from sift.kernel.jobs.families import Family
from sift.kernel.log import get_logger, timing_hook
from sift.kernel.seams import ReindexSeam, SettingsSeam
from sift.slices.library_roots import quarantine
from sift.slices.library_roots.catch_up import _differences, _folders_that_moved
from sift.slices.library_roots.moved_folders import _reconcile_folders
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
    RootIsGone,
    RootUnreachable,
    _root_answer,
    _walk_confined,
    look_at,
)

if TYPE_CHECKING:
    from sift.slices.library_roots.walking import Walk, Walked

log = get_logger(__name__)


SCAN = "scan"


#: One pass over EVERY library folder, with a scan of each hanging off it. See `scan_everything`.
LIBRARY_SCAN = "library_scan"


#: Catching a library up with what changed while Sift was not running. See `reconcile`.
RECONCILE = "library_reconcile"


def scan_shape(root_id: str, folder: FolderRow | None = None) -> dict[str, object]:
    """What identifies one scan: a root, and the folder inside it, unless that folder IS the root.

    **A whole-root walk is ONE job, whoever asked for it.** The queue decides whether the work is
    already coming by matching a payload exactly, and the same walk could have two spellings: a
    press queues `{root_id}` and the watcher would queue `{root_id, folder_id}` naming the root's
    own top folder. Those never collide, so the two would run back to back over the same files.

    The two MEAN the same walk and only look different. `_scope` resolves a missing
    `folder_id` to `ROOT_REL_PATH`, and the root's own top folder's `rel_path` IS `ROOT_REL_PATH`,
    so both spellings send the walk to the same directory and filter the sweep to the same subtree.
    Dropping the id where it names the top folder is what makes them one row rather than two.

    A narrower folder keeps its id and stays a job of its own, which is the whole point of the
    watcher naming one: two different sub-folders are two walks and both are needed.

    The key ORDER is load-bearing, because the match is on the serialized payload rather than on the
    mapping: `root_id` then `folder_id`, and whatever a caller adds (`scan_only`, `paths`) goes
    after. Every enqueue of a scan goes through here so there is one order to keep.
    """
    shape: dict[str, object] = {"root_id": root_id}
    if folder is not None and folder.rel_path != ROOT_REL_PATH:
        shape["folder_id"] = folder.id
    return shape


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


#: How many files a scan takes in before it hands out their probes. Small enough that a pass
#: stopped part way strands little; large enough that the queue is not written per file.
PROBE_HANDOUT_BATCH = 100


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
            + ". Nothing was changed. Scan it again once it is back."
        )
    with timing_hook("library.scan.walk", root_id=root_id):
        # The walk is blocking and can run for minutes on a large library, so it is done in a
        # thread and handed over as a list rather than as a generator: a generator would step the
        # blocking walk from inside the event loop, one directory per `next`.
        # In the root's storage lane: a walk is a directory listing per folder, which over a share
        # is a round trip per folder, and a dozen roots walked at once are a dozen readers.
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
    ) -> None:
        self.context = context
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
        to_read = sum(1 for _item, _rel, verdict in self.decided if verdict.reads)
        await context.set_units(to_read)
        # The folder rows of every directory about to receive a file, resolved once each BEFORE the
        # reads: the reads run several at a time, and two files of one directory arriving together
        # would each resolve and write the chain before either had remembered it.
        for _item, rel_path, verdict in self.decided:
            if verdict is Verdict.READ:
                await _folder_for(context, self.root_id, rel_path, self.folders)
        log.info(
            "library.scan_counted", root_id=self.root_id, files=len(self.found), to_read=to_read
        )

    async def hand_out(self) -> None:
        # As children of this job so a cancel takes them too, in the order the files were taken
        # in. A batch at a time rather than all at the end: a pass stopped part way (a restart,
        # a crash) then loses the probes of one batch, not of everything it had read. The lane
        # still puts this pass's reads first, so a probe handed out mid-pass waits its turn.
        batch = self.to_probe[:]
        del self.to_probe[:]
        for asset_id in batch:
            # AT THIS WALK'S OWN URGENCY, the way `scan_everything` hands its walks this job's
            # priority: a pressed Scan's reads go to the front of the queue with it. Read from the
            # row, so a walk the watcher started hands the machine's urgency down and a walk a
            # person pressed hands the person's.
            await self.context.enqueue_child(
                PROBE, _probe_payload(self.context, asset_id), priority=self.context.job.priority
            )

    async def take(
        self, gate: asyncio.Semaphore, item: Walked, rel_path: str, verdict: Verdict
    ) -> None:
        async with gate:
            await self.context.raise_if_canceled()
            self.seen.update(
                await _take_in(
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
            )
            self.finished += 1
            if len(self.to_probe) >= PROBE_HANDOUT_BATCH:
                await self.hand_out()
            # Progress stops at 0.9: the sweep is the rest, and a bar that sits at 100% while the
            # job is still working is a bar nobody believes the next time. Reported, not set: a
            # write per file would hold the write connection once per file for a bar nobody can
            # watch move that fast.
            await self.context.report_progress(0.9 * self.finished / len(self.found))

    async def read(self) -> None:
        # THE READS, A FEW AT A TIME AND FIRST IN THE LANE. The scan's reads go to the front of the
        # lane (files first), and as many are open at once as the storage is capped to, so a
        # share's read slots are not left to the probes of the files this pass took in. The probes
        # are handed out AFTER the reads.
        #
        # What a take-in CLAIMED, rather than the one path the walk found. They are the same thing
        # for a file and they are not for an archive: opening one `gallery.zip` claims a location
        # per picture inside it, and the archive's own path is claimed by nothing because no asset
        # is ever the archive. The sweep compares against this set, so a take-in that reported the
        # wrong paths would mark every picture in an archive missing on the way out.
        gate = asyncio.Semaphore(max(1, lanes.reads_at_once(self.root_abs)))
        # `gather` rather than a task group: a refusal a take-in raises (the root's folder row being
        # gone) has to reach the caller as ITSELF, and a group wraps it in an exception group that
        # nothing above knows how to read. The others are cancelled on the way out, which is what
        # the group would have done.
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
            )
            await _forget_gone_refusals(
                self.service,
                root_id=self.root_id,
                root_abs=self.root_abs,
                under=self.under,
                refused=self.refusals,
                walked={self.prefix + item.rel_path for item in self.found},
                only=self.named,
            )


async def _ask_for_what_settles(context: JobContext, settles_into: Sequence[str]) -> None:
    """The whole-library work this walk has made worth doing again, asked for HERE and not only by
    `probe`.

    `probe` is one file's job, and a scan of a library that is already indexed reads almost no file
    and hands out almost no `probe`. Asked for only there, the passes that read FOLDER AND FILE
    NAMES (which is all the evidence they have) would never run on the one press a person makes
    when they want their library read again. `probe`'s own list stays where it is: a fingerprint is
    written by `probe` and the duplicate sweep has nothing to compare before it.

    It is the same QUESTION asked from the other end (what whole-library work has this pass made
    worth doing?) and the same `enqueue_when_settled`, so the two collapse onto one waiting row
    rather than becoming two sweeps. A scan of a dozen folders is one request for each of these,
    a minute after the last walk stops.

    Not conditional on whether this walk found anything new: a library where nothing has changed
    on disk is exactly the one where a pass written since the last import has never run.
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
    """Walk a root, or one folder of it, index what is new, and mark what has gone.

    The sweep at the end is the half that is easy to leave out. Without it a file deleted from the
    library stays on the grid forever, because nothing else ever revisits a row to ask whether its
    bytes are still there.

    A `folder_id` in the payload filters both halves to that folder's subtree, which is what the
    watcher asks for when a file lands: re-walking a million files because one arrived is a design
    that works on a small library. The two halves must narrow *together* (see `_sweep`).

    Whatever was taken in is handed to the search index at the end, in one pass. A scan is the main
    way files enter a running library, and a writer that does not say what it wrote is a file that
    cannot be found by name until something else happens to rebuild the index.
    """
    root_id = _root_id(context)
    root = await context.library.get_root(root_id)
    if root is None:
        raise RootIsGone(f"library root {root_id} was removed before its scan ran")
    under = await _scope(context, root_id)
    root_abs = Path(root.abs_path)
    #: The files a change notification NAMED, or None for an ordinary walk of everything.
    #:
    #: Relative to the ROOT, like everything else stored, so a scan of a folder and a scan of named
    #: files inside it speak the same paths. See `_named_paths` for what is refused.
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
    )
    walked_dirs = one.walked_dirs()
    if named is None:
        await one.settle_moved_folders()
    await one.decide()
    await one.read()
    # Before the sweep rather than after it. The sweep is the longer half and the one that can
    # fail on its own, and a pass that took files in and then died on the way out should still
    # leave them findable: the alternative loses the index write for work that was really done.
    await reindexer.touched_many(one.taken_in)
    # And counted, for the scan's line in History: the files this walk imported that the library
    # did not hold before, archive members included.
    context.arrived(len(one.taken_in))
    await one.sweep()
    # After the sweep, so a folder is judged on what is really still in it rather than on what the
    # walk happened to find before the missing files were marked.
    await _settle_folders(context, root_id=root_id, dirs=walked_dirs, folder_settled=folder_settled)
    # What each folder's directory looked like, so the catch-up at start knows which of them to look
    # at again (see `reconcile`). Only after a real LISTING: a named-path scan stats the files it
    # was told about and never reads the directory, so it has no business saying it has seen one.
    if named is None:
        await _record_what_was_seen(context, root_id=root_id, under=under, walk=walk)
    await context.set_progress(1.0)
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

    ## Why a PATH is allowed in this payload at all

    `queue.py` states the rule (a payload carries ids, not places on disk) and gives three
    reasons. It is worth answering them one at a time rather than leaning on the fact that the
    enforced form of the check (absolute paths, `~`, and `..` segments) lets a relative one past.

    **"A path in a payload is a path in every log line, backup and diagnostics export."** Checked
    rather than assumed: no payload is ever logged, `diagnostics.py` does not touch the jobs table,
    and `media_jobs/router.py` deliberately keeps the payload off the wire for exactly this reason.
    What is left is the backup, which already contains `asset_locations`: every library path Sift
    knows. So this adds no class of information to any surface that did not already carry it.

    **"The file may have moved by the time the job runs."** True, and inherent: a notification
    describes a moment that has already passed. `look_at` is built for it: a path that has gone is
    left out, and the half of the scan that decides a file is absent does it by looking.

    **"A job that takes a path can be pointed at any file on the machine."** This one is the reason
    the rule exists and it is fully answered. The path is RELATIVE, to a root named by an id in the
    same payload; it goes through `check_rel_path` here; and `look_at` then `confine`s it against
    that root's real directory before anything opens it. Three gates, and the queue's own backstop
    is a fourth. A payload cannot name a file outside the library it names.

    ## What is refused

    Every path is checked with `check_rel_path`, the same function every stored library path goes
    through: an absolute path, a `..`, or a backslash in it must never reach a filesystem call. A
    payload naming more than the cap is refused as a whole rather than truncated: a silently
    shortened list is a scan that reports success having looked at some of what it was asked about,
    which is the worst of both.

    An EMPTY list is not the same as no list, and it is not an error either: it means "these paths
    turned out to be nothing worth opening", which a walk of nothing correctly concludes nothing
    from. It comes back as an empty list rather than None so the scan does not widen into a walk of
    the entire root because a burst happened to contain no media.
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

    ## Why this is a job and not a loop in the client

    A loop in the client would send one request per folder: on a library with a dozen of them,
    a dozen round trips, a dozen toasts saying the same thing, a dozen rows on the Jobs screen
    with no parent between them, and no total anywhere, so the question the screen exists to
    answer, how long did that scan take, could not be answered from it.

    It would also be quietly wrong about WHAT it scanned: a client looping over the folders the
    screen last loaded silently misses a folder added since. The roots are read here, now, from the
    library itself.

    ## Why the parts are still separate jobs

    Because folders are independent and the pool is wide. A dozen scans are handed out at once and
    walk alongside each other; one job walking a dozen folders in turn would take the sum of them
    on one worker while the rest of the pool had nothing to do. The parent is what makes them one
    thing to watch and one thing to stop: cancelling it cancels the tree, which is the shape the
    whole queue already has.

    ## Why it asks first rather than deduping

    `is_live` and not `enqueue(dedupe=True)`, and the two are not the same question here. Dedupe is
    for a caller who has decided to queue something and wants one of it: it collapses onto a WAITING
    row and hands back that row's id, which would count as a part of this pass while actually
    belonging to whichever pass queued it. Asking first is the question this caller has (is a walk
    of this whole folder already under way?), and a RUNNING scan answers it just as well as a
    waiting one. It is the same shape `_probe_unless_already_coming` uses a few hundred lines down.

    A folder skipped that way is not a part of this pass, and the note says so: `7 of 12 folders`
    is the honest reading of a press where five were already being walked.
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
        await context.enqueue_child(SCAN, shape, priority=context.job.priority)
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

    # A WALK OF THE WHOLE LIBRARY ALREADY COMING ANSWERS THIS, and more thoroughly. Adding a folder
    # asks for both at once (the add's own walk, and this pass from the watcher attaching to it),
    # and with no folder yet recorded every file reads as changed, so both would take every file
    # in. The walk records what each folder looked like as it goes.
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
    """Find what changed in a library while Sift was not running, without re-reading the library.

    ## Why this exists

    A watcher reports CHANGES, and a change is only a change relative to a moment it started
    watching. Anything already on the disk when it attached is not a change and never becomes one:
    a file copied in while Sift was stopped stays absent through any number of poll cycles until
    somebody presses Rescan. Quit the application, download something, open it again, and without
    this pass the file is not there and never will be. That is a correctness fault rather than a
    slow one.

    ## Why it does not walk

    A walk of the library at every start is the thing this whole design is trying to stop: on the
    libraries Sift is built for (hundreds of thousands of files, thousands of folders, multiple
    terabytes), it is minutes of reading at every launch, most of it re-reading files that have not
    moved in a year.

    So this asks the only question that scales: **which folders changed?** A directory's own
    timestamp moves when an entry is added to it or taken out of it, and a scan records what it saw
    (`folders.seen_mtime`). One `stat` per folder answers it, and the ones that did not change are
    never listed at all.

    A folder that HAS changed is listed once, and what it holds is compared with what Sift recorded
    for it. That comparison is `(name, size)` on both sides: `_recorded_in` already returns exactly
    that shape, and `os.scandir` hands back the size from the directory entry itself on the site
    Sift ships on, so a listing costs no more than the names.

    ## What it deliberately does not catch

    A file REWRITTEN in place, under the same name, while Sift was closed. A directory's timestamp
    does not move for that (on NTFS or on an SMB share), and the size comparison catches
    it only if the size changed too. Closing it properly means comparing every file's own mtime,
    which is the walk this exists to avoid. It is caught the moment anything else touches that
    folder, and by a rescan by hand. Recorded rather than hidden: the alternative is a pass that
    claims to be exhaustive and is not.
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


async def _scope(context: JobContext, root_id: str) -> str:
    """Which folder this scan is of: the root itself, or one folder inside it.

    The payload carries a folder id, never a path, so the path comes from the row, which is the
    only thing that could authorise it. A made-up id cannot point the walk anywhere: it resolves to
    a folder or it resolves to nothing.
    """
    folder_id = context.payload.get("folder_id")
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
    """Remove quarantined files older than the retention rule.

    The rule is read fresh every time rather than held, so turning retention off stops the very next
    run instead of the one after a restart.

    Its NEXT run is not queued here. The sweep is a task, and every timed task's next run is placed
    by the one scheduler (`kernel.jobs.clock`) when this run settles, whatever it found, and not
    at all while retention is off or the task only runs when pressed. Queuing itself at the end
    with its own interval would ignore quiet hours and be a second writer of the row the scheduler
    places.
    """
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
    """Claim the job types this slice owns. Called once, at boot.

    The settings, the service and the index's "this changed" seam are bound in here rather than
    reached for inside a handler: a handler is handed its context by the kernel and nothing else, so
    the alternative is a module-level global holding them, which is the thing that makes a test
    reach into a module and swap something out.

    `settles_into` is job types belonging to OTHER features that a walk makes worth running once
    over the whole library. Empty here on purpose and handed down by the composition root, for the
    reason every other list of this kind is: a slice depends on the kernel and never on another
    slice, so this module must not learn that reading folder names or looking for shoots exists.
    """
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
