# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Sift actually asks ffmpeg to do.

The argument lists are checked as literal strings. That is worth doing for two reasons: a flag in
the wrong position is not an error, it is a different command that runs perfectly and produces
something subtly wrong, and the hardware paths cannot be run at all on a machine with no GPU, so
the only place they can be checked is here.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sift.kernel import media
from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.sampling import Piece
from sift.kernel.subprocess import Priority
from sift.slices.media_jobs import ffmpeg, tuning

pytestmark = pytest.mark.unit

SOURCE = Path("/library/holiday.mp4")
DESTINATION = Path("/cache/ab/cd/01HX/thumb.jpg")
FRAMES = Path("/work/frame-%04d.jpg")

#: The same paths as the argument lists actually carry them.
#:
#: How an absolute path is SPELLED is a property of the site rather than of the command being
#: built: a leading slash is a drive-relative path on Windows, and `resolve` fills the drive in.
#: Written out by hand these read as commands nobody would run there, and they fail for a reason
#: that has nothing to do with the arguments under test.
#:
#: `probe` resolves; the rest hand over what they were given. That difference is deliberate and is
#: stated where `probe` is built, so it is kept here rather than smoothed over.
SOURCE_PROBED = str(SOURCE.resolve())
SOURCE_ARG = str(SOURCE)
FRAMES_ARG = str(FRAMES)


#: How many workers these commands are built for. `SHARE` below is what that comes to on the
#: machine running the tests, and the fixture and the pin both say it so neither can drift.
WORKERS = 2


def _share_for(workers: int) -> str:
    """The thread cap `workers` come to here, read from the helper the code itself uses.

    `media.jobs_at_once` prefers a module-level number over the settings it is handed: the one
    the running pool sets, which is right in an application with one pool and wrong in a process
    running other tests. So any test in this xdist worker that boots the app would decide the
    thread share for every command built here, and under `pytest -n` which tests share a process is
    a coin toss.

    Cleared for the read, and pinned for the tests by the fixture below: the same fault from both
    ends, so the arithmetic is what is pinned rather than whatever ran first.
    """
    was = media._jobs_at_once
    media._jobs_at_once = None
    try:
        return str(media.background_threads(Settings(worker_concurrency=workers)))
    finally:
        media._jobs_at_once = was


#: The thread cap every background command carries. Read from the same helper the code uses. What
#: is being pinned is that the flag is THERE and in the right places, not what the arithmetic says.
SHARE = _share_for(WORKERS)


@pytest.fixture(autouse=True)
def _the_pool_has_not_spoken(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin what `jobs_at_once` answers, for the reason `_share_for` gives at length."""
    monkeypatch.setattr(media, "_jobs_at_once", WORKERS)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # The worker count is fixed so the thread share every command carries is a constant here rather
    # than a number this file works out the same way the code does. Two workers on however many
    # cores the test machine has; `SHARE` above is what that comes to.
    return Settings(
        data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", worker_concurrency=WORKERS
    )


def report(*encoders: str) -> HardwareReport:
    return HardwareReport(
        cpu_count=4,
        total_ram_bytes=8 * 1024**3,
        worker_concurrency=3,
        cuda=bool(encoders),
        rocm=False,
        transcode_encoders=encoders,
        warnings=(),
    )


# --- choosing an encoder ----------------------------------------------------------------------


def test_a_machine_with_no_hardware_encoder_uses_the_cpu() -> None:
    """The ordinary case, and not a failure. Most machines Sift runs on are this one."""
    assert ffmpeg.choose_encoder(report()) is ffmpeg.Encoder.CPU


def test_the_report_decides_and_nothing_else_does() -> None:
    """The trap the hardware report exists to close, restated here where it is consumed.

    An ffmpeg can be built with NVENC on a machine with no NVIDIA card in it. Asking
    "was ffmpeg built with it" gets the wrong answer on an ordinary desktop; the report answers
    "is it built in *and* is the device there", and this must not second-guess it.
    """
    assert ffmpeg.choose_encoder(report("h264_nvenc")) is ffmpeg.Encoder.NVENC
    assert ffmpeg.choose_encoder(report("h264_vaapi")) is ffmpeg.Encoder.VAAPI
    assert ffmpeg.choose_encoder(report("h264_qsv")) is ffmpeg.Encoder.QSV


def test_the_preference_order_holds_when_several_are_usable() -> None:
    assert (
        ffmpeg.choose_encoder(report("h264_vaapi", "h264_qsv", "h264_nvenc"))
        is ffmpeg.Encoder.NVENC
    )
    assert ffmpeg.choose_encoder(report("h264_vaapi", "h264_qsv")) is ffmpeg.Encoder.QSV


def test_an_encoder_sift_does_not_build_previews_with_is_not_chosen() -> None:
    """A machine whose only hardware encoder makes something browsers cannot play is a CPU
    machine as far as previews are concerned."""
    assert ffmpeg.choose_encoder(report("hevc_nvenc", "av1_vaapi")) is ffmpeg.Encoder.CPU


# --- the argument lists -----------------------------------------------------------------------


def test_the_probe_command(settings: Settings) -> None:
    assert ffmpeg.probe_args(SOURCE, settings=settings) == [
        settings.ffprobe_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        SOURCE_PROBED,
    ]


def test_the_probe_never_counts_frames(settings: Settings) -> None:
    """Counting frames means decoding the whole file: minutes, on a long one, for a number
    nothing reads."""
    assert "-count_frames" not in ffmpeg.probe_args(SOURCE, settings=settings)


def test_a_still_is_asked_for_its_first_frame_and_a_video_is_not(settings: Settings) -> None:
    """A photograph's turn is on its decoded frame; a video's is on its stream, and decoding a
    frame of a two-hour video to learn nothing new would be a cost on every file read."""
    still = ffmpeg.probe_args(SOURCE, settings=settings, still=True)
    assert still[still.index("-show_frames") + 1 : still.index("-show_frames") + 3] == [
        "-read_intervals",
        "%+#1",
    ]
    assert still[-1] == SOURCE_PROBED
    assert "-show_frames" not in ffmpeg.probe_args(SOURCE, settings=settings)


def _turned(angle: float) -> list[dict[str, object]]:
    return [{"side_data_type": "Display Matrix", "rotation": angle}]


@pytest.mark.parametrize(
    ("angle", "size"), [(-90, (240, 320)), (90, (240, 320)), (180, (320, 240))]
)
def test_a_video_turned_a_quarter_is_recorded_the_way_it_plays(
    angle: float, size: tuple[int, int]
) -> None:
    """A phone's video is stored on its side with a turn on the stream, and every picture ffmpeg
    draws from it is turned: the size recorded is the size drawn. A half turn keeps its shape."""
    probed = ffmpeg.parse_probe(
        {
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "width": 320,
                    "height": 240,
                    "side_data_list": _turned(angle),
                }
            ]
        }
    )
    assert (probed.width, probed.height) == size


def test_a_photographs_turn_is_read_off_its_own_first_frame() -> None:
    """The decoder reads the note, so the turn arrives on the frame of the picture's own stream. A
    frame of another stream (a HEIF's thumbnail) says nothing about this one."""
    stream = {"index": 1, "codec_type": "video", "width": 600, "height": 800}
    ours = {"stream_index": 1, "side_data_list": _turned(-90)}
    another = {"stream_index": 0, "side_data_list": _turned(180)}

    turned = ffmpeg.parse_probe({"streams": [stream], "frames": [another, ours]})
    assert (turned.width, turned.height) == (800, 600)

    only_another = ffmpeg.parse_probe({"streams": [stream], "frames": [another]})
    assert (only_another.width, only_another.height) == (600, 800)


def test_a_note_ffmpeg_does_not_act_on_leaves_the_size_alone() -> None:
    """The tag is not read, only the turn ffmpeg applies: a size nothing is drawn at is worse than
    the size on disk. And a turn that is not a number is no turn."""
    stream = {"index": 0, "codec_type": "video", "width": 600, "height": 800}
    tagged = {"stream_index": 0, "tags": {"Orientation": "    6"}}
    assert ffmpeg.parse_probe({"streams": [stream], "frames": [tagged]}).width == 600
    nonsense = {**stream, "side_data_list": [{"rotation": "sideways"}, "not a dict"]}
    assert ffmpeg.parse_probe({"streams": [nonsense]}).width == 600


def test_the_frame_command_seeks_before_it_opens(settings: Settings) -> None:
    """`-ss` before `-i` is the difference between a seek and decoding the whole video to throw
    almost all of it away, thirty times over, once per sampled frame."""
    argv = ffmpeg.frame_args(SOURCE, 90_000, size=32, settings=settings)
    assert argv.index("-ss") < argv.index("-i")
    assert argv == [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-max_alloc",
        "1073741824",
        "-threads",
        SHARE,
        "-filter_threads",
        SHARE,
        "-ss",
        "90.000",
        "-i",
        SOURCE_ARG,
        "-frames:v",
        "1",
        "-vf",
        "scale=32:32:flags=bilinear",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def test_the_all_frames_command_decodes_once_with_no_seek(settings: Settings) -> None:
    """A GIF's thirty frames come from one decode, not thirty seeks: this reads the whole file with
    no `-ss` and no `-frames:v` cap, every frame in order as a grayscale-square stream the caller
    splits. It is `frame_args` without the seek and the one-frame limit: the same scale and format,
    so a frame it emits is byte-for-byte the frame a seek to the same moment would have."""
    argv = ffmpeg.all_frames_args(SOURCE, size=32, settings=settings)
    assert "-ss" not in argv
    assert "-frames:v" not in argv
    assert argv == [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-max_alloc",
        "1073741824",
        "-threads",
        SHARE,
        "-filter_threads",
        SHARE,
        "-i",
        SOURCE_ARG,
        "-vf",
        "scale=32:32:flags=bilinear",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def test_the_all_tiles_command_decodes_once_with_no_seek(settings: Settings) -> None:
    """A GIF's sprite tiles come from one decode to a numbered pattern, not one seek per tile: no
    `-ss`, no `-frames:v`, and the same width-and-quality scale a single tile gets from `still_args`,
    so a frame written here is the tile a seek to that frame would have written."""
    argv = ffmpeg.all_tiles_args(SOURCE, FRAMES, width=160, quality=5, settings=settings)
    assert "-ss" not in argv
    assert "-frames:v" not in argv
    assert argv == [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-max_alloc",
        "1073741824",
        "-threads",
        SHARE,
        "-filter_threads",
        SHARE,
        "-i",
        SOURCE_ARG,
        "-vf",
        "scale=min(160\\,iw):-2",
        "-q:v",
        "5",
        FRAMES_ARG,
    ]


def test_a_timestamp_is_never_written_in_scientific_notation(settings: Settings) -> None:
    """A float formatted the obvious way gives ffmpeg `1e-05`, which it does not read as a time."""
    argv = ffmpeg.frame_args(SOURCE, 1, size=32, settings=settings)
    assert argv[argv.index("-ss") + 1] == "0.001"


def test_the_beginning_is_asked_for_by_not_seeking_to_it(settings: Settings) -> None:
    """`-ss 0` loses the frame it is asking for, on anything one frame long.

    A photograph is read through the image2 demuxer as a video of a single frame lasting 0.04 s. An
    input seek to 0 lands on that frame's own timestamp rather than before it, so it is treated as
    already gone: ffmpeg writes no output and exits successfully, and every still in the library
    would have no thumbnail and no perceptual hash at once.
    """
    for argv in (
        ffmpeg.frame_args(SOURCE, 0, size=32, settings=settings),
        ffmpeg.thumbnail_args(SOURCE, DESTINATION, timestamp_ms=0, settings=settings),
        ffmpeg.still_args(
            SOURCE, DESTINATION, timestamp_ms=0, height=80, quality=5, settings=settings
        ),
    ):
        assert "-ss" not in argv

    # And every other moment still seeks, or sampling a long video means decoding it once per frame.
    assert "-ss" in ffmpeg.frame_args(SOURCE, 90_000, size=32, settings=settings)
    assert "-ss" in ffmpeg.still_args(
        SOURCE, DESTINATION, timestamp_ms=90_000, height=80, quality=5, settings=settings
    )


def test_the_thumbnail_command(settings: Settings) -> None:
    argv = ffmpeg.thumbnail_args(SOURCE, DESTINATION, timestamp_ms=0, settings=settings)
    assert argv == [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-max_alloc",
        "1073741824",
        "-threads",
        SHARE,
        "-filter_threads",
        SHARE,
        "-i",
        SOURCE_ARG,
        "-frames:v",
        "1",
        "-vf",
        f"scale=-2:min({tuning.THUMBNAIL_HEIGHT}\\,ih)",
        "-q:v",
        str(tuning.THUMBNAIL_QUALITY),
        str(DESTINATION),
    ]


def test_a_small_picture_is_never_scaled_up(settings: Settings) -> None:
    """`min(N,ih)` and not a bare N. Enlarging a 100px source to 480 makes a bigger file that
    looks worse than the original, which is the opposite of the job."""
    argv = ffmpeg.thumbnail_args(SOURCE, DESTINATION, timestamp_ms=0, settings=settings)
    assert f"min({tuning.THUMBNAIL_HEIGHT}\\,ih)" in argv[argv.index("-vf") + 1]


def test_a_still_is_sized_on_exactly_one_axis(settings: Settings) -> None:
    """The other follows from the source's shape. Both, or neither, is a caller that has not
    decided what it wants and would get a stretched picture."""
    with pytest.raises(ValueError, match="not both and not neither"):
        ffmpeg.still_args(SOURCE, DESTINATION, timestamp_ms=0, quality=5, settings=settings)
    with pytest.raises(ValueError, match="not both and not neither"):
        ffmpeg.still_args(
            SOURCE, DESTINATION, timestamp_ms=0, height=480, width=160, quality=5, settings=settings
        )


#: One stretch from the beginning: what a file too short to be worth sampling gets.
WHOLE = (Piece(0, 3000),)

#: Moments across a longer file, cutting to the next every two seconds.
ACROSS = (Piece(0, 2000), Piece(12_000, 2000), Piece(24_000, 2000))


def test_a_preview_is_never_asked_for_at_an_odd_height(settings: Settings) -> None:
    """x264 refuses an odd dimension, and the height is not always the ceiling.

    The width is `-2`, which rounds itself. The height is the ceiling OR the source's own height,
    whichever is smaller, so a clip shorter than the ceiling keeps its height, odd or not, and the
    encoder turns the job down with "height not divisible by 2 (720x405)". No preview is made for
    that file, ever.

    A small ceiling hides this, because almost nothing is below it; a larger one puts a large part
    of an ordinary library below it, and some of those files would get no preview. The expression
    has to make the height even itself.
    """
    argv = ffmpeg.preview_args(
        SOURCE, DESTINATION, pieces=WHOLE, encoder=ffmpeg.Encoder.CPU, settings=settings
    )
    graph = argv[argv.index("-filter_complex") + 1]

    assert "trunc(" in graph, "the height has to be rounded down to an even number"
    assert "/2)*2" in graph


def test_a_preview_is_drawn_at_the_size_the_thumbnail_is() -> None:
    """A preview replaces its thumbnail in the same rectangle rather than sitting beside it.

    Drawn smaller, every tile visibly softens the moment a cursor lands on it and sharpens again
    when it leaves, so the one moment somebody is looking closely at a tile is the one moment it
    looks worst. There is no size a preview should be other than the size a tile is.
    """
    assert tuning.PREVIEW_HEIGHT == tuning.THUMBNAIL_HEIGHT


def test_a_preview_of_no_moments_is_refused_rather_than_encoded(settings: Settings) -> None:
    """An empty list is a caller fault, and ffmpeg's own answer to it says nothing useful."""
    with pytest.raises(ValueError, match="at least one moment"):
        ffmpeg.preview_args(
            SOURCE, DESTINATION, pieces=(), encoder=ffmpeg.Encoder.CPU, settings=settings
        )


# --- previews, per encoder --------------------------------------------------------------------


def test_the_preview_command_on_the_cpu(settings: Settings) -> None:
    assert ffmpeg.preview_args(
        SOURCE, DESTINATION, pieces=WHOLE, encoder=ffmpeg.Encoder.CPU, settings=settings
    ) == [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-max_alloc",
        "1073741824",
        "-threads",
        SHARE,
        "-filter_threads",
        SHARE,
        "-t",
        "3.000",
        "-i",
        SOURCE_ARG,
        "-filter_complex",
        f"[0:v]fps={tuning.PREVIEW_FPS},scale=-2:trunc(min({tuning.PREVIEW_HEIGHT}\\,ih)/2)*2"
        ",setsar=1,format=yuv420p[p0]",
        "-map",
        "[p0]",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(tuning.PREVIEW_CRF),
        "-threads",
        SHARE,
        "-movflags",
        "+faststart",
        str(DESTINATION),
    ]


def test_the_preview_command_for_moments_across_a_video(settings: Settings) -> None:
    """The montage: one input per moment, joined by the filter graph, one encode.

    Encoding a file per moment and concatenating them afterwards takes roughly twice the wall time
    on both the processor and the card, produces a larger result, and leaves temporary files behind
    after a crash.
    """
    argv = ffmpeg.preview_args(
        SOURCE, DESTINATION, pieces=ACROSS, encoder=ffmpeg.Encoder.CPU, settings=settings
    )
    shaped = (
        f"fps={tuning.PREVIEW_FPS},scale=-2:trunc(min({tuning.PREVIEW_HEIGHT}\\,ih)/2)*2"
        ",setsar=1,format=yuv420p"
    )

    # One input per moment, each seeking to its own, except the first, which is the beginning and
    # asks for no seek at all. See `_seek` for why `-ss 0` is not the no-op it reads as.
    assert argv.count("-i") == 3
    assert argv[argv.index("-t") : argv.index("-t") + 4] == ["-t", "2.000", "-i", SOURCE_ARG]
    assert "-ss" not in argv[: argv.index("-i")]
    assert argv.count("-ss") == 2
    seeks = [argv[n + 1] for n, arg in enumerate(argv) if arg == "-ss"]
    assert seeks == ["12.000", "24.000"]

    assert argv[argv.index("-filter_complex") + 1] == (
        f"[0:v]{shaped}[p0];[1:v]{shaped}[p1];[2:v]{shaped}[p2];[p0][p1][p2]concat=n=3:v=1:a=0[cut]"
    )
    assert argv[argv.index("-map") + 1] == "[cut]"


def test_the_preview_is_cut_from_the_stream_that_moves(settings: Settings) -> None:
    """A graph fed `[0:v]` is fed the FIRST video stream, and an animated AVIF's first is its still
    cover: the clip would come out one frame held for a second, a hover preview that does not play.
    Every piece reads the named stream; the ordinary file's command names none."""
    pieces = (Piece(0, 2000), Piece(12_000, 2000))
    moving = ffmpeg.preview_args(
        SOURCE, DESTINATION, pieces=pieces, encoder=ffmpeg.Encoder.CPU, stream=1, settings=settings
    )
    graph = moving[moving.index("-filter_complex") + 1]
    assert graph.startswith("[0:v:1]")
    assert "[1:v:1]" in graph
    assert "[0:v]" not in graph

    plain = ffmpeg.preview_args(
        SOURCE, DESTINATION, pieces=pieces, encoder=ffmpeg.Encoder.CPU, settings=settings
    )
    assert plain[plain.index("-filter_complex") + 1].startswith("[0:v]fps=")

    with pytest.raises(ValueError, match="counted from zero"):
        ffmpeg.preview_args(
            SOURCE, DESTINATION, pieces=pieces, encoder=ffmpeg.Encoder.CPU, stream=-1,
            settings=settings,
        )  # fmt: skip


def test_the_preview_command_puts_the_decoder_before_every_input(settings: Settings) -> None:
    """`-hwaccel` is an INPUT option, so a montage needs one copy per moment.

    Placed once at the front it would apply to the first moment and to nothing else, which is the
    failure that reads as "hardware decoding is on" while seven eighths of the work stays on the
    processor. Nothing in the finished clip would look different.
    """
    argv = ffmpeg.preview_args(
        SOURCE,
        DESTINATION,
        pieces=ACROSS,
        encoder=ffmpeg.Encoder.CPU,
        decode=("-hwaccel", "cuda"),
        settings=settings,
    )

    inputs = [n for n, arg in enumerate(argv) if arg == "-i"]
    assert len(inputs) == 3
    assert argv.count("-hwaccel") == 3

    # Each copy sits inside its own input's run of options rather than anywhere before it: the
    # flags between one `-i` and the previous one are what that input is opened with.
    starts = [0, *[n + 2 for n in inputs[:-1]]]
    for start, end in zip(starts, inputs, strict=True):
        assert "-hwaccel" in argv[start:end]


def test_the_preview_command_asks_for_nothing_extra_when_there_is_no_card(
    settings: Settings,
) -> None:
    """The ordinary machine. An empty `decode` has to produce exactly the plain command, or
    every install without a GPU pays for the feature."""
    plain = ffmpeg.preview_args(
        SOURCE, DESTINATION, pieces=ACROSS, encoder=ffmpeg.Encoder.CPU, settings=settings
    )
    empty = ffmpeg.preview_args(
        SOURCE, DESTINATION, pieces=ACROSS, encoder=ffmpeg.Encoder.CPU, decode=(), settings=settings
    )

    assert plain == empty
    assert "-hwaccel" not in plain


def test_the_preview_forces_eight_bit_colour_for_every_software_frame(
    settings: Settings,
) -> None:
    """A 10-bit source decodes to `yuv420p10le`, and `h264_nvenc` refuses it outright with
    `CreateInputBuffer failed: invalid param (8)`, which names no pixel format and reads like a
    bad width. The CPU retry would rescue every such file, so without the conversion nothing would
    look wrong while every 10-bit file wasted an attempt on the card.

    In the graph rather than as an output flag, so it is ONE mechanism serving both encoders that
    take software frames rather than one each.
    """
    for encoder in (ffmpeg.Encoder.CPU, ffmpeg.Encoder.NVENC, ffmpeg.Encoder.QSV):
        argv = ffmpeg.preview_args(
            SOURCE, DESTINATION, pieces=ACROSS, encoder=encoder, settings=settings
        )
        graph = argv[argv.index("-filter_complex") + 1]

        assert graph.count("format=yuv420p") == len(ACROSS), encoder
        # Not also an output flag: two ways of saying one thing is two things to keep in step.
        assert "-pix_fmt" not in argv, encoder


def test_vaapi_converts_on_the_way_to_the_card_instead(settings: Settings) -> None:
    """The one encoder that must NOT have the stage above: its frames are converted to nv12 and
    uploaded by the graph, which is already eight-bit 4:2:0, and a second opinion conflicts."""
    argv = ffmpeg.preview_args(
        SOURCE,
        DESTINATION,
        pieces=ACROSS,
        encoder=ffmpeg.Encoder.VAAPI,
        device="/dev/dri/renderD128",
        settings=settings,
    )
    graph = argv[argv.index("-filter_complex") + 1]

    assert "format=yuv420p" not in graph
    assert graph.count("format=nv12") == 1


def test_the_preview_command_on_nvenc(settings: Settings) -> None:
    argv = ffmpeg.preview_args(
        SOURCE, DESTINATION, pieces=ACROSS, encoder=ffmpeg.Encoder.NVENC, settings=settings
    )
    assert argv[argv.index("-c:v") :] == [
        "-c:v",
        "h264_nvenc",
        "-preset",
        "p4",
        "-rc",
        "vbr",
        "-cq",
        str(tuning.PREVIEW_CRF),
        "-threads",
        SHARE,
        "-movflags",
        "+faststart",
        str(DESTINATION),
    ]
    assert argv[argv.index("-map") + 1] == "[cut]"


def test_the_preview_command_on_quick_sync(settings: Settings) -> None:
    argv = ffmpeg.preview_args(
        SOURCE, DESTINATION, pieces=ACROSS, encoder=ffmpeg.Encoder.QSV, settings=settings
    )
    assert argv[argv.index("-c:v") :] == [
        "-c:v",
        "h264_qsv",
        "-global_quality",
        str(tuning.PREVIEW_CRF),
        "-threads",
        SHARE,
        "-movflags",
        "+faststart",
        str(DESTINATION),
    ]
    assert argv[argv.index("-map") + 1] == "[cut]"


def test_the_preview_command_on_vaapi(settings: Settings) -> None:
    """VAAPI encodes from a frame that already lives on the GPU, so the upload is part of the graph.

    **Once, after the join, not once per moment.** Uploading each piece separately asks concat to
    combine frames belonging to several hardware contexts, which is a strictly harder thing to ask
    and buys nothing: the scaling is trivial next to the encode either way. This is also the one
    path here that cannot be run on a machine with no VAAPI device, so it is deliberately the
    simplest shape available.
    """
    argv = ffmpeg.preview_args(
        SOURCE,
        DESTINATION,
        pieces=ACROSS,
        encoder=ffmpeg.Encoder.VAAPI,
        device="/dev/dri/renderD128",
        settings=settings,
    )
    graph = argv[argv.index("-filter_complex") + 1]

    assert argv[argv.index("-vaapi_device") + 1] == "/dev/dri/renderD128"
    assert graph.count("hwupload") == 1, "one upload, of the finished picture"
    assert graph.endswith(";[cut]format=nv12,hwupload[out]")
    assert argv[argv.index("-map") + 1] == "[out]"
    assert argv[argv.index("-c:v") + 1] == "h264_vaapi"


def test_vaapi_without_a_device_is_refused_rather_than_guessed(settings: Settings) -> None:
    """An empty `-vaapi_device` is not a CPU fallback, it is an ffmpeg that fails confusingly."""
    with pytest.raises(ValueError, match="render node"):
        ffmpeg.preview_args(
            SOURCE, DESTINATION, pieces=WHOLE, encoder=ffmpeg.Encoder.VAAPI, settings=settings
        )


@pytest.mark.parametrize("encoder", list(ffmpeg.Encoder))
@pytest.mark.parametrize("pieces", [WHOLE, ACROSS], ids=["one-piece", "montage"])
def test_every_preview_opens_on_the_first_frame_and_carries_its_index_at_the_front(
    encoder: ffmpeg.Encoder, pieces: tuple[Piece, ...], settings: Settings
) -> None:
    """The two properties that make a preview instant, on every path and at either shape.

    The first moment is the file's own beginning, so the clip opens on the frame the tile is
    already showing and the swap under the cursor is invisible. `+faststart` puts the index at the
    front, and without it a browser must fetch the whole clip before it can play a frame, at which
    point "already on disk" has stopped meaning anything. A path that quietly lost either would
    still produce a preview, and it would still look right in every test but this one.
    """
    argv = ffmpeg.preview_args(
        SOURCE,
        DESTINATION,
        pieces=pieces,
        encoder=encoder,
        device="/dev/dri/renderD128",
        settings=settings,
    )
    first_input = argv.index("-i")

    assert "-ss" not in argv[:first_input], "the first moment is the beginning, so it does not seek"
    assert argv[argv.index("-movflags") + 1] == "+faststart"
    assert "-an" in argv


# --- sprites ----------------------------------------------------------------------------------


def test_the_sheet_is_shaped_to_hold_every_tile() -> None:
    assert ffmpeg.sprite_grid(30) == (6, 5)
    assert ffmpeg.sprite_grid(12) == (6, 2)
    assert ffmpeg.sprite_grid(1) == (1, 1)
    assert ffmpeg.sprite_grid(7) == (6, 2)


def test_a_short_video_does_not_get_a_row_of_blanks() -> None:
    """Four frames is a sheet four wide, not six wide with two holes in it."""
    assert ffmpeg.sprite_grid(4) == (4, 1)


def test_the_tile_command(settings: Settings) -> None:
    pattern = "/staging/tile-%04d.jpg"
    argv = ffmpeg.tile_args(pattern, DESTINATION, columns=6, rows=5, settings=settings)
    assert argv == [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-max_alloc",
        "1073741824",
        "-threads",
        SHARE,
        "-filter_threads",
        SHARE,
        "-start_number",
        "0",
        "-i",
        pattern,
        "-vf",
        "tile=6x5",
        "-frames:v",
        "1",
        "-q:v",
        str(tuning.SPRITE_QUALITY),
        str(DESTINATION),
    ]


# --- reading ffprobe's answer -----------------------------------------------------------------


def test_the_container_does_not_come_from_here() -> None:
    """ffprobe cannot answer this, so it is not asked.

    Its `format_name` for an ordinary MP4 is "mov,mp4,m4a,3gp,3g2,mj2": every format that could
    demux the file, in a fixed order, with no indication of which it actually is. Reading a
    container out of that means picking one, and picking the first calls every MP4 in the library
    a MOV. The gate identified the container from the file's structure before it was ever indexed;
    that is the answer, and this does not offer a second one to disagree with it.
    """
    assert not hasattr(ffmpeg.parse_probe({"format": {"format_name": "mov,mp4"}}), "container")


def test_a_duration_that_is_not_a_duration_is_not_one() -> None:
    """ffprobe says "N/A" for a stream with no timeline and a negative for a live one. Neither is
    a length, and both parse into something if you let them."""
    for raw in ("N/A", "", "-1", None, 0):
        probed = ffmpeg.parse_probe({"format": {"duration": raw}, "streams": []})
        assert probed.duration_ms is None, raw


def test_a_duration_is_read_off_the_stream_when_the_container_has_none() -> None:
    """Matroska routinely carries no duration at container level, and the file plays fine."""
    probed = ffmpeg.parse_probe(
        {
            "format": {"format_name": "matroska,webm"},
            "streams": [{"codec_type": "video", "duration": "12.5", "codec_name": "h264"}],
        }
    )
    assert probed.duration_ms == 12_500


def test_the_streams_are_read_by_kind_not_by_position() -> None:
    probed = ffmpeg.parse_probe(
        {
            "format": {"format_name": "mp4", "duration": "3.0"},
            "streams": [
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
            ],
        }
    )
    assert (probed.vcodec, probed.acodec) == ("h264", "aac")
    assert (probed.width, probed.height) == (1920, 1080)


def test_a_file_with_no_audio_reports_no_audio_rather_than_failing() -> None:
    probed = ffmpeg.parse_probe(
        {
            "format": {"format_name": "mp4"},
            "streams": [{"codec_type": "video", "codec_name": "h264"}],
        }
    )
    assert probed.acodec is None
    assert probed.vcodec == "h264"


def test_nonsense_from_ffprobe_does_not_raise() -> None:
    """It reports; it does not decide. Whether the file is usable was settled by the gate."""
    assert ffmpeg.parse_probe({}).width is None
    assert ffmpeg.parse_probe({"streams": "not a list"}).vcodec is None
    assert ffmpeg.parse_probe({"streams": [{"codec_type": "video", "width": "wide"}]}).width is None


# --- priority ---------------------------------------------------------------------------------
#
# Everything this slice spawns is generated work nobody has asked for yet, so all of it yields the
# machine to whatever somebody IS waiting for. The pair below is what makes that a fact rather than
# an intention: one says this slice asks for the low priority, the other says the playback path
# does not, and either alone would pass over a build that had got it backwards.


async def test_everything_this_slice_spawns_runs_at_the_low_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[Priority] = []

    async def record(argv: list[str], **kwargs: object) -> bytes:
        asked.append(kwargs["priority"])  # type: ignore[arg-type]
        return b""

    async def record_json(argv: list[str], **kwargs: object) -> dict[str, object]:
        asked.append(kwargs["priority"])  # type: ignore[arg-type]
        return {}

    monkeypatch.setattr(ffmpeg, "_run", record)
    monkeypatch.setattr(ffmpeg, "_run_json", record_json)

    await ffmpeg.run(["ffmpeg", "-i", "in.mp4", "out.jpg"])
    await ffmpeg.run_json(["ffprobe", "in.mp4"])

    assert asked == [Priority.BACKGROUND, Priority.BACKGROUND]


async def test_the_playback_path_is_left_at_full_speed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A segment has somebody watching a spinner while it renders, so it takes the machine.

    Checked at the kernel runner both slices share, because that is where the default lives: the
    player passes no priority at all, and what matters is that the absence means normal rather than
    inheriting whatever the last caller asked for.
    """
    asked: list[Priority] = []

    async def record(argv: list[str], **kwargs: object) -> object:
        asked.append(kwargs["priority"])  # type: ignore[arg-type]
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("sift.kernel.media.subprocess.run", record)

    await media.run(["ffmpeg", "-i", "in.mp4", "out.mp4"], time_limit=30)

    assert asked == [Priority.NORMAL]


def test_every_background_command_caps_its_threads(settings: Settings) -> None:
    """ffmpeg helps itself to the whole machine unless told not to, and several of these run at
    once. Uncapped, one encode can take dozens of threads; the app then queues for a timeslice
    behind its own background work, which reads as a slow database and is not one."""
    commands = {
        "frame": ffmpeg.frame_args(SOURCE, 0, size=64, settings=settings),
        "all_frames": ffmpeg.all_frames_args(SOURCE, size=64, settings=settings),
        "still": ffmpeg.still_args(
            SOURCE, DESTINATION, timestamp_ms=0, height=180, quality=4, settings=settings
        ),
        "all_tiles": ffmpeg.all_tiles_args(
            SOURCE, DESTINATION, width=160, quality=4, settings=settings
        ),
        "thumbnail": ffmpeg.thumbnail_args(SOURCE, DESTINATION, timestamp_ms=0, settings=settings),
        "tile": ffmpeg.tile_args("f%03d.jpg", DESTINATION, columns=2, rows=2, settings=settings),
    }
    for name, argv in commands.items():
        assert "-threads" in argv, f"{name} lets ffmpeg take the whole machine"
        assert "-filter_threads" in argv, f"{name} lets the filter graph take the whole machine"


def test_the_preview_caps_the_encoder_and_not_only_the_decoder(settings: Settings) -> None:
    """`-threads` before -i reaches the decoder only. An encode capped there alone still takes
    most of the threads it would uncapped; the same flag in the output position holds it to its
    share. So the preview (the one real video encode here) has to carry it twice, and a single
    occurrence means the encoder is uncapped."""
    argv = ffmpeg.preview_args(
        SOURCE,
        DESTINATION,
        pieces=ACROSS,
        encoder=media.Encoder.CPU,
        device=None,
        settings=settings,
    )
    assert argv.count("-threads") == 2, "the encoder is running unrestricted"
    assert argv.index("-threads") < argv.index("-i") < argv.index("-threads", argv.index("-i"))


def test_the_transfer_characteristics_are_read_and_lowercased_where_the_stream_states_them() -> (
    None
):
    """PQ and HLG are what the tone map keys on. Most files say nothing, and nothing is None."""
    hdr = ffmpeg.parse_probe(
        {
            "streams": [
                {"codec_type": "video", "pix_fmt": "yuv420p10le", "color_transfer": "SMPTE2084"}
            ],
            "format": {},
        }
    )
    assert hdr.color_transfer == "smpte2084"
    plain = ffmpeg.parse_probe(
        {"streams": [{"codec_type": "video", "pix_fmt": "yuv420p"}], "format": {}}
    )
    assert plain.color_transfer is None
    blank = ffmpeg.parse_probe(
        {
            "streams": [{"codec_type": "video", "pix_fmt": "yuv420p", "color_transfer": "  "}],
            "format": {},
        }
    )
    assert blank.color_transfer is None


def test_bit_depth_is_read_where_the_stream_states_it() -> None:
    """The stream's own statement wins over the pixel format, and the two are made to disagree
    here so that the test can see which of them was read.

    They agree on almost every real file, which is exactly the problem: a fixture where both say
    ten passes whether the statement is consulted or ignored. The two describe different things
    (`bits_per_raw_sample` is how deep the coded samples are, and the pixel format is the layout the
    decoder hands back), so the direct answer is the one to take.
    """
    probed = ffmpeg.parse_probe(
        {
            "streams": [
                {"codec_type": "video", "bits_per_raw_sample": "12", "pix_fmt": "yuv420p10le"}
            ],
            "format": {},
        }
    )

    assert probed.bit_depth == 12, "the pixel format was read over the stream's own statement"


def test_a_stated_depth_that_is_nonsense_falls_back_to_the_pixel_format() -> None:
    """Bounded rather than trusted. A zero or a wild number would otherwise be reported as the
    file's colour depth, and the pixel format is always there and always says something."""
    for nonsense in ("0", "-4", "512", "not a number"):
        probed = ffmpeg.parse_probe(
            {
                "streams": [
                    {"codec_type": "video", "bits_per_raw_sample": nonsense, "pix_fmt": "yuv420p"}
                ],
                "format": {},
            }
        )

        assert probed.bit_depth == 8, f"{nonsense} was taken as a colour depth"


def test_bit_depth_comes_from_the_pixel_format_where_the_stream_does_not_state_it() -> None:
    """A format is named for what it holds, and many encoders write nothing else."""
    probed = ffmpeg.parse_probe(
        {"streams": [{"codec_type": "video", "pix_fmt": "yuv444p12le"}], "format": {}}
    )

    assert probed.bit_depth == 12


def test_an_ordinary_pixel_format_carries_eight() -> None:
    """`yuv420p` has no number after the subsampling, and eight is what that means."""
    probed = ffmpeg.parse_probe(
        {"streams": [{"codec_type": "video", "pix_fmt": "yuv420p"}], "format": {}}
    )

    assert probed.bit_depth == 8


def test_the_subsampling_is_not_mistaken_for_the_depth() -> None:
    """The 420 of `yuv420p` is how the colour is sampled, not how deep it is. Reading the digits
    without accounting for it reports every ordinary file as four hundred and twenty bits."""
    probed = ffmpeg.parse_probe(
        {"streams": [{"codec_type": "video", "pix_fmt": "yuv422p10le"}], "format": {}}
    )

    assert probed.bit_depth == 10


def test_bit_depth_is_unknown_where_nothing_says() -> None:
    probed = ffmpeg.parse_probe({"streams": [{"codec_type": "video"}], "format": {}})

    assert probed.bit_depth is None


# --- how many frames a second -------------------------------------------------------------------


def test_the_average_rate_is_preferred_over_the_highest_the_timebase_can_express() -> None:
    """The two really disagree here, which is the only way this asserts a preference.

    A phone recording reports an r_frame_rate wildly above anything the file contains, so a test
    where both agree could not tell the rule from its absence.
    """
    rate = ffmpeg._frame_rate({"avg_frame_rate": "30000/1001", "r_frame_rate": "90000/1"})

    assert rate is not None
    assert 29.9 < rate < 30.0


def test_a_rate_that_is_not_a_number_is_passed_over_rather_than_raising() -> None:
    """A malformed rate must not be the thing that stops a file being read at all.

    Both halves: the pair that will not parse, and the one that parses to zero. Each falls through
    to the next key, and a file offering only nonsense has no rate rather than an exception.
    """
    assert ffmpeg._frame_rate({"avg_frame_rate": "many/1", "r_frame_rate": "25/1"}) == 25.0
    assert ffmpeg._frame_rate({"avg_frame_rate": "0/0", "r_frame_rate": "24/1"}) == 24.0
    assert ffmpeg._frame_rate({"avg_frame_rate": "many/1", "r_frame_rate": "0/0"}) is None


def test_a_rate_in_the_wrong_shape_entirely_is_passed_over() -> None:
    assert ffmpeg._frame_rate({"avg_frame_rate": 30, "r_frame_rate": "no-slash"}) is None


# --- the seconds two programs have to agree on ---------------------------------------------------


def test_a_whole_number_of_seconds_carries_no_decimal_point() -> None:
    assert ffmpeg.stash_box_seconds(12.0) == "12"


def test_a_fraction_of_a_second_keeps_exactly_the_digits_that_read_back() -> None:
    """The shortest decimal that round-trips, rather than a rounded one.

    This number is fed to another implementation and compared, so a value that prints differently
    is a value that stops matching, which is the whole worth of the fingerprint it feeds.
    """
    assert ffmpeg.stash_box_seconds(12.5) == "12.5"
    assert float(ffmpeg.stash_box_seconds(1.7976931348623157)) == 1.7976931348623157


# --- which video stream IS the file, when a container carries more than one ----------------------


def test_cover_art_is_not_read_as_the_picture() -> None:
    """An album cover or a poster frame is a video stream of one frame, and ffprobe marks it. Read
    as the file's own picture it gives the poster's dimensions and a frame rate of nothing.

    Dropped rather than ranked, and that is not the same thing: a poster frame that happened to
    state a longer duration than the film would win the comparison below and be picked anyway.
    """
    probed = ffmpeg.parse_probe(
        {
            "format": {"format_name": "mp4", "duration": "300.0"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "mjpeg",
                    "width": 600,
                    "height": 600,
                    "nb_frames": "1",
                    "duration": "9999.0",
                    "disposition": {"attached_pic": 1},
                },
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "nb_frames": "7200",
                    "duration": "300.0",
                },
            ],
        }
    )

    assert (probed.width, probed.height) == (1920, 1080)
    assert probed.vcodec == "h264"


def test_a_GIF_whose_still_COVER_comes_first_is_read_as_the_GIF() -> None:
    """The case this rule exists for, and neither stream is marked in it.

    The shape of a real animated AVIF: stream 0 is one frame at 1 fps, stream 1 is 213 frames at
    30 fps over 7.1 seconds. Read from stream 0, the file would become a photograph: no length,
    no GIF chip, no hover preview, on a file that plays perfectly.
    """
    probed = ffmpeg.parse_probe(
        {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "av1",
                    "width": 1440,
                    "height": 1800,
                    "nb_frames": "1",
                    "avg_frame_rate": "1/1",
                },
                {
                    "codec_type": "video",
                    "codec_name": "av1",
                    "width": 1440,
                    "height": 1800,
                    "nb_frames": "213",
                    "duration": "7.1",
                    "avg_frame_rate": "30/1",
                },
            ],
        }
    )

    assert probed.duration_ms == 7_100
    assert probed.fps == 30.0


def test_the_moving_picture_is_named_by_its_place_among_the_pictures() -> None:
    """A filter graph names a stream as `v:K`, counted among the video streams only.

    ffprobe's own `index` counts every stream, so a file with its sound first would name a stream
    that does not exist. The AVIF shape: the still cover first, the frames second.
    """
    cover = {"codec_type": "video", "codec_name": "av1", "nb_frames": "1"}
    frames = {"codec_type": "video", "codec_name": "av1", "nb_frames": "270", "duration": "9.0"}
    sound = {"codec_type": "audio", "codec_name": "aac"}

    assert ffmpeg.parse_probe({"streams": [cover, frames]}).picture_stream == 1
    assert ffmpeg.parse_probe({"streams": [sound, cover, frames]}).picture_stream == 1
    assert ffmpeg.parse_probe({"streams": [frames]}).picture_stream == 0
    assert ffmpeg.parse_probe({"streams": [sound]}).picture_stream == 0


def test_a_stream_with_no_frame_count_is_ranked_by_how_long_it_runs() -> None:
    """Most of a library is Matroska and MP4, where `nb_frames` is frequently absent, so a rule
    resting on it alone would be a coin toss on the files it matters most for."""
    probed = ffmpeg.parse_probe(
        {
            "format": {"format_name": "matroska,webm"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "duration": "0.5", "width": 320},
                {"codec_type": "video", "codec_name": "hevc", "duration": "42.0", "width": 3840},
            ],
        }
    )

    assert probed.vcodec == "hevc"
    assert probed.width == 3840


def test_streams_that_cannot_be_told_apart_come_back_the_same_one_every_time() -> None:
    """A tie must not silently change what a library records. `max` keeps the FIRST of equals, so a
    file whose streams say nothing that separates them reads the same on every scan."""
    alike = {"codec_type": "video", "codec_name": "h264"}
    probed = ffmpeg.parse_probe(
        {
            "format": {"format_name": "mp4"},
            "streams": [{**alike, "width": 111}, {**alike, "width": 222}],
        }
    )

    assert probed.width == 111


def test_a_container_carrying_no_picture_at_all_reports_none() -> None:
    """An audio-only file reaches the same code, and there is nothing for it to pick."""
    probed = ffmpeg.parse_probe(
        {
            "format": {"format_name": "mp4"},
            "streams": [{"codec_type": "audio", "codec_name": "aac"}],
        }
    )

    assert probed.vcodec is None
    assert probed.width is None


def test_the_sound_of_a_file_is_read_off_its_first_audio_track() -> None:
    """Two facts a planned feature needs and no later pass can invent cheaply: the only way to
    learn them afterwards is to read every file in the library again."""
    probed = ffmpeg.parse_probe(
        {
            "format": {"format_name": "mp4"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920},
                {"codec_type": "audio", "codec_name": "aac", "channels": 6, "sample_rate": "48000"},
                {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "44100"},
            ],
        }
    )

    assert probed.audio_channels == 6
    assert probed.audio_sample_rate == 48000


def test_a_file_with_no_sound_says_nothing_about_it() -> None:
    """None and not zero: a silent file has no track, and zero channels is a claim about one."""
    probed = ffmpeg.parse_probe(
        {"format": {"format_name": "mp4"}, "streams": [{"codec_type": "video", "width": 640}]}
    )

    assert probed.audio_channels is None
    assert probed.audio_sample_rate is None


def test_where_the_camera_was_standing_is_not_kept() -> None:
    """The known positive for the one rule the kept answer has.

    The tags are the real ones an mp4 muxer writes for a QuickTime location atom: the atom's own
    name is rewritten as a plain `location` with a `location-<language>` beside it, so a rule
    matching the atom's full name would keep both of the keys a real file carries.
    """
    answer = {
        "format": {
            "format_name": "mp4",
            "tags": {
                "title": "Holiday",
                "encoder": "Lavf60.16.100",
                "location": "+51.5074-000.1278/",
                "location-eng": "+51.5074-000.1278/",
                "com.apple.quicktime.location.ISO6709": "+51.5074-000.1278/",
            },
        },
        "streams": [{"codec_type": "video", "tags": {"GPSLatitude": "51.5", "encoder": "x264"}}],
    }

    stripped = ffmpeg.strip_places(answer)

    assert stripped["format"]["tags"] == {"title": "Holiday", "encoder": "Lavf60.16.100"}
    assert stripped["streams"][0]["tags"] == {"encoder": "x264"}


def test_what_is_kept_comes_back_the_way_it_went_in() -> None:
    """The stored form is this module's business and nobody else's, so both halves live here."""
    answer = {"format": {"format_name": "mp4", "tags": {"title": "Holiday"}}, "streams": []}

    body = ffmpeg.probe_body(answer)

    assert body != b""
    assert ffmpeg.read_kept_probe(body) == answer


def test_a_kept_body_that_will_not_decompress_is_refused_as_unreadable() -> None:
    """One exception for a caller to catch, whichever half of the stored form was damaged."""
    with pytest.raises(ValueError, match="does not decompress"):
        ffmpeg.read_kept_probe(b"not a compressed reading")


async def test_a_tool_that_will_not_say_its_version_answers_empty_and_is_asked_once(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not worth failing a probe over, and not worth a launch per file to find out again."""
    launches: list[list[str]] = []

    async def refuses(command: list[str], **kwargs: object) -> bytes:
        launches.append(command)
        raise ffmpeg.FFmpegError("ffprobe could not be run")

    monkeypatch.setattr(ffmpeg, "run", refuses)
    monkeypatch.setattr(ffmpeg, "_TOOL_VERSIONS", {})

    assert await ffmpeg.probe_tool(settings=settings) == ""
    assert await ffmpeg.probe_tool(settings=settings) == ""
    assert len(launches) == 1


def test_the_same_reading_of_the_same_file_is_the_same_bytes() -> None:
    """A row rewritten on every probe with an identical answer in a different order is a row that
    looks like it changed."""
    one = {"format": {"a": 1, "b": 2}, "streams": []}
    other = {"streams": [], "format": {"b": 2, "a": 1}}

    assert ffmpeg.probe_body(one) == ffmpeg.probe_body(other)


def test_the_repair_is_built_from_the_same_flags_every_other_job_driven_launch_is(
    settings: Settings,
) -> None:
    """It takes the shared flags rather than spelling its own, so it carries `-max_alloc`.

    A repair reads a file whose container is already known to be odd, which is the worst place to
    let ffmpeg size a buffer from what the header claims. The thread cap that comes with the shared
    flags is honestly near-free on a stream copy (nothing is decoded and nothing encoded), so
    what this is about is the ceiling and having one builder rather than two.
    """
    repaired = Path("/library/repaired.mp4")
    argv = ffmpeg.remux_args(SOURCE, repaired, settings=settings)

    assert argv == [
        settings.ffmpeg_path,
        *media.background_flags(settings),
        "-i",
        SOURCE_ARG,
        # The repair itself, unchanged: every stream copied, nothing re-compressed, index in front.
        "-map",
        "0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(repaired),
    ]
    # Named outright as well as taken from the shared flags, because the ceiling is the whole
    # reason this builder does not spell its own.
    assert "-max_alloc" in argv
    assert argv.index("-max_alloc") < argv.index("-i")
