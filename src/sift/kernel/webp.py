# SPDX-License-Identifier: AGPL-3.0-or-later
"""Animated WebP, which ffmpeg can write but not read, decoded by libwebp's own tools.

`webpinfo` probes and `anim_dump` decodes into a kept readable copy every later stage reads."""

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

#: A malformed file can make a decoder spin, and a stuck probe would stall the whole queue.
PROBE_TIMEOUT_SECONDS = 30

#: Frames are written one file at a time; generous, as this runs in the background.
DUMP_TIMEOUT_SECONDS = 120

#: The encode that turns those frames into something every other stage can read.
ENCODE_TIMEOUT_SECONDS = 300

#: Lossless, and what `anim_dump` writes without being asked.
_FRAME_SUFFIX = ".png"
_FRAME_PREFIX = "frame_"
_FRAME_PATTERN = f"{_FRAME_PREFIX}%04d{_FRAME_SUFFIX}"

#: For a frame with no useful duration, as browsers do.
_DEFAULT_FRAME_MS = 100

_CANVAS = re.compile(r"^\s*Canvas size\s+(\d+)\s+x\s+(\d+)\s*$", re.MULTILINE)
_ANIMATION = re.compile(r"^\s*Animation:\s*(\d+)\s*$", re.MULTILINE)
_FRAME_DURATION = re.compile(r"^\s*Duration:\s*(\d+)\s*$", re.MULTILINE)


class WebpError(Exception):
    """A WebP tool failed, or could not run; carries the tool's own message where it has one."""


@dataclass(frozen=True, slots=True)
class Animation:
    """One animated WebP: its duration sums each frame's own, as WebP times frames separately."""

    width: int
    height: int
    frames: int
    duration_ms: int

    @property
    def fps(self) -> float:
        """The average frame rate, which the readable copy is encoded at."""
        if self.duration_ms <= 0:
            return 1000 / _DEFAULT_FRAME_MS
        return self.frames * 1000 / self.duration_ms


async def inspect(path: Path, *, settings: Settings) -> Animation:
    """Read the container by absolute path, as the tools have no `--`; `WebpError` if not a GIF."""
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
    """Write every frame out composited to the full canvas, in order, and say where they went."""
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
    """ffmpeg's arguments for the readable copy: H.264 in MP4, yuv420p, even-sized."""
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
    """Whether this is a moving WebP ffmpeg cannot read, by `mime`, set before the probe."""
    return asset.mime == "image/webp" and asset.media_type != "image"


async def readable_copy(
    store: ContentStore, asset: Asset, original: Path, *, settings: Settings
) -> Path:
    """The path ffmpeg should read for this asset, rebuilding the kept copy if it was swept."""
    for existing in await store.derivatives(asset.id):
        if existing.kind is DerivativeKind.RENDITION:
            if (on_disk := await store.derivative_at(existing.rel_cache_path)) is not None:
                return on_disk
            break

    animation = await inspect(original, settings=settings)
    # Not the cache, whose sweep would offer the frames while the encode still writes them.
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
        # Moved into place, as a half-written file under a named path reads as finished.
        await asyncio.to_thread(_place, built, destination)

    log.info("webp.readable_copy", asset_id=asset.id, frames=animation.frames)
    return destination


def _place(built: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(built), str(destination))
