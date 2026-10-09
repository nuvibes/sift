# SPDX-License-Identifier: AGPL-3.0-or-later
"""The face work as background jobs: scanning a file, matching again, grouping, fetching models
and the passes over the library. Every job checks the switch first and does nothing when it is off.
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
    backs_off,
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
#: The floor pass: files whose scan refused a face for size that today's floor would accept.
FACE_FLOOR = "face_floor"
#: The tile pass: every HEIF still whose faces were read from one tile of it.
FACE_WHOLE_PICTURE = "face_whole_picture"
#: The pass over facial fingerprints (`FaceService.recognize_from_fingerprints`); the stored word
#: is older than the pass and kept, since a job row on disk names it.
FACE_PEOPLE_FROM_FILES = "face_people_from_files"
#: The box's questions: faces in files a stash-box filed somebody under (`ask_for_the_boxes`).
FACE_BOX_QUESTIONS = "face_box_questions"

#: How many rows one page of a sweep walks: the access layer's cap, since it clamps every page.
SWEEP_PAGE = MAX_PAGE_SIZE

#: How long a change to the reference gallery waits before everything is matched again: a fixed
#: window, so a burst of naming is one pass, and short, since the pass costs a sixth of a second.
GALLERY_SETTLE_SECONDS = 2


async def ask_for_grouping(
    queue: JobQueue, *, delay: int = BATCH_SETTLE_SECONDS, full: bool = False
) -> None:
    """Ask for the unclaimed faces to be grouped once scanning settles; `full` starts afresh."""
    await queue.enqueue_when_settled(FACE_REGROUP, {"full": True} if full else None, delay=delay)


async def ask_for_rematching(
    queue: JobQueue, *, delay: int = GALLERY_SETTLE_SECONDS, regroup_fully: bool = False
) -> None:
    """Ask for every unclaimed face to be compared against the gallery again.

    `regroup_fully` rebuilds the groups afterwards, as a model change needs.
    """
    await queue.enqueue_when_settled(
        FACE_REMATCH, {"regroup": "full"} if regroup_fully else None, delay=delay
    )


#: How often the download's progress is published, in seconds: not per chunk, which holds the lock.
_PROGRESS_TICK = 1.0


#: The outcomes that leave a face nobody is attached to, and so something to group.
_LEFT_SOMETHING_UNCLAIMED = frozenset({ScanStatus.NONE_IDENTIFIED, ScanStatus.SOME_IDENTIFIED})


async def scan(context: JobContext, *, service: FaceService) -> None:
    """Find and attribute the faces in one file, and ask for the rest to be grouped."""
    if not await service.enabled():
        log.info("faces.job.skipped", job=FACE_SCAN, reason="switched off")
        return

    asset_id = str(context.payload["asset_id"])
    # No depth means the settings' depth; a depth is a request for one file to be looked at harder.
    requested = context.payload.get("depth")
    depth = Depth(str(requested)) if requested else None
    # The sweep's tuning, if one queued this, so a mid-sweep change does not alter the rest.
    run = context.payload.get("run")
    run_id = str(run) if run else None
    # Held, not failed, while the models are not on disk: no attempt makes them arrive.
    waiting = await service.weights_problem(run=run_id)
    if waiting is not None:
        log.info("faces.scan.held", asset_id=asset_id, reason="models not installed")
        raise JobBlocked(waiting)
    # A press carries `AGAIN`: a finished pass starts over (`FaceService._resume_point`).
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
        # A face matching held facial fingerprints: the pass places it once the batch settles.
        await ask_for_fingerprints(context.queue)
    await context.set_progress(1.0)
    if context.job.requested_by is not None:
        # A press: screens drawing this file's faces are told, once; a sweep's scans ring nothing.
        announce_now(EVERY_ADMIN, About.LIBRARY)
    log.info("faces.scan.finished", asset_id=asset_id, status=status.value)


async def sweep(context: JobContext, *, service: FaceService) -> None:
    """Put every file that wants looking at into the queue, a page at a time, re-queueing itself.

    Covers files scanned under other settings too, or everything with `force`; runs as the admin
    who asked, and counts what it queued across the whole sweep.
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
    # The first page is the run and writes down its tuning; later pages carry its id.
    run = str(context.payload.get("run") or context.job.id)
    if context.payload.get("run") is None:
        await service.start_run(run)
    waiting, total, walked = await service.needs_scanning_page(
        viewer, offset=offset, limit=SWEEP_PAGE, force=force
    )
    for asset_id in waiting:
        await context.enqueue_child(FACE_SCAN, {"asset_id": asset_id, "run": run})

    # By what the page walked, never by what it asked for: the access layer caps a page.
    reached = offset + walked
    queued = queued_before + len(waiting)
    await context.set_progress(1.0 if total == 0 else min(1.0, reached / total))

    # An empty page also ends it, or the job would ask for itself at the same offset forever.
    done = walked == 0 or reached >= total
    if done:
        # The one grouping that starts from scratch.
        await ask_for_grouping(context.queue, full=True)
        # And one re-match, for references taken in while recognition was off.
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
    # A running count says it is still working; only the whole count reads as the answer.
    await context.set_note(_swept(queued=queued, done=done))
    log.info("faces.sweep.queued", queued=queued, seen=reached, total=total, force=force)


def _swept(*, queued: int, done: bool) -> str:
    """How many files this run is going to scan, said even when none, provisional until done."""
    files = "file" if queued == 1 else "files"
    if not done:
        return f"{queued:,} {files} queued to scan so far."
    if queued == 0:
        return "Everything has already been scanned under these settings."
    return f"{queued:,} {files} queued to scan."


async def floor_pass(context: JobContext, *, service: FaceService) -> None:
    """Look again at the files a lower size floor can change, and at nothing else.

    Each leaves the list once looked at, so the pass ends. Sift's own act on History.
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
            # The file alone, so it collapses onto a scan already queued for it.
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
    """Ask about the one face in every file a stash-box put somebody on, where nothing was asked."""
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
    """Look again, from the whole picture, at every HEIF still whose faces came from one tile."""
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
    """Compare every unattributed face against everybody, then group what is left."""
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
    """Describe the stored faces again with the model now set, a page at a time, then re-match."""
    if not await service.enabled():
        log.info("faces.job.skipped", job=FACE_REMEASURE, reason="switched off")
        return
    done_before = int(context.payload.get("done") or 0)
    # The page is sized by the previous page's clock (`next_remeasure_page`).
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
    """Where the pass has got to: how many done and how many left."""
    if not remaining:
        return f"Measured {done:,} faces again"
    return f"Measured {done:,} faces again, {remaining:,} to go"


async def regroup(
    context: JobContext, *, service: FaceService, settles_into: Sequence[str] = ()
) -> None:
    """Group the faces nobody is attached to; a full regroup then asks for `settles_into`."""
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
    """Place every held entry of facial fingerprints the library's faces match, then match again."""
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
    """Download the models this install is set to use, with progress and cancelling.

    The transfer's callback cannot wait, so it records a count and reads a stop flag; a ticker
    beside it publishes the count and checks for cancelling.
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
        """Publish progress once a second, and stop the transfer if it has been cancelled."""
        nonlocal stop
        while True:
            await asyncio.sleep(_PROGRESS_TICK)
            written, total = latest
            if total > 0:
                await context.set_progress(min(written / total, 1.0))
            try:
                await context.raise_if_canceled()
            except BaseException:
                # A flag rather than a cancel: the partial file stays whole for the next attempt.
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
        # The scans parked on the missing models run now; any that still lack them park again.
        released = await context.queue.unblock(job_type=FACE_SCAN)
        if released:
            log.info("faces.scans.released", count=len(released))
        # A family changed before its models arrived can now be measured.
        if await service.measured_by_another_model():
            await context.queue.enqueue_when_settled(FACE_REMEASURE, delay=0)


async def starters(
    context: JobContext, *, service: FaceService, door: BoxPicturesSeam | None
) -> None:
    """Fetch each person's stash-box pictures and file what passes as starter references.

    Held while the models are missing or the stash-box keys are sealed.
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
    # The press names its People; a link asks for everybody linked since (`LINKED_SINCE_STARTERS`).
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
                # None is "could not be asked now"; an empty list is remembered (`BoxPicturesSeam`).
                filed[person_id] = (
                    [] if pictures is None else await service.file_starters(person_id, pictures)
                )
                if filed[person_id] and pictures is not None:
                    sources.update(source for source, _ in pictures)
            if monotonic() - last >= _PROGRESS_TICK:
                await context.set_progress(done / len(people))
                last = monotonic()
    finally:
        # Recorded however the run ends, so a part-done run still has History and an Undo.
        await service.record_starters(filed, sorted(sources))
        if any(filed.values()):
            # Faces nobody is on are compared again, and asked about, never named.
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
    """A run's starter pictures in History, and the Undo that retires them."""

    name = STARTERS_QUEUE
    reversible = True

    def __init__(self, service: FaceService) -> None:
        self._service = service

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: its People are its subjects, and a starter is not a file."""
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
        """This run's line, worded when shown, from the payload or an older title's boxes."""
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
            # On one of the People's own pages, only her part.
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
            # The run kept its boxes, not each person's, so her part names none.
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


#: The boxes an older starters title named, before the payload carried them.
_STARTERS_FROM = re.compile(r"Added [\d,]+ starter pictures? from (?P<boxes>.+?) for .+")


class AskedOnlyRecords:
    """The reconcile's record in History, final: a name goes back on by answering, not by Undo."""

    name = ASKED_ONLY_QUEUE
    reversible = False

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: the record is a count, and its People are its subjects."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing to put back. See the class."""
        return False

    def worded(self, recorded: Recorded) -> Worded | None:
        """This record's line, worded when shown. See `kernel.workbench.Recorded`."""
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
        # Reads stored rows and opens no file: one act, not Identify's pace.
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
        # Names, not files read; one at a time, or each person is made twice.
        family=Family.OTHER,
        alone=True,
    )
    register_handler(
        FACE_STARTERS,
        lambda context: starters(context, service=service, door=door),
        name="Adding starter pictures from stash-boxes",
        # Picture checks, not files read; one at a time, or pictures are fetched twice.
        family=Family.OTHER,
        alone=True,
    )
    _register_one_at_a_time(service, left_out)


async def _fingerprints_now(queue: JobQueue) -> None:
    """The pass over facial fingerprints, asked for immediately after a folder import."""
    await ask_for_fingerprints(queue, delay=0)


def _register_one_at_a_time(service: FaceService, left_out: LeftOutStore | None) -> None:
    register_handler(
        FACE_FETCH_WEIGHTS,
        lambda context: fetch_weights(context, service=service),
        name="Downloading facial recognition model",
        # One at a time: two would append to the same partial file.
        alone=True,
    )
    backs_off(FACE_FETCH_WEIGHTS)
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
