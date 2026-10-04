# SPDX-License-Identifier: AGPL-3.0-or-later
"""The frame readers read the stream that moves, not the first one.

An animated AVIF written by ffmpeg carries its still cover as video stream 0 and its frames as
video stream 1, and neither is marked. A filter graph fed `[0:v]` is fed the cover, so a reader
of the first stream takes an animated AVIF's faces and fingerprints from its one cover frame, and
every moment after the first reads nothing at all. The hover clip asks which stream moves; the
readers ask the same.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from sift.kernel import media
from sift.kernel.config import Settings
from sift.kernel.media import FrameFiles, Moment, RawFrames
from sift.kernel.subprocess import Priority

SOURCE = Path("/library/motion.avif")
FRAME = 8 * 8
MOMENTS = [Moment(seek=()), Moment(seek=("-ss", "1.000")), Moment(seek=("-ss", "1.500"))]


@pytest.fixture
def motion(tmp_path: Path) -> Path:
    """A real animated AVIF: a still cover first, two seconds of moving frames second."""
    path = tmp_path / "motion.avif"
    argv = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10", "-t", "2", str(path),
    ]  # fmt: skip
    subprocess.run(argv, capture_output=True, check=True)
    said = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=index",
         "-of", "csv=p=0", str(path)],
        capture_output=True,
        check=True,
    )  # fmt: skip
    assert len(said.stdout.split()) == 2, "the fixture must carry a cover and the frames"
    return path


def test_a_graph_names_the_stream_that_moves_and_an_ordinary_file_is_named_as_before(
    settings: Settings,
) -> None:
    moving = media.raw_stream_args(
        SOURCE, MOMENTS, filters="scale=8:8", pixel_format="gray", settings=settings, stream=1
    )
    ordinary = media.raw_stream_args(
        SOURCE, MOMENTS, filters="scale=8:8", pixel_format="gray", settings=settings
    )
    assert "[0:v:1]" in moving[moving.index("-filter_complex") + 1]
    assert "[0:v]" in ordinary[ordinary.index("-filter_complex") + 1]
    assert "[0:v:" not in ordinary[ordinary.index("-filter_complex") + 1]

    one = media.raw_frame_args(
        SOURCE, MOMENTS[1], filters="scale=8:8", pixel_format="gray", settings=settings, stream=1
    )
    assert one[one.index("-map") + 1] == "0:v:1"
    plain = media.raw_frame_args(
        SOURCE, MOMENTS[1], filters="scale=8:8", pixel_format="gray", settings=settings
    )
    assert "-map" not in plain

    files = media.moment_files_args(
        SOURCE,
        MOMENTS[:2],
        filters="scale=8:8",
        output=("-q:v", "2"),
        destinations=[Path("/scratch/a.jpg"), Path("/scratch/b.jpg")],
        settings=settings,
        stream=1,
    )
    assert [files[i + 1] for i, one in enumerate(files) if one == "-map"] == ["0:v:1", "1:v:1"]

    clock = media.FrameClock(1, 25, 0)
    graph = media.decode_once_graph(
        [RawFrames(tuple(MOMENTS), "scale=8:8", "gray", FRAME)], clock=clock, stream=1
    )
    assert graph.startswith("[0:v:1]split=3")
    assert media.decode_once_graph(
        [RawFrames(tuple(MOMENTS), "scale=8:8", "gray", FRAME)], clock=clock
    ).startswith("[0:v]split=3")


async def test_the_stream_that_moves_is_asked_of_the_file_once(
    motion: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media, "_PICTURES", {})
    stream = await media.moving_stream_of(motion, settings=settings, priority=Priority.BACKGROUND)
    picture = await media.picture_of(motion, settings=settings, priority=Priority.BACKGROUND)

    assert stream == 1
    assert picture is not None and (picture.width, picture.height) == (64, 64)
    assert len(media._PICTURES) == 1


async def test_the_moments_of_an_animated_avif_are_read_from_its_frames_not_its_cover(
    motion: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read from the cover, every moment after the first is past the cover's one frame and comes
    back as nothing: the fingerprint is the cover held thirty times, the faces are the cover's."""
    monkeypatch.setattr(media, "_PICTURES", {})

    frames = await media.raw_moments(
        motion,
        MOMENTS,
        filters="scale=8:8",
        pixel_format="gray",
        frame_bytes=FRAME,
        settings=settings,
        time_limit=60,
    )

    assert all(one is not None for one in frames), "a moment inside the frames read nothing"
    assert frames[1] != frames[2], "two moments half a second apart are the same picture"


async def test_one_decode_of_an_animated_avif_reads_its_frames_not_its_cover(
    motion: Path, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media, "_PICTURES", {})
    raw = RawFrames(tuple(MOMENTS), "scale=8:8", "gray", FRAME)
    files = FrameFiles(tuple(MOMENTS[1:]), "scale=8:8", ".bmp", ())
    workspace = tmp_path / "once"
    workspace.mkdir()

    prepared = await media.decode_once(
        motion, [raw, files], workspace=workspace, settings=settings, time_limit=60
    )

    read = prepared.raw(motion, MOMENTS, filters="scale=8:8", pixel_format="gray")
    assert read is not None and all(one is not None for one in read)
    assert read[1] != read[2]
    made = prepared.files(motion, MOMENTS[1:], filters="scale=8:8", suffix=".bmp", output=())
    assert made is not None and all(one is not None for one in made)


def _video(**fields: Any) -> dict[str, Any]:
    return {"codec_type": "video", **fields}


def test_cover_art_is_passed_over_for_the_one_stream_that_moves() -> None:
    """A poster frame stating a longer duration than the film must still not be taken for it."""
    poster = _video(disposition={"attached_pic": 1}, nb_frames="1", duration="9999")
    film = _video(disposition={"attached_pic": 0}, nb_frames="300", duration="10")
    streams: list[dict[str, Any]] = [poster, {"codec_type": "audio"}, film]

    assert media.the_moving_picture(streams) is film
    assert media.position_among_pictures(streams, film) == 1


def test_a_count_ffprobe_cannot_state_is_ranked_by_duration_instead() -> None:
    """`N/A` for a count and for a duration ranks as nothing, never as an error: the stream with a
    stated duration wins over one that states neither."""
    unstated = _video(nb_frames="N/A", duration="N/A")
    timed = _video(duration="2.5")
    flagged = _video(nb_frames=True, duration=-1)

    assert media.the_moving_picture([unstated, timed, flagged]) is timed
    assert media.the_moving_picture([flagged, unstated]) is flagged, "a tie keeps the first"


def test_a_stream_that_is_not_among_the_pictures_is_named_as_the_first() -> None:
    """`v` with no number is what every command named before a second stream was considered."""
    streams = [_video(), _video()]
    assert media.position_among_pictures(streams, None) == 0
    assert media.position_among_pictures(streams, _video()) == 0, "by identity, not by equality"


@pytest.mark.parametrize("time_base", ["N/A", "", "1", "0/25", "1/0", "-1/25"])
def test_a_clock_ffprobe_does_not_state_is_no_clock(time_base: str) -> None:
    assert media.clock_from(_video(time_base=time_base), "0.000000") is None
    assert media.clock_from(None, "0.000000") is None


def test_a_stated_clock_counts_from_the_files_start() -> None:
    assert media.clock_from(_video(time_base="1/12800"), "1.500000") == media.FrameClock(
        1, 12800, 1_500_000
    )
    assert media.clock_from(_video(time_base="1/25"), None) == media.FrameClock(1, 25, 0)


def test_a_stream_number_below_zero_is_refused_rather_than_written_into_a_command(
    settings: Settings,
) -> None:
    with pytest.raises(ValueError, match="counted from zero"):
        media.moment_files_args(
            SOURCE,
            MOMENTS[:1],
            filters="scale=8:8",
            output=(),
            destinations=[Path("/scratch/a.jpg")],
            settings=settings,
            stream=-1,
        )
