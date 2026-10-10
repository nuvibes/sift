# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading the moments of a file, and the two things about it that fail silently.

**A seek to zero is skipped rather than passed.** A still picture is presented to the decoder as a
video one frame long, and seeking to zero on it lands *on* that frame's moment rather than before
it, so nothing is written and the decoder exits reporting success. Every photo would get no
description at all, with a green suite, because the version of the decoder a machine happens to
have behaves differently from the one that ships. This is the check that would have caught it.

**A part-picture at the end is dropped rather than padded.** A decoder killed at its time limit
leaves one, and half a picture described as a whole one is numbers about nothing, which does not
fail, it just answers searches wrongly for ever.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sift.kernel import sampling
from sift.kernel.config import Settings
from sift.slices.semantic.embed import FRAME_SIZE
from sift.slices.semantic.frames import (
    all_frames_args,
    frame_args,
    moments_for,
    split,
    thin,
)

pytestmark = pytest.mark.unit

STRIDE = FRAME_SIZE * FRAME_SIZE * 3


def test_the_first_moment_is_read_without_seeking_to_it(settings: Settings) -> None:
    """On a still, a seek to zero lands past the only frame there is and writes nothing."""
    argv = frame_args(__import__("pathlib").Path("clip.mp4"), 0, settings=settings)

    assert "-ss" not in argv


def test_any_later_moment_is_seeked_to(settings: Settings) -> None:
    argv = frame_args(__import__("pathlib").Path("clip.mp4"), 5000, settings=settings)

    assert "-ss" in argv
    assert argv[argv.index("-ss") + 1] == "5.000"


def test_a_picture_is_squashed_to_the_square_the_model_reads(settings: Settings) -> None:
    """Squashed, not cropped: it is what the model's own published preparation does, and a model
    shown pictures framed differently from its training returns numbers that are confidently
    wrong rather than an error."""
    argv = frame_args(__import__("pathlib").Path("clip.mp4"), 0, settings=settings)

    # The scaler's flag rides on the filter rather than as a global option, so the one-moment
    # command and the many-moment one say it the same way.
    assert f"scale={FRAME_SIZE}:{FRAME_SIZE}:flags=bilinear" in argv
    assert "rgb24" in argv


def test_a_gif_is_read_in_one_pass(settings: Settings) -> None:
    """It cannot be seeked cheaply, so it is read end to end rather than moment by moment."""
    argv = all_frames_args(__import__("pathlib").Path("clip.gif"), settings=settings)

    assert "-ss" not in argv
    assert "-frames:v" not in argv


async def test_an_animated_avif_is_described_from_the_stream_that_moves(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An animated AVIF is read as a GIF, and its first video stream is a still cover: read from
    that, the whole file is described as its one cover frame. The read names the stream
    `media.moving_stream_of` answers; a GIF's command stays as it was."""
    from pathlib import Path

    from sift.kernel import media
    from sift.slices.semantic import frames as frames_module
    from sift.slices.semantic.frames import Reader

    asked: list[list[str]] = []

    async def moving(path: Path, **_: object) -> int:
        return 1

    async def capture(argv: list[str], **_: object) -> bytes:
        asked.append(argv)
        return bytes(STRIDE * 2)

    monkeypatch.setattr(media, "moving_stream_of", moving)
    monkeypatch.setattr(frames_module, "subprocess_capture", capture)

    got = await Reader(settings).read(Path("motion.avif"), media_type="gif", duration_ms=2000)

    (argv,) = asked
    assert argv[argv.index("-map") + 1] == "0:v:1"
    assert argv.index("-map") > argv.index("-i")
    assert len(got) == 2
    assert "-map" not in all_frames_args(Path("clip.gif"), settings=settings)


def test_whole_pictures_are_cut_out_of_the_stream() -> None:
    raw = bytes(STRIDE * 3)

    assert len(split(raw)) == 3
    assert split(raw)[0].shape == (FRAME_SIZE, FRAME_SIZE, 3)


def test_a_part_picture_at_the_end_is_dropped() -> None:
    """What a decoder killed at its time limit leaves behind."""
    raw = bytes(STRIDE * 2 + 17)

    assert len(split(raw)) == 2


def test_nothing_read_is_no_pictures_rather_than_an_error() -> None:
    assert split(b"") == []


def test_the_moments_are_the_kernel_ladder_and_not_a_second_one() -> None:
    """Every feature that looks inside a video asks the same question and gets the same answer:
    two ladders is two answers to a question that has one."""
    for duration_ms in (5_000, 45_000, 200_000, 7_200_000):
        assert moments_for(duration_ms) == sampling.sample_frames(duration_ms)


def test_a_gif_is_thinned_evenly_rather_than_from_the_front() -> None:
    """Its opening frames are usually the same moment; its point is what happens later."""
    kept = thin(100, 5)

    assert kept[0] == 0
    assert kept[-1] == 99
    assert len(kept) == 5


def test_a_gif_shorter_than_what_was_wanted_keeps_everything() -> None:
    assert thin(3, 10) == [0, 1, 2]


def test_asking_for_one_frame_of_a_gif_takes_the_first() -> None:
    assert thin(50, 1) == [0]


def test_a_decoded_picture_is_the_shape_the_model_expects() -> None:
    pictures = split(bytes(STRIDE))

    assert pictures[0].dtype == np.uint8


# --- the plan a Build reads from agrees with the ask -----------------------------------------------


async def test_the_plan_and_the_ask_agree_so_a_build_can_read_the_file_once(
    tmp_path: Path, settings: Settings
) -> None:
    """`frame_requests` says which frames describing a video would ask for; `Reader.read` asks
    for them. The store is filled from the plan and the reader is run against a tool that refuses
    to run, so every moment it hands back came from the plan."""
    from sift.kernel import media
    from sift.slices.semantic.frames import Reader, frame_requests
    from sift.testing.tools import stand_in_tool

    refusing = stand_in_tool(tmp_path / "bin", "ffmpeg", "import sys; sys.exit(1)")
    facts = media.FileFacts(
        asset_id="a",
        path=tmp_path / "clip.mp4",
        media_type="video",
        duration_ms=45_000,
        width=320,
        height=240,
        fps=10.0,
        size_bytes=1,
    )
    (request,) = await frame_requests(facts)
    assert isinstance(request, media.RawFrames)
    pictures = [bytes([index % 251]) * request.frame_bytes for index in range(len(request.moments))]
    prepared = media.PreparedFrames()
    prepared.put_raw(facts.path, request, pictures)

    reader = Reader(settings.model_copy(update={"ffmpeg_path": refusing}))
    with media.prepared(prepared):
        moments = await reader.read(facts.path, media_type="video", duration_ms=45_000)

    assert [one.at_ms for one in moments] == list(sampling.sample_frames(45_000))
    assert all(one.pixels.shape == (FRAME_SIZE, FRAME_SIZE, 3) for one in moments)


async def test_a_still_plans_its_one_square_and_a_gif_plans_nothing() -> None:
    """A still's one square is what a task's one decode hands it (`Reader._single` asks for the
    same); a GIF is read whole in a go of its own."""
    from pathlib import Path

    from sift.kernel import media
    from sift.slices.semantic.frames import FRAME_FILTER, frame_requests

    def facts(kind: str) -> media.FileFacts:
        return media.FileFacts(
            asset_id="a",
            path=Path("x"),
            media_type=kind,
            duration_ms=1000,
            width=320,
            height=240,
            fps=10.0,
            size_bytes=1,
        )

    assert await frame_requests(facts("gif")) == []
    (still,) = await frame_requests(facts("image"))
    assert isinstance(still, media.RawFrames)
    assert still.moments == (media.Moment(seek=()),) and still.filters == FRAME_FILTER
    assert still.frame_bytes == FRAME_SIZE * FRAME_SIZE * 3
