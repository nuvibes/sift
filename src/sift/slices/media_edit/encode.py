# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ffmpeg commands that produce a compressed or converted copy of a file.

Same two rules as everywhere else ffmpeg is built in this tree: an argument list rather than a
shell string, and a pure function returning it so that what Sift asks for can be read in a test
rather than inferred from a log. The shared half (the runner, the encoder choice, the flags that
put a background job below everything else on the machine) comes from the kernel, because the
alternative is a second way of spawning ffmpeg and there is no version of that which stays in step.

**The sound is copied, never encoded.** That is not a default here, it is the shape of every
function in this module except the one whose name says otherwise. Re-encoding sound to save room is
a small saving and an immediately audible loss, and a person who wants it gone can remove it
themselves. The single exception is a compatibility conversion of a file whose sound the target
container physically cannot carry, and that path is named for what it does.
"""

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
    """The filter chain for one rung, or None when the picture is left exactly as it is.

    `hdr` puts the tone map in front of everything else: this encode forces 8-bit 4:2:0 for the
    same reason the player's does, and forced onto a PQ or HLG picture unmapped it is grey and
    washed out. The chain is the kernel's, shared with the player, so the two cannot drift.

    `-2` on the free axis keeps the shape and rounds to an even number, which several encoders
    refuse to work without. `min(N,ih)` is what stops a small source being scaled UP to the rung's
    height: a bigger file that looks worse, which is the opposite of the job, and the reason the
    floor never applies to a source that is already under it.
    """
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
    """Re-encode the picture at one rung's quality and size, and copy the sound across.

    Constant quality rather than a bitrate aimed at the target. Handing ffmpeg a bitrate makes it
    hit the number by whatever means the file demands, which on a difficult scene means visible
    breakup; constant quality holds the picture and lets the size fall where it falls, and the
    ladder is what turns that into a size somebody asked for. Two-pass was rejected for the obvious
    reason: it doubles every encode, and the ladder already re-encodes.

    `-map 0:v:0 -map 0:a?` keeps the first video stream and any sound, and drops everything else.
    That is deliberate rather than lazy: the output exists to be sent somewhere, and a second video
    stream, an attached picture or a subtitle track is exactly the kind of thing that makes a file
    play in one place and not another.

    The thread cap appears twice, and both are needed. Before the input it caps decoding and the
    filter graph; only the one in the output position reaches the encoder, which is where the work
    actually is.
    """
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
        # H.264 outside 4:2:0 is a file Safari will not play and several televisions will not
        # decode, which defeats the point of producing it.
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
    """Put the same streams in a container that plays anywhere. No decoding, nothing re-compressed.

    This is what makes a compatibility target nearly free in the common case: a file that is already
    H.264 and merely in the wrong container needs its packets moved, not its pixels rebuilt. Seconds
    instead of minutes, and identical quality because nothing was re-encoded.

    `convert_audio` is the one case where that is not entirely true, and it exists because some
    sound simply cannot travel in this container. Then the picture is still copied untouched and
    only the sound is rebuilt.
    """
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
    """A few seconds encoded exactly as the whole file would be, so it can be looked at.

    Everything about the encode matches `compress_args` (same quality, same size, same filters)
    because a sample produced any other way answers a question nobody asked. What differs is that
    it starts somewhere into the file and stops after a few seconds.

    The seek goes before the input, so ffmpeg jumps rather than decoding and discarding everything
    up to that point. On a two-hour video that is the difference between a sample somebody waits for
    and one they give up on.

    **A seek to zero is left out entirely rather than passed as `-ss 0`.** In that position it is a
    no-op on some ffmpeg builds and, on others, discards the only frame of a single-frame source and
    exits successfully having written nothing: an empty file and a zero exit code, which is the
    hardest kind of failure to attribute. A file with no known running time samples from zero, so
    this is reachable rather than theoretical, and the flag buys nothing when there is nothing to
    seek past.

    The sound is dropped. A sample exists to show what the picture will look like, it is thrown away
    afterwards, and the sound in the real output is a copy of what is already there, so there is
    nothing about it a sample could tell anybody.
    """
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
