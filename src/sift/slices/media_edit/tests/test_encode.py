# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Sift asks ffmpeg for, read off the argument list. The built image's conformance check
proves the output; this proves the flags."""

from __future__ import annotations

from pathlib import Path

from sift.kernel.config import Settings
from sift.kernel.media import HDR_TO_SDR
from sift.slices.media_edit import encode, tuning


def _args(settings: Settings, *, at_ms: int) -> list[str]:
    return encode.sample_args(
        Path("/in.mkv"), Path("/out.mp4"), rung=tuning.RUNGS[0], at_ms=at_ms, settings=settings
    )


def test_a_sample_from_partway_in_seeks_there(settings: Settings) -> None:
    argv = _args(settings, at_ms=90_000)
    assert "-ss" in argv
    # Before the input, so ffmpeg jumps rather than decoding and throwing away a minute and a half.
    assert argv.index("-ss") < argv.index("-i")


def test_a_sample_from_the_very_start_does_not_seek_at_all(settings: Settings) -> None:
    """A sample from the very start does not seek: `-ss 0` makes some builds write an empty file."""
    assert "-ss" not in _args(settings, at_ms=0)


def test_the_sound_is_copied_unless_the_container_cannot_carry_it(settings: Settings) -> None:
    """The promise the whole module is written around, asserted rather than described."""
    copied = encode.compress_args(
        Path("/in.mkv"), Path("/out.mp4"), rung=tuning.RUNGS[0], settings=settings
    )
    assert copied[copied.index("-c:a") + 1] == "copy"

    rebuilt = encode.compress_args(
        Path("/in.mkv"),
        Path("/out.mp4"),
        rung=tuning.RUNGS[0],
        settings=settings,
        convert_audio=True,
    )
    assert rebuilt[rebuilt.index("-c:a") + 1] == tuning.FALLBACK_ACODEC


def test_a_rewrap_never_re_encodes_the_picture(settings: Settings) -> None:
    """A compatibility conversion of an H.264 file copies the picture packets."""
    argv = encode.rewrap_args(Path("/in.mkv"), Path("/out.mp4"), settings=settings)
    assert argv[argv.index("-c:v") + 1] == "copy"
    assert "-crf" not in argv


# --- the filter chain, which is where the picture is actually changed ----------------------------


def test_the_chain_is_nothing_at_all_when_the_picture_is_left_as_it_is() -> None:
    """None rather than an empty string. An empty `-vf` is an argument ffmpeg reads as a chain, and
    a rung that changes neither the size nor the rate should not be filtering at all."""
    assert encode.video_filters(height=None, fps=None) is None


def test_a_rung_scales_on_the_free_axis_and_never_scales_a_small_source_UP() -> None:
    """`-2` keeps the shape on an even number; `min(N,ih)` never scales a small source up."""
    chain = encode.video_filters(height=720, fps=None)
    assert chain == "scale=-2:min(720\\,ih)"


def test_the_rate_is_set_where_the_rung_asks_for_one() -> None:
    assert encode.video_filters(height=None, fps=30) == "fps=30"
    # `:g` rather than a plain format, so 29.97 is written out and 30.0 is not written as "30.0".
    assert encode.video_filters(height=None, fps=29.97) == "fps=29.97"


def test_an_HDR_SOURCE_IS_TONE_MAPPED_IN_FRONT_OF_EVERYTHING_ELSE() -> None:
    """An HDR source is tone-mapped first, by the kernel's chain, before the 8-bit 4:2:0 encode."""
    chain = encode.video_filters(height=720, fps=30, hdr=True)

    assert chain is not None
    assert chain.startswith(HDR_TO_SDR), "the tone map is not in front of the chain"
    assert chain == f"{HDR_TO_SDR},scale=-2:min(720\\,ih),fps=30"
    # And an ordinary picture is not mapped: the map darkens one that never needed it.
    assert HDR_TO_SDR not in (encode.video_filters(height=720, fps=30) or "")
