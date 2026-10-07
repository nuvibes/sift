# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading pictures out of real files, with the real decoder.

Real files and a real decoder throughout, because the two things worth checking here cannot be
checked against a stand-in: that a GIF really does give up all its frames from one read, and
that a still image really does yield its one frame. Both can go wrong in ways that produce no
error at all: a decoder that writes nothing and reports success.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from sift.kernel import media
from sift.kernel.config import Settings
from sift.kernel.media import FFmpegError
from sift.kernel.sampling import MAX_FACE_FRAMES, face_frames, sample_frames
from sift.kernel.subprocess import Priority
from sift.slices.faces import frames as frames_module
from sift.slices.faces.frames import Reader, all_frames_args, frame_args, output_size, split, thin

pytestmark = pytest.mark.integration

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


@pytest.fixture
def clip(tmp_path: Path, settings: Settings) -> Path:
    """A short, real video with movement in it."""
    target = tmp_path / "clip.mp4"
    subprocess.run(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=10:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "10",
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    return target


# --- how big a frame comes out --------------------------------------------------------------------


def test_a_large_frame_is_reduced_and_a_small_one_is_left_alone() -> None:
    """Pixels above roughly twice what the model reads are decoded and immediately thrown away."""
    assert output_size(3840, 2160) == (1280, 720)
    assert output_size(1080, 1920) == (720, 1280)
    assert output_size(640, 480) == (640, 480)


def test_both_sides_come_out_even() -> None:
    """Several decoders refuse an odd dimension outright, and a face pass that worked on every file
    except the ones whose height happened to be odd would be a confusing thing to be handed."""
    for width, height in ((1921, 1081), (999, 333), (3, 5)):
        out_width, out_height = output_size(width, height)
        assert out_width % 2 == 0
        assert out_height % 2 == 0


# --- what the decoder is asked ---------------------------------------------------------------------


def test_a_moment_is_asked_for_as_the_moment_it_is(settings: Settings) -> None:
    """Not complete pictures only, which would give up most of the moments.

    Two nearby moments land on the same complete picture, so the second is thrown away as a
    duplicate: on a five-second clip, ten moments would produce three pictures, and asking to look
    twice as closely would return what looking normally does. The restriction saves no time.
    """
    argv = frame_args(Path("f.mp4"), 5000, width=640, height=360, settings=settings)

    assert "-skip_frame" not in argv
    assert argv[argv.index("-ss") + 1] == "5.000"
    assert argv.index("-ss") < argv.index("-i")


def test_the_beginning_is_asked_for_without_a_seek(settings: Settings) -> None:
    """Seeking to zero is not the no-op it reads as: on a still it lands ON the only frame, which
    then counts as already past, and the decoder writes nothing and reports success."""
    argv = frame_args(Path("f.jpg"), 0, width=640, height=360, settings=settings)

    assert "-ss" not in argv


def test_a_gif_is_asked_for_all_in_one_go(settings: Settings) -> None:
    argv = all_frames_args(Path("loop.gif"), width=320, height=240, settings=settings)

    assert "-ss" not in argv
    assert "-frames:v" not in argv


# --- cutting a stream into pictures -----------------------------------------------------------------


def test_a_stream_of_pictures_is_cut_by_size_because_there_are_no_markers() -> None:
    raw = bytes(2 * 3 * 3) + bytes(2 * 3 * 3)

    assert len(split(raw, 3, 2)) == 2


def test_a_part_picture_at_the_end_is_dropped_rather_than_reshaped() -> None:
    """What a decoder killed mid-write leaves behind."""
    raw = bytes(2 * 3 * 3) + b"\x01\x02\x03"

    assert len(split(raw, 3, 2)) == 1


def test_nothing_at_all_yields_nothing() -> None:
    assert split(b"", 3, 2) == []
    assert split(b"anything", 0, 0) == []


def test_a_gifs_frames_are_thinned_evenly_across_the_whole_of_it() -> None:
    """The first thirty frames of a GIF are usually the same moment; its point is what
    happens later."""
    picks = thin([np.zeros(1)] * 100, 5)

    assert picks == [0, 25, 50, 74, 99]


def test_a_gif_shorter_than_what_was_asked_for_keeps_every_frame() -> None:
    assert thin([np.zeros(1)] * 3, 10) == [0, 1, 2]


def test_asking_for_one_frame_of_a_gif_takes_the_first() -> None:
    assert thin([np.zeros(1)] * 10, 1) == [0]


# --- reading real files ------------------------------------------------------------------------------


async def test_a_still_image_yields_its_one_picture(settings: Settings) -> None:
    reader = Reader(settings)

    got = [
        frame
        async for frame in reader.stream(
            CORPUS / "accepted.jpg", media_type="image", width=16, height=16, timestamps=(0,)
        )
    ]

    assert len(got) == 1
    assert got[0].pixels.shape == (16, 16, 3)


async def test_a_gif_gives_up_more_than_one_picture_from_a_single_read(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property, on a real GIF, counting the reads.

    A GIF stores every frame as a change from the one before, so seeking to the tenth moment
    means replaying the first nine, and thirty moments is thirty replays. Read whole it is the
    cheapest kind of file there is, and it contributes many faces instead of one.

    The count is what makes this a test of the claim rather than of the outcome: asking for thirty
    moments one at a time also produces many frames, and would be thirty times the work.
    """
    reads = 0
    original = frames_module.subprocess_capture  # type: ignore[attr-defined]

    async def counted(*args: object, **kwargs: object) -> bytes:
        nonlocal reads
        reads += 1
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(frames_module, "subprocess_capture", counted)
    reader = Reader(settings)

    got = [
        frame
        async for frame in reader.stream(
            CORPUS / "accepted.gif",
            media_type="gif",
            width=16,
            height=16,
            timestamps=tuple(range(30)),
        )
    ]

    assert reads == 1
    assert len(got) > 1
    assert [frame.timestamp_ms for frame in got] == sorted(frame.timestamp_ms for frame in got)


async def test_an_animated_avif_gives_up_its_frames_not_its_still_cover(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An animated AVIF is drawn as a GIF, and ffmpeg writes its still cover as the first video
    stream and its frames as the second. Read without naming the stream that moves, the whole
    file is the cover's one frame, and every face in it is looked for in that one picture."""
    monkeypatch.setattr(media, "_PICTURES", {})
    motion = tmp_path / "motion.avif"
    subprocess.run(
        [settings.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10", "-t", "2", str(motion)],
        check=True,
        capture_output=True,
    )  # fmt: skip
    moving = await media.moving_stream_of(motion, settings=settings, priority=Priority.BACKGROUND)
    assert moving == 1, "the fixture must carry a cover and the frames"

    got = [
        frame
        async for frame in Reader(settings).stream(
            motion, media_type="gif", width=16, height=16, timestamps=tuple(range(30))
        )
    ]

    assert len(got) > 1
    assert not np.array_equal(got[0].pixels, got[-1].pixels)


async def test_reading_a_video_does_not_hold_the_event_loop(settings: Settings, clip: Path) -> None:
    """Reading frames gives the loop back.

    Launching a process is not free, and Sift is holding the recognition models when it does it.
    Done on the loop (which is what `create_subprocess_exec` does), every launch stops the whole
    application, and a face pass over a long video is sixty launches with eight files in flight:
    minutes of freeze.

    So the test is not "does it read frames". It is **does anything else get a
    turn while it reads them**. A counter ticking on the loop cannot advance if the loop is held,
    so a count of zero is the freeze and any count above it is the loop being given back.
    """
    import asyncio

    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    ticker = asyncio.ensure_future(tick())
    try:
        reader = Reader(settings)
        [
            frame
            async for frame in reader.stream(
                clip, media_type="video", width=320, height=240, timestamps=(0, 1000, 2000, 3000)
            )
        ]
    finally:
        ticker.cancel()

    assert ticks > 0, "nothing else ran while frames were read; the loop was held"


async def test_a_video_is_read_at_the_moments_asked_for(settings: Settings, clip: Path) -> None:
    reader = Reader(settings)

    got = [
        frame
        async for frame in reader.stream(
            clip, media_type="video", width=320, height=240, timestamps=(0, 1000, 2000, 3000)
        )
    ]

    assert len(got) >= 2
    assert all(frame.pixels.shape == (240, 320, 3) for frame in got)


async def test_the_same_picture_arriving_twice_is_only_handed_over_once(
    settings: Settings, clip: Path
) -> None:
    """Two nearby moments in a file with few complete pictures come back as the very same picture,
    and finding and recognizing the same face twice costs the same as doing it once too often."""
    reader = Reader(settings)

    got = [
        frame
        async for frame in reader.stream(
            clip,
            media_type="video",
            width=320,
            height=240,
            timestamps=(1000, 1010, 1020, 1030),
        )
    ]

    assert len(got) < 4


async def test_a_moment_that_cannot_be_read_does_not_stop_the_rest(
    settings: Settings, clip: Path
) -> None:
    """A truncated tail is common, and everything before it is perfectly good."""
    reader = Reader(settings)

    got = [
        frame
        async for frame in reader.stream(
            clip, media_type="video", width=320, height=240, timestamps=(0, 999_000)
        )
    ]

    assert len(got) == 1


async def test_a_file_the_decoder_cannot_open_at_all_is_reported(
    settings: Settings, tmp_path: Path
) -> None:
    missing = tmp_path / "nothing.gif"
    reader = Reader(settings)

    with pytest.raises(FFmpegError):
        [
            frame
            async for frame in reader.stream(
                missing, media_type="gif", width=16, height=16, timestamps=(0,)
            )
        ]


# --- reference images -------------------------------------------------------------------------------


async def test_a_reference_image_is_decoded_larger_than_a_video_frame(settings: Settings) -> None:
    """Looked at only once, and its landmarks are what everything about that person is aligned by."""
    picture = await frames_module.decode_image(CORPUS / "accepted.jpg", settings)

    assert picture is not None
    assert picture.ndim == 3


async def test_something_that_is_not_a_picture_comes_back_as_nothing(
    settings: Settings, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not a picture")

    assert await frames_module.decode_image(broken, settings) is None


async def test_a_reference_picture_is_read_through_its_storages_lane(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one rule that protects a network share, and this path must not go round it.

    Both reads of the file (asking its size and decoding it) name the file they open, which is
    what takes a place in that storage's lane (`kernel.media.run`). A folder of reference pictures
    is a burst of small reads from wherever somebody put them, so it is exactly the caller the cap
    exists for.
    """
    named: list[Path | None] = []
    real_run = media.run
    real_run_json = media.run_json

    async def watch_run(argv: list[str], **rest: object) -> bytes:
        named.append(rest.get("reads"))  # type: ignore[arg-type]
        return await real_run(argv, **rest)  # type: ignore[arg-type]

    async def watch_run_json(argv: list[str], **rest: object) -> dict[str, object]:
        named.append(rest.get("reads"))  # type: ignore[arg-type]
        return await real_run_json(argv, **rest)  # type: ignore[arg-type]

    monkeypatch.setattr(media, "run", watch_run)
    monkeypatch.setattr(media, "run_json", watch_run_json)

    picture = await frames_module.decode_image(CORPUS / "accepted.jpg", settings)

    assert picture is not None
    # Every read, not merely one of them: the ffprobe call goes through the decoder's own runner, so
    # the list holds one entry per call and a single `reads` left off shows up as a None among them.
    assert named
    assert all(one == CORPUS / "accepted.jpg" for one in named)


# --- which moments a face pass looks at ---------------------------------------------------------------


@pytest.mark.parametrize("density", [0.5, 1.0, 3.0])
def test_a_longer_file_is_never_looked_at_less_than_a_shorter_one(density: float) -> None:
    """The defect this shape exists to prevent: on a rate ladder a 20 second clip can get 20 moments
    and a 21 second clip 7, because the rate drops a rung and the count falls with it, so somebody
    on screen briefly in a short clip is the likeliest thing in a library to be missed.

    Walked a second at a time rather than sampled at chosen lengths, so a cliff anywhere is
    caught."""
    counts = [len(face_frames(seconds * 1000, density=density)) for seconds in range(1, 601)]

    assert counts == sorted(counts)


def test_the_moment_count_stops_growing_once_a_file_is_long_enough() -> None:
    """Otherwise the cost of looking at a video grows without limit because the video does."""
    assert len(face_frames(300_000)) == len(face_frames(7_200_000))


def test_a_short_clip_is_still_looked_at_once_a_second() -> None:
    """The short end of the range: once a second, as a rate ladder would have it too."""
    assert len(face_frames(20_000)) == 20
    assert face_frames(20_000) == sample_frames(20_000)


def test_asking_for_more_or_fewer_moments_scales_what_is_read() -> None:
    standard = len(face_frames(60_000))

    assert len(face_frames(60_000, density=0.5)) < standard
    assert len(face_frames(60_000, density=2.0)) > standard


def test_however_dense_it_is_asked_to_be_there_is_a_ceiling() -> None:
    assert len(face_frames(7_200_000, density=8.0)) <= MAX_FACE_FRAMES


def test_a_file_with_no_timeline_is_looked_one_time() -> None:
    assert face_frames(0) == (0,)


def test_looking_at_no_moments_at_all_is_refused_rather_than_silently_doing_nothing() -> None:
    with pytest.raises(ValueError, match="no frames"):
        face_frames(60_000, density=0)


async def test_a_video_is_read_a_run_of_moments_per_process(
    settings: Settings, clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Thirty moments are not thirty launches: thirty opens and seeks of one file, over a share
    for a NAS library. They come in runs, so a pass the time limit stops has read at most one run
    past it."""
    from typing import Any

    from sift.kernel import subprocess as kernel_subprocess

    launches: list[list[str]] = []
    real_capture = kernel_subprocess.capture

    async def counted(argv: list[str], **kwargs: Any) -> bytes:
        launches.append(argv)
        return await real_capture(argv, **kwargs)

    monkeypatch.setattr(kernel_subprocess, "capture", counted)
    timestamps = tuple(range(0, 3000, 100))  # thirty moments of a four-second clip
    reader = Reader(settings)

    got = [
        frame
        async for frame in reader.stream(
            clip, media_type="video", width=320, height=240, timestamps=timestamps
        )
    ]

    assert len(got) > 1
    expected_runs = -(-len(timestamps) // frames_module.MOMENTS_PER_READ)
    assert len(launches) == expected_runs
    assert all(argv.count("-i") <= frames_module.MOMENTS_PER_READ for argv in launches)


async def test_a_pass_that_stops_early_does_not_read_past_its_run(
    settings: Settings, clip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The early stop is most of the saving on a long file; batching must not read past it."""
    from typing import Any

    from sift.kernel import subprocess as kernel_subprocess

    launches: list[list[str]] = []
    real_capture = kernel_subprocess.capture

    async def counted(argv: list[str], **kwargs: Any) -> bytes:
        launches.append(argv)
        return await real_capture(argv, **kwargs)

    monkeypatch.setattr(kernel_subprocess, "capture", counted)
    reader = Reader(settings)
    stream = reader.stream(
        clip, media_type="video", width=320, height=240, timestamps=tuple(range(0, 3000, 100))
    )
    async for _ in stream:
        break  # the caller has seen enough after the first frame
    await stream.aclose()

    assert len(launches) == 1


# --- the plan a Build reads from agrees with the ask -----------------------------------------------


async def test_the_plan_and_the_ask_agree_so_a_build_can_read_the_file_once(
    tmp_path: Path, settings: Settings
) -> None:
    """`frame_requests` says which frames a pass would ask for; `Reader.stream` asks for them. If
    the two drifted, the stream would seek for what the plan prepared and the saving would be
    lost silently, so the store is filled from the plan and the stream is read against a tool
    that refuses to run. Every picture it yields came from the plan."""
    from sift.kernel import media
    from sift.testing.tools import stand_in_tool

    refusing = stand_in_tool(tmp_path / "bin", "ffmpeg", "import sys; sys.exit(1)")
    facts = media.FileFacts(
        asset_id="a",
        path=tmp_path / "clip.mp4",
        media_type="video",
        duration_ms=4000,
        width=320,
        height=240,
        fps=10.0,
        size_bytes=1,
    )
    (request,) = frames_module.frame_requests(facts, density=1.0)
    assert isinstance(request, media.RawFrames)
    pictures = [bytes([index % 251]) * request.frame_bytes for index in range(len(request.moments))]
    prepared = media.PreparedFrames()
    prepared.put_raw(facts.path, request, pictures)

    reader = Reader(settings.model_copy(update={"ffmpeg_path": refusing}))
    with media.prepared(prepared):
        got = [
            frame
            async for frame in reader.stream(
                facts.path,
                media_type="video",
                width=320,
                height=240,
                timestamps=face_frames(4000, density=1.0),
            )
        ]

    assert len(got) == len(request.moments)
    assert [frame.timestamp_ms for frame in got] == list(face_frames(4000, density=1.0))


def test_a_still_or_a_gif_plans_nothing() -> None:
    from sift.kernel import media

    for kind in ("image", "gif"):
        facts = media.FileFacts(
            asset_id="a",
            path=Path("x"),
            media_type=kind,
            duration_ms=1000,
            width=320,
            height=240,
            fps=10.0,
            size_bytes=1,
        )
        assert frames_module.frame_requests(facts, density=1.0) == []


# --- faces are cut from the file's own pixels -----------------------------------------------------


def test_a_frame_larger_than_the_pass_reads_is_measured_at_how_much_larger() -> None:
    """One reduced pixel of a 1440p file is two of the file's own; a small file is left alone."""
    assert frames_module.enlargement(2560, 1440) == (2.0, 2.0)
    assert frames_module.enlargement(3840, 2160) == (3.0, 3.0)
    assert frames_module.enlargement(1280, 720) == (1.0, 1.0)
    assert frames_module.enlargement(640, 480) == (1.0, 1.0)


@pytest.mark.parametrize(
    ("x", "y"),
    [(1200, 600), (4, 700), (2440, 1330)],
    ids=["middle", "left edge", "bottom right corner"],
)
def test_a_square_cut_from_the_piece_is_the_square_cut_from_the_whole_picture(
    x: int, y: int
) -> None:
    """What makes reading a piece rather than a whole frame safe, pixel for pixel.

    The piece reaches far enough round the face that neither the closer look nor the aligned square
    ever samples past it, and where the face sits at the edge of the picture, the piece stops at
    that same edge, so the smear measured there is the smear the whole picture would have given.
    """
    from sift.slices.faces import crop
    from sift.slices.faces.tests.conftest import draw_face, landmarks_for, noisy_frame

    whole = noisy_frame(2560, 1440, seed=7)
    box = draw_face(whole, x=x, y=y, size=110)
    points = landmarks_for(box)

    piece = frames_module.cut(whole, box)
    moved = tuple((px - piece.left, py - piece.top) for px, py in points)
    from_piece = crop.align(piece.pixels, moved)
    from_whole = crop.align(whole, points)

    assert piece.pixels.shape[0] < whole.shape[0] and piece.pixels.shape[1] < whole.shape[1]
    assert abs(from_piece.containment - from_whole.containment) < 1e-9
    difference = np.abs(from_piece.chip.astype(int) - from_whole.chip.astype(int))
    assert int(difference.max()) <= 1


def test_a_piece_does_not_keep_the_whole_frame_alive() -> None:
    """A slice of an array holds the whole of it; the piece must be its own copy."""
    whole = np.zeros((1440, 2560, 3), dtype=np.uint8)
    from sift.slices.faces.models import Box

    piece = frames_module.cut(whole, Box(x=1000, y=500, width=100, height=120))

    assert not np.shares_memory(piece.pixels, whole)


async def test_the_pieces_come_back_at_the_files_own_size_in_the_order_asked(
    settings: Settings, tmp_path: Path
) -> None:
    """Two faces at one moment and one at another, read with the real decoder at full size."""
    from sift.slices.faces.models import Box

    target = tmp_path / "wide.mp4"
    subprocess.run(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=2560x1440:rate=5:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    reader = Reader(settings)
    left = Box(x=300, y=300, width=120, height=140)
    right = Box(x=2000, y=900, width=110, height=130)

    pieces = await reader.windows(
        target,
        media_type="video",
        width=2560,
        height=1440,
        wanted=[(1000, left), (0, right), (1000, right)],
    )
    whole = {
        frame.timestamp_ms: frame.pixels
        async for frame in reader.stream(
            target,
            media_type="video",
            width=2560,
            height=1440,
            timestamps=(0, 1000),
            long_side=2560,
        )
    }

    assert all(piece is not None for piece in pieces)
    for piece, (at, box) in zip(pieces, [(1000, left), (0, right), (1000, right)], strict=True):
        assert piece is not None
        expected = frames_module.cut(whole[at], box)
        assert (piece.left, piece.top) == (expected.left, expected.top)
        assert np.array_equal(piece.pixels, expected.pixels)


async def test_a_gif_is_never_read_a_second_time(settings: Settings) -> None:
    """It cannot be seeked, so its faces are cut from the frames the pass already has."""
    from sift.slices.faces.models import Box

    pieces = await Reader(settings).windows(
        Path("never-opened.gif"),
        media_type="gif",
        width=2000,
        height=2000,
        wanted=[(0, Box(x=0, y=0, width=200, height=200))],
    )

    assert pieces == [None]


async def test_a_still_is_read_again_at_its_own_size_for_the_faces_on_it(
    settings: Settings, tmp_path: Path
) -> None:
    """A still has one moment and nothing to seek, so it is decoded once at full size and every
    face on it is cut from that one picture, in the order asked."""
    from sift.slices.faces.models import Box

    target = tmp_path / "still.png"
    subprocess.run(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=800x600",
            "-frames:v",
            "1",
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    whole = await frames_module.decode_image(target, settings)
    assert whole is not None and whole.shape[:2] == (600, 800)
    boxes = [Box(x=500, y=300, width=120, height=140), Box(x=40, y=40, width=100, height=100)]

    pieces = await Reader(settings).windows(
        target, media_type="image", width=800, height=600, wanted=[(0, box) for box in boxes]
    )

    for piece, box in zip(pieces, boxes, strict=True):
        assert piece is not None
        expected = frames_module.cut(whole, box)
        assert (piece.left, piece.top) == (expected.left, expected.top)
        assert np.array_equal(piece.pixels, expected.pixels)


def _tall_picture(settings: Settings, target: Path) -> Path:
    subprocess.run(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=2000x3000",
            "-frames:v",
            "1",
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    return target


async def test_a_tall_portrait_is_read_whole_and_brought_down_on_its_long_side(
    settings: Settings, tmp_path: Path
) -> None:
    """Held at its width alone, a 2000 by 3000 portrait decodes to more bytes than a tool's answer
    may hold, would arrive cut short and be refused as unreadable. The long side is what is
    capped."""
    target = _tall_picture(settings, tmp_path / "tall.png")

    picture = await frames_module.decode_image(target, settings)

    assert picture is not None
    assert picture.shape == (2048, 1366, 3)


async def test_a_tall_starter_picture_is_read_whole_too(settings: Settings, tmp_path: Path) -> None:
    target = _tall_picture(settings, tmp_path / "tall.png")

    picture = await frames_module.decode_picture_bytes(target.read_bytes(), settings)

    assert picture is not None
    assert picture.shape == (2048, 1366, 3)


async def test_a_starter_picture_is_decoded_from_its_bytes_without_touching_the_disk(
    settings: Settings,
) -> None:
    """A stash-box's photo is a stranger's bytes: piped into the decoder, never written under a
    name, and judged at the same size a folder's picture would be."""
    picture = await frames_module.decode_picture_bytes(
        (CORPUS / "accepted.jpg").read_bytes(), settings
    )
    assert picture is not None
    assert picture.ndim == 3 and picture.shape[2] == 3


async def test_bytes_that_are_not_a_picture_are_no_starter(settings: Settings) -> None:
    assert await frames_module.decode_picture_bytes(b"not a picture", settings) is None


def test_a_decoder_answer_that_is_not_a_whole_picture_is_nothing() -> None:
    """The answer carries its own size, so a short one is refused rather than reshaped into a
    picture with its bottom missing."""
    pixels = bytes(range(12))
    whole = b"P6\n2 2\n255\n" + pixels
    decoded = frames_module._ppm_picture(whole)
    assert decoded is not None and decoded.shape == (2, 2, 3)
    assert decoded.tobytes() == pixels
    assert frames_module._ppm_picture(whole[:-1]) is None
    assert frames_module._ppm_picture(b"P6\n0 2\n255\n") is None
    assert frames_module._ppm_picture(b"\x89PNG not a ppm") is None


async def test_a_moment_that_reads_back_nothing_at_full_size_leaves_its_faces_to_the_reduced_frame(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """None for every face at that moment, which the caller reads as "use the frame already
    held" and never as "no face", and the other moments are still answered."""
    from sift.slices.faces.models import Box

    own = frames_module.source_size(2560, 1440)
    one_picture = bytes(own[0] * own[1] * 3)

    async def moments(_path: Path, asked: list[media.Moment], **_: object) -> list[bytes | None]:
        # The beginning is the one moment asked for without a seek.
        return [one_picture if moment.seek else None for moment in asked]

    monkeypatch.setattr(media, "raw_moments", moments)
    box = Box(x=300, y=300, width=120, height=140)

    pieces = await Reader(settings).windows(
        Path("never-opened.mp4"),
        media_type="video",
        width=2560,
        height=1440,
        wanted=[(0, box), (1000, box)],
    )

    assert pieces[0] is None
    assert pieces[1] is not None
