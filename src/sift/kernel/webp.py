# SPDX-License-Identifier: AGPL-3.0-or-later
"""Animated WebP: the one format ffmpeg can write and cannot read.

ffmpeg is built with libwebp and encodes these happily. Handed one to decode it reports *image data
not found* and produces a stream of width zero: there is no animated-WebP demuxer, only the
still-image one. That is a long-standing gap rather than a misconfiguration, and no newer ffmpeg
closes it.

The image hosts serve these where they used to serve GIFs, so they arrive constantly, and they
arrive with a `.webp` extension and a still-image signature. Left unhandled, one becomes a row that
can never finish importing and never draws anything.

Two tools from libwebp close it, at arm's length as a subprocess exactly like ffmpeg:

- `webpinfo` reads the container and reports the canvas size, the animation flag and one block per
  frame. That is the probe.
- `anim_dump` writes every frame out as a full-canvas picture. That is the decode.

Neither is a library binding, so the same rule holds as for ffmpeg: arguments are passed as a list,
never a shell string, and a filename containing a space or a semicolon is a filename.

**What the rest of Sift sees is a readable copy, not this.** One conversion into an ordinary video
happens once, when the file is first probed, and is kept alongside the thumbnails. Everything after
that (the thumbnail, the preview, the sprite strip, the face pass) reads that copy through
ffmpeg and needs no branch for the format. Playback is the exception that needs nothing at all:
browsers render animated WebP natively, so the original bytes are served as they always were.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sift.kernel import media, subprocess
from sift.kernel.config import Settings
from sift.kernel.content import Asset, ContentStore, DerivativeKind
from sift.kernel.log import get_logger

log = get_logger(__name__)

#: A malformed file is one of the few things that can make a decoder spin, and a probe on the
#: ingest path that waits forever is a denial of service against every other file in the queue.
PROBE_TIMEOUT_SECONDS = 30

#: Frames are written one file at a time, so a long GIF is bounded by how many there are
#: rather than by how big it is. Generous: this runs in the background on a file being imported.
DUMP_TIMEOUT_SECONDS = 120

#: The encode that turns those frames into something every other stage can read.
ENCODE_TIMEOUT_SECONDS = 300

#: What a frame is written as, and what the encode then reads. PNG because it is lossless and
#: `anim_dump` writes it without being asked.
_FRAME_SUFFIX = ".png"
_FRAME_PREFIX = "frame_"
_FRAME_PATTERN = f"{_FRAME_PREFIX}%04d{_FRAME_SUFFIX}"

#: When a file says nothing useful about how long its frames last. Browsers do the same thing with
#: a zero or absent duration rather than rendering the whole GIF in an instant.
_DEFAULT_FRAME_MS = 100

_CANVAS = re.compile(r"^\s*Canvas size\s+(\d+)\s+x\s+(\d+)\s*$", re.MULTILINE)
_ANIMATION = re.compile(r"^\s*Animation:\s*(\d+)\s*$", re.MULTILINE)
_FRAME_DURATION = re.compile(r"^\s*Duration:\s*(\d+)\s*$", re.MULTILINE)


class WebpError(Exception):
    """A WebP tool failed, or could not be run at all.

    Carries the tool's own message where there is one. An operator reading a job's error column is
    the person who needs it, and "it did not work" is not something anybody can act on.
    """


@dataclass(frozen=True, slots=True)
class Animation:
    """What one animated WebP turned out to be.

    `duration_ms` is the sum of the frames' own durations rather than a frame count times a
    guessed rate: a WebP times each frame separately, and GIFs that pause on one frame are
    common enough that averaging would make a two-second file report as half a second.
    """

    width: int
    height: int
    frames: int
    duration_ms: int

    @property
    def fps(self) -> float:
        """How fast the frames go by on average. What the readable copy is encoded at."""
        if self.duration_ms <= 0:
            return 1000 / _DEFAULT_FRAME_MS
        return self.frames * 1000 / self.duration_ms


async def inspect(path: Path, *, settings: Settings) -> Animation:
    """Read the container. Raises `WebpError` if it is not a readable GIF.

    Absolute path, always, for the same reason ffprobe is given one: these tools have no `--` to
    end their options, so a file called `-h` would be read as a flag, and the filename came from
    a remote site or an upload form.
    """
    argv = await asyncio.to_thread(_inspect_args, path, settings)
    try:
        result = await subprocess.run(argv, time_limit=PROBE_TIMEOUT_SECONDS, capture_stdout=True)
    except subprocess.SubprocessError as error:
        raise WebpError(str(error)) from error

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() or "no detail"
        raise WebpError(f"webpinfo failed: {detail}")

    report = result.stdout.decode("utf-8", "replace")
    if not (canvas := _CANVAS.search(report)):
        raise WebpError("webpinfo reported no canvas size")
    animated = _ANIMATION.search(report)
    if animated is None or animated.group(1) == "0":
        raise WebpError("this WebP is a still picture, not a GIF")

    durations = [
        int(match.group(1)) or _DEFAULT_FRAME_MS for match in _FRAME_DURATION.finditer(report)
    ]
    if not durations:
        raise WebpError("webpinfo found no frames")

    return Animation(
        width=int(canvas.group(1)),
        height=int(canvas.group(2)),
        frames=len(durations),
        duration_ms=sum(durations),
    )


async def dump_frames(path: Path, into: Path, *, settings: Settings) -> list[Path]:
    """Write every frame out, in order, and say where they went.

    Full canvas each, which is the thing worth knowing: a WebP frame is often only the rectangle
    that changed since the last one, and reading those directly would produce a strip of fragments.
    `anim_dump` composites them.
    """
    argv = await asyncio.to_thread(_dump_args, path, into, settings)
    try:
        result = await subprocess.run(argv, time_limit=DUMP_TIMEOUT_SECONDS, capture_stdout=True)
    except subprocess.SubprocessError as error:
        raise WebpError(str(error)) from error

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() or "no detail"
        raise WebpError(f"anim_dump failed: {detail}")

    frames = await asyncio.to_thread(_written_frames, into)
    if not frames:
        raise WebpError("anim_dump wrote no frames")
    return frames


def _inspect_args(path: Path, settings: Settings) -> list[str]:
    return [settings.webpinfo_path, str(path.resolve())]


def _dump_args(path: Path, into: Path, settings: Settings) -> list[str]:
    into.mkdir(parents=True, exist_ok=True)
    return [
        settings.anim_dump_path,
        "-folder",
        str(into.resolve()),
        "-prefix",
        _FRAME_PREFIX,
        str(path.resolve()),
    ]


def _written_frames(into: Path) -> list[Path]:
    return sorted(into.glob(f"{_FRAME_PREFIX}*{_FRAME_SUFFIX}"))


def encode_args(frames_in: Path, destination: Path, *, fps: float, settings: Settings) -> list[str]:
    """ffmpeg's arguments for turning a folder of frames into the readable copy.

    H.264 in MP4, which is what everything else here already reads, at the rate the frames were
    timed for. `yuv420p` because an odd-sized GIF otherwise produces a file some decoders
    refuse, and the scale filter rounds both sides up for the same reason: a 405x721 GIF is
    not unusual and the encoder will not take it.
    """
    return [
        settings.ffmpeg_path,
        *media.background_flags(settings),
        "-framerate",
        f"{fps:.4f}",
        "-i",
        str(frames_in / _FRAME_PATTERN),
        "-vf",
        "scale=ceil(iw/2)*2:ceil(ih/2)*2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(destination),
    ]


def needs_a_readable_copy(asset: Asset) -> bool:
    """Whether ffmpeg can read this asset's own bytes.

    One format, named precisely: WebP bytes that are not a still. A still WebP is an image and
    ffmpeg reads it perfectly well, so the media type is half the answer and the format is the
    other half. Any other kind a WebP is filed under (a GIF, or a video on a row typed by an older
    classifier) is a GIF, which ffmpeg cannot decode, so the kind is asked whether it is a
    still and never which moving kind it is.

    Read from `mime` rather than from `container`, and that is not interchangeable. The container
    column is filled in by the probe, so during the probe itself, which is the first thing that
    needs to decode the file, it is still empty. `mime` comes off the ingress gate and is set from
    the moment the row exists.
    """
    return asset.mime == "image/webp" and asset.media_type != "image"


async def readable_copy(
    store: ContentStore, asset: Asset, original: Path, *, settings: Settings
) -> Path:
    """The path ffmpeg should read for this asset, making it first if it is not there.

    Kept as a derivative beside the thumbnails, so it is swept with them and rebuilt from the
    original the same way. Rebuilt here rather than only at import time because the cache is
    disposable by design: an operator who clears it should get their GIFs back on the next
    thing that asks for one, not a library of files that quietly stopped drawing.
    """
    for existing in await store.derivatives(asset.id):
        if existing.kind is DerivativeKind.RENDITION:
            if (on_disk := await store.derivative_at(existing.rel_cache_path)) is not None:
                return on_disk
            break

    animation = await inspect(original, settings=settings)
    # The system's temporary directory rather than the cache, which is where the sprite builder
    # stages its tiles too. Frames under the cache would be offered for removal by the maintenance
    # sweep while the encode that is writing them is still running.
    with tempfile.TemporaryDirectory(prefix="sift-webp-") as workspace:
        scratch = Path(workspace)
        await dump_frames(original, scratch, settings=settings)
        built = scratch / "readable.mp4"
        await media.run(
            encode_args(scratch, built, fps=animation.fps, settings=settings),
            time_limit=ENCODE_TIMEOUT_SECONDS,
            priority=subprocess.Priority.BACKGROUND,
        )
        size = await asyncio.to_thread(lambda: built.stat().st_size)
        derivative = await store.add_derivative(
            asset.id, DerivativeKind.RENDITION, extension="mp4", size_bytes=size
        )
        destination = settings.cache_dir / derivative.rel_cache_path
        # Moved into place rather than encoded there: a half-written file under a path a row
        # already names is a rendition every later reader treats as finished.
        await asyncio.to_thread(_place, built, destination)

    log.info("webp.readable_copy", asset_id=asset.id, frames=animation.frames)
    return destination


def _place(built: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(built), str(destination))
