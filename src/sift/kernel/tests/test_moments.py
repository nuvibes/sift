# SPDX-License-Identifier: AGPL-3.0-or-later
"""Many moments of one file from one process, and the per-moment path it falls back to.

Two kinds of test. The pure ones check the command that is built: one input per moment, each with
its own seek, the frame-keeping chain on every input, chunks that fit a command line. The ones
marked integration run the real ffmpeg on a clip built for the purpose and assert the batched
answer is BYTE-IDENTICAL to the one-process-per-moment answer, which is the whole contract, since
a fingerprint that is quietly different matches nothing and says nothing.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from sift.kernel import media
from sift.kernel.config import Settings
from sift.kernel.media import Moment
from sift.kernel.subprocess import Priority
from sift.testing.tools import stand_in_tool

SOURCE = Path("/library/clip.mp4")


def _moments(count: int) -> list[Moment]:
    return [Moment(seek=() if i == 0 else ("-ss", f"{i}.000")) for i in range(count)]


def test_the_stream_names_one_input_per_moment_with_its_own_seek(settings: Settings) -> None:
    argv = media.raw_stream_args(
        SOURCE, _moments(3), filters="scale=32:32", pixel_format="gray", settings=settings
    )
    assert argv.count("-i") == 3
    # The first moment is the beginning and is not seeked to; the other two are.
    assert argv[argv.index("-i") - 1] != "-ss"
    assert argv.count("-ss") == 2
    assert "2.000" in argv


def test_the_stream_keeps_exactly_the_first_frame_of_every_input(settings: Settings) -> None:
    argv = media.raw_stream_args(
        SOURCE, _moments(4), filters="scale=32:32", pixel_format="gray", settings=settings
    )
    graph = argv[argv.index("-filter_complex") + 1]
    assert graph.count("trim=end_frame=1,setpts=PTS-STARTPTS,scale=32:32") == 4
    assert "concat=n=4:v=1:a=0[out]" in graph
    # Without this the muxer sees four frames on one clock and keeps two of them.
    assert argv[argv.index("-fps_mode") + 1] == "passthrough"
    assert argv[-3:] == ["-f", "rawvideo", "pipe:1"]


def test_the_files_form_maps_each_input_to_its_own_destination(settings: Settings) -> None:
    into = Path("/scratch")
    argv = media.moment_files_args(
        SOURCE,
        _moments(2),
        filters="scale=160:-2",
        output=["-q:v", "8"],
        destinations=[into / "0000.jpg", into / "0001.jpg"],
        settings=settings,
    )
    assert argv.count("-i") == 2
    first = argv.index("-map")
    assert argv[first : first + 6] == ["-map", "0:v", "-frames:v", "1", "-vf", "scale=160:-2"]
    assert str(into / "0000.jpg") in argv and str(into / "0001.jpg") in argv
    assert argv.index(str(into / "0000.jpg")) < argv.index(str(into / "0001.jpg"))


def test_a_chunk_fits_the_command_line_and_shrinks_with_a_long_path() -> None:
    short = media.chunk_size(Path("c.mp4"), _moments(400))
    long = media.chunk_size(Path("S:/" + "a" * 500 + "/clip.mp4"), _moments(400))
    assert 1 <= long < short
    # Never past the budget, whatever the path: the estimate is the path plus the seek plus a
    # margin for the rest of the line, per input.
    assert long * (len("S:/" + "a" * 500 + "/clip.mp4") + 10) < media.COMMAND_LINE_BUDGET


def test_a_chunk_is_never_smaller_than_one_moment() -> None:
    assert media.chunk_size(Path("x" * 40_000), _moments(3)) == 1


# --- a chunk also fits in memory -----------------------------------------------------------------
#
# Every seeked input is a decoder holding pictures at the file's full size, so a hundred inputs of
# a 4K file in one process are tens of gigabytes, enough to starve the database behind them of
# memory.

UHD = media.Picture(width=3840, height=2160, bytes_per_pixel=1.5)


def test_every_input_gets_the_thread_share_and_not_only_the_first(settings: Settings) -> None:
    """An input option reaches the one input after it. With the share written once, before the
    first input, every other decoder started a thread and a picture in flight per processor."""
    argv = media.raw_stream_args(
        SOURCE, _moments(3), filters="scale=32:32", pixel_format="gray", settings=settings
    )
    share = str(media.background_threads(settings))
    inputs = [i for i, one in enumerate(argv) if one == "-i"]
    assert len(inputs) == 3
    for at in inputs:
        assert argv[at - 2 : at] == ["-threads", share], argv[at - 4 : at + 2]


def test_a_bigger_picture_or_a_deeper_one_fits_fewer_inputs() -> None:
    budget = 2 << 30
    uhd = media.moments_in_memory(UHD, threads=2, budget=budget)
    full_hd = media.moments_in_memory(
        media.Picture(width=1920, height=1080, bytes_per_pixel=1.5), threads=2, budget=budget
    )
    deep = media.moments_in_memory(
        media.Picture(width=3840, height=2160, bytes_per_pixel=3.0), threads=2, budget=budget
    )
    assert full_hd >= 4 * uhd
    assert 1 <= deep < uhd
    # A 4K H.264 input at two threads holds about 220 MB. The estimate is above it.
    assert media.input_bytes(UHD, threads=2) >= 220 * 2**20
    assert media.moments_in_memory(UHD, threads=2, budget=1) == 1


@pytest.mark.parametrize(
    ("stream", "expected"),
    [
        ({"width": 3840, "height": 2160, "pix_fmt": "yuv420p"}, UHD),
        (
            {"width": 1920, "height": 1080, "pix_fmt": "yuv420p10le"},
            media.Picture(width=1920, height=1080, bytes_per_pixel=3.0),
        ),
        (
            {"width": 640, "height": 480, "pix_fmt": "yuv444p"},
            media.Picture(width=640, height=480, bytes_per_pixel=3.0),
        ),
        (
            {"width": 640, "height": 480, "pix_fmt": "yuv422p"},
            media.Picture(width=640, height=480, bytes_per_pixel=2.0),
        ),
        ({"width": 0, "height": 480, "pix_fmt": "yuv420p"}, None),
        ({"pix_fmt": "yuv420p"}, None),
    ],
)
def test_the_picture_is_read_from_what_ffprobe_says(
    stream: dict[str, object], expected: media.Picture | None
) -> None:
    assert media.picture_from(stream) == expected


async def test_a_file_ffprobe_cannot_read_has_no_picture_and_is_asked_once_per_version(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal is an answer too: the read is then sized by its command line alone, and the same
    version of the same file is not put to ffprobe again for every chunk of every pass."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"not a video")
    asked: list[list[str]] = []

    async def refuses(argv: list[str], **_kwargs: object) -> dict[str, object]:
        asked.append(argv)
        raise media.FFmpegError("Invalid data found when processing input")

    monkeypatch.setattr(media, "_PICTURES", {})
    monkeypatch.setattr(media, "run_json", refuses)

    first = await media.picture_of(source, settings=settings, priority=Priority.BACKGROUND)
    again = await media.picture_of(source, settings=settings, priority=Priority.BACKGROUND)

    assert (first, again) == (None, None)
    assert len(asked) == 1 and asked[0][-1] == str(source)


async def test_the_pictures_remembered_are_bounded_and_the_oldest_is_forgotten_first(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long pass over a big library must not hold one entry per file it ever read: past the
    bound the oldest goes, and a file forgotten is simply asked about again."""
    asked: list[str] = []

    async def answers(argv: list[str], **_kwargs: object) -> dict[str, object]:
        asked.append(Path(argv[-1]).name)
        return {"streams": [{"width": 3840, "height": 2160, "pix_fmt": "yuv420p"}]}

    monkeypatch.setattr(media, "_PICTURES", {})
    monkeypatch.setattr(media, "_PICTURES_KEPT", 2)
    monkeypatch.setattr(media, "run_json", answers)
    files = []
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        files.append(tmp_path / name)
        files[-1].write_bytes(name.encode())

    for one in (*files, files[2], files[0]):
        assert await media.picture_of(one, settings=settings, priority=Priority.BACKGROUND) == UHD

    assert asked == ["a.mp4", "b.mp4", "c.mp4", "a.mp4"]
    assert len(media._PICTURES) == 2


async def test_a_read_is_cut_into_chunks_that_fit_the_memory_planned_for_it(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seven moments of a 4K file with room for three decoders: three processes, of three, three
    and one moment, never one process holding all seven."""
    log = tmp_path / "calls.txt"
    tool = stand_in_tool(
        tmp_path / "bin",
        "ffmpeg",
        f"""
        import sys
        argv = sys.argv[1:]
        with open({str(log)!r}, "a") as handle:
            handle.write(str(argv.count("-i")) + "\\n")
        sys.stdout.buffer.write(b"\\x01" * argv.count("-i"))
        """,
    )

    async def four_k(*_args: object, **_kwargs: object) -> media.Picture:
        return UHD

    threads = media.background_threads(settings)
    room = 3 * media.input_bytes(UHD, threads=threads) + 1
    monkeypatch.setattr(media, "picture_of", four_k)
    monkeypatch.setattr("sift.kernel.subprocess.planned_memory", lambda _at_once: room)

    read = await media.raw_moments(
        SOURCE,
        _moments(7),
        filters="scale=1:1",
        pixel_format="gray",
        frame_bytes=1,
        settings=settings.model_copy(update={"ffmpeg_path": tool}),
        time_limit=30,
    )

    assert read == [b"\x01"] * 7
    assert log.read_text().split() == ["3", "3", "1"]


def test_no_moments_asks_for_nothing(settings: Settings) -> None:
    import asyncio

    assert (
        asyncio.run(
            media.raw_moments(
                SOURCE,
                [],
                filters="scale=1:1",
                pixel_format="gray",
                frame_bytes=1,
                settings=settings,
                time_limit=1,
            )
        )
        == []
    )
    assert (
        asyncio.run(
            media.moments_to_files(
                SOURCE,
                [],
                into=Path("/nowhere"),
                suffix=".jpg",
                filters="scale=1:1",
                output=[],
                settings=settings,
                time_limit=1,
            )
        )
        == []
    )


async def test_a_short_stream_is_read_again_one_moment_at_a_time(
    tmp_path: Path, settings: Settings
) -> None:
    """The one property that makes batching safe for a fingerprint: a moment that reads nothing
    keeps its POSITION. The stand-in answers the batched command short and the per-moment command
    per moment, with the second moment empty."""
    log = tmp_path / "calls.txt"
    tool = stand_in_tool(
        tmp_path / "bin",
        "ffmpeg",
        f"""
        import sys
        argv = sys.argv[1:]
        with open({str(log)!r}, "a") as handle:
            handle.write(str(argv.count("-i")) + "\\n")
        if "-filter_complex" in argv:
            sys.stdout.buffer.write(b"\\x01" * 4)  # two frames of three
            sys.exit(0)
        if "-ss" in argv and argv[argv.index("-ss") + 1] == "1.000":
            sys.exit(0)  # the second moment is past the end: nothing comes out
        sys.stdout.buffer.write(b"\\x07" * 2)
        """,
    )
    read = await media.raw_moments(
        SOURCE,
        _moments(3),
        filters="scale=1:2",
        pixel_format="gray",
        frame_bytes=2,
        settings=settings.model_copy(update={"ffmpeg_path": tool}),
        time_limit=30,
    )
    assert read == [b"\x07\x07", None, b"\x07\x07"]
    calls = log.read_text().split()
    # One batched attempt with three inputs, then three single-input reads.
    assert calls == ["3", "1", "1", "1"]


async def test_a_whole_stream_is_cut_into_its_moments(tmp_path: Path, settings: Settings) -> None:
    tool = stand_in_tool(
        tmp_path / "bin",
        "ffmpeg",
        """
        import sys
        sys.stdout.buffer.write(bytes(range(6)))
        """,
    )
    read = await media.raw_moments(
        SOURCE,
        _moments(3),
        filters="scale=1:2",
        pixel_format="gray",
        frame_bytes=2,
        settings=settings.model_copy(update={"ffmpeg_path": tool}),
        time_limit=30,
    )
    assert read == [b"\x00\x01", b"\x02\x03", b"\x04\x05"]


async def test_a_file_that_did_not_come_out_is_none_in_its_place(
    tmp_path: Path, settings: Settings
) -> None:
    tool = stand_in_tool(
        tmp_path / "bin",
        "ffmpeg",
        """
        import sys
        from pathlib import Path
        argv = sys.argv[1:]
        # Write every destination but the one for the second input. A destination is the
        # last argument of its output block, which is the first .jpg after its -map.
        maps = [i for i, one in enumerate(argv) if one == "-map"]
        for at in maps:
            destination = next(one for one in argv[at:] if one.endswith(".jpg"))
            if argv[at + 1] == "1:v":
                continue
            Path(destination).write_bytes(b"jpeg")
        """,
    )
    into = tmp_path / "out"
    into.mkdir()
    files = await media.moments_to_files(
        SOURCE,
        _moments(3),
        into=into,
        suffix=".jpg",
        filters="scale=160:-2",
        output=["-q:v", "8"],
        settings=settings.model_copy(update={"ffmpeg_path": tool}),
        time_limit=30,
    )
    assert files == [into / "0000.jpg", None, into / "0002.jpg"]


async def test_a_refused_chunk_of_files_is_read_one_moment_at_a_time(
    tmp_path: Path, settings: Settings
) -> None:
    log = tmp_path / "calls.txt"
    tool = stand_in_tool(
        tmp_path / "bin",
        "ffmpeg",
        f"""
        import sys
        from pathlib import Path
        argv = sys.argv[1:]
        with open({str(log)!r}, "a") as handle:
            handle.write(str(argv.count("-i")) + "\\n")
        if argv.count("-i") > 1:
            sys.stderr.write("cannot"); sys.exit(1)
        Path(argv[-1]).write_bytes(b"jpeg")
        """,
    )
    into = tmp_path / "out"
    into.mkdir()
    files = await media.moments_to_files(
        SOURCE,
        _moments(2),
        into=into,
        suffix=".jpg",
        filters="scale=160:-2",
        output=[],
        settings=settings.model_copy(update={"ffmpeg_path": tool}),
        time_limit=30,
    )
    assert files == [into / "0000.jpg", into / "0001.jpg"]
    assert log.read_text().split() == ["2", "1", "1"]


# --- the real tool: batched is the same bytes as one process per moment -----------------------------


@pytest.fixture
def clip(tmp_path: Path, settings: Settings) -> Path:
    if shutil.which(settings.ffmpeg_path) is None and not Path(settings.ffmpeg_path).exists():
        pytest.skip("no ffmpeg on this machine")
    target = tmp_path / "clip.mp4"
    subprocess.run(
        [
            settings.ffmpeg_path,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=25:duration=12",
            "-c:v",
            "libx264",
            "-g",
            "50",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    return target


@pytest.mark.integration
async def test_the_stream_is_byte_identical_to_one_process_per_moment(
    clip: Path, settings: Settings
) -> None:
    moments = [Moment(seek=() if i == 0 else ("-ss", f"{i * 0.37:.3f}")) for i in range(30)]
    batched = await media.raw_moments(
        clip,
        moments,
        filters="scale=32:32:flags=bilinear",
        pixel_format="gray",
        frame_bytes=32 * 32,
        settings=settings,
        time_limit=120,
    )
    singly = []
    for moment in moments:
        argv = media.raw_frame_args(
            clip,
            moment,
            filters="scale=32:32:flags=bilinear",
            pixel_format="gray",
            settings=settings,
        )
        singly.append(await media.run(argv, time_limit=60, capture=True))
    assert len(batched) == 30 and all(one is not None for one in batched)
    assert batched == singly


@pytest.mark.integration
async def test_the_files_are_byte_identical_to_one_process_per_moment(
    clip: Path, tmp_path: Path, settings: Settings
) -> None:
    moments = [Moment(seek=() if i == 0 else ("-ss", f"{i * 0.9:.3f}")) for i in range(12)]
    into = tmp_path / "batched"
    into.mkdir()
    files = await media.moments_to_files(
        clip,
        moments,
        into=into,
        suffix=".jpg",
        filters="scale=min(160\\,iw):-2",
        output=["-q:v", "8"],
        settings=settings,
        time_limit=120,
    )
    alone = tmp_path / "alone"
    alone.mkdir()
    for index, moment in enumerate(moments):
        argv = media.moment_files_args(
            clip,
            [moment],
            filters="scale=min(160\\,iw):-2",
            output=["-q:v", "8"],
            destinations=[alone / f"{index:04d}.jpg"],
            settings=settings,
        )
        await media.run(argv, time_limit=60)
    assert all(one is not None for one in files)
    for index, one in enumerate(files):
        assert one is not None
        assert one.read_bytes() == (alone / f"{index:04d}.jpg").read_bytes()


@pytest.mark.integration
async def test_a_moment_past_the_end_keeps_its_place(clip: Path, settings: Settings) -> None:
    """The twelve-second clip is asked about at fifty seconds in the middle of the run."""
    moments = [
        Moment(seek=("-ss", "1.000")),
        Moment(seek=("-ss", "50.000")),
        Moment(seek=("-ss", "2.000")),
    ]
    read = await media.raw_moments(
        clip,
        moments,
        filters="scale=32:32:flags=bilinear",
        pixel_format="gray",
        frame_bytes=32 * 32,
        settings=settings,
        time_limit=120,
    )
    assert read[1] is None
    assert read[0] is not None and read[2] is not None and read[0] != read[2]


async def test_a_moment_the_tool_refuses_outright_is_none_in_its_place(
    tmp_path: Path, settings: Settings
) -> None:
    """The per-moment path carries on past one refusal, as reading the moments one at a time
    does: a truncated tail is common and the rest is perfectly good."""
    tool = stand_in_tool(
        tmp_path / "bin",
        "ffmpeg",
        """
        import sys
        argv = sys.argv[1:]
        if "-filter_complex" in argv:
            sys.exit(1)  # the batched form refuses outright
        if "-ss" in argv and argv[argv.index("-ss") + 1] == "1.000":
            sys.stderr.write("cannot decode"); sys.exit(1)
        sys.stdout.buffer.write(b"\\x07" * 2)
        """,
    )
    read = await media.raw_moments(
        SOURCE,
        _moments(3),
        filters="scale=1:2",
        pixel_format="gray",
        frame_bytes=2,
        settings=settings.model_copy(update={"ffmpeg_path": tool}),
        time_limit=30,
    )
    assert read == [b"\x07\x07", None, b"\x07\x07"]


# --- One decode for every moment of every consumer ------------------------------------------------


def _raw(moments: Sequence[Moment]) -> media.RawFrames:
    return media.RawFrames(
        moments=tuple(moments),
        filters="scale=32:32:flags=bilinear",
        pixel_format="gray",
        frame_bytes=32 * 32,
    )


def _files(moments: Sequence[Moment]) -> media.FrameFiles:
    return media.FrameFiles(
        moments=tuple(moments), filters="scale=160:-2", suffix=".bmp", output=("-c:v", "bmp")
    )


#: A stream timed in 1/12800 of a second, from a file whose timeline starts at zero.
CLOCK = media.FrameClock(1, 12800, 0)


def test_a_seeks_seconds_are_read_as_whole_microseconds_with_the_rest_dropped() -> None:
    """As the tool reads `-ss`: six digits of fraction kept, any further one dropped, never
    rounded, so a moment written as `18.060000000000002` is 18060000 microseconds."""
    assert media.microseconds_of("1.500") == 1_500_000
    assert media.microseconds_of("18.060000000000002") == 18_060_000
    assert media.microseconds_of("2.0779999999999998") == 2_077_999
    assert media.microseconds_of("7") == 7_000_000
    assert media.microseconds_of("-0.023220") == -23_220
    assert media.microseconds_of("soon") is None
    assert media.microseconds_of("5e-05") is None


def test_the_clock_rounds_to_the_nearest_tick_a_half_away_from_zero() -> None:
    """`av_rescale_q`'s rounding: 1/30 of a second is 426.67 ticks of 1/12800, so 427."""
    assert CLOCK.ticks(33_333) == 427
    assert CLOCK.ticks(-33_333) == -427
    half = media.FrameClock(1, 2, 0)
    assert half.ticks(250_000) == 1 and half.ticks(-250_000) == -1


def test_a_file_that_starts_late_is_counted_from_its_start_rounded_twice() -> None:
    """A seek is the file's start plus the moment, rounded; the decode counts from the start,
    rounded on its own. The difference of the two roundings, not the rounded difference."""
    late = media.FrameClock(1, 1000, 1_400)
    # 1.4 ms is 1 tick of a millisecond (rounded); 1.4 ms + 0.2 ms is 2 ticks. One tick in.
    assert late.at(200) == 1
    assert media.FrameClock(1, 1000, 0).at(200) == 0


def test_the_select_keeps_exactly_the_frame_the_seek_stops_at() -> None:
    """The first frame at or past the moment's tick; the first frame of the file has no frame
    before it. No seek at all is the first frame decoded, whatever its time."""
    assert media.select_expression(Moment(seek=("-ss", "1.500")), CLOCK) == (
        "gte(pts\\,19200)*(isnan(prev_pts)+lt(prev_pts\\,19200))"
    )
    assert media.select_expression(Moment(seek=()), CLOCK) == "eq(n\\,0)"
    assert media.select_expression(Moment(seek=("-sseof", "-1")), CLOCK) is None
    assert media.select_expression(Moment(seek=("-ss", "soon")), CLOCK) is None


def test_the_graph_splits_once_and_chains_every_moment_of_every_request() -> None:
    graph = media.decode_once_graph(
        [_raw(_moments(2)), _files([Moment(seek=("-ss", "3.000"))])], clock=CLOCK
    )
    lines = graph.strip().split(";\n")
    assert lines[0] == "[0:v]split=3[s0][s1][s2]"
    assert lines[1].startswith("[s0]select='eq(n\\,0)") and lines[1].endswith(
        ",scale=32:32:flags=bilinear[o0_0]"
    )
    assert lines[3].startswith("[s2]select='gte(pts\\,38400)") and lines[3].endswith(
        ",scale=160:-2[o1_0]"
    )


def test_a_moment_this_cannot_select_gets_no_chain_and_no_output(
    tmp_path: Path, settings: Settings
) -> None:
    odd = Moment(seek=("-sseof", "-1"))
    requests = [_raw([Moment(seek=()), odd])]
    assert media.decode_once_graph(requests, clock=CLOCK).count("select=") == 1
    argv = media.decode_once_args(
        SOURCE, requests, script=tmp_path / "g", workspace=tmp_path, settings=settings
    )
    assert argv.count("-map") == 1


def test_the_args_write_each_moment_as_its_own_output_in_its_consumers_form(
    tmp_path: Path, settings: Settings
) -> None:
    argv = media.decode_once_args(
        SOURCE,
        [_raw(_moments(1)), _files(_moments(1))],
        script=tmp_path / "g",
        workspace=tmp_path,
        settings=settings,
    )
    assert argv[:1] == [settings.ffmpeg_path]
    assert argv[argv.index("-i") + 1] == str(SOURCE)
    assert "-filter_complex_script" in argv
    joined = " ".join(argv)
    assert "-map [o0_0] -frames:v 1 -pix_fmt gray -f rawvideo" in joined
    assert "-map [o1_0] -frames:v 1 -c:v bmp" in joined
    assert str(tmp_path / "00-0000.raw") in argv and str(tmp_path / "01-0000.bmp") in argv


def test_the_graph_has_no_budget_and_the_outputs_do() -> None:
    short = ["ffmpeg", "-i", "x"]
    assert media.decode_once_fits(short)
    assert not media.decode_once_fits(["x" * (media.COMMAND_LINE_BUDGET + 1)])


def test_nothing_to_select_is_an_empty_graph() -> None:
    assert media.decode_once_graph([], clock=CLOCK) == ""
    assert media.decode_once_graph([_raw([Moment(seek=("-sseof", "-1"))])], clock=CLOCK) == ""


async def test_nothing_to_select_is_an_empty_store_and_no_launch(
    tmp_path: Path, settings: Settings
) -> None:
    """An empty graph is answered before the tool is named, so a tool that is not there does
    not matter, and the answer is an empty store, not an error."""
    workspace = tmp_path / "once"
    workspace.mkdir()
    prepared = await media.decode_once(
        SOURCE,
        [],
        workspace=workspace,
        settings=settings.model_copy(update={"ffmpeg_path": str(tmp_path / "no-such-ffmpeg")}),
        time_limit=10,
    )
    assert prepared.count == 0
    assert not (workspace / "graph.txt").exists(), "nothing was written for nothing"


def _timed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every file read as one whose frames are timed by `CLOCK`, without asking ffprobe."""

    async def reading(*_args: object, **_kwargs: object) -> media._Reading:
        return media._Reading(picture=None, stream=0, clock=CLOCK)

    monkeypatch.setattr(media, "_reading_of", reading)


async def test_a_file_whose_frames_cannot_be_timed_is_seeked_and_nothing_is_launched(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the stream's ticks the exact frame cannot be named, and a fingerprint from a
    neighbouring frame matches nothing: an empty store, so the consumers seek."""

    async def untimed(*_args: object, **_kwargs: object) -> media._Reading:
        return media._Reading(picture=None, stream=0, clock=None)

    monkeypatch.setattr(media, "_reading_of", untimed)
    workspace = tmp_path / "once"
    workspace.mkdir()
    prepared = await media.decode_once(
        SOURCE,
        [_raw(_moments(2))],
        workspace=workspace,
        settings=settings.model_copy(update={"ffmpeg_path": str(tmp_path / "no-such-ffmpeg")}),
        time_limit=10,
    )
    assert prepared.count == 0
    assert not (workspace / "graph.txt").exists()


async def test_a_command_line_past_the_budget_is_refused_before_the_tool_is_launched(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over the budget, the refusal is this module's own error and the tool is never run: the
    stand-in would succeed, and a success here would mean the budget was not checked."""
    _timed(monkeypatch)
    tool = stand_in_tool(tmp_path / "bin", "ffmpeg", "import sys; sys.exit(0)")
    monkeypatch.setattr(media, "COMMAND_LINE_BUDGET", 1)
    workspace = tmp_path / "once"
    workspace.mkdir()
    with pytest.raises(media.FFmpegError, match="too many moments"):
        await media.decode_once(
            SOURCE,
            [_raw(_moments(2))],
            workspace=workspace,
            settings=settings.model_copy(update={"ffmpeg_path": tool}),
            time_limit=10,
        )


async def test_a_tool_that_cannot_be_started_is_a_media_error(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The consumers catch `FFmpegError` and seek instead; a tool that is not there must be
    reported as one of those and not as the OS error underneath it."""
    _timed(monkeypatch)
    workspace = tmp_path / "once"
    workspace.mkdir()
    with pytest.raises(media.FFmpegError, match="could not run"):
        await media.decode_once(
            SOURCE,
            [_raw(_moments(2))],
            workspace=workspace,
            settings=settings.model_copy(update={"ffmpeg_path": str(tmp_path / "no-such-ffmpeg")}),
            time_limit=10,
        )


# --- prepared frames are served without a launch ---------------------------------------------------


async def test_prepared_raw_frames_are_served_without_launching_the_tool(
    tmp_path: Path, settings: Settings
) -> None:
    """A consumer whose ask matches what was planned gets its frames from the store; the tool is
    never run. The stand-in refuses everything, so a launch would be a failure here."""
    tool = stand_in_tool(tmp_path / "bin", "ffmpeg", "import sys; sys.exit(1)")
    frames = media.PreparedFrames()
    request = _raw(_moments(3))
    frames.put_raw(SOURCE, request, [b"a" * 1024, None, b"c" * 1024])
    with media.prepared(frames):
        read = await media.raw_moments(
            SOURCE,
            list(request.moments[1:]),
            filters=request.filters,
            pixel_format=request.pixel_format,
            frame_bytes=request.frame_bytes,
            settings=settings.model_copy(update={"ffmpeg_path": tool}),
            time_limit=10,
        )
    assert read == [None, b"c" * 1024], "a run of the moments, a missing one None in its place"


async def test_an_ask_that_was_not_prepared_seeks_as_before(
    tmp_path: Path, settings: Settings
) -> None:
    tool = stand_in_tool(
        tmp_path / "bin", "ffmpeg", "import sys; sys.stdout.buffer.write(b'x' * 2)"
    )
    frames = media.PreparedFrames()
    frames.put_raw(SOURCE, _raw(_moments(1)), [b"a" * 1024])
    with media.prepared(frames):
        read = await media.raw_moments(
            SOURCE,
            _moments(1),
            filters="scale=1:2",  # a different filter: not what was prepared
            pixel_format="gray",
            frame_bytes=2,
            settings=settings.model_copy(update={"ffmpeg_path": tool}),
            time_limit=10,
        )
    assert read == [b"xx"]


async def test_files_that_were_not_prepared_are_written_as_before(
    tmp_path: Path, settings: Settings
) -> None:
    """The files form of the same rule: a store that holds files for some OTHER ask (here a
    different suffix) is not a match, and the moments are read from the tool."""
    tool = stand_in_tool(
        tmp_path / "bin",
        "ffmpeg",
        """
        import sys
        from pathlib import Path
        Path(sys.argv[-1]).write_bytes(b"jpeg")
        """,
    )
    frames = media.PreparedFrames()
    frames.put_files(SOURCE, _files(_moments(1)), [tmp_path / "prepared.bmp"])
    into = tmp_path / "into"
    into.mkdir()
    with media.prepared(frames):
        files = await media.moments_to_files(
            SOURCE,
            _moments(1),
            into=into,
            suffix=".jpg",  # a different suffix: not what was prepared
            filters="scale=160:-2",
            output=[],
            settings=settings.model_copy(update={"ffmpeg_path": tool}),
            time_limit=10,
        )
    assert files == [into / "0000.jpg"]
    assert (into / "0000.jpg").read_bytes() == b"jpeg", "written by the tool, not moved"
    assert frames.count == 1, "the store was not touched"


async def test_prepared_files_are_moved_to_where_the_consumer_wanted_them(
    tmp_path: Path, settings: Settings
) -> None:
    tool = stand_in_tool(tmp_path / "bin", "ffmpeg", "import sys; sys.exit(1)")
    made = tmp_path / "made"
    made.mkdir()
    (made / "one.bmp").write_bytes(b"BM1")
    frames = media.PreparedFrames()
    request = _files(_moments(2))
    frames.put_files(SOURCE, request, [made / "one.bmp", None])
    into = tmp_path / "into"
    into.mkdir()
    with media.prepared(frames):
        files = await media.moments_to_files(
            SOURCE,
            list(request.moments),
            into=into,
            suffix=".bmp",
            filters=request.filters,
            output=list(request.output),
            settings=settings.model_copy(update={"ffmpeg_path": tool}),
            time_limit=10,
        )
    assert files == [into / "0000.bmp", None]
    assert (into / "0000.bmp").read_bytes() == b"BM1"
    assert not (made / "one.bmp").exists(), "moved, not copied"
    assert (
        frames.files(
            SOURCE,
            list(request.moments),
            filters=request.filters,
            suffix=".bmp",
            output=request.output,
        )
        is None
    ), "handed out once"


def test_outside_the_context_nothing_is_prepared() -> None:
    assert media._PREPARED.get() is None


# --- the rule ---------------------------------------------------------------------------------------


def _rates(
    *, decode_fps: float = 600.0, seek: float = 0.03, share: media.StorageRead | None = None
) -> media.ReadRates:
    return media.ReadRates(decode_fps=decode_fps, seek_seconds=seek, storage=share)


def _shape(
    *,
    moments: int = 55,
    seconds: float = 10,
    fps: float = 30,
    codec: str | None = "h264",
    size_bytes: int = 10**7,
    rates: media.ReadRates | None = None,
) -> media.ReadShape:
    return media.choose_read_shape(
        moments=moments,
        duration_seconds=seconds,
        fps=fps,
        width=1920,
        height=1080,
        size_bytes=size_bytes,
        codec=codec,
        rates=rates,
    )


def test_a_seek_is_priced_in_the_files_own_frames_by_its_codec() -> None:
    """The rule's arithmetic on a local disk: one decode is every frame; seeking is the moments
    times what one seek costs in that codec's frames. On the edge, seeking."""
    h264 = media.frames_per_seek("h264")
    # 55 moments of H.264 cost 55 * 50 = 2750 frames: 2749 frames decode once, 2750 seek.
    assert h264 == 50
    assert _shape(seconds=2749, fps=1) is media.ReadShape.DECODE_ONCE
    assert _shape(seconds=2750, fps=1) is media.ReadShape.SEEK
    # The same length in HEVC seeks at more than twice the frames, so it is still decoded once.
    assert media.frames_per_seek("hevc") > 2 * h264
    assert _shape(seconds=2750, fps=1, codec="hevc") is media.ReadShape.DECODE_ONCE
    # The frame rate counts the frames to decode, and the seeks are what they were.
    assert _shape(seconds=54.98, fps=50) is media.ReadShape.DECODE_ONCE
    assert _shape(seconds=55.02, fps=50) is media.ReadShape.SEEK


def test_a_short_clip_is_decoded_once_and_a_long_file_seeks_measured_or_not() -> None:
    """The fingerprints' fifty-five moments: a ten second clip decodes once in every codec, an
    hour seeks in every codec, and neither needs a machine that was ever measured."""
    for codec in ("h264", "hevc", "vp9", "av1", "vp8", "prores", None, "something-new"):
        assert _shape(seconds=10, codec=codec) is media.ReadShape.DECODE_ONCE
        assert _shape(seconds=3600, codec=codec) is media.ReadShape.SEEK
        assert _shape(seconds=10, codec=codec, rates=_rates()) is media.ReadShape.DECODE_ONCE


def test_a_codec_not_named_is_priced_as_h264() -> None:
    assert media.frames_per_seek("something-new") == media.frames_per_seek("h264")
    assert media.frames_per_seek(None) == media.frames_per_seek("h264")


def test_a_codec_of_keyframes_alone_seeks_where_a_long_gop_one_decodes() -> None:
    """Thirty seconds at 60 fps is 1800 frames: ProRes seeks (55 * 27 = 1485), HEVC decodes."""
    assert _shape(seconds=30, fps=60, codec="prores") is media.ReadShape.SEEK
    assert _shape(seconds=30, fps=60, codec="hevc") is media.ReadShape.DECODE_ONCE


def test_a_share_charges_each_seek_its_round_trip_and_the_decode_its_bytes() -> None:
    """With a share both sides are seconds at the measured decode rate, scaled by pixels (1080p
    is 2.25 of the 720p the rate is taken at)."""
    share = media.StorageRead(megabytes_per_second=80.0, seek_seconds=0.2)
    # 120 s of H.264 seeks locally (3600 frames against 2750); on the share each of the 55
    # seeks adds 0.2 s: 2750 * 2.25 / 600 + 11 = 21.3 s against 3600 * 2.25 / 600 = 13.5 s.
    assert _shape(seconds=120) is media.ReadShape.SEEK
    assert _shape(seconds=120, rates=_rates(share=share)) is media.ReadShape.DECODE_ONCE
    # A file whose bytes take longer to move than the seeks cost is priced by the move: 4 GB at
    # 80 MB/s is 50 s.
    assert (
        _shape(seconds=120, size_bytes=4 * 10**9, rates=_rates(share=share)) is media.ReadShape.SEEK
    )


def test_a_share_whose_throughput_was_never_measured_charges_only_its_seeks() -> None:
    share = media.StorageRead(megabytes_per_second=0.0, seek_seconds=0.2)
    assert (
        _shape(seconds=120, size_bytes=4 * 10**9, rates=_rates(share=share))
        is media.ReadShape.DECODE_ONCE
    )


def test_local_rates_do_not_move_the_rule() -> None:
    """Both sides decode the same pictures, so how fast this machine decodes cancels out."""
    for rates in (None, _rates(decode_fps=50.0, seek=5.0), _rates(decode_fps=5000.0, seek=0.001)):
        assert _shape(seconds=2749, fps=1, rates=rates) is media.ReadShape.DECODE_ONCE
        assert _shape(seconds=2750, fps=1, rates=rates) is media.ReadShape.SEEK


def test_a_file_with_no_timeline_or_no_moments_seeks() -> None:
    assert _shape(moments=0) is media.ReadShape.SEEK
    assert _shape(seconds=0) is media.ReadShape.SEEK


# --- on the real tool -------------------------------------------------------------------------------


@pytest.mark.integration
async def test_one_decode_is_byte_identical_to_the_seek_form_for_every_consumer(
    clip: Path, tmp_path: Path, settings: Settings
) -> None:
    """Raw pixels and image files, thirty moments each, from one decode of the twelve-second clip:
    the same bytes the seek form produces, and a moment past the end None in its place, which is
    also the case where the tool exits non-zero having written everything else."""
    raw_moments = [Moment(seek=() if i == 0 else ("-ss", f"{i * 0.37:.3f}")) for i in range(30)]
    file_moments = [Moment(seek=("-ss", f"{i * 0.9:.3f}")) for i in range(12)] + [
        Moment(seek=("-ss", "50.000"))
    ]
    raw = _raw(raw_moments)
    files = media.FrameFiles(
        moments=tuple(file_moments),
        filters="scale=min(160\\,iw):-2",
        suffix=".jpg",
        output=("-q:v", "8"),
    )
    workspace = tmp_path / "once"
    workspace.mkdir()
    prepared = await media.decode_once(
        clip, [raw, files], workspace=workspace, settings=settings, time_limit=120
    )

    seeked_raw = await media.raw_moments(
        clip,
        raw_moments,
        filters=raw.filters,
        pixel_format=raw.pixel_format,
        frame_bytes=raw.frame_bytes,
        settings=settings,
        time_limit=120,
    )
    assert (
        prepared.raw(clip, raw_moments, filters=raw.filters, pixel_format=raw.pixel_format)
        == seeked_raw
    )
    assert all(one is not None for one in seeked_raw)

    into = tmp_path / "seeked"
    into.mkdir()
    seeked_files = await media.moments_to_files(
        clip,
        file_moments,
        into=into,
        suffix=".jpg",
        filters=files.filters,
        output=list(files.output),
        settings=settings,
        time_limit=120,
    )
    once_files = prepared.files(
        clip, file_moments, filters=files.filters, suffix=".jpg", output=files.output
    )
    assert once_files is not None
    assert once_files[-1] is None and seeked_files[-1] is None, "past the end, both say so"
    for made, seeked in zip(once_files[:-1], seeked_files[:-1], strict=True):
        assert made is not None and seeked is not None
        assert made.read_bytes() == seeked.read_bytes()


@pytest.mark.integration
async def test_a_moment_inside_a_frame_is_the_frame_the_seek_stops_at(
    clip: Path, tmp_path: Path, settings: Settings
) -> None:
    """Moments a few hundred microseconds past a frame, and seconds written to seventeen
    digits: the seek stops at the NEXT frame, and a select by rounded seconds would keep the
    one before it. The ticks name the same frame the seek does."""
    moments = [
        Moment(seek=("-ss", one))
        for one in ("0.0404", "0.4801", "1.0000001", "2.0779999999999998", "3.1599")
    ]
    raw = _raw(moments)
    workspace = tmp_path / "once"
    workspace.mkdir()
    prepared = await media.decode_once(
        clip, [raw], workspace=workspace, settings=settings, time_limit=120
    )
    seeked = await media.raw_moments(
        clip,
        moments,
        filters=raw.filters,
        pixel_format=raw.pixel_format,
        frame_bytes=raw.frame_bytes,
        settings=settings,
        time_limit=120,
    )
    assert all(one is not None for one in seeked)
    assert prepared.raw(clip, moments, filters=raw.filters, pixel_format=raw.pixel_format) == seeked


@pytest.mark.integration
async def test_a_decode_that_produces_nothing_is_a_failure_the_consumers_recover_from(
    clip: Path, tmp_path: Path, settings: Settings
) -> None:
    """Every moment past the end: no file at all, and that is an error rather than an empty
    store that would read as 'every moment is None'."""
    workspace = tmp_path / "once"
    workspace.mkdir()
    past = [Moment(seek=("-ss", "50.000")), Moment(seek=("-ss", "60.000"))]
    with pytest.raises(media.FFmpegError, match="produced no frame"):
        await media.decode_once(
            clip,
            [_raw(past)],
            workspace=workspace,
            settings=settings,
            time_limit=30,
        )


# --- a tile's stills: the still and its grey pixels from one read ----------------------------------

#: A stand-in ffmpeg that writes every still and every grey frame it is asked for, except for the
#: moments a test names: those it refuses outright (exit 1) or leaves half written.
_STILLS_TOOL = """
import sys
from pathlib import Path
argv = sys.argv[1:]
with open({log!r}, "a") as handle:
    handle.write(str(argv.count("-i")) + "\\n")
if argv.count("-i") > {most}:
    sys.stderr.write("too many inputs"); sys.exit(1)
outputs = [one for one in argv if one.endswith((".jpg", ".gray"))]
if any(Path(one).stem in {refused!r} for one in outputs):
    sys.stderr.write("cannot place this moment"); sys.exit(1)
for one in outputs:
    short = Path(one).stem in {short!r}
    if one.endswith(".jpg"):
        Path(one).write_bytes(b"jpeg")
    else:
        Path(one).write_bytes(b"\\x80" * (2 if short else 4))
"""


async def _stills(
    tmp_path: Path, settings: Settings, *, most: int, refused: tuple[str, ...] = (),
    short: tuple[str, ...] = (), count: int = 3,
) -> tuple[list[media.CutStill | None], list[str]]:  # fmt: skip
    log = tmp_path / "calls.txt"
    tool = stand_in_tool(
        tmp_path / "bin",
        "ffmpeg",
        _STILLS_TOOL.format(log=str(log), most=most, refused=refused, short=short),
    )
    into = tmp_path / "stills"
    into.mkdir()
    cut = await media.moments_to_stills(
        SOURCE,
        _moments(count),
        into=into,
        still_filters="scale=-2:180",
        still_output=["-q:v", "4"],
        level_filters="scale=2:2,format=gray",
        level_bytes=4,
        settings=settings.model_copy(update={"ffmpeg_path": tool}),
        time_limit=30,
    )
    return cut, log.read_text().split()


async def test_every_moment_is_cut_as_a_still_and_its_grey_pixels_from_one_process(
    tmp_path: Path, settings: Settings
) -> None:
    cut, calls = await _stills(tmp_path, settings, most=99)

    assert calls == ["3"], "three moments, one process"
    into = tmp_path / "stills"
    assert cut == [
        media.CutStill(still=into / f"{i:04d}.jpg", levels=b"\x80" * 4) for i in range(3)
    ]


async def test_a_grey_frame_of_the_wrong_size_is_no_still_rather_than_a_misread_brightness(
    tmp_path: Path, settings: Settings
) -> None:
    cut, _calls = await _stills(tmp_path, settings, most=99, short=("0001",))

    assert cut[0] is not None and cut[2] is not None
    assert cut[1] is None


async def test_a_refused_chunk_of_stills_is_cut_a_moment_at_a_time_and_loses_only_its_bad_moment(
    tmp_path: Path, settings: Settings
) -> None:
    """One moment the tool cannot place costs that moment, not the two around it."""
    cut, calls = await _stills(tmp_path, settings, most=1, refused=("0001",))

    assert calls == ["3", "1", "1", "1"]
    assert [one is not None for one in cut] == [True, False, True]


async def test_no_moments_cuts_no_stills_and_launches_nothing(
    tmp_path: Path, settings: Settings
) -> None:
    cut = await media.moments_to_stills(
        SOURCE,
        [],
        into=tmp_path,
        still_filters="scale=-2:180",
        still_output=[],
        level_filters="format=gray",
        level_bytes=4,
        settings=settings.model_copy(update={"ffmpeg_path": str(tmp_path / "no-such-ffmpeg")}),
        time_limit=30,
    )
    assert cut == []


def test_the_stills_command_maps_the_moving_stream_to_both_outputs_of_every_moment(
    settings: Settings,
) -> None:
    argv = media.moment_stills_args(
        SOURCE,
        _moments(2),
        still_filters="scale=-2:180",
        still_output=["-q:v", "4"],
        level_filters="format=gray",
        stills=[Path("/s/0.jpg"), Path("/s/1.jpg")],
        levels=[Path("/s/0.gray"), Path("/s/1.gray")],
        settings=settings,
        stream=1,
    )
    assert [argv[i + 1] for i, one in enumerate(argv) if one == "-map"] == [
        "0:v:1", "0:v:1", "1:v:1", "1:v:1",
    ]  # fmt: skip
    still = argv.index(str(Path("/s/0.jpg")))
    assert argv[still - 2 : still] == ["-q:v", "4"]
    assert argv[-5:] == ["-pix_fmt", "gray", "-f", "rawvideo", str(Path("/s/1.gray"))]


# --- what one decode prepared, and who may take it -------------------------------------------------


def test_a_store_holds_an_ask_only_where_every_one_of_its_moments_is_there(tmp_path: Path) -> None:
    """`holds` is what keeps a product from launching a read of its own: one moment missing means
    the ask is not held, for raw pixels and for files alike."""
    prepared = media.PreparedFrames()
    two, three = _moments(2), _moments(3)
    prepared.put_raw(SOURCE, _raw(two), [b"a", None])
    prepared.put_files(SOURCE, _files(two), [tmp_path / "a.bmp", None])

    assert prepared.holds(SOURCE, _raw(two))
    assert prepared.holds(SOURCE, _files(two))
    assert not prepared.holds(SOURCE, _raw(three))
    assert not prepared.holds(SOURCE, _files(three))
    assert not prepared.holds(Path("/library/other.mp4"), _raw(two))


def test_a_merged_store_holds_both_and_the_later_read_wins_a_moment_both_hold(
    tmp_path: Path,
) -> None:
    first, later = media.PreparedFrames(), media.PreparedFrames()
    first.put_raw(SOURCE, _raw(_moments(2)), [b"old", b"kept"])
    later.put_raw(SOURCE, _raw(_moments(1)), [b"new"])
    later.put_files(SOURCE, _files(_moments(1)), [tmp_path / "a.bmp"])

    both = first.merged(later)

    raw = _raw(_moments(2))
    assert both.raw(SOURCE, _moments(2), filters=raw.filters, pixel_format=raw.pixel_format) == [
        b"new",
        b"kept",
    ]
    assert both.holds(SOURCE, _files(_moments(1)))
    assert first.count == 2, "merging leaves the stores it was made from as they were"


def test_frames_are_prepared_for_what_runs_inside_and_for_nothing_after() -> None:
    frames = media.PreparedFrames()
    assert media.prepared_now() is None
    with media.prepared(frames):
        assert media.prepared_now() is frames
    assert media.prepared_now() is None


def test_a_raw_frame_missing_or_cut_short_by_the_decode_is_none_in_its_place(
    tmp_path: Path,
) -> None:
    """A moment past the end writes nothing and a frame cut short is not a frame: both read as
    None, so the consumer seeks for that moment rather than fingerprinting half a picture."""
    moments = _moments(3)
    raw = _raw(moments)
    (tmp_path / "00-0000.raw").write_bytes(b"\x01" * raw.frame_bytes)
    (tmp_path / "00-0001.raw").write_bytes(b"\x01" * (raw.frame_bytes - 1))

    frames = media._collect(SOURCE, [raw], tmp_path)

    assert frames.raw(SOURCE, moments, filters=raw.filters, pixel_format=raw.pixel_format) == [
        b"\x01" * raw.frame_bytes,
        None,
        None,
    ]


async def test_a_timed_file_with_nothing_selectable_launches_nothing(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _timed(monkeypatch)
    workspace = tmp_path / "once"
    workspace.mkdir()
    prepared = await media.decode_once(
        SOURCE,
        [_raw([Moment(seek=("-sseof", "-1"))])],
        workspace=workspace,
        settings=settings.model_copy(update={"ffmpeg_path": str(tmp_path / "no-such-ffmpeg")}),
        time_limit=10,
    )
    assert prepared.count == 0
    assert not (workspace / "graph.txt").exists()
