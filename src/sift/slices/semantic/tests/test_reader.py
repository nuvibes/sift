# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a file's moments, with the decoder stood in for.

What is proved here is the shape of the reading rather than the decoding: which moments are asked
for, what happens when one of them cannot be read, and that a still, a GIF and a video are
each read the cheapest way for what they are.

**One unreadable moment is not an unreadable file.** A truncated tail is common and everything
before it is perfectly good, so the pass carries on with what it has: a file that lost its last
second should not be a file with no description at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sift.kernel import media
from sift.kernel import subprocess as kernel_subprocess
from sift.kernel.config import Settings
from sift.slices.semantic import frames as semantic_frames
from sift.slices.semantic.embed import FRAME_SIZE
from sift.slices.semantic.frames import Reader

pytestmark = pytest.mark.unit

STRIDE = FRAME_SIZE * FRAME_SIZE * 3


class Decoder:
    """Answers each launch with bytes the test chose, and records what it was asked to run."""

    def __init__(self, answers: list[bytes | Exception]) -> None:
        self.answers = answers
        self.argvs: list[list[str]] = []

    async def __call__(self, argv: list[str], **_: Any) -> bytes:
        self.argvs.append(argv)
        answer = self.answers.pop(0) if self.answers else b""
        if isinstance(answer, Exception):
            raise answer
        return answer


def one_picture(value: int = 1) -> bytes:
    return bytes([value]) * STRIDE


@pytest.fixture
def decoder(monkeypatch: pytest.MonkeyPatch) -> Decoder:
    """Stands in for the decoder on both roads: the slice's own launch (a still, a GIF) and
    the kernel's many-moments launch, which a video goes through."""
    stub = Decoder([])
    monkeypatch.setattr(semantic_frames, "subprocess_capture", stub)
    monkeypatch.setattr(kernel_subprocess, "capture", stub)
    return stub


def whole_stream(*pictures: bytes) -> bytes:
    """What one process answers for a whole ladder: every picture back to back, in order."""
    return b"".join(pictures)


async def test_a_still_is_read_once_at_its_only_moment(
    settings: Settings, decoder: Decoder
) -> None:
    decoder.answers = [one_picture()]

    moments = await Reader(settings).read(Path("still.jpg"), media_type="image", duration_ms=0)

    assert [moment.at_ms for moment in moments] == [0]
    assert len(decoder.argvs) == 1


async def test_a_video_is_read_at_every_moment_of_the_ladder(
    settings: Settings, decoder: Decoder
) -> None:
    wanted = semantic_frames.moments_for(5_000)
    decoder.answers = [whole_stream(*(one_picture(index + 1) for index in range(len(wanted))))]

    moments = await Reader(settings).read(Path("clip.mp4"), media_type="video", duration_ms=5_000)

    assert tuple(moment.at_ms for moment in moments) == wanted
    # ONE launch for the whole ladder, with one seeked input per moment, rather than one launch per
    # moment (thirty opens of the same file over a share); the count is the whole claim.
    assert len(decoder.argvs) == 1
    assert decoder.argvs[0].count("-i") == len(wanted)


async def test_the_same_picture_twice_is_kept_once(settings: Settings, decoder: Decoder) -> None:
    """Two nearby moments in a file with few complete pictures decode to the very same bytes."""
    wanted = semantic_frames.moments_for(5_000)
    decoder.answers = [whole_stream(*(one_picture(9) for _ in wanted))]

    moments = await Reader(settings).read(Path("clip.mp4"), media_type="video", duration_ms=5_000)

    assert len(moments) == 1


async def test_one_unreadable_moment_does_not_lose_the_file(
    settings: Settings, decoder: Decoder
) -> None:
    """A stream one picture short cannot say which moment is missing, so the kernel reads the
    ladder again one moment at a time, and the moment that refuses is the only one lost."""
    from sift.kernel.subprocess import SubprocessError

    wanted = semantic_frames.moments_for(5_000)
    decoder.answers = [
        # The one-process stream, a picture short.
        whole_stream(*(one_picture(index + 1) for index in range(len(wanted) - 1))),
        # Then one launch per moment: the first refuses, the rest answer.
        SubprocessError("truncated"),
        *(one_picture(index + 1) for index in range(len(wanted) - 1)),
    ]

    moments = await Reader(settings).read(Path("clip.mp4"), media_type="video", duration_ms=5_000)

    assert len(moments) == len(wanted) - 1
    assert [moment.at_ms for moment in moments] == list(wanted[1:])


async def test_a_gif_is_read_in_one_pass_and_thinned(settings: Settings, decoder: Decoder) -> None:
    """It cannot be seeked cheaply, so it is read end to end and spread across rather than taken
    from the front."""
    decoder.answers = [b"".join(one_picture(index + 1) for index in range(50))]

    moments = await Reader(settings).read(Path("clip.gif"), media_type="gif", duration_ms=5_000)

    assert len(decoder.argvs) == 1
    assert len(moments) == len(semantic_frames.moments_for(5_000))
    # A GIF carries no reliable per-frame timing, so the position in the sequence is the
    # honest answer rather than an invented second.
    assert moments[0].at_ms == 0
    assert moments[-1].at_ms == 49


async def test_a_gif_that_decodes_to_nothing_is_no_moments(
    settings: Settings, decoder: Decoder
) -> None:
    decoder.answers = [b""]

    assert await Reader(settings).read(Path("x.gif"), media_type="gif", duration_ms=1000) == []


async def test_a_decoder_that_cannot_be_started_is_reported_as_a_decoder_failure(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So that one caller's error type covers both "the tool failed" and "the tool never ran"."""
    from sift.kernel.subprocess import SubprocessError

    async def refuse(argv: list[str], **_: Any) -> bytes:
        raise SubprocessError("no such tool")

    monkeypatch.setattr(semantic_frames, "subprocess_capture", refuse)

    with pytest.raises(media.FFmpegError, match="no such tool"):
        await Reader(settings).read(Path("still.jpg"), media_type="image", duration_ms=0)


async def test_the_pictures_come_back_as_the_shape_the_model_reads(
    settings: Settings, decoder: Decoder
) -> None:
    decoder.answers = [one_picture()]

    moments = await Reader(settings).read(Path("still.jpg"), media_type="image", duration_ms=0)

    assert moments[0].pixels.shape == (FRAME_SIZE, FRAME_SIZE, 3)
    assert moments[0].pixels.dtype == np.uint8
