# SPDX-License-Identifier: AGPL-3.0-or-later
"""The read of a file: what it is, written on its row, and the work it hands out."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from sift.kernel import heif, lanes, media, mp4
from sift.kernel.config import Settings
from sift.kernel.content import (
    Asset,
    ContentStore,
    ProbeKeep,
)
from sift.kernel.hardware import HardwareReport
from sift.kernel.ingress import (
    IngressRejected,
    IngressResult,
    Kind,
    Origin,
    verify_decodable,
    verify_ingress,
    verify_probed,
)
from sift.kernel.jobs import (
    BACKGROUND_PRIORITY,
    STOP_TO_CANCEL,
    JobCanceled,
    JobContext,
    JobQueue,
    JobSwitchedOff,
    in_claim_order,
)
from sift.kernel.log import get_logger, timing_hook
from sift.kernel.media import (
    MissingAsset,
    NoReadableCopy,
    Source,
    resolve_decodable,
)
from sift.slices.media_jobs import ffmpeg, tuning
from sift.slices.media_jobs.fingerprints import _has_its_fingerprints
from sift.slices.media_jobs.job_types import (
    _DERIVATIVE_JOBS,
    _GIF,
    _IMAGE,
    _PICTURE_KINDS,
    FINGERPRINT_FILE,
    FINGERPRINT_FOR_STASH_BOXES,
    KEEP_PROBES,
    PROBE,
    RECLASSIFY,
    REMUX,
    THUMBNAIL,
    FollowOnJobs,
    FollowOnPayloads,
    SettlingJobs,
    ShouldGenerate,
)
from sift.slices.media_jobs.shared import Unusable, _asset_id, _made_for

log = get_logger(__name__)


async def probe(
    context: JobContext,
    *,
    settings: Settings,
    hardware: HardwareReport,
    should_generate: ShouldGenerate | None = None,
    follow_on: FollowOnJobs = (),
    follow_on_payloads: FollowOnPayloads | None = None,
    settles_into: SettlingJobs = (),
) -> None:
    """Work out what a file actually is, then start the jobs that draw it.

    The only stage that decides whether a file is real: a truncated download has a perfect header
    and is caught here, by the ffprobe that reads it, before a thumbnail and a preview meet it.
    """
    async with lanes.the_read():
        await _probe(
            context,
            settings=settings,
            hardware=hardware,
            should_generate=should_generate,
            follow_on=follow_on,
            follow_on_payloads=follow_on_payloads,
            settles_into=settles_into,
        )


async def _probe(
    context: JobContext,
    *,
    settings: Settings,
    hardware: HardwareReport,
    should_generate: ShouldGenerate | None,
    follow_on: FollowOnJobs,
    follow_on_payloads: FollowOnPayloads | None,
    settles_into: SettlingJobs,
) -> None:
    asset_id = _asset_id(context)
    store = context.content
    source = await resolve_decodable(store, asset_id, settings=settings)

    with timing_hook("probe.verify", asset_id=asset_id):
        checked, answer = await _verified(source, settings=settings)

    if await _sent_back_to_the_classifier(context, asset_id, source, checked):
        return

    probed, keep = await _read_and_keep(source, asset_id, settings=settings, answer=answer)

    # Read off the pool's heartbeat, which a stop wakes immediately: no write of its own.
    if context.stopping() == STOP_TO_CANCEL:
        raise JobCanceled(f"job {context.job.id} was stopped while its file was read")

    #: A pass asked for the READING alone. See the return further down for what else it turns off.
    scan_only = bool(context.payload.get("scan_only"))

    # THE READ DOES NOT HASH: it hands the fingerprints out as a job of their own
    # (`FINGERPRINT_FILE`), after the file's pictures, and a scan asked for alone leaves them to
    # the catch-up pass (`fingerprint_stash_box`). Neither is asked of a file that has them.
    already_hashed = _has_its_fingerprints(source.asset)
    gap = await _interleave_gap(source, asset_id)
    updated = await _record_reading(
        store, asset_id, source=source, checked=checked, probed=probed, gap=gap, keep=keep
    )

    # THE THUMBNAIL GOES OUT FIRST, ahead of everything else this read hands out: it needs only
    # what was just written. It asks no switch: it is how a file is drawn at all, so every arriving
    # file gets one, a scan asked for the reading alone included.
    children: list[tuple[str, dict[str, Any]]] = [(THUMBNAIL, {"asset_id": asset_id})]

    # A scan asked for on its own hands out nothing more: probing is where every other stage is
    # started from, and each of those has a catch-up that a Build runs whenever it is asked for.
    if scan_only:
        await _enqueue_children(context, children)
        await _ask_for_the_skipped_fingerprints(
            context, asset_id, already_hashed=already_hashed, should_generate=should_generate
        )
        return

    children += await _hand_out(
        asset_id,
        updated,
        already_hashed=already_hashed,
        should_generate=should_generate,
        follow_on=follow_on,
        follow_on_payloads=follow_on_payloads,
    )
    await _enqueue_children(context, children)
    await _settle(context, settles_into=settles_into, should_generate=should_generate)


async def _enqueue_children(
    context: JobContext, children: list[tuple[str, dict[str, Any]]]
) -> None:
    """The file's work in one write, at this read's own urgency: work a person started hands its
    children the person's urgency, and work the machine started the machine's. None of it is the
    read's own family, so none is work handed on."""
    await context.queue.enqueue_children(context.job.id, children, priority=context.job.priority)


async def _sent_back_to_the_classifier(
    context: JobContext, asset_id: str, source: Source, checked: IngressResult
) -> bool:
    """Whether the row went back to the classifier because its own bytes refute its kind.

    A row is not read as a kind its bytes refute: its pictures and its mime would be made for what
    it is not, and a mime of one kind on a row of another goes unnoticed. The kind is the
    classifier's to write, so the row goes back below its line and the reclassify pass types it
    and asks for this read again.
    """
    if str(checked.media.kind) != source.asset.media_type:
        log.warning(
            "probe.kind_refuted",
            asset_id=asset_id,
            stored=source.asset.media_type,
            read=str(checked.media.kind),
            mime=checked.media.mime,
        )
        await context.content.send_to_classifier(asset_id)
        await context.queue.enqueue_when_settled(RECLASSIFY, priority=BACKGROUND_PRIORITY)
        return True
    return False


async def _read_and_keep(
    source: Source, asset_id: str, *, settings: Settings, answer: dict[str, Any] | None = None
) -> tuple[ffmpeg.Probed, ProbeKeep]:
    """The fields read out of the file, and the tool's whole answer as it is kept on the row.

    The whole answer is kept, not only the fields read out of it, so a field somebody needs later
    is not another pass over the library. Places are stripped before it is stored; see
    `ffmpeg.probe_body`. `answer` is the one the gate's check already asked for (a still).
    """
    with timing_hook("probe.metadata", asset_id=asset_id):
        payload, probed = await _reading_of(source, settings=settings, answer=answer)
        keep = ProbeKeep(
            body=ffmpeg.probe_body(payload), tool=await ffmpeg.probe_tool(settings=settings)
        )
    if _described(source) == source.path:
        # The frame readers that come next read this file: they need not ask the tool again.
        await media.remember_reading(source.path, payload)
    return probed, keep


async def _interleave_gap(source: Source, asset_id: str) -> int | None:
    """How far this file stores its audio from the video for the same moment, or None for a still.

    A large distance makes a browser thrash after a seek, and the file needs a re-muxed copy. It
    reads the file's index rather than its media: tens of milliseconds against the decode.
    """
    if source.asset.media_type in (_IMAGE, _GIF):
        return None
    with timing_hook("probe.interleave", asset_id=asset_id):
        async with lanes.reading(source.path):
            return await asyncio.to_thread(mp4.worst_gap, source.path)


async def _record_reading(
    store: ContentStore,
    asset_id: str,
    *,
    source: Source,
    checked: IngressResult,
    probed: ffmpeg.Probed,
    gap: int | None,
    keep: ProbeKeep,
) -> Asset:
    """Write what the read found on the file's row, and the row as it now stands."""
    # ffprobe read the file; the gate read its first four kilobytes. Where they disagree about the
    # type, the one that decoded it is worth listening to, but only within what the gate already
    # allowed, which is why this corrects the mime and can never change what the file may be.
    mime = checked.media.mime if checked.media.mime != source.asset.mime else None

    updated = await store.record_probe(
        asset_id,
        # WITHOUT the fingerprints, which is not the same as writing None for them: `record_probe`
        # sets every column it names, so None would BLANK the fingerprints of a file read again.
        keep_fingerprints=True,
        width=probed.width,
        height=probed.height,
        duration_ms=_duration_of(source, probed),
        fps=probed.fps,
        # From the gate, not from ffprobe: the gate worked out what the container is by reading
        # its structure, while ffprobe only lists what could open it.
        container=checked.media.name,
        vcodec=probed.vcodec,
        acodec=probed.acodec,
        bit_depth=probed.bit_depth,
        color_transfer=probed.color_transfer or "",
        mime=mime,
        interleave_gap=gap,
        audio_channels=probed.audio_channels,
        audio_sample_rate=probed.audio_sample_rate,
        video_duration_ms=_picture_length_of(source.asset.media_type, probed),
        keep=keep,
    )
    if updated is None:
        raise MissingAsset(f"asset {asset_id} was removed while it was being probed")
    return updated


async def _ask_for_the_skipped_fingerprints(
    context: JobContext,
    asset_id: str,
    *,
    already_hashed: bool,
    should_generate: ShouldGenerate | None,
) -> None:
    """Ask for the fingerprint pass a scan-only read did not do itself, once the batch settles.

    The pass is about the library rather than this file, so every file of a batch collapses onto
    one request; the chain otherwise only asks for itself between batches. Gated on the same switch
    an ordinary read's fingerprints are, asked of this file, and only for a file that lacks them.
    """
    if already_hashed:
        log.info("probe.fingerprints_already_kept", asset_id=asset_id)
    elif should_generate is None or await should_generate(FINGERPRINT_FOR_STASH_BOXES, asset_id):
        try:
            await context.queue.enqueue_when_settled(
                FINGERPRINT_FOR_STASH_BOXES, priority=BACKGROUND_PRIORITY
            )
        except JobSwitchedOff:
            log.info(
                "media.settling_skipped",
                job_type=FINGERPRINT_FOR_STASH_BOXES,
                reason="switched off",
            )


async def _hand_out(
    asset_id: str,
    updated: Asset,
    *,
    already_hashed: bool,
    should_generate: ShouldGenerate | None,
    follow_on: FollowOnJobs,
    follow_on_payloads: FollowOnPayloads | None,
) -> list[tuple[str, dict[str, Any]]]:
    """The rest of a file's work, in claim order, each where its switch wants it."""
    # A file whose audio sits too far from its video gets a repaired copy: asked for here rather
    # than listed with the pictures, because it is a whole extra copy built for almost no file.
    needs_repair = (
        updated.interleave_gap is not None
        and updated.interleave_gap >= tuning.MAX_INTERLEAVE_GAP_BYTES
    )
    wanted = [REMUX] if needs_repair else []
    # Everything but the thumbnail, which went out as soon as the file was read; and the
    # fingerprints, asked of their switch in the loop like every picture.
    wanted += [job for job in (*_DERIVATIVE_JOBS, *follow_on) if job != THUMBNAIL]
    wanted.append(FINGERPRINT_FILE)

    children: list[tuple[str, dict[str, Any]]] = []
    # In the order each type declared where its handler is registered, not this list's: the list
    # joins this slice's products and other features', and neither half can order the whole.
    for job_type in in_claim_order(wanted):
        kind = _PICTURE_KINDS.get(job_type)
        if kind is not None and not _made_for(kind, updated):
            # A still has no hover clip and nothing to scrub through.
            continue
        if should_generate is not None and not await should_generate(job_type, asset_id):
            # Turned off in the performance settings, read per file; a rescan fills it in later.
            continue
        if job_type is REMUX:
            log.info("probe.needs_remux", asset_id=asset_id, interleave_gap=updated.interleave_gap)
        if job_type == FINGERPRINT_FILE:
            # Only for a file that lacks them: a second read of a file is ordinary.
            if already_hashed:
                log.info("probe.fingerprints_already_kept", asset_id=asset_id)
                continue
            children.append((FINGERPRINT_FILE, {"asset_id": asset_id}))
            continue
        children.append(
            (job_type, {**(follow_on_payloads or {}).get(job_type, {}), "asset_id": asset_id})
        )
    return children


async def _settle(
    context: JobContext, *, settles_into: SettlingJobs, should_generate: ShouldGenerate | None
) -> None:
    """Ask for the whole-library work this file has made worth doing again, once the batch stops
    arriving. What reads the fingerprints is asked for by the job that writes them instead."""
    for job_type in settles_into:
        if should_generate is not None and not await should_generate(job_type, None):
            continue
        try:
            await context.queue.enqueue_when_settled(job_type, priority=BACKGROUND_PRIORITY)
        # Switched off at the queue's own board: the passes over the whole library belong to no
        # folder, so their switches are there rather than at the import gate.
        except JobSwitchedOff:
            log.info("media.settling_skipped", job_type=job_type, reason="switched off")


def _whole_heif(source: Source) -> bool:
    return heif.is_heif_still(source.asset) and source.path != source.original


def _described(source: Source) -> Path:
    """The file the reading describes: the copy a decoder reads, but a HEIF photograph's own."""
    return source.original if _whole_heif(source) else source.path


def _reading_args(source: Source, *, settings: Settings) -> list[str]:
    return ffmpeg.probe_args(
        _described(source), settings=settings, still=source.asset.media_type == _IMAGE
    )


async def _reading_of(
    source: Source, *, settings: Settings, answer: dict[str, Any] | None = None
) -> tuple[dict[str, Any], ffmpeg.Probed]:
    """What ffprobe says the file is: its whole answer, and the fields read out of it.

    A HEIF photograph is described from its OWN bytes and measured from its decoded copy, the grid
    assembled and turned upright, since ffprobe's size for it is one tile (see `kernel.heif`).
    """
    whole = _whole_heif(source)
    described = _described(source)
    payload = (
        answer
        if answer is not None
        else await ffmpeg.run_json(_reading_args(source, settings=settings), reads=described)
    )
    probed = ffmpeg.parse_probe(payload)
    if whole:
        copy = ffmpeg.parse_probe(
            await ffmpeg.run_json(
                ffmpeg.probe_args(source.path, settings=settings), reads=source.path
            )
        )
        probed = replace(probed, width=copy.width, height=copy.height)
    return payload, probed


def _duration_of(source: Source, probed: ffmpeg.Probed) -> int | None:
    """How long the file runs. Always nothing for a still, whatever ffprobe said.

    Through the `image2` demuxer a photograph is a one-frame video at 25 fps, 0.04 seconds: true of
    a decoder, false of a photograph, and ordinary-looking enough to survive every check after it.
    A GIF keeps its duration: it really does run.
    """
    return None if source.asset.media_type == _IMAGE else probed.duration_ms


def _picture_length_of(media_type: str, probed: ffmpeg.Probed) -> int | None:
    """How long the picture runs, as the row stores it. See `Asset.video_duration_ms`.

    Nothing for a still, for the reason `_duration_of` gives. ZERO for a moving file whose reading
    names none, so a file that has been read is never mistaken for one that has not.
    """
    if media_type == _IMAGE:
        return None
    return probed.video_duration_ms or 0


async def _verified(
    source: Source, *, settings: Settings
) -> tuple[IngressResult, dict[str, Any] | None]:
    """Put the file back through the gate, and refuse to go on if it does not pass.

    Re-verified rather than trusted: a file in a library root can be replaced under a path that is
    still indexed. `Origin.SCAN`, because that is where the file is now, and a scanned file is
    never moved when it is refused: it is the person's file, indexed where it lies.

    A still or a video read where it lies is checked through the reading's own answer, returned
    with the result: one ffprobe rather than two.
    """
    answer: dict[str, Any] | None = None
    try:
        # On a thread, like every caller of `verify_ingress`: against a network share it would stall
        # the loop, and with it the API, the job feed and every video being watched.
        async with lanes.reading(source.original):
            checked = await asyncio.to_thread(
                verify_ingress, source.original, origin=Origin.SCAN, settings=settings
            )
            if _read_with_the_check(source, checked):
                answer = await verify_probed(
                    checked, _reading_args(source, settings=settings), settings=settings
                )
            else:
                await verify_decodable(checked, settings=settings)
    except IngressRejected as exc:
        raise Unusable(
            f"this file could not be read as {source.asset.media_type} ({exc.reason})",
            reason=exc.reason,
        ) from exc
    return checked, answer


def _read_with_the_check(source: Source, checked: IngressResult) -> bool:
    """Whether the gate's decode check is asked of the reading itself: a still or a video the gate
    takes as one, whose reading is of the file the gate read."""
    return (
        source.asset.media_type in (_IMAGE, Kind.VIDEO.value)
        and str(checked.media.kind) == source.asset.media_type
        and checked.media.name != "webp-animated"
        and _described(source) == source.original
    )


async def keep_probes(
    context: JobContext,
    *,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """Read every file whose kept reading is missing or older than `PROBE_VERSION`, and keep it.

    Raising that version is how a change to what a reading keeps reaches the files already read.
    It runs the tool and nothing else (no decode but a still's first frame, no seeking, no
    children), at background priority. A file on a drive that is not plugged in is left for when
    the drive is back; a file the tool refuses gets an empty answer, or it would head every run.
    """
    # Here rather than at the top: nothing at start needs it.
    from sift.kernel import jpeg_turn

    store = context.content
    ids = await store.assets_lacking_probe_rows(tuning.STAMP_BATCH)
    if not ids:
        return

    log.info("probe.keep_start", count=len(ids))
    kept = 0
    for index, asset_id in enumerate(ids):
        await context.raise_if_canceled()
        await context.set_progress(index / len(ids))
        try:
            source = await resolve_decodable(store, asset_id, settings=settings)
        except (MissingAsset, NoReadableCopy):
            continue

        # A JPEG photograph read before the browser's turn was asked: read again only where the
        # browser and ffmpeg turn it differently, which is when it is read through a copy.
        photograph = jpeg_turn.needs_a_look(source.asset)
        turned = photograph and source.path != source.original
        if photograph and not turned and await store.probe_still_current(asset_id):
            kept += 1
            continue

        tool = await ffmpeg.probe_tool(settings=settings)
        try:
            # Asked exactly as the read asks, so the kept answer and the size are the ones a file
            # read today has: a still's turn is on its first frame (see `ffmpeg.probe_args`), and a
            # HEIF photograph's size is its whole picture's.
            payload, probed = await _reading_of(source, settings=settings)
        except (ffmpeg.FFmpegError, OSError) as exc:
            log.warning("probe.keep_unreadable", asset_id=asset_id, reason=str(exc))
            await store.keep_probe(asset_id, ProbeKeep(body=ffmpeg.probe_body({}), tool=tool))
            kept += 1
            continue

        await store.keep_probe(
            asset_id,
            ProbeKeep(body=ffmpeg.probe_body(payload), tool=tool),
            audio_channels=probed.audio_channels,
            audio_sample_rate=probed.audio_sample_rate,
            video_duration_ms=_picture_length_of(source.asset.media_type, probed),
            width=probed.width,
            height=probed.height,
        )
        kept += 1
        if turned:
            # Its tile was drawn the other way up: drawn again, through the copy.
            await context.queue.enqueue(THUMBNAIL, {"asset_id": asset_id}, dedupe=True)
            log.info("probe.turned_as_the_browser_draws", asset_id=asset_id)

    await context.set_progress(1.0)
    log.info("probe.keep_done", looked_at=len(ids), kept=kept)

    # More than one batch, AND this batch got somewhere: a batch that kept nothing has learnt only
    # that a drive is away, and asking for the same page again would loop for ever.
    if len(ids) == tuning.STAMP_BATCH and kept > 0:
        await context.queue.enqueue_when_settled(KEEP_PROBES, delay=tuning.STAMP_SETTLE_SECONDS)


async def read_unread(context: JobContext, *, settings: Settings, hardware: HardwareReport) -> None:
    """Hand out a probe for every file that was taken in and never read.

    A stopped scan hands out no probes for what it had taken in, and the next scan skips those
    files as unchanged, so each would sit on the wall as a tile that never finishes importing.
    Asked for at start while any such file exists; a page at a time, handing out the scan's own
    floor (the read and the picture) for each file with no probe already on its way.
    """
    del settings, hardware  # every handler in the table takes them; this one has no use for them
    store = context.content
    offset = 0
    handed = 0
    already = 0
    while True:
        await context.raise_if_canceled()
        ids = await store.unread_page(offset=offset, limit=tuning.UNREAD_BATCH)
        if not ids:
            break
        for asset_id in ids:
            if await probe_is_coming(context.queue, asset_id):
                already += 1
                continue
            await context.enqueue_child(
                PROBE, {"asset_id": asset_id, "scan_only": True}, dedupe=True
            )
            handed += 1
        offset += len(ids)
    await context.set_progress(1.0)
    log.info("probe.unread_handed_out", handed=handed, already_coming=already)


async def probe_is_coming(queue: JobQueue, asset_id: str) -> bool:
    """Whether a probe for this file is waiting or under way, in either shape a scan queues one.

    Both shapes, because `is_live` matches the payload exactly and a scan-only probe carries a
    field an ordinary one does not.
    """
    for shape in ({"asset_id": asset_id}, {"asset_id": asset_id, "scan_only": True}):
        if await queue.is_live(PROBE, shape):
            return True
    return False
