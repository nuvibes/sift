# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ffmpeg command, assembled and inspected without running anything: the properties are about
the command's shape, and the wrong argument order still makes correct video."""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.kernel.media import Encoder, FFmpegError
from sift.slices.player import tuning
from sift.slices.player.transcode import (
    SegmentSpec,
    realtime_ratio,
    segment_args,
    split_fragment,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


def spec(**overrides: object) -> SegmentSpec:
    base: dict[str, object] = {
        "source": Path("/library/clip.mkv"),
        "destination": Path("/cache/seg.m4s"),
        "start_seconds": 0.0,
        "duration_seconds": 2.0,
    }
    base.update(overrides)
    return SegmentSpec(**base)  # type: ignore[arg-type]


# --- the silent ten-second mistake -----------------------------------------------------------------


def test_the_seek_goes_before_the_input() -> None:
    """The seek goes before the input, so ffmpeg jumps rather than decoding from the start; both
    make identical video, so only the argv shows it."""
    argv = segment_args(spec(start_seconds=90.0), settings=Settings())

    assert "-ss" in argv
    assert argv.index("-ss") < argv.index("-i"), (
        "-ss after -i decodes the whole file up to the offset and throws it away"
    )


def test_the_decoder_goes_before_the_input_too() -> None:
    """`-hwaccel` goes before the input, where it applies; after `-i` it silently does nothing."""
    argv = segment_args(spec(start_seconds=90.0, decode=("-hwaccel", "cuda")), settings=Settings())

    assert argv.index("-hwaccel") < argv.index("-i")
    assert argv[argv.index("-hwaccel") + 1] == "cuda"


def test_a_machine_with_no_card_gets_the_command_it_always_got() -> None:
    """The known positive for the case above, and the thing that protects the ordinary machine:
    an empty `decode` has to leave the command byte for byte as it was."""
    assert "-hwaccel" not in segment_args(spec(start_seconds=90.0), settings=Settings())


def test_no_seek_is_emitted_for_the_first_segment() -> None:
    """`-ss 0` is harmless but noise. Its absence is what says segment zero starts at zero."""
    argv = segment_args(spec(start_seconds=0.0), settings=Settings())
    assert "-ss" not in argv


def test_a_segment_is_bounded_by_its_duration() -> None:
    argv = segment_args(spec(duration_seconds=1.4), settings=Settings())
    assert argv[argv.index("-t") + 1] == "1.400"


# --- remux vs transcode ------------------------------------------------------------------------------


def test_every_segment_is_encoded_and_none_is_a_stream_copy() -> None:
    """Every segment is encoded; a `-c copy` would be the broken per-segment tier (see
    `policy.decide`)."""
    argv = segment_args(spec(), settings=Settings())

    assert "copy" not in argv, "segments are always encoded; the copy tier is a whole-file one"
    assert argv[argv.index("-c:v") + 1] == Encoder.CPU.value


def test_a_transcode_produces_h264_and_aac() -> None:
    """The compat encode: what every browser on every site can play."""
    argv = segment_args(spec(), settings=Settings())

    assert argv[argv.index("-c:v") + 1] == Encoder.CPU.value
    assert argv[argv.index("-c:a") + 1] == "aac"
    assert argv[argv.index("-preset") + 1] == tuning.COMPAT_PRESET
    assert argv[argv.index("-crf") + 1] == str(tuning.COMPAT_CRF)


def test_a_transcode_forces_a_keyframe_at_the_segment_boundary() -> None:
    """Without this the segments do not begin at a keyframe, and a player seeking to one shows a
    grey mess until the next one arrives."""
    argv = segment_args(spec(), settings=Settings())
    assert "-force_key_frames" in argv


def test_segments_are_fragmented_mp4() -> None:
    argv = segment_args(spec(), settings=Settings())
    assert argv[argv.index("-f") + 1] == "mp4"
    assert "empty_moov" in argv[argv.index("-movflags") + 1]


def test_each_segment_carries_its_own_place_on_the_timeline() -> None:
    """Otherwise every segment claims to start at zero and the player cannot stitch them."""
    argv = segment_args(spec(start_seconds=6.0), settings=Settings())
    assert argv[argv.index("-output_ts_offset") + 1] == "6.000"


# --- scaling ------------------------------------------------------------------------------------------


def test_scaling_keeps_the_aspect_ratio_and_never_enlarges() -> None:
    """`-2` rounds the width to an even number, which H.264 requires; `min(h,ih)` is what stops a
    file already smaller than the ceiling from being scaled *up* into a bigger, slower, blurrier
    encode than the source."""
    argv = segment_args(spec(scale_height=720), settings=Settings())

    filters = argv[argv.index("-vf") + 1]
    assert "scale=-2:min(720" in filters


def test_an_hdr_source_is_mapped_to_sdr_before_it_is_scaled_and_an_ordinary_one_is_not() -> None:
    """The map sits in front of the scale, so the scale and the encoder both see an ordinary
    picture; a file that is not HDR gets exactly the chain it always got."""
    argv = segment_args(spec(scale_height=720, hdr=True), settings=Settings())
    filters = argv[argv.index("-vf") + 1]
    assert filters.startswith("zscale=t=linear")
    assert "tonemap=" in filters
    assert filters.index("tonemap=") < filters.index("scale=-2:min(720")
    assert "format=yuv420p" in filters

    plain = segment_args(spec(scale_height=720, hdr=False), settings=Settings())
    assert "tonemap" not in plain[plain.index("-vf") + 1]


def test_no_scale_filter_is_added_when_the_picture_is_kept() -> None:
    """The ordinary case, and the default: full resolution unless measurement says no."""
    argv = segment_args(spec(scale_height=None), settings=Settings())
    assert "-vf" not in argv


# --- hardware encoders ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("encoder", "expected"),
    [
        (Encoder.NVENC, "-cq"),
        (Encoder.QSV, "-global_quality"),
    ],
)
def test_a_hardware_encoder_gets_its_own_quality_flag(encoder: Encoder, expected: str) -> None:
    """Each encoder spells quality differently, and giving one another's flag fails to open."""
    argv = segment_args(spec(encoder=encoder), settings=Settings())
    assert argv[argv.index("-c:v") + 1] == encoder.value
    assert expected in argv


def test_vaapi_is_given_its_render_node() -> None:
    argv = segment_args(
        spec(encoder=Encoder.VAAPI, device="/dev/dri/renderD128"), settings=Settings()
    )
    assert argv[argv.index("-vaapi_device") + 1] == "/dev/dri/renderD128"
    assert "hwupload" in argv[argv.index("-vf") + 1]


def test_vaapi_without_a_render_node_is_refused_rather_than_attempted() -> None:
    """It cannot work, and failing here says so with the reason attached."""
    with pytest.raises(ValueError, match="render node"):
        segment_args(spec(encoder=Encoder.VAAPI, device=None), settings=Settings())


# --- splitting the fragment --------------------------------------------------------------------------------


def test_a_fragment_is_cut_into_a_header_and_its_frames() -> None:
    """ffmpeg writes a self-contained little file; HLS wants the header separately."""
    raw = _box(b"ftyp", b"isom") + _box(b"moov", b"tracks") + _box(b"moof", b"frames")

    init, media = split_fragment(raw)

    assert b"ftyp" in init
    assert b"moov" in init
    assert b"moof" in media
    assert b"moov" not in media, "the header must not be repeated in every segment"
    assert init + media == raw, "cutting must not lose or duplicate a byte"


def test_a_fragment_whose_header_has_no_track_description_is_refused() -> None:
    """An init segment without `moov` is refused, so the reason reaches the job's error column."""
    raw = _box(b"ftyp", b"isom") + _box(b"moof", b"frames")

    with pytest.raises(FFmpegError, match="track description"):
        split_fragment(raw)


def test_boxes_before_the_media_are_carried_into_the_header() -> None:
    """The header cut is made at `moof`, so boxes before the media, known or not, are kept."""
    raw = (
        _box(b"ftyp", b"isom")
        + _box(b"free", b"padding")
        + _box(b"moov", b"tracks")
        + _box(b"sidx", b"index")
        + _box(b"moof", b"frames")
    )

    init, media = split_fragment(raw)

    assert b"moov" in init
    assert b"sidx" in init
    assert media.startswith((4 + 4 + len(b"frames")).to_bytes(4, "big") + b"moof")


def test_a_fragment_that_is_all_header_yields_no_media() -> None:
    """A zero-frame render. The honest answer rather than a guess at where media would have been."""
    raw = _box(b"ftyp", b"isom") + _box(b"moov", b"tracks")
    init, media = split_fragment(raw)

    assert init == raw
    assert media == b""


def test_a_box_claiming_an_impossible_length_does_not_loop_forever() -> None:
    """This walks bytes ffmpeg produced, but a truncated or corrupt file is a real outcome (a
    full disk mid-write), and a parser that never terminates on one is worse than a wrong answer.
    """
    raw = (2).to_bytes(4, "big") + b"ftyp"
    init, media = split_fragment(raw)
    assert init + media == raw


def test_a_box_claiming_to_run_to_the_end_of_the_file_terminates() -> None:
    """A zero length means "everything after this", so there is no media following it."""
    raw = (0).to_bytes(4, "big") + b"moov" + b"whatever"
    _, media = split_fragment(raw)
    assert media == b""


def test_something_far_too_short_to_be_a_box_is_handled() -> None:
    init, media = split_fragment(b"abc")
    assert init == b"abc"
    assert media == b""


def _box(name: bytes, payload: bytes) -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + name + payload


# --- the measured realtime ratio -----------------------------------------------------------------------


def test_the_realtime_ratio_is_video_seconds_over_wall_clock() -> None:
    """Two seconds of video produced in one second of work is 2x, and 2x sustains playback."""
    assert realtime_ratio(elapsed_seconds=1.0, video_seconds=2.0) == 2.0


@pytest.mark.parametrize(
    ("elapsed", "video"),
    [(0.0, 2.0), (-1.0, 2.0), (1.0, 0.0), (1.0, -2.0)],
)
def test_a_ratio_that_cannot_be_computed_is_reported_as_unknown(
    elapsed: float, video: float
) -> None:
    """Rather than as zero, which would read as "infinitely slow" and refuse a working file."""
    assert realtime_ratio(elapsed_seconds=elapsed, video_seconds=video) is None


@pytest.mark.parametrize(
    "encoder", [Encoder.CPU, Encoder.NVENC, Encoder.QSV], ids=lambda e: e.value
)
def test_every_software_fed_encoder_is_told_to_produce_8_bit_420(encoder: Encoder) -> None:
    """Every encoder is given `-pix_fmt yuv420p`: NVENC cannot take ProRes's `yuv422p10le`."""
    argv = segment_args(spec(encoder=encoder), settings=Settings())

    assert argv[argv.index("-pix_fmt") + 1] == "yuv420p"


def test_vaapi_is_not_given_a_second_opinion_about_the_pixel_format() -> None:
    """Its frames are converted and uploaded by the filter chain, and two answers conflict."""
    argv = segment_args(
        spec(encoder=Encoder.VAAPI, device="/dev/dri/renderD128"), settings=Settings()
    )

    assert "-pix_fmt" not in argv
    assert "format=nv12" in argv[argv.index("-vf") + 1]
