# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every feature that touches media needs: ffmpeg, and the way from an asset to a file.

ffmpeg is a subprocess given an argument list, never a shell string, and every argument builder is
a pure function returning `list[str]`, so what ffmpeg is asked to do is tested without spawning.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import os
import shutil
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sift.kernel import budget, hardware, lanes, subprocess
from sift.kernel.config import Settings
from sift.kernel.log import get_logger
from sift.kernel.media_card import CARD_SMALLEST_SIDE as CARD_SMALLEST_SIDE
from sift.kernel.media_card import ENCODER_PREFERENCE as ENCODER_PREFERENCE
from sift.kernel.media_card import GIVE_UP_AFTER as GIVE_UP_AFTER
from sift.kernel.media_card import Accelerator as Accelerator
from sift.kernel.media_card import Encoder as Encoder
from sift.kernel.media_card import FFmpegError as FFmpegError
from sift.kernel.media_card import choose_encoder as choose_encoder
from sift.kernel.media_card import decode_flags as decode_flags
from sift.kernel.media_card import render_node as render_node
from sift.kernel.media_frames import FRAMES_PER_SEEK as FRAMES_PER_SEEK
from sift.kernel.media_frames import FRAMES_PER_SEEK_UNKNOWN as FRAMES_PER_SEEK_UNKNOWN
from sift.kernel.media_frames import PICTURES_PER_INPUT as PICTURES_PER_INPUT
from sift.kernel.media_frames import PICTURES_PER_THREAD as PICTURES_PER_THREAD
from sift.kernel.media_frames import REFERENCE_PIXELS as REFERENCE_PIXELS
from sift.kernel.media_frames import FileFacts as FileFacts
from sift.kernel.media_frames import FrameClock as FrameClock
from sift.kernel.media_frames import FrameFiles as FrameFiles
from sift.kernel.media_frames import FrameRequest as FrameRequest
from sift.kernel.media_frames import Moment as Moment
from sift.kernel.media_frames import Picture as Picture
from sift.kernel.media_frames import PreparedFrames as PreparedFrames
from sift.kernel.media_frames import RawFrames as RawFrames
from sift.kernel.media_frames import ReadRates as ReadRates
from sift.kernel.media_frames import ReadShape as ReadShape
from sift.kernel.media_frames import StorageRead as StorageRead
from sift.kernel.media_frames import _output_of as _output_of
from sift.kernel.media_frames import _selectable as _selectable
from sift.kernel.media_frames import choose_read_shape as choose_read_shape
from sift.kernel.media_frames import clock_from as clock_from
from sift.kernel.media_frames import frames_per_seek as frames_per_seek
from sift.kernel.media_frames import input_bytes as input_bytes
from sift.kernel.media_frames import microseconds_of as microseconds_of
from sift.kernel.media_frames import moments_in_memory as moments_in_memory
from sift.kernel.media_frames import picture_from as picture_from
from sift.kernel.media_frames import position_among_pictures as position_among_pictures
from sift.kernel.media_frames import seconds as seconds
from sift.kernel.media_frames import select_expression as select_expression
from sift.kernel.media_frames import the_moving_picture as the_moving_picture
from sift.kernel.media_sources import MissingAsset as MissingAsset
from sift.kernel.media_sources import NoReadableCopy as NoReadableCopy
from sift.kernel.media_sources import Source as Source
from sift.kernel.media_sources import resolve as resolve
from sift.kernel.media_sources import resolve_decodable as resolve_decodable

log = get_logger(__name__)


#: The most memory ffmpeg may allocate for one buffer, against a malformed file's huge ask.
MAX_ALLOC_BYTES = str(1 << 30)

#: How far apart two running times may be and still be the same video, in milliseconds: one
#: number for both askers, loose because different encodes trim differently.
MAX_DURATION_GAP_MS = 10_000

#: Prepended to every invocation; without `-nostdin` ffmpeg reads the server's own input.
BASE_FLAGS: tuple[str, ...] = (
    "-hide_banner",
    "-loglevel",
    "error",
    "-nostdin",
    "-y",
    "-max_alloc",
    MAX_ALLOC_BYTES,
)


#: How many jobs the pool really runs, as the settings say; None until the pool is configured.
_jobs_at_once: int | None = None


def set_jobs_at_once(workers: int) -> bool:
    """Record how many jobs really run together. True when the number actually changed."""
    global _jobs_at_once
    if workers < 1 or workers == _jobs_at_once:
        return False
    _jobs_at_once = workers
    return True


def jobs_at_once(settings: Settings) -> int:
    """How many jobs run together: what the pool was told, or the hardware answer before it was."""
    if _jobs_at_once is not None:
        return _jobs_at_once
    return max(1, hardware.worker_concurrency(settings))


#: The workers running and the percent of the device they share, or None for the whole device.
_share: tuple[int, int] | None = None


def set_share(*, running: int, percent: int) -> bool:
    """Record the share in force: `running` workers sharing `percent` of the device.

    Below the whole device, each background tool is also held to its threads by the system.
    True when the share changed.
    """
    global _share
    share = (max(1, running), min(budget.WHOLE_DEVICE, max(1, percent)))
    if share == _share:
        return False
    _share = share
    cores = os.cpu_count() or 1
    threads = budget.tool_threads(cores, running=share[0], percent=share[1])
    held = share[1] < budget.WHOLE_DEVICE
    subprocess.hold_background(budget.processor_rate(threads, cores) if held else None)
    return True


def background_threads(settings: Settings) -> int:
    """How many threads one background ffmpeg may use: its share of the share in force.

    Left alone ffmpeg sizes its pool from the cores, per process, and the app queues behind them.
    """
    cores = os.cpu_count() or 1
    if _share is None:
        return budget.tool_threads(
            cores, running=jobs_at_once(settings), percent=budget.WHOLE_DEVICE
        )
    running, percent = _share
    return budget.tool_threads(cores, running=running, percent=percent)


def background_flags(settings: Settings) -> tuple[str, ...]:
    """`BASE_FLAGS` plus a thread cap on decoding and the filter graph; an encoder needs its own."""
    share = str(background_threads(settings))
    return (*BASE_FLAGS, "-threads", share, "-filter_threads", share)


#: HDR to SDR in software (Hable, back to BT.709 8-bit 4:2:0): one chain for every such encode.
HDR_TO_SDR = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)


def cover_picture_args(
    destination: Path,
    *,
    height: int,
    quality: int,
    settings: Settings,
) -> list[str]:
    """Re-encode an uploaded picture from standard input into Sift's own JPEG.

    The bytes never touch the disk, and what comes out is Sift's picture, never the uploader's file.
    No input seek: `-ss 0` before a still discards its only frame.
    """
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        "-i",
        "pipe:0",
        "-frames:v",
        "1",
        "-map_metadata",
        "-1",
        "-an",
        "-vf",
        f"scale=-2:min({height}\\,ih)",
        "-q:v",
        str(quality),
        str(destination),
    ]


def cover_frame_args(
    source: Path,
    destination: Path,
    *,
    crop: str,
    quality: int,
    settings: Settings,
) -> list[str]:
    """Cut a cover's window out of a picture Sift wrote itself, into its own JPEG; no input seek."""
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-map_metadata",
        "-1",
        "-an",
        "-vf",
        crop,
        "-q:v",
        str(quality),
        str(destination),
    ]


#: What ffmpeg says when the data is broken rather than the moment, matched case-insensitively.
#: Each phrase is one a real broken file produced: a phrase too loose costs a good file for ever.
BROKEN_DATA_PHRASES: tuple[str, ...] = (
    # A truncated or corrupt H.264 stream.
    "invalid nal unit size",
    "error splitting the input into nal units",
    "missing picture in access unit",
    # A corrupt still.
    "invalid sbit size",
    "decoding error: invalid data found when processing input",
    "decode error rate",
)

# Left out on purpose: `moov atom not found` is also a file still being copied, and the bare
# `invalid data found when processing input` also a partial read.


def is_broken_data(detail: str) -> bool:
    """Whether what the tool said means the bytes are broken; unknown words keep their retries."""
    lowered = detail.lower()
    return any(phrase in lowered for phrase in BROKEN_DATA_PHRASES)


async def run(
    argv: list[str],
    *,
    time_limit: float,
    capture: bool = False,
    priority: subprocess.Priority = subprocess.Priority.NORMAL,
    stdin: bytes | None = None,
    reads: Path | None = None,
) -> bytes:
    """Run ffmpeg or ffprobe to completion; `FFmpegError` on anything but success.

    `reads` is the library file it opens, which takes a place in its storage's lane first.
    """
    try:
        async with lanes.reading_if(reads):
            result = await subprocess.run(
                argv, time_limit=time_limit, capture_stdout=capture, priority=priority, stdin=stdin
            )
    except subprocess.SubprocessError as error:
        raise FFmpegError(str(error)) from error

    if result.returncode != 0:
        said = result.stderr.decode("utf-8", "replace").strip()
        detail = said or subprocess.unsaid(result.returncode)
        raise FFmpegError(f"{Path(argv[0]).name} failed: {detail}")
    return result.stdout


async def run_json(
    argv: list[str],
    *,
    time_limit: float,
    priority: subprocess.Priority = subprocess.Priority.NORMAL,
    reads: Path | None = None,
) -> dict[str, Any]:
    """Run a tool that answers in JSON (ffprobe) and parse what it said."""
    raw = await run(argv, time_limit=time_limit, capture=True, priority=priority, reads=reads)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise FFmpegError(f"{Path(argv[0]).name} did not return JSON") from error
    if not isinstance(payload, dict):
        raise FFmpegError(f"{Path(argv[0]).name} returned something other than an object")
    return payload


# --- Many moments of one file, from one process ---------------------------------------------------
# One process takes every moment as its own seeked input (`-ss T -i file` each), saving a launch
# per moment. A short raw stream cannot say which moment is missing, so its chunk is read again one
# process per moment. Chunks are sized by the command line's length and by each input's decoder
# memory.


#: The most characters a command line is allowed here, under Windows' 32,767, everywhere.
COMMAND_LINE_BUDGET = 30_000

#: What one input costs on the line beyond its path and seek; generous, as a refusal costs more.
_PER_MOMENT_OVERHEAD = 160

#: What the fixed part of the command costs: the tool, the base flags and the output options.
_FIXED_OVERHEAD = 600

#: The per-input chain that keeps exactly the first frame and puts its clock back to zero.
_FIRST_FRAME = "trim=end_frame=1,setpts=PTS-STARTPTS"


def chunk_size(source: Path, moments: Sequence[Moment]) -> int:
    """How many moments one command may name for this file, from what each costs on the line."""
    longest = max((sum(len(one) + 1 for one in moment.seek) for moment in moments), default=0)
    per_moment = len(str(source)) + longest + _PER_MOMENT_OVERHEAD
    return max(1, (COMMAND_LINE_BUDGET - _FIXED_OVERHEAD) // per_moment)


@dataclass(frozen=True, slots=True)
class _Reading:
    """What the frame readers ask of a file first: its picture, its moving stream and its clock."""

    picture: Picture | None
    stream: int
    clock: FrameClock | None = None


#: Readings already asked of a file, by where it is, its size and when it was written.
_PICTURES: dict[tuple[str, int, int], _Reading] = {}
_PICTURES_KEPT = 256

#: A file that cannot be asked: no size, and the stream ffmpeg reads when none is named.
_UNREAD = _Reading(picture=None, stream=0)


async def _reading_of(
    source: Path, *, settings: Settings, priority: subprocess.Priority
) -> _Reading:
    """This file's picture and moving stream, asked of ffprobe once per version of the file."""
    try:
        found = await asyncio.to_thread(source.stat)
    except OSError:
        return _UNREAD
    key = (str(source), found.st_size, found.st_mtime_ns)
    if key in _PICTURES:
        return _PICTURES[key]
    argv = [
        settings.ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "v",
        "-show_entries",
        "stream=codec_type,width,height,pix_fmt,nb_frames,duration,time_base"
        ":stream_disposition=attached_pic:format=start_time",
        "-of",
        "json",
        str(source),
    ]
    try:
        said = await run_json(argv, time_limit=30, priority=priority, reads=source)
    except FFmpegError:
        reading = _UNREAD
    else:
        listed = said.get("streams")
        streams = [
            {"codec_type": "video", **one}
            for one in (listed if isinstance(listed, list) else [])
            if isinstance(one, dict)
        ]
        video = the_moving_picture(streams)
        container = said.get("format")
        reading = _Reading(
            picture=picture_from(video) if video is not None else None,
            stream=position_among_pictures(streams, video),
            clock=clock_from(
                video, container.get("start_time") if isinstance(container, dict) else None
            ),
        )
    if len(_PICTURES) >= _PICTURES_KEPT:
        _PICTURES.pop(next(iter(_PICTURES)))
    _PICTURES[key] = reading
    return reading


async def picture_of(
    source: Path, *, settings: Settings, priority: subprocess.Priority
) -> Picture | None:
    """The size of this file's moving picture, asked once per version; None if unreadable."""
    return (await _reading_of(source, settings=settings, priority=priority)).picture


async def moving_stream_of(
    source: Path, *, settings: Settings, priority: subprocess.Priority
) -> int:
    """Which of this file's video streams its moments are read from: the one that moves."""
    return (await _reading_of(source, settings=settings, priority=priority)).stream


def _picked(stream: int) -> str:
    """How a command names the stream that moves; the first is plain `v`."""
    if stream < 0:
        raise ValueError("a video stream is counted from zero")
    return f"v:{stream}" if stream else "v"


async def moments_per_process(
    source: Path,
    moments: Sequence[Moment],
    *,
    settings: Settings,
    priority: subprocess.Priority,
) -> int:
    """How many of these moments one process may take: what fits the line and fits the memory."""
    fits_line = chunk_size(source, moments)
    if len(moments) <= 1:
        return fits_line
    picture = await picture_of(source, settings=settings, priority=priority)
    if picture is None:
        return fits_line
    fits_memory = moments_in_memory(
        picture,
        threads=background_threads(settings),
        budget=subprocess.planned_memory(jobs_at_once(settings)),
    )
    return min(fits_line, fits_memory)


def _inputs(source: Path, moments: Sequence[Moment], settings: Settings) -> list[str]:
    """One seeked input per moment, each held to the same thread share as the first."""
    share = str(background_threads(settings))
    argv: list[str] = []
    for moment in moments:
        argv += [*moment.seek, "-threads", share, "-i", str(source)]
    return argv


def raw_stream_args(
    source: Path,
    moments: Sequence[Moment],
    *,
    filters: str,
    pixel_format: str,
    settings: Settings,
    stream: int = 0,
) -> list[str]:
    """Every moment as raw pixels on stdout, in order, from one process.

    A filter graph's `[0:v]` is the first video stream, so `stream` names the one that moves."""
    picked = _picked(stream)
    chains = ";".join(f"[{i}:{picked}]{_FIRST_FRAME},{filters}[v{i}]" for i in range(len(moments)))
    joined = "".join(f"[v{i}]" for i in range(len(moments)))
    graph = f"{chains};{joined}concat=n={len(moments)}:v=1:a=0[out]"
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        *_inputs(source, moments, settings),
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-fps_mode",
        "passthrough",
        "-pix_fmt",
        pixel_format,
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def raw_frame_args(
    source: Path,
    moment: Moment,
    *,
    filters: str,
    pixel_format: str,
    settings: Settings,
    stream: int = 0,
) -> list[str]:
    """One moment as raw pixels on stdout: the per-moment shape the stream falls back to."""
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        *moment.seek,
        "-i",
        str(source),
        *(("-map", f"0:{_picked(stream)}") if stream else ()),
        "-frames:v",
        "1",
        "-vf",
        filters,
        "-pix_fmt",
        pixel_format,
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def moment_files_args(
    source: Path,
    moments: Sequence[Moment],
    *,
    filters: str,
    output: Sequence[str],
    destinations: Sequence[Path],
    settings: Settings,
    stream: int = 0,
) -> list[str]:
    """Every moment as its own file, from one process, landing at `destinations` in order."""
    picked = _picked(stream)
    argv = [settings.ffmpeg_path, *background_flags(settings), *_inputs(source, moments, settings)]
    for index, destination in enumerate(destinations):
        argv += ["-map", f"{index}:{picked}", "-frames:v", "1", "-vf", filters, *output]
        argv.append(str(destination))
    return argv


async def raw_moments(
    source: Path,
    moments: Sequence[Moment],
    *,
    filters: str,
    pixel_format: str,
    frame_bytes: int,
    settings: Settings,
    time_limit: float,
    priority: subprocess.Priority = subprocess.Priority.BACKGROUND,
) -> list[bytes | None]:
    """Every moment of `source` as raw pixels, in order; None where a moment read back nothing.

    Uncapped, on a thread: a capped read would drop good pictures silently.
    """
    if not moments:
        return []
    ready = _PREPARED.get()
    if ready is not None:
        prepared = ready.raw(source, moments, filters=filters, pixel_format=pixel_format)
        if prepared is not None:
            return prepared
    answer: list[bytes | None] = []
    size = await moments_per_process(source, moments, settings=settings, priority=priority)
    stream = await moving_stream_of(source, settings=settings, priority=priority)
    for start in range(0, len(moments), size):
        chunk = moments[start : start + size]
        answer.extend(
            await _raw_chunk(
                source,
                chunk,
                filters=filters,
                pixel_format=pixel_format,
                frame_bytes=frame_bytes,
                settings=settings,
                time_limit=time_limit,
                priority=priority,
                stream=stream,
            )
        )
    return answer


async def _raw_chunk(
    source: Path,
    chunk: Sequence[Moment],
    *,
    filters: str,
    pixel_format: str,
    frame_bytes: int,
    settings: Settings,
    time_limit: float,
    priority: subprocess.Priority,
    stream: int = 0,
) -> list[bytes | None]:
    if len(chunk) > 1:
        argv = raw_stream_args(
            source,
            chunk,
            filters=filters,
            pixel_format=pixel_format,
            settings=settings,
            stream=stream,
        )
        try:
            async with lanes.reading(source):
                raw = await subprocess.capture(argv, time_limit=time_limit, priority=priority)
        except subprocess.SubprocessError as error:
            log.info("media.moments.stream_refused", source=str(source), detail=str(error))
        else:
            if len(raw) == len(chunk) * frame_bytes:
                return [raw[i * frame_bytes : (i + 1) * frame_bytes] for i in range(len(chunk))]
            log.info(
                "media.moments.stream_short",
                source=str(source),
                wanted=len(chunk),
                got=len(raw) // frame_bytes,
            )
    # One process per moment, so a missing or refused moment keeps its place.
    pictures: list[bytes | None] = []
    for moment in chunk:
        argv = raw_frame_args(
            source,
            moment,
            filters=filters,
            pixel_format=pixel_format,
            settings=settings,
            stream=stream,
        )
        try:
            async with lanes.reading(source):
                raw = await subprocess.capture(argv, time_limit=time_limit, priority=priority)
        except subprocess.SubprocessError as error:
            log.info(
                "media.moments.moment_refused",
                source=str(source),
                seek=" ".join(moment.seek),
                detail=str(error),
            )
            pictures.append(None)
            continue
        pictures.append(raw if len(raw) == frame_bytes else None)
    return pictures


async def moments_to_files(
    source: Path,
    moments: Sequence[Moment],
    *,
    into: Path,
    suffix: str,
    filters: str,
    output: Sequence[str],
    settings: Settings,
    time_limit: float,
    priority: subprocess.Priority = subprocess.Priority.BACKGROUND,
) -> list[Path | None]:
    """Every moment of `source` written as `into/NNNN{suffix}`, in order; None where none came."""
    if not moments:
        return []
    destinations = [into / f"{index:04d}{suffix}" for index in range(len(moments))]
    ready = _PREPARED.get()
    if ready is not None:
        prepared = ready.files(source, moments, filters=filters, suffix=suffix, output=output)
        if prepared is not None:
            return await asyncio.to_thread(_moved, prepared, destinations)
    size = await moments_per_process(source, moments, settings=settings, priority=priority)
    stream = await moving_stream_of(source, settings=settings, priority=priority)
    for start in range(0, len(moments), size):
        chunk = moments[start : start + size]
        wanted = destinations[start : start + size]
        argv = moment_files_args(
            source,
            chunk,
            filters=filters,
            output=output,
            destinations=wanted,
            settings=settings,
            stream=stream,
        )
        try:
            await run(argv, time_limit=time_limit, priority=priority, reads=source)
            continue
        except FFmpegError as error:
            log.info("media.moments.files_refused", source=str(source), detail=str(error))
        for moment, destination in zip(chunk, wanted, strict=True):
            argv = moment_files_args(
                source,
                [moment],
                filters=filters,
                output=output,
                destinations=[destination],
                settings=settings,
                stream=stream,
            )
            try:
                await run(argv, time_limit=time_limit, priority=priority, reads=source)
            except FFmpegError as error:
                log.info(
                    "media.moments.moment_refused",
                    source=str(source),
                    seek=" ".join(moment.seek),
                    detail=str(error),
                )
    present = await asyncio.to_thread(lambda: [one.exists() for one in destinations])
    return [one if there else None for one, there in zip(destinations, present, strict=True)]


# --- A still and its brightness, from one read -----------------------------------------------------
# Each seeked input is mapped to the still and to a few grey pixels its brightness is read off.


@dataclass(frozen=True, slots=True)
class CutStill:
    """One moment, cut: the still on disk and the grey pixels its brightness is read from."""

    still: Path
    levels: bytes


def moment_stills_args(
    source: Path,
    moments: Sequence[Moment],
    *,
    still_filters: str,
    still_output: Sequence[str],
    level_filters: str,
    stills: Sequence[Path],
    levels: Sequence[Path],
    settings: Settings,
    stream: int = 0,
) -> list[str]:
    """Every moment as a still and as grey pixels, from one process."""
    picked = _picked(stream)
    argv = [settings.ffmpeg_path, *background_flags(settings), *_inputs(source, moments, settings)]
    for index, (still, level) in enumerate(zip(stills, levels, strict=True)):
        argv += ["-map", f"{index}:{picked}", "-frames:v", "1", "-vf", still_filters, *still_output]
        argv.append(str(still))
        argv += ["-map", f"{index}:{picked}", "-frames:v", "1", "-vf", level_filters]
        argv += ["-pix_fmt", "gray", "-f", "rawvideo", str(level)]
    return argv


async def moments_to_stills(
    source: Path,
    moments: Sequence[Moment],
    *,
    into: Path,
    still_filters: str,
    still_output: Sequence[str],
    level_filters: str,
    level_bytes: int,
    settings: Settings,
    time_limit: float,
    priority: subprocess.Priority = subprocess.Priority.BACKGROUND,
) -> list[CutStill | None]:
    """Every moment of `source` cut as a still and read as grey pixels, in order; None where nothing
    came out. A refused chunk is read a moment at a time."""
    if not moments:
        return []
    stills = [into / f"{index:04d}.jpg" for index in range(len(moments))]
    levels = [into / f"{index:04d}.gray" for index in range(len(moments))]
    stream = await moving_stream_of(source, settings=settings, priority=priority)

    def argv_for(start: int, end: int) -> list[str]:
        return moment_stills_args(
            source,
            moments[start:end],
            still_filters=still_filters,
            still_output=still_output,
            level_filters=level_filters,
            stills=stills[start:end],
            levels=levels[start:end],
            settings=settings,
            stream=stream,
        )

    size = await moments_per_process(source, moments, settings=settings, priority=priority)
    for start in range(0, len(moments), size):
        end = min(start + size, len(moments))
        try:
            await run(argv_for(start, end), time_limit=time_limit, priority=priority, reads=source)
            continue
        except FFmpegError as error:
            log.info("media.stills.refused", source=str(source), detail=str(error))
        for one in range(start, end):
            try:
                await run(
                    argv_for(one, one + 1), time_limit=time_limit, priority=priority, reads=source
                )
            except FFmpegError as error:
                log.info("media.stills.moment_refused", source=str(source), detail=str(error))

    def collect() -> list[CutStill | None]:
        answer: list[CutStill | None] = []
        for still, level in zip(stills, levels, strict=True):
            raw = level.read_bytes() if level.exists() else b""
            fine = still.exists() and still.stat().st_size > 0 and len(raw) == level_bytes
            answer.append(CutStill(still=still, levels=raw) if fine else None)
        return answer

    return await asyncio.to_thread(collect)


def _moved(prepared: Sequence[Path | None], destinations: Sequence[Path]) -> list[Path | None]:
    """Move the prepared files to where the caller wanted them. Blocking, for a thread."""
    answer: list[Path | None] = []
    for made, destination in zip(prepared, destinations, strict=True):
        if made is None:
            answer.append(None)
            continue
        # Sift's own scratch both sides, never a library file.
        shutil.move(  # nosemgrep: sift-no-file-removal-outside-delete-trash
            str(made), str(destination)
        )
        answer.append(destination)
    return answer


# --- One decode for every moment of every consumer ------------------------------------------------
# For a short file one decode is cheaper than many seeks (`choose_read_shape`). Each moment selects
# the frame its seek would stop at, byte for byte; the graph goes in a file, past the line's budget.
# Consumers ask as usual and find the frames prepared for the task (`prepared`) first.


def decode_once_graph(
    requests: Sequence[FrameRequest], *, clock: FrameClock, stream: int = 0
) -> str:
    """The filter graph: one split of the moving stream, a select-and-filter chain per moment."""
    chains: list[str] = []
    picks = [
        (r, m, select_expression(moment, clock))
        for r, request in enumerate(requests)
        for m, moment in enumerate(request.moments)
    ]
    kept = [(r, m, one) for r, m, one in picks if one is not None]
    if not kept:
        return ""
    split = "".join(f"[s{i}]" for i in range(len(kept)))
    chains.append(f"[0:{_picked(stream)}]split={len(kept)}{split}")
    for index, (r, m, one) in enumerate(kept):
        chains.append(f"[s{index}]select='{one}',{requests[r].filters}[o{r}_{m}]")
    return ";\n".join(chains) + "\n"


def decode_once_args(
    source: Path,
    requests: Sequence[FrameRequest],
    *,
    script: Path,
    workspace: Path,
    settings: Settings,
) -> list[str]:
    """One decode of `source`, every moment of every request written under `workspace`. Pure."""
    argv = [
        settings.ffmpeg_path,
        *background_flags(settings),
        "-i",
        str(source),
        "-filter_complex_script",
        str(script),
    ]
    for r, request in enumerate(requests):
        for m, moment in enumerate(request.moments):
            if not _selectable(moment):
                continue
            argv += ["-map", f"[o{r}_{m}]", "-frames:v", "1"]
            if isinstance(request, RawFrames):
                argv += ["-pix_fmt", request.pixel_format, "-f", "rawvideo"]
            else:
                argv += list(request.output)
            argv.append(str(_output_of(workspace, r, m, request)))
    return argv


def decode_once_fits(argv: Sequence[str]) -> bool:
    """Whether the command line is within the budget every site is held to."""
    return sum(len(one) + 1 for one in argv) <= COMMAND_LINE_BUDGET


#: The frames prepared for the task on this stack, unknown to the consumers three features deep.
_PREPARED: contextvars.ContextVar[PreparedFrames | None] = contextvars.ContextVar(
    "prepared_frames", default=None
)


def prepared_now() -> PreparedFrames | None:
    """The frames prepared for the task on this stack, if any."""
    return _PREPARED.get()


@contextlib.contextmanager
def prepared(frames: PreparedFrames) -> Iterator[None]:
    """Hand these frames to every `raw_moments` and `moments_to_files` call inside."""
    token = _PREPARED.set(frames)
    try:
        yield
    finally:
        _PREPARED.reset(token)


def _collect(source: Path, requests: Sequence[FrameRequest], workspace: Path) -> PreparedFrames:
    """Read what the decode produced. Blocking, for a thread."""
    frames = PreparedFrames()
    for r, request in enumerate(requests):
        outputs = [
            _output_of(workspace, r, m, request) if _selectable(moment) else None
            for m, moment in enumerate(request.moments)
        ]
        if isinstance(request, RawFrames):
            raws: list[bytes | None] = []
            for made in outputs:
                if made is None or not made.exists():
                    raws.append(None)
                    continue
                raw = made.read_bytes()
                raws.append(raw if len(raw) == request.frame_bytes else None)
            frames.put_raw(source, request, raws)
        else:
            frames.put_files(
                source,
                request,
                [made if made is not None and made.exists() else None for made in outputs],
            )
    return frames


async def decode_once(
    source: Path,
    requests: Sequence[FrameRequest],
    *,
    workspace: Path,
    settings: Settings,
    time_limit: float,
    priority: subprocess.Priority = subprocess.Priority.BACKGROUND,
) -> PreparedFrames:
    """Decode `source` once and take every moment of every request from the one stream.

    `FFmpegError` only when nothing came out: a moment past the end also exits non-zero.
    """
    script = workspace / "graph.txt"
    reading = await _reading_of(source, settings=settings, priority=priority)
    if reading.clock is None:
        # Without ticks the exact frame cannot be named; the consumers seek.
        log.info("media.decode_once.no_clock", source=str(source))
        return PreparedFrames()
    graph = decode_once_graph(requests, clock=reading.clock, stream=reading.stream)
    if not graph:
        return PreparedFrames()
    await asyncio.to_thread(script.write_text, graph, encoding="ascii")
    argv = decode_once_args(source, requests, script=script, workspace=workspace, settings=settings)
    if not decode_once_fits(argv):
        raise FFmpegError("too many moments for one command line")
    try:
        async with lanes.reading(source):
            result = await subprocess.run(
                argv, time_limit=time_limit, capture_stdout=False, priority=priority
            )
    except subprocess.SubprocessError as error:
        raise FFmpegError(str(error)) from error
    frames = await asyncio.to_thread(_collect, source, requests, workspace)
    produced = sum(
        1
        for held in list(frames._raw.values()) + list(frames._files.values())
        for one in held.values()
        if one is not None
    )
    if produced == 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[-400:] or "no detail"
        raise FFmpegError(f"{Path(argv[0]).name} produced no frame: {detail}")
    log.info(
        "media.decode_once",
        source=str(source),
        moments=frames.count,
        produced=produced,
        returncode=result.returncode,
    )
    return frames
