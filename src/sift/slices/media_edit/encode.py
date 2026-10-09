# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ffmpeg commands that produce a compressed or converted copy of a file.
The sound is copied, never encoded, except where the target container cannot carry it."""

from __future__ import annotations

from pathlib import Path

from sift.kernel.config import Settings
from sift.kernel.media import (
    HDR_TO_SDR,
    Encoder,
    background_flags,
    background_threads,
    seconds,
)
from sift.slices.media_edit import tuning
from sift.slices.media_edit.tuning import Rung

__all__ = [
    "compress_args",
    "rewrap_args",
    "sample_args",
    "video_filters",
]


def video_filters(*, height: int | None, fps: float | None, hdr: bool = False) -> str | None:
    """The filter chain for one rung, or None. HDR is tone-mapped first; `min(N,ih)` never scales
    up."""
    parts: list[str] = []
    if hdr:
        parts.append(HDR_TO_SDR)
    if height is not None:
        parts.append(f"scale=-2:min({height}\\,ih)")
    if fps is not None:
        parts.append(f"fps={fps:g}")
    return ",".join(parts) if parts else None


def compress_args(
    source: Path,
    destination: Path,
    *,
    rung: Rung,
    settings: Settings,
    convert_audio: bool = False,
    hdr: bool = False,
) -> list[str]:
    """Re-encode the picture at one rung's constant quality and size, and copy the sound across.
    The thread cap appears twice: before the input for decoding, after it for the encoder."""
    filters = video_filters(height=rung.height, fps=rung.fps, hdr=hdr)
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        *(["-vf", filters] if filters else []),
        "-c:v",
        Encoder.CPU.value,
        "-preset",
        "medium",
        "-crf",
        str(rung.crf),
        # H.264 outside 4:2:0 will not play in Safari or on several televisions.
        "-pix_fmt",
        "yuv420p",
        *_audio_args(convert_audio),
        "-threads",
        str(background_threads(settings)),
        "-movflags",
        "+faststart",
        "-f",
        tuning.COMPATIBLE_CONTAINER,
        str(destination),
    ]


def rewrap_args(
    source: Path,
    destination: Path,
    *,
    settings: Settings,
    convert_audio: bool = False,
) -> list[str]:
    """Put the same streams in a container that plays anywhere, rebuilding only sound it cannot
    carry."""
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "copy",
        *_audio_args(convert_audio),
        "-movflags",
        "+faststart",
        "-f",
        tuning.COMPATIBLE_CONTAINER,
        str(destination),
    ]


def sample_args(
    source: Path,
    destination: Path,
    *,
    rung: Rung,
    at_ms: int,
    settings: Settings,
) -> list[str]:
    """A few seconds encoded exactly as the whole file would be, without sound. A zero seek is left
    out."""
    filters = video_filters(height=rung.height, fps=rung.fps)
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        *(["-ss", seconds(at_ms)] if at_ms > 0 else []),
        "-t",
        f"{tuning.SAMPLE_SECONDS:g}",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-an",
        *(["-vf", filters] if filters else []),
        "-c:v",
        Encoder.CPU.value,
        "-preset",
        "medium",
        "-crf",
        str(rung.crf),
        "-pix_fmt",
        "yuv420p",
        "-threads",
        str(background_threads(settings)),
        "-movflags",
        "+faststart",
        "-f",
        tuning.COMPATIBLE_CONTAINER,
        str(destination),
    ]


def _audio_args(convert: bool) -> list[str]:
    """Copy the sound, or, in the one case a container leaves no choice, rebuild it."""
    if not convert:
        return ["-c:a", "copy"]
    return ["-c:a", tuning.FALLBACK_ACODEC, "-b:a", tuning.FALLBACK_AUDIO_BITRATE]
