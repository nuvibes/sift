# SPDX-License-Identifier: AGPL-3.0-or-later
"""A recap's deck as a video: the frames the client drew, encoded by Sift's own ffmpeg.

The client draws every frame from the same painting a saved picture is (the card, its slide in
and its figure counting up) and sends each distinct frame once with how many frames it is held
for, so a card standing still for two seconds is one picture, not sixty. Here the frames are laid
end to end on ffmpeg's input (`image2pipe`, 30 a second) and encoded as H.264 MP4 with its index
at the front, so a phone plays it as it downloads. Nothing of the library is read: the film is
pictures the reader's own screen drew of cards they were allowed to save.
"""

from __future__ import annotations

import asyncio
import shutil
import struct
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.media import BASE_FLAGS, Accelerator, Encoder, run

#: Frames a second, as the client draws them.
FPS = 30

#: The most frames a film may hold once each is held: 18 cards of about three seconds, with room.
MOST_FRAMES = 30 * 60 * FPS // 10

#: The most bytes of pictures one request may carry.
MOST_BYTES = 400 * 1024 * 1024

#: How long the encode may take before it is given up on: somebody is waiting, so it runs at
#: normal priority, and a slow machine is still given its minutes.
TIME_LIMIT = 900.0

#: A frame's head on the wire: its length in bytes and how many frames it is held for.
_HEAD = struct.Struct(">IH")

#: The quality asked of each encoder: a card is flat colour and sharp type.
_QUALITY = 20


class NotAFilm(ValueError):
    """The body is not frames this route can encode: said in words to the person."""


@dataclass(frozen=True, slots=True)
class Frame:
    picture: bytes
    held: int


async def frames_of(chunks: AsyncIterator[bytes]) -> list[Frame]:
    """The frames of a request body, each a JPEG and how many frames it is held for."""
    buffer = bytearray()
    frames: list[Frame] = []
    count = taken = 0
    async for chunk in chunks:
        buffer.extend(chunk)
        taken += len(chunk)
        if taken > MOST_BYTES:
            raise NotAFilm("That film is too large to encode.")
        while len(buffer) >= _HEAD.size:
            size, held = _HEAD.unpack_from(buffer)
            if len(buffer) < _HEAD.size + size:
                break
            picture = bytes(buffer[_HEAD.size : _HEAD.size + size])
            del buffer[: _HEAD.size + size]
            if not picture.startswith(b"\xff\xd8") or held < 1:
                raise NotAFilm("Those aren't frames Sift can encode.")
            count += held
            if count > MOST_FRAMES:
                raise NotAFilm("That film is too long to encode.")
            frames.append(Frame(picture, held))
    if buffer or not frames:
        raise NotAFilm("Those aren't frames Sift can encode.")
    return frames


def piped(frames: list[Frame]) -> bytes:
    """The frames end to end, each as many times as it is held."""
    return b"".join(one.picture for one in frames for _ in range(one.held))


def encode_args(
    ffmpeg: str, destination: Path, encoder: Encoder, device: str | None = None
) -> list[str]:
    """The pictures on standard input to an H.264 MP4 at 30 frames a second."""
    hardware: list[str] = []
    shaped = ["-pix_fmt", "yuv420p"]
    video = ["-c:v", encoder.value]
    if encoder is Encoder.CPU:
        video += ["-preset", "veryfast", "-crf", str(_QUALITY), "-tune", "stillimage"]
    elif encoder is Encoder.NVENC:
        video += ["-preset", "p4", "-rc", "vbr", "-cq", str(_QUALITY)]
    elif encoder is Encoder.QSV:
        shaped = ["-vf", "format=nv12"]
        video += ["-global_quality", str(_QUALITY)]
    else:
        if not device:
            raise ValueError("VAAPI needs a render node, and none was given")
        hardware = ["-vaapi_device", device]
        shaped = ["-vf", "format=nv12,hwupload"]
        video += ["-qp", str(_QUALITY)]
    return [
        ffmpeg,
        *BASE_FLAGS,
        *hardware,
        "-f",
        "image2pipe",
        "-framerate",
        str(FPS),
        "-c:v",
        "mjpeg",
        "-i",
        "-",
        *shaped,
        *video,
        "-r",
        str(FPS),
        "-movflags",
        "+faststart",
        "-an",
        str(destination),
    ]


async def encode(
    frames: list[Frame], *, ffmpeg: str, accelerator: Accelerator | None, device: str | None
) -> bytes:
    """The film as MP4 bytes: on the graphics card where the machine has one that works, else
    on the processor (`Accelerator.run` keeps the score). With no accelerator, the processor."""
    pictures = piped(frames)
    workspace = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="sift-recap-video-"))
    try:
        destination = workspace / "recap.mp4"

        async def attempt(encoder: Encoder, _decode: tuple[str, ...]) -> bytes:
            argv = encode_args(
                ffmpeg, destination, encoder, device if encoder is Encoder.VAAPI else None
            )
            await run(argv, time_limit=TIME_LIMIT, stdin=pictures)
            return await asyncio.to_thread(destination.read_bytes)

        if accelerator is None:
            return await attempt(Encoder.CPU, ())
        return await accelerator.run(attempt)
    finally:
        await asyncio.to_thread(shutil.rmtree, workspace, True)
