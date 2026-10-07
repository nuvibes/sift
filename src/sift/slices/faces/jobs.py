# SPDX-License-Identifier: AGPL-3.0-or-later
"""The face work, as background jobs.

Four of them, and they are separate because they cost wildly different amounts and are triggered
by different things.

**Scanning one file** is the expensive one: it reads the file. It runs at background priority, so
whoever is watching something right now always wins.

**Matching again** costs nothing by comparison (no file is opened, only stored numbers are
compared), and it runs whenever the reference gallery changes. That is the whole point of storing
the numbers: adding a person is seconds, not another pass over the library.

**Grouping** the faces nobody has claimed is a whole-library operation, so it runs once after a
batch settles rather than once per file. Running it per file would rebuild the same grouping a
thousand times during an import.

**Fetching the models** is the odd one out: it is the only job here that touches the network, and
it is the one thing that has to happen before any of the others can do anything at all. It is a job
rather than a request because it takes minutes, because the dashboard already draws a bar for every
job, and because cancelling a job is already a thing somebody can do.

Every one of them checks the switch first and does nothing at all when it is off. A job that was
queued before somebody turned the feature off finds it off and stops, rather than doing the work
its payload describes.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from contextlib import suppress
from time import monotonic

from sift.kernel.access import Viewer
from sift.kernel.access import sentences as say
from sift.kernel.access.sentences import files as files_counted
from sift.kernel.access.sentences import people as people_counted
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.jobs import (
    JobBlocked,
    JobContext,
    JobQueue,
    JobSwitchedOff,
    WaitingForPassword,
    register_handler,
)
from sift.kernel.jobs.families import AGAIN, Family
from sift.kernel.jobs.tuning import BATCH_SETTLE_SECONDS
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.ml.weights import WeightError
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.kernel.seams import BoxPicturesSeam
from sift.kernel.workbench import DOER, Named, Piece, Preview, Recorded, Worded
from sift.slices.faces.folder_import import FACE_FOLDER_IMPORT, import_folder
from sift.slices.faces.models import Depth, ScanStatus
from sift.slices.faces.service import (
    ASKED_ONLY_QUEUE,
    FACE_STARTERS,
    REMEASURE_PAGE,
    STARTERS_PER_PERSON,
    STARTERS_QUEUE,
    FaceService,
    next_remeasure_page,
)
from sift.slices.faces.store_left_out import LeftOutStore

log = get_logger(__name__)

FACE_SCAN = "face_scan"
FACE_REMATCH = "face_rematch"
FACE_REGROUP = "face_regroup"
FACE_FETCH_WEIGHTS = "face_fetch_weights"
FACE_SWEEP = "face_sweep"
#: Delete face data, a batch per write (`forget_all`).
FACE_FORGET = "face_forget"
FACE_REMEASURE = "face_remeasure"
#: The floor pass: the files whose scan refused a face for size that the size floor set now would
#: accept, looked at again. Asked for by a start while any wait (`FaceService.floor_pass_owed`).
FACE_FLOOR = "face_floor"
#: The tile pass: every HEIF still whose faces were read from one tile of it, looked at again from
#: the whole picture. Asked for by a start while any wait (`FaceService.tile_pass_owed`).
FACE_WHOLE_PICTURE = "face_whole_picture"
#: The pass over facial fingerprints: every held entry the library's faces match placed by face
#: (`FaceService.recognize_from_fingerprints`). Asked for by an import, by a scan whose faces match
#: an entry, and the moment `Create people from these fingerprints as their faces are recognized`
#: is turned on (`wiring/reactions.py`). The stored word is older than the pass and kept, since a
#: job row on disk names it.
FACE_PEOPLE_FROM_FILES = "face_people_from_files"
#: The box's questions: every face in a file a stash-box put somebody on that the box's answer is a
#: claim about, asked about them (`FaceService.ask_for_the_boxes`). Asked for when a box files
#: people (the enrichment's write) and by a start while any wait (`box_questions_owed`).
FACE_BOX_QUESTIONS = "face_box_questions"

#: How much of the library one sweep job looks at.
#:
#: The sweep scans nothing itself (it enqueues one scan per file and stops), so this bounds the
#: number of rows one job writes, not the work. A library of a hundred thousand files would
#: otherwise put a hundred thousand jobs in the queue in a single write, which is a queue nobody
#: can read and a progress bar that means nothing. It re-queues itself while there is more, so the
#: whole library is still covered; it arrives in readable pieces.
#: How many rows one page of a sweep walks.
#:
#: **Taken from the cap rather than chosen, because the cap wins and a second number here only
#: disagrees with it.** `visible_assets` clamps every page to `MAX_PAGE_SIZE`, so a larger number
#: here would never be meant. The walk steps by what came back, so a clamp cannot skip rows, but
#: the number is read as fact elsewhere: the screen that watches a sweep works out from it how
#: many pages a library will take, and a number larger than the clamp would undercount the pages
#: and stop that screen counting a total altogether.
SWEEP_PAGE = MAX_PAGE_SIZE

#: How long a change to the reference gallery waits before everything is matched against it again.
#:
#: **Two seconds.** Naming six people in a row should be one pass over the library rather than six,
#: but the pass is cheap: at a couple of thousand unclaimed faces the matching is about 30 ms and
#: the regrouping that follows about 115 ms, so a longer window would be waited on for a pass
#: costing a sixth of a second.
#:
#: The window is still worth having and is why this is not zero: a burst of naming collapses onto
#: one request, and two seconds is longer than the gap between two presses. What it must not be is
#: longer than somebody's patience, because the thing on the other side of it is the faces LIKE the
#: one they just named, which is the answer they are sitting there waiting for.
#:
#: It is a fixed window rather than a restarting debounce: the first request sets the time, and
#: everything arriving before then collapses onto it. So this is the longest anybody ever waits,
#: not a countdown that a second press pushes back.
GALLERY_SETTLE_SECONDS = 2


async def ask_for_grouping(
    queue: JobQueue, *, delay: int = BATCH_SETTLE_SECONDS, full: bool = False
) -> None:
    """Ask for the unclaimed faces to be piled up, once the current batch of scanning has settled.

    Incrementally unless `full`: the faces in no pile are placed and the piles that exist are
    kept. A full grouping is for the end of a sweep, a model change, and the button. See
    `FaceService.regroup`.
    """
    await queue.enqueue_when_settled(FACE_REGROUP, {"full": True} if full else None, delay=delay)


async def ask_for_rematching(
    queue: JobQueue, *, delay: int = GALLERY_SETTLE_SECONDS, regroup_fully: bool = False
) -> None:
    """Ask for every unclaimed face to be compared against the gallery again.

    Called wherever the gallery changes: a face confirmed as somebody, a pack imported, a folder
    of galleries read in. No file is opened by the work this asks for; it is arithmetic over
    numbers already stored, which is the whole reason adding a person can be seconds rather than
    another pass over the library.

    `regroup_fully` asks the grouping that follows the re-match to start from scratch: what
    measuring every face again with a different model needs, because the piles' middles are the
    previous model's numbers and a new face compared against them would join the wrong pile.
    """
    await queue.enqueue_when_settled(
        FACE_REMATCH, {"regroup": "full"} if regroup_fully else None, delay=delay
    )


#: How often the download's progress is published, in seconds.
#:
#: Once a second rather than once a chunk. A chunk is a fraction of a megabyte, so a write per
#: chunk would hold the write lock for the length of a large model's download, competing with
#: exactly the work somebody started this to be able to do.
_PROGRESS_TICK = 1.0


#: The outcomes that leave a face nobody is attached to, and therefore something to group.
#:
#: A file with no faces in it, or one where everybody was recognized, has left nothing unclaimed:
#: asking for a grouping after those is asking the machine to prove there is no work to do.
_LEFT_SOMETHING_UNCLAIMED = frozenset({ScanStatus.NONE_IDENTIFIED, ScanStatus.SOME_IDENTIFIED})


async def scan(context: JobContext, *, service: FaceService) -> None:
    """Find and attribute the faces in one file, and ask for what it could not place to be grouped."""
    if not await service.enabled():
        log.info("faces.job.skipped", job=FACE_SCAN, reason="switched off")
        return

    asset_id = str(context.payload["asset_id"])
    # No depth in the payload means "whatever the settings say", NOT "fast": a fast default would
    # quietly override the setting on every scan a sweep starts, and the tuning recorded against
    # each result would say fast while the settings screen said deep, with nothing to report it.
    #
    # A depth in the payload is still honoured, because that is a request for ONE file to be looked
    # at harder than the setting.
    requested = context.payload.get("depth")
    depth = Depth(str(requested)) if requested else None
    # The sweep that queued this file, if one did. Its tuning is used in place of whatever the
    # settings say now, so a change made halfway through a sweep does not change the rest of it.
    run = context.payload.get("run")
    run_id = str(run) if run else None
    # Held, not failed, when the models are not on disk yet.
    #
    # A model family changed while a sweep is running takes minutes to download, and every scan the
    # sweep already queued would otherwise fail three times over about files that are perfectly
    # fine. Three attempts is the right answer to something that might work next time; a file
    # cannot be read with a model that has not arrived, and no number of attempts
    # changes that. It is the same shape the stash-box work uses for sealed keys and the download
    # work for a login it does not have: the job waits, costs no attempt, says what it is waiting
    # for, and the moment the thing arrives it runs.
    #
    # The one that releases it is `fetch_weights`, which is the only thing that can make this
    # sentence stop being true.
    waiting = await service.weights_problem(run=run_id)
    if waiting is not None:
        log.info("faces.scan.held", asset_id=asset_id, reason="models not installed")
        raise JobBlocked(waiting)
    # A PRESS ("Look for faces again" on a file, or its Run task) carries `AGAIN`, and the scan
    # reads it: a finished pass pressed again starts from the first moment rather than carrying on
    # from where its last readable moment was. See `FaceService._resume_point`.
    again = context.payload.get(AGAIN) is True
    try:
        status = await service.scan(asset_id, depth=depth, run=run_id, again=again)
    except WeightError:
        # There a moment ago and not now (a download replacing them): held, not failed.
        waiting = await service.weights_problem(run=run_id)
        if waiting is None:
            raise
        log.info("faces.scan.held", asset_id=asset_id, reason="models missing at load")
        raise JobBlocked(waiting) from None
    if status in _LEFT_SOMETHING_UNCLAIMED:
        await ask_for_grouping(context.queue)
    if await service.fingerprints_match_file(asset_id):
        # A face here matches a held entry of facial fingerprints: the pass places it once the
        # batch settles, collapsed onto one run however many files of a sweep ask.
        await ask_for_fingerprints(context.queue)
    await context.set_progress(1.0)
    if context.job.requested_by is not None:
        # SOMEBODY PRESSED THIS ONE AND IS LOOKING AT THE FILE ("Look for faces again" on its
        # page), so every screen drawing its faces is told, once. Without it the strip under the
        # file stays as it was: a scan that finds nobody new rings nothing, and neither does one
        # whose faces match nobody.
        #
        # Only for a press. A sweep's scans and an import's are queued by the machine and carry no
        # requester, and ringing the library bell once per file across a sweep would have every
        # open screen re-reading its lists for the length of it. `announce_now` because the scan's
        # writes have all landed by here. See `FaceService.rematch`, which rings the same way.
        announce_now(EVERY_ADMIN, About.LIBRARY)
    log.info("faces.scan.finished", asset_id=asset_id, status=status.value)


async def sweep(context: JobContext, *, service: FaceService) -> None:
    """Put every file that wants looking at into the queue, a page at a time.

    This is what "scan my library" means for somebody who has just switched the feature on: nothing
    before this moment was examined, because nothing was allowed to be. Files imported afterwards
    are scanned as they arrive and never reach this.

    It covers more than that. A file already looked at under *different* settings wants looking
    at again, and the tuning each result was produced under is stored beside it so that can be told
    apart, so turning the depth up and sweeping again does what it plainly ought to do. `force`
    goes further and offers everything, whatever it was scanned under.

    Re-queues itself rather than looping. A job that ran until the library was done would hold a
    worker for hours, could not report progress anybody could read, and would lose everything it
    had queued if the machine went down halfway. Each page is a complete, small unit of work.

    It carries the user who asked for it. The work list comes from the access layer, so the
    sweep covers what that admin can see rather than reaching past them, and a user who has
    gone since is a sweep that stops rather than one that quietly runs as nobody.

    What it queued is counted across the whole sweep and not just this page, because a page is an
    implementation detail and "I queued 40" reported four times over is not an answer to how much
    work was started.
    """
    if not await service.enabled():
        log.info("faces.job.skipped", job=FACE_SWEEP, reason="switched off")
        return

    viewer = await service.viewer_for(str(context.payload["viewer"]))
    if viewer is None:
        log.info("faces.job.skipped", job=FACE_SWEEP, reason="the user that asked is gone")
        return

    offset = int(context.payload.get("offset") or 0)
    force = bool(context.payload.get("force"))
    queued_before = int(context.payload.get("queued") or 0)
    # The first page IS the run, so it is the one that writes down what the run is running under.
    # Every page after it carries the id rather than asking again: asking again a page later is
    # the very thing this exists to stop.
    run = str(context.payload.get("run") or context.job.id)
    if context.payload.get("run") is None:
        await service.start_run(run)
    waiting, total, walked = await service.needs_scanning_page(
        viewer, offset=offset, limit=SWEEP_PAGE, force=force
    )
    for asset_id in waiting:
        await context.enqueue_child(FACE_SCAN, {"asset_id": asset_id, "run": run})

    # By what the page WALKED, never by what it was asked for.
    #
    # The access layer caps a page, so asking for 500 and stepping on by 500 steps over every file
    # between the cap and the request: 300 of every 500, never offered, with nothing to see but a
    # sweep that kept saying it had finished. Stepping by what came back cannot do that whatever the
    # cap is, or if it changes.
    reached = offset + walked
    queued = queued_before + len(waiting)
    await context.set_progress(1.0 if total == 0 else min(1.0, reached / total))

    # An empty page also ends it, and that is the guard rather than a tidy-up: a page that walked
    # nothing advances nothing, so re-queueing on one would be this job asking for itself again at
    # the same offset, forever.
    done = walked == 0 or reached >= total
    if done:
        # The one grouping that starts from scratch. Its scans are still running when this is
        # asked for, so it may run before the last of them; the grouping each of those asks for
        # places what arrives after it, and the piles it rebuilt keep their identities.
        await ask_for_grouping(context.queue, full=True)
        # And one re-match. References can arrive while recognition is off (a fingerprints file
        # is taken in then), and the faces already stored from before it was switched off were
        # never compared with them; each file this pass scans is, but the settled ones are not.
        await ask_for_rematching(context.queue)
    if not done:
        await context.enqueue_child(
            FACE_SWEEP,
            {
                "viewer": viewer.id,
                "offset": reached,
                "force": force,
                "queued": queued,
                "run": run,
            },
        )
    # A count only when it is the whole count. Until then it says it is still working it out, which
    # is a different kind of sentence and cannot be mistaken for the answer.
    await context.set_note(_swept(queued=queued, done=done))
    log.info("faces.sweep.queued", queued=queued, seen=reached, total=total, force=force)


def _swept(*, queued: int, done: bool) -> str:
    """How many files this run is going to scan, once that is known.

    A button that finishes in ten milliseconds having done nothing is indistinguishable from a
    broken one. Nothing to do is a perfectly good answer: it just has to be given.

    **One number, and it is the only one worth giving.** How many files the sweep walked past is
    bookkeeping about the sweep: "200 of 480 checked" beside a job that finished instantly says the
    machine looked at two hundred files, and it opened none of them. A sweep reads a list, decides
    what wants a pass, and queues those. Finishing in an instant is what it is supposed to do.

    **The running total is given while the run is still going**, in a sentence that says it is
    provisional, which a bare count would not. A sweep walks 200 files a page, and each page waits
    behind the scans the page before it queued, so a large library takes hours; without the running
    total the screen would say "working out which files need a look" all that while, which reads
    exactly like a sweep that queued two hundred files and stopped.
    """
    files = "file" if queued == 1 else "files"
    if not done:
        # Provisional, and it says so. The count is the run's, not the page's: every page carries
        # the total so far forward, so this only ever grows.
        return f"{queued:,} {files} queued so far, still going through the library\u2026"
    if queued == 0:
        return "Everything has already been scanned under these settings."
    return f"{queued:,} {files} queued to scan."


async def floor_pass(context: JobContext, *, service: FaceService) -> None:
    """Look again at the files a lower size floor can change, and at nothing else.

    A lowered floor (96 on balanced and lenient, where every scan was taken at 112) would make a
    face refused for size yesterday a face today, and the library is not offered again for it:
    the tuning's fingerprint holds the floor's slot still (`Configured.shape`), since a file
    whose scan refused nothing for size, or refused only faces under today's floor, would find
    exactly what it found before. The scan wrote down its biggest size refusal, so the files that
    can change are known, and each is scanned again. A file looked at
    again leaves the list whatever it finds (`Store.under_an_earlier_floor`), so the pass ends.
    Each is scanned under the settings set now. Sift's own act: nobody pressed it, and its run is
    Sift's on History.
    """
    if not await service.enabled():
        log.info("faces.job.skipped", job=FACE_FLOOR, reason="switched off")
        return
    after = ""
    queued = 0
    while True:
        page = await service.under_an_earlier_floor(after=after, limit=SWEEP_PAGE)
        if not page:
            break
        for asset_id in page:
            # The file alone, as an arriving file's scan is asked for, so a start that finds the
            # same files still waiting collapses onto the scans already queued for them.
            await context.enqueue_child(FACE_SCAN, {"asset_id": asset_id}, dedupe=True)
        queued += len(page)
        after = page[-1]
    await context.set_progress(1.0)
    files = "file" if queued == 1 else "files"
    await context.set_note(
        f"{queued:,} {files} queued to look at again under the size floor set now."
        if queued
        else "No file had a face the size floor set now would accept."
    )
    log.info("faces.floor_pass.queued", files=queued)


async def box_questions(context: JobContext, *, service: FaceService) -> None:
    """Ask about the one face in every file a stash-box put somebody on, where nothing has been
    asked. Sift's own act: nobody pressed it, and each person's record says so on History."""
    if not await service.enabled():
        log.info("faces.job.skipped", job=FACE_BOX_QUESTIONS, reason="switched off")
        return
    named: set[str] = set()
    asked = await service.ask_for_the_boxes(boxes=named)
    await context.set_progress(1.0)
    faces = "face" if asked == 1 else "faces"
    by = say.and_then(sorted(named)) or "a stash-box"
    await context.set_note(
        f"Asked about {asked:,} {faces} in files {by} filed people under."
        if asked
        else "No face in a file a stash-box filed was waiting to be asked about."
    )


async def tile_pass(context: JobContext, *, service: FaceService) -> None:
    """Look again, from the whole picture, at every HEIF still whose faces came from one tile.

    Each through the path a press of "Look for faces again" takes (`AGAIN`: the pass starts over
    on a finished file), so the faces found on the tile go and the whole picture's come. A file
    looked at again is newer than its copy and leaves the list, so the pass ends
    (`FaceService.read_from_a_tile`). Sift's own act: nobody pressed it, and its run is Sift's on
    History.
    """
    if not await service.enabled():
        log.info("faces.job.skipped", job=FACE_WHOLE_PICTURE, reason="switched off")
        return
    after = ""
    queued = 0
    while True:
        tiles, after = await service.read_from_a_tile(after=after, limit=SWEEP_PAGE)
        for asset_id in tiles:
            await context.enqueue_child(FACE_SCAN, {"asset_id": asset_id, AGAIN: True}, dedupe=True)
        queued += len(tiles)
        if not after:
            break
    await context.set_progress(1.0)
    files = "photo" if queued == 1 else "photos"
    await context.set_note(
        f"{queued:,} {files} queued to look at again from the whole picture."
        if queued
        else "Every photo's faces were already read from the whole picture."
    )
    log.info("faces.tile_pass.queued", files=queued)


async def rematch(context: JobContext, *, service: FaceService) -> None:
    """Compare every unattributed face against everybody, after the gallery changed.

    A face claimed here has left the unclaimed pool, so the piles are no longer what they were:
    hence the grouping that follows, and hence why it follows rather than being asked for
    separately by whoever changed the gallery. Ordering matters: grouping the pool before matching
    it would pile up faces that were about to find their owner.
    """
    if not await service.enabled():
        log.info("faces.job.skipped", job=FACE_REMATCH, reason="switched off")
        return
    attributed = await service.rematch()
    full = context.payload.get("regroup") == "full"
    if attributed or full:
        await ask_for_grouping(context.queue, delay=0, full=full)
    await context.set_progress(1.0)
    log.info("faces.rematch.finished", attributed=attributed)


async def remeasure(context: JobContext, *, service: FaceService) -> None:
    """Describe the library's stored faces again with the model now set, a page at a time.

    Asked for when the model family changes and, at boot, whenever any file is still described
    by another model (a change made while the models were not yet fetched, or a process that
    stopped halfway) is finished rather than forgotten. Re-queues itself while any remain, like
    the sweep and for the same reasons; when none do, what Sift decided by arithmetic is decided
    again, which is the re-match and the grouping that follows it.
    """
    if not await service.enabled():
        log.info("faces.job.skipped", job=FACE_REMEASURE, reason="switched off")
        return
    done_before = int(context.payload.get("done") or 0)
    # The page is sized by the clock of the page before it, carried in the payload the way the
    # running total is. A fixed page cannot be right: a page of 100 files can take over ten minutes
    # underneath a concurrent scan and seconds on an idle machine. See `next_remeasure_page` for
    # the bounds and why only growth is bounded.
    size = max(1, int(context.payload.get("page") or REMEASURE_PAGE))
    started = monotonic()
    page = await service.remeasure(limit=size)
    took = monotonic() - started
    done = done_before + page.files + page.references
    if page.remaining:
        await context.enqueue_child(
            FACE_REMEASURE, {"done": done, "page": next_remeasure_page(size=size, seconds=took)}
        )
        await context.set_progress(done / (done + page.remaining) if done else 0.0)
    else:
        await ask_for_rematching(context.queue, delay=0, regroup_fully=True)
        await context.set_progress(1.0)
    await context.set_note(_remeasured(done=done, remaining=page.remaining))
    log.info(
        "faces.remeasure.finished",
        files=page.files,
        references=page.references,
        remaining=page.remaining,
        page=size,
        took_ms=round(took * 1000),
    )


def _remeasured(*, done: int, remaining: int) -> str:
    """Where the pass has got to, in one sentence.

    It says how many are LEFT as well as how many are done. A count that
    only ever goes up answers "is it moving" and not "how much longer", and this pass is the one
    somebody sits through after changing the model family.
    """
    if not remaining:
        return f"Measured {done:,} faces again"
    return f"Measured {done:,} faces again, {remaining:,} to go"


async def regroup(
    context: JobContext, *, service: FaceService, settles_into: Sequence[str] = ()
) -> None:
    """Pile up the faces nobody has been attached to.

    A full regroup then asks for `settles_into`, the passes that read what the groups are (the
    folder reader, which proposes a group as somebody): a rebuilt group has a new id, and a
    proposal about the old one is made again only by a pass that looks. Work somebody switched
    off is left off.
    """
    if not await service.enabled():
        log.info("faces.job.skipped", job=FACE_REGROUP, reason="switched off")
        return
    full = bool(context.payload.get("full"))
    piles = await service.regroup(full=full)
    await context.set_progress(1.0)
    log.info("faces.regroup.finished", piles=piles, full=full)
    if full:
        for job_type in settles_into:
            with suppress(JobSwitchedOff):
                await context.queue.enqueue_when_settled(job_type)


async def ask_for_fingerprints(queue: JobQueue, *, delay: int = BATCH_SETTLE_SECONDS) -> None:
    """Ask for the pass over facial fingerprints once the current batch has settled."""
    await queue.enqueue_when_settled(FACE_PEOPLE_FROM_FILES, delay=delay)


async def people_from_files(context: JobContext, *, service: FaceService) -> None:
    """Place every held entry of facial fingerprints the library's faces match, then match again.

    Each person made or given an entry is announced and written to History as it happens
    (`FaceService.recognize_from_fingerprints`), named on the faces that matched it and holding
    their files, so People fills in while the run goes. Their new references name any other face
    that matches them in the one re-match asked for at the end.
    """
    if not await service.enabled():
        log.info("faces.job.skipped", job=FACE_PEOPLE_FROM_FILES, reason="switched off")
        return
    run = await service.recognize_from_fingerprints()
    if run.made or run.claimed:
        await ask_for_rematching(context.queue, delay=0)
    await context.set_progress(1.0)
    await context.set_note(_placed(made=len(run.made), claimed=len(run.claimed), asked=run.asked))
    log.info(
        "faces.job.people_from_files",
        made=len(run.made),
        claimed=len(run.claimed),
        asked=run.asked,
    )


def _placed(*, made: int, claimed: int, asked: int) -> str:
    """What the pass says it did, in Activity."""
    parts = []
    if made:
        parts.append(f"added {people_counted(made)}")
    if claimed:
        parts.append(f"added fingerprints to {people_counted(claimed)} already here")
    if asked:
        parts.append(f"{asked:,} waiting for your answer")
    if not parts:
        return "No faces matched the facial fingerprints"
    said = ", ".join(parts)
    return said[0].upper() + said[1:]


async def fetch_weights(context: JobContext, *, service: FaceService) -> None:
    """Download the models this install is set to use.

    A job, because it takes minutes, the dashboard draws its bar, and a job can be cancelled.

    The awkward part is the seam between the two halves and it is worth naming. Reporting progress
    is asynchronous (it writes to the queue), and the callback the transfer offers is an
    ordinary function called once per chunk, which cannot wait for anything. So the callback does
    the only two things it can do without waiting: it writes the latest count into a variable, and
    it reads a flag saying whether to stop. A ticker beside the transfer is what turns those into a
    progress row and a cancellation, on its own schedule rather than on the network's.
    """
    if not await service.enabled():
        log.info("faces.job.skipped", job=FACE_FETCH_WEIGHTS, reason="switched off")
        return

    latest = [0, 0]
    stop = False

    def note(written: int, total: int) -> bool:
        latest[0], latest[1] = written, total
        return not stop

    async def report() -> None:
        """Publish what the transfer has managed so far, and stop it if it has been cancelled.

        Once a second rather than once a chunk: a chunk is a fraction of a megabyte, and a write to
        the queue per chunk would put a large model's download on the write lock for its whole
        length, competing with the very work somebody is waiting for.
        """
        nonlocal stop
        while True:
            await asyncio.sleep(_PROGRESS_TICK)
            written, total = latest
            if total > 0:
                await context.set_progress(min(written / total, 1.0))
            try:
                await context.raise_if_canceled()
            except BaseException:
                # Setting the flag rather than cancelling the transfer: the reader stops asking for
                # the next chunk and leaves a partial file behind, so the next attempt resumes from
                # where this one stopped. Tearing the task down mid-write would leave a file whose
                # length nobody can trust.
                stop = True
                raise

    ticker = asyncio.create_task(report())
    try:
        # A press of "Download the models again" fetches the files already here too.
        installed = await service.install_models(
            progress=note, force=context.payload.get("again") is True
        )
    finally:
        ticker.cancel()
        with suppress(asyncio.CancelledError):
            await ticker

    await context.set_progress(1.0)
    log.info("faces.weights.job_finished", installed=len(installed))
    if installed:
        # THE SCANS PARKED ON THE MISSING MODELS RUN NOW. This is the only thing that can make the
        # sentence they are waiting on stop being true, so it is the only thing that can release
        # them: the same reasoning that puts the re-measure below here rather than anywhere else.
        # Named by type, so a job parked on something quite different (a stash-box key, a site
        # login) is left where it is.
        #
        # It releases EVERY parked scan, including one pinned to a run whose family is still not
        # here, and that is deliberate rather than overlooked: which family a scan wants is inside
        # its run's tuning, the queue cannot read it, and a scan released too early parks itself
        # again for the cost of a claim and no attempt. The alternative is a scan that stays parked
        # because nobody asked it, which is the expensive mistake of the two.
        released = await context.queue.unblock(job_type=FACE_SCAN)
        if released:
            log.info("faces.scans.released", count=len(released))
        # A family changed before its models were fetched could not be measured against them. Now
        # it can; nothing else would ask.
        if await service.measured_by_another_model():
            await context.queue.enqueue_when_settled(FACE_REMEASURE, delay=0)


async def starters(
    context: JobContext, *, service: FaceService, door: BoxPicturesSeam | None
) -> None:
    """Fetch each person's stash-box pictures and file what passes as STARTER references.

    Queued at link time for one person (`Recognition.linked`) and by the press for everybody the
    count named. HELD, not failed, while the models are not on disk (the checks need them, and no
    number of attempts makes a model arrive; see `scan` for the measurement behind that rule), and
    while the stash-box keys are sealed, the way the stash-box work itself waits. Each person is
    asked again whether she still wants starters when her turn comes, so a face confirmed after the
    press is not followed by starters that would be retired immediately.
    """
    if not await service.enabled():
        log.info("faces.job.skipped", job=FACE_STARTERS, reason="switched off")
        return
    if door is None:
        log.warning("faces.starter.no_door")
        return
    waiting = await service.weights_problem()
    if waiting is not None:
        log.info("faces.starter.held", reason="models not installed")
        raise JobBlocked(waiting)
    key = await context.master_key()
    if key is None:
        raise WaitingForPassword("the stash-box keys")
    # The press names its People (the count it showed); a link asks for everybody linked since
    # starters existed who still has no reference row. See `LINKED_SINCE_STARTERS`.
    named = context.payload.get("people")
    people = (
        [str(one) for one in named]
        if isinstance(named, list)
        else await service.starters_wanted(await door.linked_people(with_picture_lists=True))
    )
    filed: dict[str, list[str]] = {}
    sources: set[str] = set()
    last = monotonic()
    try:
        for done, person_id in enumerate(people, start=1):
            if await service.wants_starters([person_id]):
                pictures = await door.pictures_of(person_id, key, most=STARTERS_PER_PERSON)
                # None is "could not be asked now" and is left for the next Run; an empty list is
                # every box answering with nothing, which `file_starters` remembers, so she leaves
                # the count instead of being offered for ever. See `BoxPicturesSeam`.
                filed[person_id] = (
                    [] if pictures is None else await service.file_starters(person_id, pictures)
                )
                if filed[person_id] and pictures is not None:
                    sources.update(source for source, _ in pictures)
            if monotonic() - last >= _PROGRESS_TICK:
                await context.set_progress(done / len(people))
                last = monotonic()
    finally:
        # What was filed is recorded however the run ends, and that is the whole of this block:
        # written after the loop, a run stopped part-way (by the graphics card, say) would have
        # filed its People's starters with no line in History, no Undo and no re-match.
        # `file_starters` writes a person's pictures only after every check on them has run, so what
        # `filed` holds is exactly what landed; an attempt after a failure asks `wants_starters`
        # again, skips those People, and records its own.
        await service.record_starters(filed, sorted(sources))
        if any(filed.values()):
            # Every face nobody is on is compared again, now against the starters too, and a face
            # that resembles one of them is ASKED about, never named. See `FaceService.file_starters`.
            await ask_for_rematching(context.queue)
    await context.set_progress(1.0)
    await context.set_note(_started(filed))
    log.info("faces.starter.finished", people=len(people), filed=sum(map(len, filed.values())))


def _started(filed: dict[str, list[str]]) -> str:
    """What the task says it did, in Activity: the same count History's line gives."""
    pictures = sum(len(ids) for ids in filed.values())
    people = sum(1 for ids in filed.values() if ids)
    if pictures == 0:
        return "No stash-box picture passed the checks for a starter"
    return (
        f"Added {pictures:,} starter {'picture' if pictures == 1 else 'pictures'} "
        f"for {people:,} {'person' if people == 1 else 'people'}"
    )


class StarterRecords:
    """A run's starter pictures in History, and the Undo that retires them.

    Retired, the same as a "no" retires them: Sift stops asking from those pictures, and the rows
    stay so the same pictures are not filed again by the next link or press.
    """

    name = STARTERS_QUEUE
    reversible = True

    def __init__(self, service: FaceService) -> None:
        self._service = service

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: its People are its subjects, and a starter is a stash-box's photo, not a file."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Retire every starter the run filed that is still in use."""
        try:
            held = json.loads(payload).get("references") or {}
        except (ValueError, AttributeError):
            return False
        ids = [str(one) for ids in held.values() for one in ids]
        await self._service.retire_starters(ids)
        return True

    def worded(self, recorded: Recorded) -> Worded | None:
        """This run's line, worded when shown. See `kernel.workbench.Recorded`.

        An older stored title said "Added 300 starter pictures from FansDB and StashDB for 80
        people", with nobody doing it. So Sift does it, the pictures and the People are counted from
        the payload, and one person is named: "them" on their own page. The boxes are the
        payload's, or an older title's, read strictly (`_STARTERS_FROM`); a title that does not
        match says none.
        """
        held = recorded.held()
        references = held.get("references")
        if not isinstance(references, dict):
            return None
        counts = {
            str(person): len(ids)
            for person, ids in references.items()
            if isinstance(ids, list) and ids
        }
        if (
            recorded.page is not None
            and recorded.page[0] == "person"
            and recorded.page[1] in counts
        ):
            # On one of the People's own pages, the part about them: "Sift added 4 starter pictures
            # from FansDB for them", never the whole run's count. The name is still the reader's to
            # say, as "them".
            counts = {recorded.page[1]: counts[recorded.page[1]]}
            narrowed = True
        else:
            narrowed = False
        pictures = sum(counts.values())
        if not pictures:
            return None
        boxes = held.get("boxes")
        if isinstance(boxes, list) and boxes and all(isinstance(one, str) for one in boxes):
            source: str | None = " and ".join(boxes)
        else:
            shape = _STARTERS_FROM.fullmatch(recorded.title.strip())
            source = shape["boxes"] if shape else None
        if narrowed and source is not None and " and " in source:
            # The run kept which boxes it used, not which box each person's pictures came from, so
            # her part names no box rather than one that may not be hers.
            source = None
        who: tuple[Piece, ...] = (
            (Named(kind="person", id=next(iter(counts))),)
            if len(counts) == 1
            else (f"{len(counts):,} people",)
        )
        added = f" added {pictures:,} starter {'picture' if pictures == 1 else 'pictures'}"
        where: tuple[Piece, ...] = (f" from {source}",) if source else ()
        more: tuple[Piece, ...] = (recorded.detail,) if recorded.detail else ()
        return Worded(said=(DOER, added, *where, " for ", *who), more=more)


#: The boxes an older starters title named: "Added 300 starter pictures from FansDB and StashDB for
#: 80 people". The shape `FaceService.record_starters` wrote before the payload carried them.
_STARTERS_FROM = re.compile(r"Added [\d,]+ starter pictures? from (?P<boxes>.+?) for .+")


class AskedOnlyRecords:
    """The reconcile's record in History, and the answer that it cannot be taken back.

    A reverser with no card, like `queue.IdentifiedRecords`, registered so History draws the
    record as final rather than offering an Undo that would refuse: putting a name back on a file
    Sift only asked about is the fault the reconcile repaired. Answering the question Yes is how a
    name goes back on, one face at a time, which is the only way it was ever meant to.
    """

    name = ASKED_ONLY_QUEUE
    #: Final. See the class.
    reversible = False

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: the record is a count over thousands of files, and its People are its subjects."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing to put back. See the class."""
        return False

    def worded(self, recorded: Recorded) -> Worded | None:
        """This record's line, worded when shown. See `kernel.workbench.Recorded`.

        The stored title said "3,000 files no longer list a person Sift only asked you about":
        nobody doing it, and the People counted nowhere though the payload keeps each one. So Sift
        does it, and one person is named. The stored detail stays under it: it is the explanation.
        """
        held = recorded.held()
        count, people = held.get("files"), held.get("people")
        if not isinstance(count, int) or count < 1 or not isinstance(people, dict) or not people:
            return None
        where = f" off {files_counted(count)} where it had only asked you about them"
        who: tuple[Piece, ...] = (
            (Named(kind="person", id=str(next(iter(people)))),)
            if len(people) == 1
            else (f"{len(people):,} people",)
        )
        more: tuple[Piece, ...] = (recorded.detail,) if recorded.detail else ()
        return Worded(said=(DOER, " took ", *who, where), more=more)


def register_handlers(
    *,
    service: FaceService,
    door: BoxPicturesSeam | None = None,
    regroup_settles_into: Sequence[str] = (),
    left_out: LeftOutStore | None = None,
) -> None:
    register_handler(
        FACE_SCAN,
        lambda context: scan(context, service=service),
        name="Scanning for faces",
        counts="files looked at for faces",
        family=Family.IDENTIFY,
        by_itself=True,
    )
    register_handler(
        FACE_SWEEP,
        lambda context: sweep(context, service=service),
        name="Checking for new faces",
        family=Family.IDENTIFY,
    )
    register_handler(
        FACE_REMEASURE,
        lambda context: remeasure(context, service=service),
        name="Measuring faces with the chosen model",
        family=Family.IDENTIFY,
    )
    register_handler(
        FACE_FLOOR,
        lambda context: floor_pass(context, service=service),
        name="Looking again at faces refused for size",
        family=Family.IDENTIFY,
        # One at a time: two copies would queue the same files twice.
        alone=True,
    )
    register_handler(
        FACE_WHOLE_PICTURE,
        lambda context: tile_pass(context, service=service),
        name="Looking again at faces in photos read from one tile",
        family=Family.IDENTIFY,
        alone=True,
    )
    register_handler(
        FACE_BOX_QUESTIONS,
        lambda context: box_questions(context, service=service),
        name="Asking about faces in files a stash-box filed",
        # Not a pass over files: it opens none and reads stored rows, so it is counted as one
        # act, not as Identify's pace (see `FACE_STARTERS` below).
        family=Family.OTHER,
        # One at a time: two copies would read the same faces before either wrote.
        alone=True,
    )
    register_handler(
        FACE_REMATCH,
        lambda context: rematch(context, service=service),
        name="Re-matching People",
        family=Family.IDENTIFY,
    )
    register_handler(
        FACE_REGROUP,
        lambda context: regroup(context, service=service, settles_into=regroup_settles_into),
        name="Grouping unnamed faces",
        family=Family.IDENTIFY,
    )
    register_handler(
        FACE_PEOPLE_FROM_FILES,
        lambda context: people_from_files(context, service=service),
        name="Recognizing People from facial fingerprints",
        # Not a pass over files: counted as Identify, its pace would count names as files read.
        family=Family.OTHER,
        # One at a time: two runs over the same names would make each person twice.
        alone=True,
    )
    register_handler(
        FACE_STARTERS,
        lambda context: starters(context, service=service, door=door),
        name="Adding starter pictures from stash-boxes",
        # Not a pass over files: as Identify, its pace would count picture checks as files read.
        family=Family.OTHER,
        # One at a time: two runs over the same People would fetch the same pictures twice.
        alone=True,
    )
    _register_one_at_a_time(service, left_out)


async def _fingerprints_now(queue: JobQueue) -> None:
    """The pass over facial fingerprints, asked for immediately after a folder import landed faces."""
    await ask_for_fingerprints(queue, delay=0)


def _register_one_at_a_time(service: FaceService, left_out: LeftOutStore | None) -> None:
    register_handler(
        FACE_FETCH_WEIGHTS,
        lambda context: fetch_weights(context, service=service),
        name="Downloading facial recognition model",
        # One at a time: two would append to the same partial file.
        alone=True,
    )
    register_handler(
        FACE_FORGET,
        lambda context: forget(context, service=service),
        name="Deleting face data",
        alone=True,
    )
    register_handler(
        FACE_FOLDER_IMPORT,
        lambda context: import_folder(
            context, service=service, ask=_fingerprints_now, left_out=left_out
        ),
        name="Importing a folder of people",
        # Not a pass over library files: it reads a folder of pictures that never join the library.
        family=Family.OTHER,
        # One at a time: two imports of one gallery would read every picture twice.
        alone=True,
    )


async def forget(context: JobContext, *, service: FaceService) -> None:
    """Delete face data for whoever pressed it, a batch per write. See `forget_all`."""
    by = Actor.user(context.pressed_by) if context.pressed_by else Actor.sift("faces")
    await service.forget_everything(actor=by, progress=context.set_progress)
