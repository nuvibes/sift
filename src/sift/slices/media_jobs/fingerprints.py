# SPDX-License-Identifier: AGPL-3.0-or-later
"""The near-duplicate fingerprints: an arriving file's own, and the pass that fills in the rest."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from sift.kernel import media
from sift.kernel import sampling as sampler
from sift.kernel.config import Settings
from sift.kernel.content import (
    Asset,
    ContentStore,
    VerdictProduct,
    hashing,
    perceptual,
)
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import (
    JobContext,
)
from sift.kernel.log import get_logger, timing_hook
from sift.kernel.media import (
    MissingAsset,
    NoReadableCopy,
    Source,
    moments_to_files,
    raw_moments,
    resolve_decodable,
)
from sift.kernel.subprocess import Priority
from sift.slices.media_jobs import ffmpeg, tuning
from sift.slices.media_jobs.job_types import _GIF, _IMAGE, FINGERPRINT_FOR_STASH_BOXES, SettlingJobs
from sift.slices.media_jobs.shared import Unusable, _asset_id, broken_bytes

log = get_logger(__name__)


def fingerprint_requests(probed: ffmpeg.Probed) -> list[media.FrameRequest]:
    """What a video's fingerprints read: the thirty hash frames `_fingerprint` asks for and the
    twenty-five stills `_video_phash` asks for, built from the same pieces and the same reading
    of the file, so a read made for them serves every ask."""
    size = perceptual.HASH_FRAME_SIZE
    span = sampler.picture_span(probed.duration_ms, probed.video_duration_ms)
    requests: list[media.FrameRequest] = [
        media.RawFrames(
            moments=tuple(ffmpeg.hash_frame_moment(at) for at in sampler.hash_frames(span)),
            filters=ffmpeg.hash_frame_filter(size),
            pixel_format=ffmpeg.HASH_PIXEL_FORMAT,
            frame_bytes=size * size,
        )
    ]
    if probed.duration_seconds:
        requests.append(
            media.FrameFiles(
                moments=tuple(
                    ffmpeg.stash_box_moment(at)
                    for at in perceptual.scene_frame_times(probed.duration_seconds)
                ),
                filters=ffmpeg.stash_box_filter(perceptual.SCENE_SHOT_WIDTH),
                suffix=".bmp",
                output=ffmpeg.STASH_BOX_OUTPUT,
            )
        )
    return requests


def still_request() -> media.RawFrames:
    """The frame `_fingerprint` reads of a still, as a request a reader that decodes the still for
    something else can fill (`thumbnails.thumbnail`)."""
    size = perceptual.HASH_FRAME_SIZE
    return media.RawFrames(
        moments=(ffmpeg.hash_frame_moment(0),),
        filters=ffmpeg.hash_frame_filter(size),
        pixel_format=ffmpeg.HASH_PIXEL_FORMAT,
        frame_bytes=size * size,
    )


async def probed_of(
    store: ContentStore, source: Source, *, settings: Settings, ask: bool = True
) -> ffmpeg.Probed:
    """What the probe read of this file, from the answer it kept; the tool is asked again only
    where none was kept or it holds no picture, and only if `ask`. A still's fingerprint needs
    none of it.

    The kept answer is also handed to the frame readers, so they do not ask the tool either.
    """
    if source.asset.media_type == _IMAGE:
        return ffmpeg.parse_probe({})
    body = await store.kept_probe(source.asset.id)
    payload: dict[str, object] = {}
    if body is not None:
        try:
            payload = ffmpeg.read_kept_probe(body)
        except ValueError as error:
            log.info(
                "fingerprint.kept_probe_unreadable", asset_id=source.asset.id, detail=str(error)
            )
    probed = ffmpeg.parse_probe(payload)
    if probed.vcodec is None:
        if not ask:
            return probed
        payload = await ffmpeg.run_json(
            ffmpeg.probe_args(source.path, settings=settings), reads=source.path
        )
        probed = ffmpeg.parse_probe(payload)
    await media.remember_reading(source.path, payload)
    return probed


@asynccontextmanager
async def _read_once(
    source: Source, probed: ffmpeg.Probed, *, settings: Settings
) -> AsyncIterator[None]:
    """Decode a video once for its fingerprints, where that is cheaper than seeking each moment.

    A seek decodes from the keyframe before its moment, so fifty-five seeks into a short clip
    decode most of it fifty-five times; `media.choose_read_shape` weighs that against one decode.
    What the task already prepared is not read again. Inside, the fingerprints ask exactly as
    they would without it and are served the same frames; a decode the tool refuses leaves them
    to seek.
    """
    if source.asset.media_type in (_IMAGE, _GIF):
        yield
        return
    already = media.prepared_now()
    try:
        planned = fingerprint_requests(probed)
    except ValueError as error:
        # A moment the plan cannot place is one the asks cannot place either; they say so.
        log.info("fingerprint.read_unplanned", asset_id=source.asset.id, detail=str(error))
        planned = []
    wanted = [one for one in planned if already is None or not already.holds(source.path, one)]
    shape = media.choose_read_shape(
        moments=sum(len(one.moments) for one in wanted),
        duration_seconds=probed.duration_seconds or 0.0,
        fps=probed.fps or 0.0,
        width=probed.width or 0,
        height=probed.height or 0,
        size_bytes=0,
        codec=probed.vcodec,
        rates=None,
    )
    if shape is not media.ReadShape.DECODE_ONCE:
        yield
        return
    with tempfile.TemporaryDirectory(prefix="sift-fingerprints-") as workspace:
        frames: media.PreparedFrames | None = None
        try:
            with timing_hook("fingerprint.decode_once", asset_id=source.asset.id):
                frames = await media.decode_once(
                    source.path,
                    wanted,
                    workspace=Path(workspace),
                    settings=settings,
                    time_limit=tuning.SUBPROCESS_TIMEOUT_SECONDS,
                )
        except media.FFmpegError as error:
            # The asks seek, as they would have without this, and meet whatever the tool said.
            log.info("fingerprint.decode_refused", asset_id=source.asset.id, detail=str(error))
        if frames is None:
            yield
            return
        with media.prepared(already.merged(frames) if already is not None else frames):
            yield


async def _no_fingerprints(store: ContentStore, asset_id: str, refused: Unusable) -> None:
    """Write down that this file cannot be fingerprinted, in the decoder's own words.

    Filed under its own product: the file page says it, and `unfingerprinted` stops offering it.
    """
    await store.record_verdict(
        asset_id,
        VerdictProduct.FINGERPRINTS,
        code=refused.code,
        reason=str(refused),
        transient=refused.transient,
    )


async def _fingerprint(
    source: Source, probed: ffmpeg.Probed, *, settings: Settings
) -> tuple[str | None, str | None]:
    """The near-duplicate fingerprints: one for a frame, one for the whole thing.

    A still gets a frame fingerprint and no video one (there is no timeline to walk), and the
    two are different lengths on purpose, so nothing can compare a photograph to a video.
    """
    if source.asset.media_type == _IMAGE:
        frame = await _grey_frame(source.path, 0, settings=settings)
        # Off the loop: thirty frame hashes of pure Python would hold it beside every report.
        one = await asyncio.to_thread(perceptual.phash, frame) if frame else None
        return one, None

    hashes: list[str | None]
    if source.asset.media_type == _GIF:
        # A GIF cannot seek cheaply, so its thirty frames come from one decode, not thirty.
        hashes = await _gif_frame_hashes(source.path, settings=settings)
    else:
        # Thirty moments from one process, a moment that read nothing kept in its place. Across the
        # picture, not the file (see `sampler.picture_span`); the stash-box grid below divides the
        # file's length, because that is what other people's software divides.
        timestamps = sampler.hash_frames(
            sampler.picture_span(probed.duration_ms, probed.video_duration_ms)
        )
        frames = await _grey_frames(source.path, timestamps, settings=settings)
        hashes = await asyncio.to_thread(
            lambda: [perceptual.phash(frame) if frame else None for frame in frames]
        )

    filled = _hold_the_last_frame(hashes)
    if filled is None:
        return None, None
    return filled[0], perceptual.videohash(filled)


async def _video_phash(source: Source, probed: ffmpeg.Probed, *, settings: Settings) -> str | None:
    """The whole-video fingerprint the public stash-boxes match on. None if it cannot be built.

    Twenty-five stills at moments from the running time, as a grid reduced to one number. No
    running time, or a still that reads back nothing, is no fingerprint rather than a wrong one.
    Nothing here fails the caller but a refusal that means the bytes are broken.
    """
    if not probed.duration_seconds:
        return None

    try:
        # Twenty-five stills from ONE process, each written as its own file and read back in order.
        moments = [
            ffmpeg.stash_box_moment(at)
            for at in perceptual.scene_frame_times(probed.duration_seconds)
        ]
        with tempfile.TemporaryDirectory(prefix="sift-stash-box-") as workspace:
            files = await moments_to_files(
                source.path,
                moments,
                into=Path(workspace),
                suffix=".bmp",
                filters=ffmpeg.stash_box_filter(perceptual.SCENE_SHOT_WIDTH),
                output=ffmpeg.STASH_BOX_OUTPUT,
                settings=settings,
                time_limit=tuning.SUBPROCESS_TIMEOUT_SECONDS,
            )
            if any(one is None for one in files):
                return None
            shots = await asyncio.to_thread(
                lambda: [one.read_bytes() for one in files if one is not None]
            )
        if any(not shot for shot in shots):
            return None
        return await asyncio.to_thread(perceptual.video_phash, shots)
    except (ffmpeg.FFmpegError, ValueError) as exc:
        if isinstance(exc, ffmpeg.FFmpegError) and media.is_broken_data(str(exc)):
            raise
        log.warning("probe.no_stash_box_fingerprint", asset_id=source.asset.id, reason=str(exc))
        return None


def _hold_the_last_frame(hashes: list[str | None]) -> list[str] | None:
    """Fill in the moments that read back nothing, so the fingerprint is always the same length.

    A seek past a file's real end returns nothing, and a shorter fingerprint compares to nothing.
    A gap holds the previous frame's hash, leading gaps the first that read: deterministic, so two
    copies of one file agree. None if nothing read at all.
    """
    if not any(hashes):
        return None

    filled = list(hashes)
    last: str | None = None
    for index, value in enumerate(filled):
        if value is None:
            filled[index] = last
        else:
            last = value

    first = next(value for value in filled if value is not None)
    return [value if value is not None else first for value in filled]


async def _gif_frame_hashes(path: Path, *, settings: Settings) -> list[str | None]:
    """The fingerprint frames of a GIF, read in one decode instead of thirty seeks.

    Decoded once to grey squares, MAX_FRAMES of them taken evenly by POSITION (a raw frame carries
    no timestamp). Every picked index is in range for a non-empty decode, so there are no gaps.
    """
    size = perceptual.HASH_FRAME_SIZE
    stream = await media.moving_stream_of(path, settings=settings, priority=Priority.BACKGROUND)
    raw = await ffmpeg.run(
        ffmpeg.all_frames_args(path, size=size, settings=settings, stream=stream),
        capture=True,
        reads=path,
    )
    stride = size * size
    count = len(raw) // stride
    if count == 0:
        return [None] * sampler.MAX_FRAMES
    frames = [raw[i * stride : (i + 1) * stride] for i in range(count)]
    return await asyncio.to_thread(
        lambda: [
            perceptual.phash(frames[i * count // sampler.MAX_FRAMES])
            for i in range(sampler.MAX_FRAMES)
        ]
    )


async def _grey_frames(
    path: Path, timestamps: Sequence[int], *, settings: Settings
) -> list[bytes | None]:
    """The frames at these moments, each reduced to what the fingerprint reads, in order. None
    where there is nothing there (a seek past the last frame; see `media.raw_moments`)."""
    size = perceptual.HASH_FRAME_SIZE
    return await raw_moments(
        path,
        [ffmpeg.hash_frame_moment(at) for at in timestamps],
        filters=ffmpeg.hash_frame_filter(size),
        pixel_format=ffmpeg.HASH_PIXEL_FORMAT,
        frame_bytes=size * size,
        settings=settings,
        time_limit=tuning.SUBPROCESS_TIMEOUT_SECONDS,
    )


async def _grey_frame(path: Path, timestamp_ms: int, *, settings: Settings) -> bytes | None:
    """One frame, reduced to what the fingerprint reads. None if there is nothing there. The
    one-moment form of `_grey_frames`, for a still."""
    (frame,) = await _grey_frames(path, [timestamp_ms], settings=settings)
    return frame


def _has_any_fingerprint(asset: Asset) -> bool:
    """Whether this file carries any fingerprint that was measured, of any generation."""
    return any(one for one in (asset.phash, asset.videohash, asset.oshash, asset.video_phash))


def _has_its_fingerprints(asset: Asset) -> bool:
    """Whether this file already carries every fingerprint a full probe would write for it.

    Per media type, all of them rather than some, and of the generation in use: a row below
    `perceptual.FINGERPRINT_VERSION` holds values the arithmetic in use would not produce.
    """
    version = asset.fingerprint_version
    if version is not None and version < perceptual.FINGERPRINT_VERSION:
        return False
    if asset.media_type == _IMAGE:
        return asset.phash is not None
    if asset.media_type == _GIF:
        return asset.videohash is not None
    return None not in (asset.phash, asset.videohash, asset.oshash, asset.video_phash)


def _from(skip: int) -> dict[str, int] | None:
    """The fingerprint chain's payload for a page, and nothing for the first one.

    Nothing rather than `{"skip": 0}`: the settle collapses requests by matching the payload
    exactly, so a first page carrying a field would no longer collapse onto an import's request.
    """
    return {"skip": skip} if skip else None


def _may_step_over(skipped: int) -> bool:
    """Whether the chain has any of its walk left. See `tuning.FINGERPRINT_SKIP_PAGES`."""
    return skipped < tuning.FINGERPRINT_SKIP_PAGES * tuning.FINGERPRINT_BATCH


async def fingerprint_stash_box(
    context: JobContext,
    *,
    settings: Settings,
    hardware: HardwareReport,
    settles_into: SettlingJobs = (),
) -> None:
    """Give every near-duplicate fingerprint to every file that is missing one.

    A library from before the fingerprints, and every file a scan-only read did not fingerprint.
    All four from one decode, a batch at a time, each batch asking for the next when the queue is
    quiet, so it is a row somebody can watch and cancel. `settles_into` is what reads the
    fingerprints (the duplicate sweep), asked for once the chain has written its last page.
    """
    store = context.content
    # WHERE IN THE LIST TO START, only ever the pass stepping over its own stuck head. In the
    # payload rather than stored: anything that asks for the pass afresh reads from the beginning,
    # which is what makes a drive coming back fix itself.
    skipped = int(context.payload.get("skip", 0) or 0)
    ids = await store.unfingerprinted(tuning.FINGERPRINT_BATCH, skip=skipped)
    if not ids:
        return

    log.info("stash_box.backfill_start", count=len(ids), skip=skipped)
    fingerprinted = 0
    recorded = 0
    for index, asset_id in enumerate(ids):
        await context.raise_if_canceled()
        await context.set_progress(index / len(ids))
        outcome = await fingerprint_one(store, asset_id, settings=settings)
        if outcome is not None:
            recorded += 1
            fingerprinted += outcome

    await context.set_progress(1.0)
    log.info(
        "stash_box.backfill_done", read=len(ids), recorded=recorded, fingerprinted=fingerprinted
    )
    await _after_the_page(
        context, page=len(ids), skipped=skipped, recorded=recorded, settles_into=settles_into
    )


async def _after_the_page(
    context: JobContext, *, page: int, skipped: int, recorded: int, settles_into: SettlingJobs
) -> None:
    """Ask for the chain's next page (at the chain's own urgency, read from the row), step over a
    stuck one, or ask for what reads the result."""
    # **`recorded` is what makes this terminate, and a full batch alone is not**: a file on a
    # drive that is not plugged in stays in the work list, so the next page would be the same page.
    if page == tuning.FINGERPRINT_BATCH and recorded > 0:
        # What this page recorded has left the work list, so the same offset is the next batch.
        await context.queue.enqueue_when_settled(
            FINGERPRINT_FOR_STASH_BOXES, _from(skipped), priority=context.job.priority
        )
    elif recorded == 0 and page == tuning.FINGERPRINT_BATCH and _may_step_over(skipped):
        # NOTHING RECORDED, AND THERE IS MORE BEHIND IT: a drive that is away, or files the decoder
        # will never give up, which would hold every file behind them. Stepped over, within
        # `FINGERPRINT_SKIP_PAGES`; nothing is written, so a fresh request offers them again.
        log.info("stash_box.backfill_stepped_over", skipped=skipped, count=page)
        await context.queue.enqueue_when_settled(
            FINGERPRINT_FOR_STASH_BOXES,
            _from(skipped + page),
            priority=context.job.priority,
        )
    elif recorded > 0:
        # The chain's last page, and it wrote something. The duplicate sweep probing settles into
        # ran before this chain started, so it compared fingerprints that did not exist yet.
        await context.queue.settle_into(settles_into)


async def fingerprint_one(
    store: ContentStore,
    asset_id: str,
    *,
    settings: Settings,
    arriving: bool = False,
    source: Source | None = None,
) -> bool | None:
    """Every near-duplicate fingerprint for one file, recorded. The unit the sweep, the Build and an
    arriving file's own job share; `arriving` is that last one's, and writes no line of history
    (see `ContentStore.record_fingerprints`). `source` is the file as a caller already resolved it.

    None where the file could not be reached, left for the next pass. Otherwise whether the
    stash-box grid was made; a file that is there and will not be read gets empty fingerprints.
    """
    if source is None:
        try:
            source = await resolve_decodable(store, asset_id, settings=settings)
        except (MissingAsset, NoReadableCopy):
            return None

    video = source.asset.media_type not in (_IMAGE, _GIF)
    try:
        probed = await probed_of(store, source, settings=settings)
        async with _read_once(source, probed, settings=settings):
            frame, whole = await _fingerprint(source, probed, settings=settings)
            # Video only, as probing has it: a stash-box value for a photograph or a GIF would
            # have nothing to compare to, and a file that can never satisfy the query never
            # leaves it.
            exact = await hashing.oshash_file(source.path) if video else None
            scene = await _video_phash(source, probed, settings=settings) if video else None
    except (ffmpeg.FFmpegError, OSError) as exc:
        refused = broken_bytes(exc) if isinstance(exc, ffmpeg.FFmpegError) else None
        if refused is None and arriving:
            # An arriving file's own job keeps its retries for anything that is not about the
            # BYTES (a share that blinked): a permanent empty answer on a good file cannot be
            # taken back. The passes over the library have no retry, so they write one.
            raise
        await _unreadable(store, asset_id, source, exc, refused, arriving=arriving, video=video)
        return False

    if video and exact is None:
        # Too small to have an exact-file hash at all, which is also permanent: empty, not NULL.
        exact = ""
    await store.record_fingerprints(
        asset_id,
        # A file that read but yielded no usable frame gets the same empty answer: NULL means
        # nobody has looked, and this pass has.
        phash=frame if frame is not None else "",
        videohash=(
            None if source.asset.media_type == _IMAGE else (whole if whole is not None else "")
        ),
        oshash=exact,
        video_phash=(scene if scene is not None else "") if video else None,
        # What the file was called at this moment, for the event the store writes, in the file
        # page's order; read off the copy just decoded rather than queried again per file.
        name=source.asset.title or source.location.filename or source.asset.original_filename,
        arriving=arriving,
    )
    return scene is not None


async def _unreadable(
    store: ContentStore,
    asset_id: str,
    source: Source,
    exc: Exception,
    refused: Unusable | None,
    *,
    arriving: bool,
    video: bool,
) -> None:
    """Write down a file whose fingerprints could not be taken.

    A refusal about the bytes is written on the file in the decoder's own words; every column the
    file's kind is asked for is written empty, since one left NULL brings it straight back.
    """
    if refused is not None:
        await _no_fingerprints(store, asset_id, refused)
        # AND WHAT THE FILE ALREADY HAD IS KEPT: empties would blank numbers it was given while
        # it still decoded, and the verdict is what stops it being offered again. An arriving
        # file's job writes no empties at all.
        if arriving or _has_any_fingerprint(source.asset):
            log.info("stash_box.refused_kept", asset_id=asset_id, reason=str(refused))
            return
    log.warning("stash_box.unreadable", asset_id=asset_id, reason=str(exc))
    await store.record_fingerprints(
        asset_id,
        phash="",
        videohash="" if source.asset.media_type != _IMAGE else None,
        oshash="" if video else None,
        video_phash="" if video else None,
        name=source.asset.title or source.location.filename or source.asset.original_filename,
        arriving=arriving,
    )


async def fingerprint_arrival(
    context: JobContext, *, settings: Settings, settles_into: SettlingJobs = ()
) -> None:
    """One arriving file's fingerprints, handed out by its read after its pictures. See
    `FINGERPRINT_FILE`.

    A file that already has them is left alone. What reads the fingerprints is asked for once they
    are written, a settle after the last file's fingerprints rather than its read.
    """
    asset_id = _asset_id(context)
    asset = await context.content.get(asset_id)
    if asset is None:
        raise MissingAsset(f"asset {asset_id} is gone")
    if _has_its_fingerprints(asset):
        log.info("probe.fingerprints_already_kept", asset_id=asset_id)
        return
    with timing_hook("fingerprint.file", asset_id=asset_id, media_type=asset.media_type):
        written = await fingerprint_one(context.content, asset_id, settings=settings, arriving=True)
    if written is None:
        # Not there to read just now. Left without them, so the catch-up pass finds it again.
        log.info("fingerprint.file_unreachable", asset_id=asset_id)
        return
    await context.queue.settle_into(settles_into)
    await context.set_progress(1.0)
