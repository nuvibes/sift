# SPDX-License-Identifier: AGPL-3.0-or-later
"""Producing a compressed or edited copy, and a few seconds of one to look at first.
The write seam places every file; every call that waits runs on a thread."""

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

#: Sift's own cache, never a library folder: a sample is thrown away.
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
    """One file: encode it down to the target, put it beside the original, and index it."""
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
        # A no-op once placed; otherwise no scratch file is left in somebody's folder.
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
    """The user this job acts for, with the vault open: a job has no session, and concealment is
    their own flag."""
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


def _converts_audio(request: _Request, facts: plan.SourceFacts) -> bool:
    """Whether this run rebuilds the sound: only for compatibility, and only when the container
    needs it."""
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
    """Write the finished bytes to the scratch path: rewrap when enough, else climb the ladder."""
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
        # Compatibility with no size: one encode at the best rung.
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
    """Encode, weigh, decide, repeat, and end holding the best rung that fitted; re-encoded if
    overwritten."""
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
        # Nothing fitted and the caller said go ahead: keep the smallest, with a note.
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
        # ffmpeg can exit 0 and write nothing.
        raise ProductionFailed(
            "ffmpeg read the file and produced nothing from it, which usually means it is "
            "truncated or is not the kind of media it claims to be"
        )


async def _require_room(directory: Path, *, facts: plan.SourceFacts, target: int | None) -> None:
    """Refuse before starting if the disk has no room; the estimate errs high."""
    expected = target or facts.size_bytes or 0
    free = (await asyncio.to_thread(shutil.disk_usage, directory)).free
    if free < expected + tuning.FREE_SPACE_HEADROOM_BYTES:
        raise ProductionFailed(
            "there is not enough free space on that disk to write the compressed copy"
        )


async def _run(argv: list[str]) -> None:
    """Run ffmpeg at background priority, with this feature's own time limit."""
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
    """Take the finished file in through the gate, then make it a copy of its source."""
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
    await duplicates.mark_unrelated(source_asset_id, copy_id)  # a copy matches its original
    await reindexer.touched(copy_id)  # its tags were added after indexing
    for job_type in follow_on:  # named by the composition root
        await context.enqueue_child(job_type, {"asset_id": copy_id})
    for job_type, extra in also:
        await context.enqueue_child(job_type, {"asset_id": copy_id, **extra})

    log.info(
        "produced.indexed",
        asset_id=copy_id,
        source_asset_id=source_asset_id,
        operation=operation,
    )


@dataclass(frozen=True, slots=True)
class _Edit:
    """An edit's payload, once it has been read and checked."""

    asset_id: str
    actor_id: str
    steps: tuple[EditStep, ...]
    #: Decided when the edit was asked for, so the cut matches what was aimed at.
    orientation: Orientation
    #: Decided when asked for: the name already carries its extension.
    gif_format: str
    #: An intent, never a job name.
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
    """One file, one edit: build the new picture or cut, put it beside the original, index it."""
    request = _read_edit(context)
    viewer = await _actor(access, request.actor_id)
    # Which piece of the source this is, known only now.
    cut = next((step for step in request.steps if step.operation in ON_MOVING), None)
    cut_start = (cut.start_ms or 0) if cut else 0
    cut_length = (cut.duration_ms or 0) if cut else 0

    source = await resolve_decodable(context.content, request.asset_id, settings=settings)
    staged = await writer.stage_beside(request.asset_id, filename=request.filename, actor=viewer)
    try:
        # An edit's output is never meaningfully bigger than its source.
        await _require_room(staged.working.parent, facts=source_facts(source.asset), target=None)
        with timing_hook("edit.encode", asset_id=request.asset_id, steps=len(request.steps)):
            await _run(_edit_args(source, staged, request=request, settings=settings))
        if not await asyncio.to_thread(staged.working.exists):
            # ffmpeg can exit 0 having written nothing.
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
        # Only when asked, with the three facts about the cut a follow-on cannot find for itself.
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
        # A payload from a version that meant something else by it.
        raise ProductionFailed("this job's edits are not in a shape Sift understands") from broken

    return _Edit(
        asset_id=context.require_str("asset_id", "this job needs an asset_id"),
        actor_id=context.require_str("actor_id", "this job needs the user that asked for it"),
        steps=steps,
        orientation=Orientation(
            quarter_turns=int(payload.get("quarter_turns") or 0),
            mirrored=bool(payload.get("mirrored")),
        ),
        # Older jobs lack the field or used its older name; `gif` is what they meant.
        gif_format=str(payload.get("gif_format") or payload.get("animation_format") or "gif"),
        as_loop=bool(payload.get("as_loop")),
        filename=context.require_str("filename", "this job needs a name for the file it makes"),
    )


def _edit_args(source: Source, staged: Staged, *, request: _Edit, settings: Settings) -> list[str]:
    """The one command this edit is: a cut by its container, stills as one chain over one decoded
    frame."""
    if any(step.operation in ON_MOVING for step in request.steps):
        moving = operations.moving_format_for(source.asset.mime)
        if moving is None:  # pragma: no cover - the service refuses this before anything is queued
            raise ProductionFailed("Sift cannot cut a file in that container")
        cut = next(step for step in request.steps if step.operation in ON_MOVING)
        # A GIF rebuilds the picture and drops the sound, so it is not a cut.
        if cut.operation is Operation.GIF:
            # Cap the short edge, from the probed shape.
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
        # A clip is re-encoded to start exactly where marked; a trim is copied.
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
        # The camera's note first, so the chain works on the picture as seen.
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


async def compress_sample(
    context: JobContext,
    *,
    settings: Settings,
    access: Repository,
) -> None:
    """A few seconds encoded the way the whole file would be, from a quarter of the way in, into the
    cache."""
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
    """Claim the job types this feature owns, binding what each handler needs. Called once, at boot."""
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
            # Named from outside: this slice never learns what a Loop is.
            also_if_asked=also_if_asked,
        ),
        name="Saving edit",
    )


# No job limit here: compress takes its share through the division every long pass uses.
