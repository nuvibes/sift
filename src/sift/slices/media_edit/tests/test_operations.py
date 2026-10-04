# SPDX-License-Identifier: AGPL-3.0-or-later
"""What ffmpeg is asked for, read rather than inferred.

Every one of these builds a command and looks at it. That is the right split for an argument list
and it leaves one hole, which is that an argument can be perfectly well-formed and mean something
different on the ffmpeg that ships. The conformance check runs these same builders against the real
tool inside the image, and that is where a behaviour is confirmed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.kernel.ingress import ALLOWED_MEDIA, Kind
from sift.slices.media_edit import operations
from sift.slices.media_edit.operations import (
    MOVING_FORMATS,
    STILL_FORMATS,
    Operation,
    Turn,
)
from sift.slices.media_edit.tuning import GIF_FPS, GIF_SHORT_EDGE

SOURCE = Path("/library/holiday.jpg")
SCRATCH = Path("/library/.sift-working-01")


def _after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


# --- the formats are the allowlist's, not a second list -------------------------------------------


def test_every_format_sift_accepts_has_somewhere_to_be_saved() -> None:
    """The property the module checks as it loads, asserted here so the reason is written down.

    A format Sift takes in and cannot write back out is an editor offered on a file it will refuse
    at the last moment. Adding one to the allowlist without adding it here has to fail, and it has
    to fail on the way up rather than months later.
    """
    for media in ALLOWED_MEDIA:
        if media.kind is Kind.IMAGE:
            assert media.name in STILL_FORMATS
        elif media.kind is Kind.VIDEO:
            assert media.name in MOVING_FORMATS


def test_a_missing_format_is_an_error_while_the_application_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutate the table away and watch the check refuse, rather than trusting that it would."""
    monkeypatch.setitem(operations._STILL_NAME_BY_MIME, "image/nonesuch", "nonesuch")
    with pytest.raises(RuntimeError, match="nowhere to save"):
        operations._check_every_format_has_somewhere_to_go()


def test_a_photograph_off_a_phone_is_saved_as_a_jpeg() -> None:
    """The one format that does not keep its own, because the shipped ffmpeg cannot write it."""
    assert operations.still_format_for("image/heic") is not None
    assert operations.still_format_for("image/heic").extension == "jpg"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("mime", "extension"),
    [("image/jpeg", "jpg"), ("image/png", "png"), ("image/webp", "webp")],
)
def test_every_other_picture_keeps_its_own_format(mime: str, extension: str) -> None:
    fmt = operations.still_format_for(mime)
    assert fmt is not None and fmt.extension == extension


def test_a_file_that_is_not_a_picture_has_no_still_format() -> None:
    assert operations.still_format_for("video/mp4") is None
    assert operations.still_format_for(None) is None


@pytest.mark.parametrize(
    ("mime", "muxer"),
    [
        ("video/mp4", "mp4"),
        ("video/quicktime", "mov"),
        ("video/x-matroska", "matroska"),
        ("video/webm", "webm"),
    ],
)
def test_a_cut_keeps_the_container_it_came_from(mime: str, muxer: str) -> None:
    fmt = operations.moving_format_for(mime)
    assert fmt is not None and fmt.muxer == muxer


def test_a_gif_is_not_something_this_will_cut() -> None:
    """A GIF and an animated WebP both answer no, and the promise is why.

    A cut is a stream copy. A GIF's frames each depend on the one before, and an animated WebP is
    not readable by ffmpeg at all: what would be cut is the video Sift built to display it. Both
    would have to be re-encoded, and a trim that quietly re-encodes is the one thing this is not.
    """
    assert operations.moving_format_for("image/gif") is None
    assert operations.moving_format_for("image/webp") is None


# --- the still commands ---------------------------------------------------------------------------


def test_a_crop_keeps_exactly_the_rectangle_it_was_given(settings: Settings) -> None:
    """No rounding to even numbers. That rule belongs to video encoders, not to pictures."""
    argv = operations.still_args(
        SOURCE,
        SCRATCH,
        filters=[operations.crop_filter(left=11, top=7, width=641, height=403)],
        fmt=STILL_FORMATS["jpeg"],
        settings=settings,
    )
    assert _after(argv, "-vf") == "crop=641:403:11:7"
    assert _after(argv, "-frames:v") == "1"
    assert argv[-1] == str(SCRATCH)


def test_a_resize_keeps_the_shape(settings: Settings) -> None:
    argv = operations.still_args(
        SOURCE,
        SCRATCH,
        filters=[operations.resize_filter(width=1280)],
        fmt=STILL_FORMATS["png"],
        settings=settings,
    )
    assert _after(argv, "-vf") == "scale=1280:-1"


@pytest.mark.parametrize(
    ("turn", "chain"),
    [
        (Turn.RIGHT, "transpose=1"),
        (Turn.LEFT, "transpose=2"),
        (Turn.HALF, "transpose=2,transpose=2"),
    ],
)
def test_each_turn_has_its_own_filter(turn: Turn, chain: str, settings: Settings) -> None:
    argv = operations.still_args(
        SOURCE,
        SCRATCH,
        filters=[operations.turn_filter(turn)],
        fmt=STILL_FORMATS["jpeg"],
        settings=settings,
    )
    assert _after(argv, "-vf") == chain


def test_a_still_always_names_its_muxer_and_its_codec(settings: Settings) -> None:
    """The scratch path has no extension, so anything ffmpeg is not told it has to guess.

    This is the whole reason the format tables exist. Left to infer, ffmpeg reads a path with no
    suffix on it and picks, and what it picks is not something to depend on.
    """
    for name, fmt in STILL_FORMATS.items():
        argv = operations.still_args(
            SOURCE,
            SCRATCH,
            filters=[operations.turn_filter(Turn.RIGHT)],
            fmt=fmt,
            settings=settings,
        )
        assert "-f" in argv, name
        assert "-c:v" in argv, name


def test_writing_one_picture_says_so_rather_than_leaving_the_muxer_to_assume(
    settings: Settings,
) -> None:
    """`-update 1` is what makes the image muxer write the exact path it was handed.

    Without it, ffmpeg warns that the name is not a numbered pattern and its behaviour on a path
    that is not one is not something to build on.
    """
    argv = operations.still_args(
        SOURCE,
        SCRATCH,
        filters=[operations.crop_filter(left=0, top=0, width=10, height=10)],
        fmt=STILL_FORMATS["png"],
        settings=settings,
    )
    assert _after(argv, "-f") == "image2"
    assert _after(argv, "-update") == "1"


# --- the cut ---------------------------------------------------------------------------------------


def test_a_cut_copies_the_streams_rather_than_encoding_them(settings: Settings) -> None:
    """What a TRIM is, and the promise it rests on. If this becomes an encode it is a different
    feature: a trim runs over a whole video and is offered because it costs nothing."""
    argv = operations.cut_args(
        SOURCE,
        SCRATCH,
        start_ms=90_000,
        duration_ms=15_000,
        fmt=MOVING_FORMATS["mp4"],
        settings=settings,
    )
    assert _after(argv, "-c:v") == "copy"
    assert _after(argv, "-c:a") == "copy"
    assert "-crf" not in argv
    assert "libx264" not in argv
    assert "-shortest" not in argv


def test_an_exact_cut_re_encodes_the_picture_and_still_copies_the_sound(settings: Settings) -> None:
    """What a CLIP is. Re-encoding the picture is the only way a piece can begin where it was
    marked; re-encoding the sound would lose quality to fix something already inaudible."""
    argv = operations.cut_args(
        SOURCE,
        SCRATCH,
        start_ms=9_000,
        duration_ms=1_900,
        fmt=MOVING_FORMATS["mp4"],
        settings=settings,
        exact=True,
    )
    assert _after(argv, "-c:v") == "libx264"
    assert _after(argv, "-crf") == "18"
    assert _after(argv, "-c:a") == "aac"
    # Cut at whichever stream runs out first, so the piece is never LONGER than what was marked.
    assert "-shortest" in argv
    # Still a fast seek: ffmpeg jumps to the nearest whole frame and decodes forward to the moment,
    # so the start is exact WITHOUT reading the whole video up to it.
    assert argv.index("-ss") < argv.index("-i")


def test_an_exact_cut_uses_a_codec_the_container_can_actually_hold(settings: Settings) -> None:
    """A `.webm` cannot hold H.264: ffmpeg refuses to write it. Without a per-container encoder
    the promise "a clip is exactly what you marked" would quietly depend on the container, which
    is not something anybody looking at their video can see."""
    in_webm = operations.cut_args(
        SOURCE,
        SCRATCH,
        start_ms=1_000,
        duration_ms=2_000,
        fmt=MOVING_FORMATS["webm"],
        settings=settings,
        exact=True,
    )
    assert _after(in_webm, "-c:v") == "libvpx-vp9"
    assert _after(in_webm, "-c:a") == "libopus"
    assert "libx264" not in in_webm
    assert "aac" not in in_webm
    for name in ("mp4", "mov", "mkv"):
        argv = operations.cut_args(
            SOURCE,
            SCRATCH,
            start_ms=1_000,
            duration_ms=2_000,
            fmt=MOVING_FORMATS[name],
            settings=settings,
            exact=True,
        )
        assert _after(argv, "-c:v") == "libx264", name


def test_only_a_COPY_is_restamped_from_zero(settings: Settings) -> None:
    """An exact clip must not come out at more than twice its length.

    `-avoid_negative_ts make_zero` is what stops a COPIED cut opening on a stall. On a re-encode it
    does the opposite of nothing: the encoder restamps the picture from zero while the sound still
    carries its original timestamps, the shift is computed from the sound, and the picture is pushed
    out behind it. On a 2.309-second mark with the shipped ffmpeg: **5.025 s with the flag, 2.309 s
    without.**"""
    fmt = MOVING_FORMATS["mp4"]
    copied = operations.cut_args(
        SOURCE, SCRATCH, start_ms=9_000, duration_ms=1_900, fmt=fmt, settings=settings
    )
    exact = operations.cut_args(
        SOURCE, SCRATCH, start_ms=9_000, duration_ms=1_900, fmt=fmt, settings=settings, exact=True
    )
    assert _after(copied, "-avoid_negative_ts") == "make_zero"
    assert "-avoid_negative_ts" not in exact


def test_the_seek_goes_before_the_input_and_the_length_after(settings: Settings) -> None:
    """Before the input ffmpeg jumps; after it, it decodes and throws away everything up to there.

    On a two-hour video that is the difference between instant and minutes. The length has to be on
    the other side, where it measures the output.
    """
    argv = operations.cut_args(
        SOURCE,
        SCRATCH,
        start_ms=90_000,
        duration_ms=15_000,
        fmt=MOVING_FORMATS["mp4"],
        settings=settings,
    )
    assert argv.index("-ss") < argv.index("-i") < argv.index("-t")
    assert _after(argv, "-ss") == "90.000"
    assert _after(argv, "-t") == "15.000"


def test_a_cut_from_the_very_beginning_passes_no_seek_at_all(settings: Settings) -> None:
    """`-ss 0` before the input is a no-op on some builds and destroys the output on others.

    Where it destroys the output the failure is silent (an empty file and a zero exit code), and
    it buys nothing when there is nothing to seek past.
    """
    argv = operations.cut_args(
        SOURCE,
        SCRATCH,
        start_ms=0,
        duration_ms=15_000,
        fmt=MOVING_FORMATS["mp4"],
        settings=settings,
    )
    assert "-ss" not in argv


def test_a_cut_starting_mid_file_does_not_open_on_a_stall(settings: Settings) -> None:
    """Without this the first packets keep the timestamps they had in the original."""
    argv = operations.cut_args(
        SOURCE,
        SCRATCH,
        start_ms=5_000,
        duration_ms=1_000,
        fmt=MOVING_FORMATS["mp4"],
        settings=settings,
    )
    assert _after(argv, "-avoid_negative_ts") == "make_zero"


def test_the_index_is_moved_forward_only_where_there_is_one(settings: Settings) -> None:
    """Matroska and WebM have no index to move, so the flag would be noise on them."""
    in_mp4 = operations.cut_args(
        SOURCE, SCRATCH, start_ms=0, duration_ms=1_000, fmt=MOVING_FORMATS["mp4"], settings=settings
    )
    in_mkv = operations.cut_args(
        SOURCE, SCRATCH, start_ms=0, duration_ms=1_000, fmt=MOVING_FORMATS["mkv"], settings=settings
    )
    assert "+faststart" in in_mp4
    assert "+faststart" not in in_mkv


def test_a_cut_keeps_the_sound_and_survives_a_file_with_none(settings: Settings) -> None:
    argv = operations.cut_args(
        SOURCE, SCRATCH, start_ms=0, duration_ms=1_000, fmt=MOVING_FORMATS["mp4"], settings=settings
    )
    assert "0:a?" in argv


# --- what the copy is called -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("milliseconds", "written"),
    [(0, "0s"), (45_000, "45s"), (90_000, "1m30s"), (3_600_000, "1h"), (3_661_000, "1h1m1s")],
)
def test_a_moment_is_written_the_way_somebody_would_say_it(milliseconds: int, written: str) -> None:
    assert operations.stamp(milliseconds) == written


def test_a_moment_before_the_beginning_reads_as_the_beginning() -> None:
    assert operations.stamp(-5_000) == "0s"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"operation": Operation.CROP, "width": 800, "height": 600}, "-cropped-800x600"),
        ({"operation": Operation.RESIZE, "width": 1280}, "-1280px"),
        ({"operation": Operation.ROTATE, "turn": Turn.RIGHT}, "-rotated-right"),
        ({"operation": Operation.ROTATE, "turn": Turn.LEFT}, "-rotated-left"),
        ({"operation": Operation.ROTATE, "turn": Turn.HALF}, "-rotated-180"),
        ({"operation": Operation.TRIM}, "-trimmed"),
        ({"operation": Operation.CLIP, "start_ms": 90_000}, "-from-1m30s"),
    ],
)
def test_each_edit_says_what_it_was_in_the_name(kwargs: dict[str, object], expected: str) -> None:
    assert operations.suffix(**kwargs) == expected  # type: ignore[arg-type]


def test_two_different_crops_of_one_picture_do_not_collide() -> None:
    """The write seam refuses a name something already holds, so this is not cosmetic.

    Without the size in it, cropping a photograph twice is a second edit that will not save until
    somebody renames the first by hand.
    """
    first = operations.suffix(Operation.CROP, width=800, height=600)
    second = operations.suffix(Operation.CROP, width=400, height=400)
    assert first != second


def test_the_copy_keeps_the_original_name_and_takes_the_new_extension() -> None:
    assert (
        operations.output_filename("beach trip.heic", suffix_text="-rotated-right", extension="jpg")
        == "beach trip-rotated-right.jpg"
    )


def test_a_file_with_no_name_at_all_still_gets_one() -> None:
    assert operations.output_filename(None, suffix_text="-trimmed", extension="mp4") == (
        "file-trimmed.mp4"
    )
    assert operations.output_filename(".mp4", suffix_text="-trimmed", extension="mp4") == (
        "file-trimmed.mp4"
    )


@pytest.mark.parametrize(
    ("drawn", "cut"),
    [
        ((0, 0, 800, 600), (0, 0, 800, 600)),
        ((11, 7, 641, 403), (10, 6, 640, 402)),
        ((1, 1, 3, 3), (0, 0, 2, 2)),
    ],
)
def test_a_rectangle_is_snapped_onto_the_pictures_colour_blocks(
    drawn: tuple[int, int, int, int], cut: tuple[int, int, int, int]
) -> None:
    """A screen promising 784 by 605 must not land 784 by 604 on disk.

    Colour in almost every photograph is stored at half resolution, so a crop has to land on
    two-by-two blocks. Handed an odd number the filter rounds it quietly, and every number the
    person was shown is then a number about a different rectangle.
    """
    assert operations.snap_to_even(*drawn) == cut


def test_snapping_only_ever_makes_a_rectangle_smaller() -> None:
    """Rounded down, never up, so a rectangle drawn inside the picture stays inside it."""
    for value in range(0, 40):
        left, top, width, height = operations.snap_to_even(value, value, value, value)
        assert left <= value and top <= value and width <= value and height <= value


# --- the mirrors ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("turn", "chain"),
    [
        (Turn.MIRROR, "hflip"),
        (Turn.FLIP, "vflip"),
    ],
)
def test_each_mirror_has_its_own_filter(turn: Turn, chain: str) -> None:
    """A mirror is not a turn, and the two must not share a filter or a name."""
    assert operations.turn_filter(turn) == chain


def test_every_way_round_has_a_filter_and_a_name() -> None:
    """Read off the enum rather than listed here.

    A list written beside the thing it describes is only ever as right as the last person to edit
    both, so the next value added to the enum fails this rather than shipping as a picture that
    does nothing and a copy named after the empty string.
    """
    for turn in Turn:
        assert operations.turn_filter(turn)
        assert operations.suffix(Operation.ROTATE, turn=turn).startswith("-")


def test_a_mirror_is_not_named_as_a_rotation() -> None:
    """Two copies called `-rotated-left` and `-rotated-mirror` read as two turns. One is not."""
    assert operations.suffix(Operation.ROTATE, turn=Turn.MIRROR) == "-mirrored"
    assert operations.suffix(Operation.ROTATE, turn=Turn.FLIP) == "-flipped"
    assert operations.suffix(Operation.ROTATE, turn=Turn.HALF) == "-rotated-180"


# --- one chain, however many links ------------------------------------------------------------------


def test_several_filters_become_one_chain_in_the_order_they_were_given(settings: Settings) -> None:
    """The order is the difference between cropping the picture and cropping what the turn made."""
    argv = operations.still_args(
        SOURCE,
        SCRATCH,
        filters=["transpose=1", operations.crop_filter(left=1, top=2, width=30, height=40)],
        fmt=STILL_FORMATS["jpeg"],
        settings=settings,
    )
    assert _after(argv, "-vf") == "transpose=1,crop=30:40:1:2"


def test_the_note_is_turned_by_the_chain_alone(settings: Settings) -> None:
    """The chain begins with the photograph's own turn, so ffmpeg must not turn it first: left on,
    a quarter-turn note becomes a half turn in the copy and the rectangle lands elsewhere.
    `-noautorotate` is an input option, so it has to come before `-i`."""
    argv = operations.still_args(
        SOURCE, SCRATCH, filters=["transpose=1"], fmt=STILL_FORMATS["jpeg"], settings=settings
    )
    assert "-noautorotate" in argv
    assert argv.index("-noautorotate") < argv.index("-i")


def test_a_chain_with_nothing_in_it_still_passes_the_frame_through(settings: Settings) -> None:
    """An empty `-vf` is a parse error, and the chain is empty in one ordinary case: an upright
    picture asked only for the turn its own note had already given it."""
    argv = operations.still_args(
        SOURCE, SCRATCH, filters=[], fmt=STILL_FORMATS["jpeg"], settings=settings
    )
    assert _after(argv, "-vf") == "null"


# --- a GIF ------------------------------------------------------------------------------


def _animate(
    fmt: str, settings: Settings, *, landscape: bool = True, start_ms: int = 0
) -> list[str]:
    return operations.gif_args(
        SOURCE,
        SCRATCH,
        start_ms=start_ms,
        duration_ms=3_000,
        fmt=operations.GIF_FORMATS[fmt],
        landscape=landscape,
        settings=settings,
    )


def test_the_graph_names_its_own_input_and_its_own_output(settings: Settings) -> None:
    """The one whose absence makes this fail outright.

    A `-filter_complex` builds its own graph and is NOT fed by `-map`. With the first pad unlabelled
    ffmpeg answers "Cannot find a matching stream for unlabeled input pad fps:default" and exits
    without writing anything, and `-map 0:v:0` maps the source straight past the graph. Both ends
    have to be named.
    """
    argv = _animate("gif", settings)

    graph = _after(argv, "-filter_complex")
    assert graph.startswith("[0:v]")
    assert graph.endswith("[out]")
    assert _after(argv, "-map") == "[out]"


def test_the_palette_is_built_from_the_frames_that_will_be_written(settings: Settings) -> None:
    """A GIF holds 256 colours and there is no default worth having.

    Handed a video with no palette to work from, ffmpeg falls back to a fixed web palette and the
    result is banded and looks nothing like the source. So the graph forks the stream, builds a
    palette from one branch and maps the other through it, and the SCALE comes before the palette,
    which is not interchangeable: a palette built from full-size frames describes colours that are
    not in the smaller output.
    """
    graph = _after(_animate("gif", settings), "-filter_complex")

    assert "palettegen" in graph and "paletteuse" in graph
    assert graph.index("scale=") < graph.index("palettegen")
    assert "stats_mode=full" in graph, "only the first frame's colours is the wrong palette"
    assert f"fps={GIF_FPS}" in graph


def test_the_size_caps_the_SHORT_edge_whichever_way_round_the_picture_is(
    settings: Settings,
) -> None:
    """Capping the width instead only means "480p" for a portrait clip.

    A 16:9 landscape one comes out 480x270: a third of the pixels of the portrait version, from
    the same number, for no reason anybody could see. `-2` rather than `-1` keeps the derived edge
    even, which every encoder here expects.
    """
    assert f"scale=-2:{GIF_SHORT_EDGE}" in _after(_animate("gif", settings), "-filter_complex")
    assert f"scale={GIF_SHORT_EDGE}:-2" in _after(
        _animate("gif", settings, landscape=False), "-filter_complex"
    )


def test_each_format_is_written_by_its_own_encoder_and_its_own_muxer(settings: Settings) -> None:
    """All three animate and Sift files all three as a GIF; what differs is the size, by
    35x. So the format is a choice, and each one has to actually be built as itself."""
    assert _after(_animate("webp", settings), "-c:v") == "libwebp"
    assert _after(_animate("webp", settings), "-f") == "webp"
    assert _after(_animate("avif", settings), "-c:v") == "libsvtav1"
    assert _after(_animate("avif", settings), "-f") == "avif"
    # A GIF's codec IS its muxer, so it names no encoder at all.
    assert "-c:v" not in _animate("gif", settings)
    assert _after(_animate("gif", settings), "-f") == "gif"


def test_only_the_gif_needs_a_forked_graph(settings: Settings) -> None:
    """The asymmetry is the format rather than a preference: a GIF needs a palette built from the
    frames that will be written, and the other two carry their own colour."""
    assert "-filter_complex" in _animate("gif", settings)
    for fmt in ("webp", "avif"):
        argv = _animate(fmt, settings)
        assert "-filter_complex" not in argv
        assert "fps=" in _after(argv, "-vf")


def test_a_gif_loops_forever_and_carries_no_sound(settings: Settings) -> None:
    """`-loop 0` is what everybody means by a GIF; without it it plays once and stops on its
    last frame, which reads as a broken picture. The sound is dropped by name rather than left to
    stream selection, so a source with audio builds the same command as one without."""
    for fmt in ("gif", "webp", "avif"):
        argv = _animate(fmt, settings)
        assert _after(argv, "-loop") == "0"
        assert "-an" in argv


def test_a_gif_seeks_before_the_input_and_measures_after_it(settings: Settings) -> None:
    """The seek goes before the input so ffmpeg jumps to it; the length goes after, where it
    measures the OUTPUT rather than how much input to read. And a seek of zero is left out
    entirely: on some builds `-ss 0` discards the only frame of a single-frame source and exits
    successfully having written nothing."""
    from_the_start = _animate("gif", settings, start_ms=0)
    assert "-ss" not in from_the_start

    later = _animate("gif", settings, start_ms=12_000)
    assert later.index("-ss") < later.index("-i")
    assert later.index("-i") < later.index("-t")


def test_a_gif_is_named_by_the_moment_it_starts_at() -> None:
    """Two GIFs made from one video would otherwise land on the same name, and the write seam
    refuses a name something already holds, which happens in the background job, after the screen
    has already said it was saving."""
    assert operations.suffix(Operation.GIF, start_ms=0, gif_format="gif") == "-gif-from-0s"
    assert operations.suffix(Operation.GIF, start_ms=90_000, gif_format="gif") == "-gif-from-1m30s"


def test_a_gif_is_named_by_the_format_it_is_written_in() -> None:
    """A stem saying `gif` whichever format was about to be produced would land an AVIF as
    `holiday-gif-from-30s.avif`: a name saying one format over an extension saying another."""
    assert operations.suffix(Operation.GIF, start_ms=30_000, gif_format="avif") == "-avif-from-30s"
    assert operations.suffix(Operation.GIF, start_ms=30_000, gif_format="webp") == "-webp-from-30s"


def test_a_gif_with_no_format_is_refused_rather_than_called_a_gif() -> None:
    """The default that suggests itself is `gif`, which is that fault wearing a friendlier
    face. Nothing can reach this: the format is settled before the copy is named."""
    with pytest.raises(ValueError, match="named after the format"):
        operations.suffix(Operation.GIF, start_ms=0)
