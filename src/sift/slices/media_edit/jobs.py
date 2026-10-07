# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producing a compressed copy, and producing a few seconds of one to look at first.

Two jobs, and the shape of the first is the whole feature: settle where the file may be written
before anything is written, encode, measure, decide whether to try again, put the finished file in
place under a name nothing else holds, and only then tell the rest of Sift about it.

**Nothing here writes to a library folder itself.** The path is decided, and the finished file put
in place, by the one service allowed to change files somebody else put there, reached through the
kernel's write seam, because a feature may not import another feature and, more to the point,
because there should be exactly one piece of code deciding whether a write is allowed. What this
does is point ffmpeg at a path that service handed it.

**Every call that waits runs on a thread.** Encoding is a subprocess, measuring is a stat, checking
for room is a disk read; the API, the job feed and every video anybody is watching share one event
loop, and a call that waits on this loop stops all of them together with nothing logged.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from pydantic import ValidationError

from sift.kernel.access import Repository, Viewer, lineage
from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ingress import IngressRejected, Origin, verify_ingress
from sift.kernel.jobs import JobContext, register_handler
from sift.kernel.library_write import Placed, Staged
from sift.kernel.log import get_logger, timing_hook
from sift.kernel.media import FFmpegError, Source, resolve_decodable
from sift.kernel.media import run as run_tool
from sift.kernel.seams import DuplicatePairSeam, LibraryWriteSeam, ReindexSeam
from sift.kernel.subprocess import Priority
from sift.kernel.vocabulary import ACT_COMPRESS, ACT_EDIT
from sift.slices.media_edit import encode, operations, plan, provenance, tuning
from sift.slices.media_edit.editor import EDIT, EDITED_TAG, EditService, recorded_operation
from sift.slices.media_edit.models import EditStep
from sift.slices.media_edit.operations import ON_MOVING, Operation
from sift.slices.media_edit.orientation import Orientation
from sift.slices.media_edit.refusals import ProductionFailed
from sift.slices.media_edit.service import (
    COMPRESS,
    COMPRESS_SAMPLE,
    OPERATION,
    PRODUCED_TAG,
    CompressService,
    source_facts,
)
from sift.slices.media_edit.tuning import Rung

log = get_logger(__name__)

#: Where a sample lands. Sift's own cache, never a library folder: it is thrown away, and a file
#: that is going to be thrown away has no business sitting in somebody's collection even briefly.
SAMPLES_DIRECTORY = "compress-samples"


@dataclass(frozen=True, slots=True)
class _Request:
    """The payload, once it has been read and checked."""

    asset_id: str
    actor_id: str
    target_bytes: int | None
    compatibility: bool
    preset: str | None
    filename: str


async def compress(
    context: JobContext,
    *,
    settings: Settings,
    access: Repository,
    writer: LibraryWriteSeam,
    service: CompressService,
    duplicates: DuplicatePairSeam,
    reindexer: ReindexSeam,
    database: Database,
    follow_on: Sequence[str] = (),
) -> None:
    """One file: encode it down to the target, put it beside the original, and index it.

    The original is never touched. Every path out of here either produces a new file under a name
    nothing else held, or produces nothing at all: there is no third outcome, and that is a
    property of the write seam rather than of the care taken here.
    """
    request = _read(context)
    viewer = await _actor(access, request.actor_id)

    source = await resolve_decodable(context.content, request.asset_id, settings=settings)
    facts = source_facts(source.asset)

    staged = await writer.stage_beside(request.asset_id, filename=request.filename, actor=viewer)
    try:
        await _require_room(staged.working.parent, facts=facts, target=request.target_bytes)
        await _produce(context, source, staged, request=request, facts=facts, settings=settings)
        placed = await writer.keep(staged)
    finally:
        # A no-op once the file has been put in place, and the whole point on every other path: a
        # failure must not leave a scratch file sitting in somebody's folder.
        await writer.discard(staged)

    await _index(
        context,
        placed=placed,
        source_asset_id=request.asset_id,
        operation=OPERATION,
        preset=request.preset,
        target_bytes=request.target_bytes,
        actor_id=request.actor_id,
        tag_name=PRODUCED_TAG,
        act=ACT_COMPRESS,
        now=service.now(),
        access=access,
        duplicates=duplicates,
        reindexer=reindexer,
        database=database,
        settings=settings,
        follow_on=follow_on,
    )


async def _actor(access: Repository, actor_id: str) -> Viewer:
    """The user this job is acting for, rebuilt from its id, with the vault open.

    **That is the one deliberate difference from a request, and without it the whole feature is
    unusable on a concealed file.** Whether the vault is unlocked is a fact about a browsing
    session, and a job has no session: rebuilt from an id alone it always comes back shut. So the
    file somebody selected with the vault open is, minutes later, a file this cannot see, and the
    failure is not a refusal anybody can act on, it is "There is no such file" against a file they
    are looking at.

    It grants nothing. Revealing turns off concealment, which is this user's own flag and one
    they had already turned off to select the file; every share and restrict is untouched, and so
    is the separate question of whether a produced file may be written into that folder at all.
    The same reasoning, and the same call, as the other two features whose work outlives a session.
    """
    viewer = await access.load_viewer(actor_id, show_hidden=True)
    if viewer is None:
        raise ProductionFailed("the user who asked for this no longer exists")
    return viewer


def _read(context: JobContext) -> _Request:
    payload = context.payload
    target = payload.get("target_bytes")
    return _Request(
        asset_id=context.require_str("asset_id", "this job needs an asset_id"),
        actor_id=context.require_str("actor_id", "this job needs the user that asked for it"),
        target_bytes=int(target) if isinstance(target, int) else None,
        compatibility=bool(payload.get("compatibility")),
        preset=payload.get("preset") if isinstance(payload.get("preset"), str) else None,
        filename=context.require_str("filename", "this job needs a name for the file it makes"),
    )


# --- making the file --------------------------------------------------------------------------


def _converts_audio(request: _Request, facts: plan.SourceFacts) -> bool:
    """Whether this run touches the sound at all. Almost always no.

    Two conditions, and BOTH are required. The sound is only ever rebuilt when the container that
    plays anywhere cannot carry what is there, and only when playing anywhere is what was asked
    for. On a size target it is copied through whatever it is, because the saving is small next to
    the picture and the loss is immediately audible.

    Written as its own function rather than inline at the two call sites, because two call sites
    are how the second condition gets dropped: a rewrap path with it and an encode path without it
    would quietly re-encode the sound of a file given a size target.
    """
    return request.compatibility and plan.needs_audio_conversion(facts)


async def _produce(
    context: JobContext,
    source: Source,
    staged: Staged,
    *,
    request: _Request,
    facts: plan.SourceFacts,
    settings: Settings,
) -> None:
    """Write the finished bytes to the scratch path, whatever it takes to get there.

    Three routes, cheapest first. A file that already plays anywhere and already fits needs nothing,
    but it never reaches here, because the panel and the service both skip it. A file whose
    picture is already in a form that plays anywhere and is small enough has its packets rewrapped,
    which is seconds and lossless. Everything else climbs the ladder.
    """
    if plan.rewrap_is_enough(facts, target_bytes=request.target_bytes):
        with timing_hook("compress.rewrap", asset_id=request.asset_id):
            await _run(
                encode.rewrap_args(
                    source.path,
                    staged.working,
                    settings=settings,
                    convert_audio=_converts_audio(request, facts),
                )
            )
        return

    if request.target_bytes is None:
        # Compatibility with no size asked for, on a file whose picture cannot be copied across.
        # One encode at the best rung: nothing is trying to hit a number, so nothing steps down.
        rungs = plan.allowed_rungs(facts)
        rung = rungs[0] if rungs else tuning.RUNGS[0]
        await _encode_rung(
            context, source, staged, rung=rung, request=request, facts=facts, settings=settings
        )
        return

    await _climb(context, source, staged, request=request, facts=facts, settings=settings)


async def _climb(
    context: JobContext,
    source: Source,
    staged: Staged,
    *,
    request: _Request,
    facts: plan.SourceFacts,
    settings: Settings,
) -> None:
    """Encode, weigh, decide, repeat, and end holding the best rung that fitted.

    The scratch file is one file, so each attempt writes over the last. That is what the final
    re-encode is for: when the winner is not the attempt that happens to be sitting there, it is
    produced again. One extra pass in the uncommon case, against keeping several full-size copies
    of a video on somebody's disk at the same time, which is the alternative and is worse on the
    machines Sift runs on.
    """
    target = request.target_bytes
    assert target is not None  # noqa: S101 (the caller branches on this above)
    rungs = plan.allowed_rungs(facts)
    if not rungs:  # pragma: no cover - a source always has at least the floor rung available
        raise ProductionFailed("there is no quality setting Sift can use for this file")

    attempts: tuple[plan.Attempt, ...] = ()
    last_encoded: int | None = None

    while True:
        await context.raise_if_canceled()
        index = plan.next_attempt(attempts, target_bytes=target, source=facts)
        if index is None:
            break
        await _encode_rung(
            context,
            source,
            staged,
            rung=rungs[index],
            request=request,
            facts=facts,
            settings=settings,
        )
        last_encoded = index
        weighed = await _size_of(staged.working)
        attempts += (plan.Attempt(index=index, size_bytes=weighed),)
        await context.set_progress(min(0.9, len(attempts) / tuning.MAX_ATTEMPTS))
        log.info(
            "compress.attempt",
            asset_id=request.asset_id,
            rung=index,
            size_bytes=weighed,
            target_bytes=target,
        )

    best = plan.best_result(attempts, target_bytes=target)
    if best is None:
        # Nothing fitted, and the caller said to go ahead anyway. The smallest attempt is the
        # closest Sift can come, and it is what is kept, with a note, so the job says so.
        smallest = min(attempts, key=lambda each: each.size_bytes)
        await context.set_note(
            f"Couldn't reach the target \u2014 kept the smallest Sift could make, "
            f"{smallest.size_bytes // (1024 * 1024)} MB."
        )
        best = smallest

    if best.index != last_encoded:
        await _encode_rung(
            context,
            source,
            staged,
            rung=rungs[best.index],
            request=request,
            facts=facts,
            settings=settings,
        )


async def _encode_rung(
    context: JobContext,
    source: Source,
    staged: Staged,
    *,
    rung: Rung,
    request: _Request,
    facts: plan.SourceFacts,
    settings: Settings,
) -> None:
    await context.raise_if_canceled()
    with timing_hook("compress.encode", asset_id=staged.asset_id, crf=rung.crf):
        await _run(
            encode.compress_args(
                source.path,
                staged.working,
                rung=rung,
                settings=settings,
                convert_audio=_converts_audio(request, facts),
                hdr=facts.hdr,
            )
        )
    if not await asyncio.to_thread(staged.working.exists):
        # ffmpeg can exit 0 and write nothing: a file whose only frames are unreadable does
        # exactly that. Said properly here rather than as a missing-file traceback later.
        raise ProductionFailed(
            "ffmpeg read the file and produced nothing from it, which usually means it is "
            "truncated or is not the kind of media it claims to be"
        )


async def _require_room(directory: Path, *, facts: plan.SourceFacts, target: int | None) -> None:
    """Refuse before starting if the disk has no room for what is about to be written.

    Filling somebody's media disk is worse than not compressing: everything else writing to it
    fails at the same moment, and whatever notices first is rarely what caused it. The estimate
    errs high (the source's own size when nothing better is known), because the direction that
    matters is not starting a write there is no room for.
    """
    expected = target or facts.size_bytes or 0
    free = (await asyncio.to_thread(shutil.disk_usage, directory)).free
    if free < expected + tuning.FREE_SPACE_HEADROOM_BYTES:
        raise ProductionFailed(
            "there is not enough free space on that disk to write the compressed copy"
        )


async def _run(argv: list[str]) -> None:
    """Run ffmpeg below everything else on the machine, with this feature's own time limit.

    Background priority for the same reason every generated thing in Sift uses it: nobody is
    watching a compression finish, and the processor it would take from playback is a stutter
    somebody sees. The limit is this feature's rather than the derivative jobs', because a whole
    video at real quality is a different order of work from three seconds of preview.
    """
    try:
        await run_tool(
            argv,
            time_limit=tuning.SUBPROCESS_TIMEOUT_SECONDS,
            priority=Priority.BACKGROUND,
        )
    except FFmpegError as failure:
        raise ProductionFailed(str(failure)) from failure


async def _size_of(path: Path) -> int:
    return (await asyncio.to_thread(path.stat)).st_size


# --- telling the rest of Sift about it ----------------------------------------------------------


async def _index(
    context: JobContext,
    *,
    placed: Placed,
    source_asset_id: str,
    operation: str,
    preset: str | None,
    target_bytes: int | None,
    actor_id: str,
    tag_name: str,
    act: str,
    now: int,
    access: Repository,
    duplicates: DuplicatePairSeam,
    reindexer: ReindexSeam,
    database: Database,
    settings: Settings,
    follow_on: Sequence[str],
    also: Sequence[tuple[str, Mapping[str, object]]] = (),
) -> None:
    """Take the finished file in the way every other file is taken in, then say what it is.

    Through the gate first, exactly as a scan would. A file Sift wrote seconds ago is still put
    through it: the alternative is a second way into the library, and the second one is always the
    one that is subtly wrong.

    Everything after the gate is what makes the copy a copy rather than a stranger: what it
    inherits, what it is tagged, where it came from, and the note to duplicate detection that this
    pair is not a mistake somebody made.

    **Both halves of this slice end here, and that is the point of it taking so many arguments.**
    A compressed copy and an edited one differ in how they were made and in nothing about what they
    then owe. Written twice, the second copy would be the one missing the line that carries the
    hidden flag, and nothing would fail: it would simply appear in a grid somebody had hidden it
    from.
    """
    try:
        checked = await asyncio.to_thread(
            verify_ingress, placed.path, origin=Origin.SCAN, settings=settings
        )
    except IngressRejected as rejection:
        raise ProductionFailed(
            f"the file Sift produced did not pass its own check ({rejection.reason})"
        ) from rejection

    ingested = await context.content.ingest(
        checked,
        root_id=placed.root_id,
        rel_path=placed.rel_path,
        folder_id=placed.folder_id,
    )

    if not ingested.asset_is_new:
        # The bytes are already in the library under another path, so this is a second place an
        # existing asset sits rather than a new file. Nothing is inherited and nothing recorded:
        # writing over what that asset already carries would be this feature editing a file it did
        # not make.
        log.info("produced.already_known", asset_id=ingested.asset.id, operation=operation)
        return

    copy_id = ingested.asset.id
    await lineage.inherit(
        database,
        access,
        source_asset_id=source_asset_id,
        copy_asset_id=copy_id,
        now=now,
    )
    await lineage.tag_produced(
        database,
        asset_id=copy_id,
        tag_name=tag_name,
        act=act,
        now=now,
        new_id=new_id(),
    )
    await provenance.record(
        database,
        asset_id=copy_id,
        source_asset_id=source_asset_id,
        operation=operation,
        preset=preset,
        target_bytes=target_bytes,
        actor_id=actor_id,
        now=now,
    )
    # A copy is the same picture at a smaller size, so every duplicate method matches it against
    # its original. Settled now, before anybody is asked, or a run over forty files puts forty
    # questions in front of somebody with an obviously wrong answer available on each.
    await duplicates.mark_unrelated(source_asset_id, copy_id)
    # Its tags and People arrived after it was indexed, so the text the search box matches on is
    # out of date the moment it is written.
    await reindexer.touched(copy_id)

    # Probing, and whatever else the composition root says a new file is worth starting. Named
    # from outside rather than in here: this feature must not learn that thumbnails or face
    # recognition exist, and probing is another feature's job type.
    for job_type in follow_on:
        await context.enqueue_child(job_type, {"asset_id": copy_id})
    # And the ones only THIS file asked for, each with whatever the caller decided they need to
    # know. They carry more than an id because a follow-on cannot read the new file yet: the
    # probing that measures it is a sibling enqueued in the same breath, with no ordering between
    # them.
    for job_type, extra in also:
        await context.enqueue_child(job_type, {"asset_id": copy_id, **extra})

    log.info(
        "produced.indexed",
        asset_id=copy_id,
        source_asset_id=source_asset_id,
        operation=operation,
    )


# --- the edit -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Edit:
    """An edit's payload, once it has been read and checked.

    The steps arrive as the shapes the request was made of rather than as loose numbers, because
    there can be several of them and each one carries its own. What they are checked against was
    settled before the job was queued; what is left here is turning them into one command.
    """

    asset_id: str
    actor_id: str
    steps: tuple[EditStep, ...]
    #: Which way up the source really is, decided when the edit was asked for rather than read
    #: again here. Two reads is two answers to one question and a way for the picture somebody
    #: aimed at to differ from the picture that gets cut.
    orientation: Orientation
    #: Which format a GIF is written in, decided when the edit was asked for rather
    #: than read again here, for the same reason the turn above is carried. The NAME was
    #: built from it, so a second read could produce an AVIF in a file called `.gif`.
    gif_format: str
    #: Whether the produced file should also be marked as a loop, covering the whole of it. An
    #: intent rather than a job name: what it means is named by the composition root.
    as_loop: bool
    filename: str


async def edit(
    context: JobContext,
    *,
    settings: Settings,
    access: Repository,
    writer: LibraryWriteSeam,
    editor: EditService,
    duplicates: DuplicatePairSeam,
    reindexer: ReindexSeam,
    database: Database,
    follow_on: Sequence[str] = (),
    also_if_asked: Sequence[str] = (),
) -> None:
    """One file, one edit: build the new picture or the new cut, put it beside the original, index it.

    Exactly the shape the compression above has, and deliberately so: the same staged write, the
    same claim into a name nothing else holds, the same discard in a `finally`, and the same ending.
    What differs is the one line that builds the command, because that is the only thing an edit
    does differently from a compression.

    The original is never touched, and that is a property of the write seam rather than of care
    taken here.
    """
    request = _read_edit(context)
    viewer = await _actor(access, request.actor_id)
    # WHICH piece of the source this is, taken from what was asked for rather than measured
    # afterwards. Exact for a clip, which is re-encoded from the moment marked rather than copied
    # from the nearest keyframe before it, and not knowable later at all, because by the time
    # anything downstream runs, the produced file is a file with a duration and no memory of where
    # in anything else it came from.
    cut = next((step for step in request.steps if step.operation in ON_MOVING), None)
    cut_start = (cut.start_ms or 0) if cut else 0
    cut_length = (cut.duration_ms or 0) if cut else 0

    source = await resolve_decodable(context.content, request.asset_id, settings=settings)
    staged = await writer.stage_beside(request.asset_id, filename=request.filename, actor=viewer)
    try:
        # The output of an edit is never bigger than the file it came from by any meaningful
        # margin, so the source's own size is the honest estimate and it errs high.
        await _require_room(staged.working.parent, facts=source_facts(source.asset), target=None)
        with timing_hook("edit.encode", asset_id=request.asset_id, steps=len(request.steps)):
            await _run(_edit_args(source, staged, request=request, settings=settings))
        if not await asyncio.to_thread(staged.working.exists):
            # ffmpeg can exit 0 having written nothing. Said properly here rather than as a
            # missing-file traceback three calls further on.
            raise ProductionFailed(
                "ffmpeg read the file and produced nothing from it, which usually means it is "
                "truncated or is not the kind of media it claims to be"
            )
        placed = await writer.keep(staged)
    finally:
        await writer.discard(staged)

    await _index(
        context,
        placed=placed,
        source_asset_id=request.asset_id,
        operation=recorded_operation(request.steps),
        preset=None,
        target_bytes=None,
        actor_id=request.actor_id,
        tag_name=EDITED_TAG,
        act=ACT_EDIT,
        now=editor.now(),
        access=access,
        duplicates=duplicates,
        reindexer=reindexer,
        database=database,
        settings=settings,
        follow_on=follow_on,
        # Only where the request asked for it, and carrying WHICH PIECE OF WHAT this file is,
        # because the job that acts on it cannot find any of that out for itself. How long, since
        # the probing that measures a produced file is a sibling of this follow-on with no ordering
        # between them; and where it was cut from, since a produced file carries no memory of the
        # stretch it was.
        #
        # Three plain facts about a cut, and that is the whole of what leaves this slice. It
        # enqueues a job type it was handed and never learns what the job is or what it will do
        # with them, which is what lets the composition root decide that "this piece has been
        # made into a file" retires whatever stood for that piece.
        also=(
            [
                (
                    job_type,
                    {
                        "duration_ms": cut_length,
                        "cut_from_asset_id": request.asset_id,
                        "cut_from_start_ms": cut_start,
                    },
                )
                for job_type in also_if_asked
            ]
            if request.as_loop
            else []
        ),
    )


def _read_edit(context: JobContext) -> _Edit:
    payload = context.payload
    raw = payload.get("steps")
    if not isinstance(raw, list) or not raw:
        raise ProductionFailed("this job needs to know which edits to make")
    try:
        steps = tuple(EditStep.model_validate(each) for each in raw)
    except ValidationError as broken:
        # A payload that no longer parses is a job written by a version that meant something else
        # by it. Said as a sentence rather than as a traceback out of a validator.
        raise ProductionFailed("this job's edits are not in a shape Sift understands") from broken

    return _Edit(
        asset_id=context.require_str("asset_id", "this job needs an asset_id"),
        actor_id=context.require_str("actor_id", "this job needs the user that asked for it"),
        steps=steps,
        orientation=Orientation(
            quarter_turns=int(payload.get("quarter_turns") or 0),
            mirrored=bool(payload.get("mirrored")),
        ),
        # Defaulted rather than required, because a job queued before the format was carried has
        # no such field and is still a perfectly good edit, and `gif` is what it meant. The
        # field's older name is read too, so a job queued under it runs as it was asked.
        gif_format=str(payload.get("gif_format") or payload.get("animation_format") or "gif"),
        as_loop=bool(payload.get("as_loop")),
        filename=context.require_str("filename", "this job needs a name for the file it makes"),
    )


def _edit_args(source: Source, staged: Staged, *, request: _Edit, settings: Settings) -> list[str]:
    """The one command this edit is. Chosen here, built in the operations module.

    A cut is looked up by the source's own container and a still by its own format, from the tables
    the ingress allowlist is checked against, so the produced file is written by a muxer that was
    named rather than one ffmpeg guessed from a scratch path with no extension on it.

    Several still operations become one chain rather than one command each. The frame is decoded
    once, every filter runs over it in the order it was asked for, and one file is written, which
    is what makes a Save carrying a crop and a turn produce one picture instead of two.
    """
    if any(step.operation in ON_MOVING for step in request.steps):
        moving = operations.moving_format_for(source.asset.mime)
        if moving is None:  # pragma: no cover - the service refuses this before anything is queued
            raise ProductionFailed("Sift cannot cut a file in that container")
        cut = next(step for step in request.steps if step.operation in ON_MOVING)
        # A GIF is not a cut with a different container on it. A cut writes the streams it
        # was given (copied or re-encoded, but the same picture and sound in the same shape)
        # and this rebuilds the picture at another rate, another size and a palette of its own, and
        # drops the sound because the format cannot hold any. Its own builder, before the branch
        # below, because that branch is about how a CUT is written.
        if cut.operation is Operation.GIF:
            # Which way round the picture is, so the size caps the SHORT edge. Read from what the
            # probe recorded rather than assumed: capping the width instead would give a landscape
            # clip a third of the pixels of a portrait one at the same setting.
            width, height = source.asset.width or 0, source.asset.height or 0
            return operations.gif_args(
                source.path,
                staged.working,
                start_ms=cut.start_ms or 0,
                duration_ms=cut.duration_ms or 0,
                fmt=operations.GIF_FORMATS[request.gif_format],
                landscape=width >= height,
                settings=settings,
            )
        # A clip is re-encoded so it begins exactly where it was marked; a trim is copied. The
        # choice is the operation itself rather than a setting, because the two verbs already mean
        # different things about length: a clip is a named piece (the editor offers up to sixty
        # seconds of one, a loop is usually a few), while a trim tops and tails a whole video and
        # would spend minutes re-encoding to fix an inaccuracy nobody notices at that scale.
        return operations.cut_args(
            source.path,
            staged.working,
            start_ms=cut.start_ms or 0,
            duration_ms=cut.duration_ms or 0,
            fmt=moving,
            settings=settings,
            exact=cut.operation is Operation.CLIP,
        )

    still = operations.still_format_for(source.asset.mime)
    if still is None:  # pragma: no cover - the service refuses this before anything is queued
        raise ProductionFailed("Sift cannot save a picture in that format")
    return operations.still_args(
        source.path,
        staged.working,
        # The camera's note first, so everything after it works on the picture the person was
        # looking at rather than on the way the bytes happen to be stored.
        filters=[*request.orientation.filters(), *(_filter_for(step) for step in request.steps)],
        fmt=still,
        settings=settings,
    )


def _filter_for(step: EditStep) -> str:
    """One still step, as the one filter it is."""
    if step.operation is Operation.CROP:
        return operations.crop_filter(
            left=step.left or 0,
            top=step.top or 0,
            width=step.width or 0,
            height=step.height or 0,
        )
    if step.operation is Operation.RESIZE:
        return operations.resize_filter(width=step.width or 0)
    if step.turn is None:  # pragma: no cover - a rotation without a direction is refused
        raise ProductionFailed("this job needs to know which way to turn the picture")
    return operations.turn_filter(step.turn)


# --- the sample -----------------------------------------------------------------------------


async def compress_sample(
    context: JobContext,
    *,
    settings: Settings,
    access: Repository,
) -> None:
    """A few seconds, encoded the way the whole file would be, written to Sift's own cache.

    It is thrown away, so it never goes near a library folder, and it is a job like any other, at
    background priority, so asking for one does not take the machine away from anything.

    Taken from a quarter of the way in rather than the beginning. The first seconds of a video are
    very often a title card or a fade, which compresses beautifully and says nothing at all about
    what the rest will look like.
    """
    asset_id = context.require_str("asset_id", "this job needs an asset_id")
    actor_id = context.require_str("actor_id", "this job needs the user that asked for it")
    await _actor(access, actor_id)

    payload = context.payload
    target = payload.get("target_bytes")
    target_bytes = int(target) if isinstance(target, int) else None

    source = await resolve_decodable(context.content, asset_id, settings=settings)
    facts = source_facts(source.asset)
    rungs = plan.allowed_rungs(facts)
    index = plan.best_rung_under(target_bytes, facts) if target_bytes is not None else 0
    rung = rungs[index if index is not None else len(rungs) - 1] if rungs else tuning.RUNGS[0]

    directory = samples_directory(settings)
    await asyncio.to_thread(partial(directory.mkdir, parents=True, exist_ok=True))
    destination = directory / f"{context.job.id}.{tuning.COMPATIBLE_CONTAINER}"

    at_ms = int((facts.duration_ms or 0) * tuning.SAMPLE_AT_FRACTION)
    with timing_hook("compress.sample", asset_id=asset_id):
        await _run(
            encode.sample_args(source.path, destination, rung=rung, at_ms=at_ms, settings=settings)
        )
    if not await asyncio.to_thread(destination.exists):
        raise ProductionFailed("ffmpeg produced no sample from that part of the file")


def samples_directory(settings: Settings) -> Path:
    """Where samples are kept: Sift's own cache, in a folder of their own so a sweep can find them."""
    return settings.cache_dir / SAMPLES_DIRECTORY


# --- registration ---------------------------------------------------------------------------


def register_handlers(
    *,
    settings: Settings,
    access: Repository,
    writer: LibraryWriteSeam,
    service: CompressService,
    editor: EditService,
    duplicates: DuplicatePairSeam,
    reindexer: ReindexSeam,
    database: Database,
    follow_on: Sequence[str] = (),
    also_if_asked: Sequence[str] = (),
) -> None:
    """Claim the job types this feature owns. Called once, at boot.

    Everything a handler needs is bound in here rather than reached for. A handler is given its
    context by the kernel and nothing else, so the alternative is a module-level global holding
    half the application, which is exactly what makes a feature untestable without one.
    """
    register_handler(
        COMPRESS,
        partial(
            compress,
            settings=settings,
            access=access,
            writer=writer,
            service=service,
            duplicates=duplicates,
            reindexer=reindexer,
            database=database,
            follow_on=follow_on,
        ),
        name="Compressing",
    )
    register_handler(
        COMPRESS_SAMPLE,
        partial(compress_sample, settings=settings, access=access),
        name="Testing compression size",
    )
    register_handler(
        EDIT,
        partial(
            edit,
            settings=settings,
            access=access,
            writer=writer,
            editor=editor,
            duplicates=duplicates,
            reindexer=reindexer,
            database=database,
            follow_on=follow_on,
            # What an edit is worth starting ONLY where the request asked for it. Named from
            # outside for the same reason `follow_on` is: this feature must not learn what a loop
            # is, and a request must not be able to choose what the server runs.
            also_if_asked=also_if_asked,
        ),
        name="Saving edit",
    )


# No job limit is declared here, and that is deliberate. Compress takes its share of the machine
# through the division every long pass takes it through. See where the application is assembled.
# A cap here as well would mean a compression could never use a share nobody else was using, which
# is the one thing the division exists to allow.
