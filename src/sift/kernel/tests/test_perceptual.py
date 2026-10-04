# SPDX-License-Identifier: AGPL-3.0-or-later
"""The near-duplicate fingerprint.

The golden tests pin the arithmetic on frames built in Python: these numbers are stored, and a
change orphans every fingerprint ever taken without anything visibly breaking. The behaviour tests
re-encode real pictures through ffmpeg and assert distances, never exact hashes, which ffmpeg may
draw differently between versions.
"""

from __future__ import annotations

import itertools
import math
import struct
import subprocess
from pathlib import Path

import pytest

from sift.kernel.content.perceptual import (
    HASH_FRAME_SIZE,
    NEAR_SHARE,
    PHASH_BITS,
    SCENE_FRAMES,
    _median,
    _selected_median,
    apart,
    distance,
    phash,
    scene_duration,
    scene_frame_times,
    video_phash,
    videohash,
)

pytestmark = pytest.mark.unit

#: Below this many differing bits, two pictures are one picture that has been through something;
#: the tests below fail if the gap between the two populations closes.
NEAR_DUPLICATE_BITS = 12


def structured_frame(shift: float = 0.0, brightness: int = 0) -> bytes:
    """A frame with detail at several scales, built without ffmpeg: a flat or graded picture has
    almost nothing past its first coefficient, so its fingerprint means little."""
    pixels = bytearray()
    for y in range(HASH_FRAME_SIZE):
        for x in range(HASH_FRAME_SIZE):
            value = (
                127
                + 70 * math.sin((x + shift) / 3.0) * math.cos(y / 5.0)
                + 40 * math.sin((x + y + shift) / 7.0)
                + brightness
            )
            pixels.append(max(0, min(255, int(value))))
    return bytes(pixels)


# --- the arithmetic, pinned


def test_the_fingerprint_is_what_it_has_always_been() -> None:
    """The golden value: if it changes, every stored fingerprint is orphaned."""
    assert phash(structured_frame()) == "1e18fe18fe0681ab"


def test_a_fingerprint_is_the_expected_width() -> None:
    assert PHASH_BITS == 63
    assert len(phash(structured_frame())) == 16
    assert int(phash(structured_frame()), 16).bit_length() <= PHASH_BITS


def test_the_same_frame_always_hashes_the_same() -> None:
    assert phash(structured_frame()) == phash(structured_frame())


def test_brightness_alone_does_not_change_the_fingerprint() -> None:
    """Brightness alone does not change the fingerprint: the first coefficient is thrown away.

    Fifteen and no more: the brightest pixel sits 19 below 255, and past that clipping would change
    the structure.
    """
    assert phash(structured_frame()) == phash(structured_frame(brightness=15))


def test_a_different_picture_gets_a_distant_fingerprint() -> None:
    apart = distance(phash(structured_frame()), phash(structured_frame(shift=11.0)))
    assert apart is not None
    assert apart > NEAR_DUPLICATE_BITS


def test_a_frame_of_the_wrong_size_is_refused() -> None:
    """A frame of the wrong size is refused: a truncated subprocess read would hash a picture that
    was never there."""
    with pytest.raises(ValueError, match="1024 bytes"):
        phash(b"\x00" * 100)
    with pytest.raises(ValueError, match="1024 bytes"):
        phash(b"")


def test_a_frame_of_a_consistent_but_unsupported_size_is_refused() -> None:
    """A matching frame of an unsupported size is refused: the cosine table is built for one square,
    and the wrong one would produce a stored fingerprint that matches nothing."""
    with pytest.raises(ValueError, match="built for 32px frames, not 16"):
        phash(b"\x00" * (16 * 16), size=16)


def test_the_median_of_an_even_count_is_the_middle_pair_averaged() -> None:
    """The even-count median, unreachable from `phash` (the kept corner is odd), is right before the
    corner size changes."""
    assert _median([1.0, 3.0]) == 2.0
    assert _median([4.0, 1.0, 3.0, 2.0]) == 2.5
    assert _median([1.0, 2.0, 30.0]) == 2.0


# --- distance


def test_distance_counts_differing_bits() -> None:
    assert distance("0" * 16, "0" * 16) == 0
    assert distance("0" * 16, "0" * 15 + "1") == 1
    assert distance("0" * 16, "0" * 15 + "3") == 2
    assert distance("0" * 16, "f" * 16) == 64


def test_two_fingerprints_of_different_lengths_are_not_comparable() -> None:
    """A still and a video are not comparable, rather than matched on the overlap."""
    assert distance("0" * 16, "0" * 32) is None
    assert distance("", "") is None
    assert distance("nonsense", "nonsense") is None


def test_apart_is_the_share_of_each_fingerprints_own_width() -> None:
    """Apart is the share of each fingerprint's width, the unit `NEAR_SHARE` is stated in."""
    assert apart("0" * 16, "0" * 15 + "3") == 2 / 64
    assert apart("0" * 64, "0" * 63 + "3") == 2 / 256
    assert apart("0" * 16, "0" * 32) is None
    twelve_on_a_frame = apart("0" * 16, "0" * 13 + "fff")
    twelve_on_four_frames = apart("0" * 64, "0" * 61 + "fff")
    assert twelve_on_a_frame is not None and twelve_on_four_frames is not None
    assert twelve_on_four_frames < NEAR_SHARE < twelve_on_a_frame


# --- videohash


def test_a_videos_fingerprint_is_its_frames_in_order() -> None:
    assert videohash(["aaaa", "bbbb"]) == "aaaabbbb"
    assert videohash(["aaaa", "bbbb"]) != videohash(["bbbb", "aaaa"])


def test_a_video_with_no_frames_has_no_fingerprint() -> None:
    """None rather than an empty string, which would match every other empty string."""
    assert videohash([]) is None


def test_one_changed_frame_moves_a_video_only_a_little() -> None:
    """One changed frame moves a video by one frame's bits: the frames are joined, not averaged."""
    frames = [phash(structured_frame(shift=index)) for index in range(10)]
    changed = [*frames[:9], phash(structured_frame(shift=99.0))]
    apart = distance(videohash(frames) or "", videohash(changed) or "")
    assert apart is not None
    assert 0 < apart <= PHASH_BITS


# --- against real pictures, through real ffmpeg


def grey_square(path: Path, settings_ffmpeg: str = "ffmpeg") -> bytes:
    """Reduce a real image to what the hash reads, the way the job does."""
    return subprocess.run(
        [
            settings_ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            # One frame: handed a video, it would return every frame concatenated.
            "-frames:v",
            "1",
            "-vf",
            f"scale={HASH_FRAME_SIZE}:{HASH_FRAME_SIZE}:flags=bilinear",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout


def draw(path: Path, source: str, *filters: str) -> Path:
    argv = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        source,
        "-frames:v",
        "1",
    ]
    if filters:
        argv += ["-vf", ",".join(filters)]
    subprocess.run([*argv, str(path)], check=True, capture_output=True)
    return path


@pytest.mark.integration
def test_a_fingerprint_survives_what_a_duplicate_survives(tmp_path: Path) -> None:
    """A re-encode, a resize and a brightness change leave the fingerprint recognisably the same,
    which a digest of the bytes is not."""
    original = draw(tmp_path / "original.png", "testsrc2=size=640x480:rate=1")
    recompressed = tmp_path / "recompressed.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(original),
            "-q:v",
            "28",
            str(recompressed),
        ],
        check=True,
        capture_output=True,
    )
    resized = draw(tmp_path / "resized.png", "testsrc2=size=640x480:rate=1", "scale=160:120")
    brightened = draw(tmp_path / "bright.png", "testsrc2=size=640x480:rate=1", "eq=brightness=0.15")

    reference = phash(grey_square(original))
    for name, path in (
        ("recompressed", recompressed),
        ("resized", resized),
        ("brightened", brightened),
    ):
        apart = distance(reference, phash(grey_square(path)))
        assert apart is not None
        assert apart <= NEAR_DUPLICATE_BITS, (
            f"{name} was {apart} bits away and should be a duplicate"
        )


@pytest.mark.integration
def test_two_different_pictures_are_not_confused(tmp_path: Path) -> None:
    """Different pictures are not confused, or calling everything a duplicate would pass above."""
    first = phash(grey_square(draw(tmp_path / "a.png", "testsrc2=size=640x480:rate=1")))
    second = phash(grey_square(draw(tmp_path / "b.png", "mandelbrot=size=640x480:rate=1")))
    third = phash(grey_square(draw(tmp_path / "c.png", "testsrc=size=640x480:rate=1")))

    for other in (second, third):
        apart = distance(first, other)
        assert apart is not None
        assert apart > NEAR_DUPLICATE_BITS, f"two different pictures were only {apart} bits apart"


# --- the fingerprint the public stash-boxes share


def bitmap(width: int, height: int, index: int) -> bytes:
    """One still as ffmpeg hands it over (24-bit, bottom-up, rows padded to 4), built by arithmetic
    so the pinned value never depends on a decoder."""
    stride = (width * 3 + 3) & ~3
    rows = bytearray()
    for y in range(height - 1, -1, -1):
        row = bytearray()
        for x in range(width):
            row += bytes((((x + y + index) % 256), ((y * 5 + index * 7) % 256),
                          ((x * 3 + index * 11) % 256)))  # fmt: skip
        row += b"\0" * (stride - len(row))
        rows += row
    header = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(rows), 0, 0, 14 + 40)
    info = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(rows), 2835, 2835, 0, 0)
    return bytes(header + info + bytes(rows))


def grid_of(count: int = SCENE_FRAMES) -> list[bytes]:
    return [bitmap(160, 90, index) for index in range(count)]


def test_the_scene_fingerprint_is_pinned() -> None:
    """The golden scene fingerprint: stored against every video and compared with values software
    Sift did not write, so a change here is a bug whatever it was meant to be."""
    assert video_phash(grid_of()) == "a8012a7f3e2f3768"


def test_a_grid_with_a_hole_in_it_has_no_fingerprint() -> None:
    """A grid short a still has no fingerprint: once it is one number it looks like a real grid."""
    short = grid_of(SCENE_FRAMES - 1)
    assert video_phash(short) is None

    holed = grid_of()
    holed[7] = b""
    assert video_phash(holed) is None


def test_the_moments_skip_the_first_and_last_twentieth() -> None:
    """The stills come from the middle nine tenths, evenly: logos, titles and credits sit at the
    ends."""
    times = scene_frame_times(100.0)

    assert len(times) == SCENE_FRAMES
    assert times[0] == pytest.approx(5.0)
    assert times[-1] == pytest.approx(5.0 + 24 * 3.6)
    assert times[-1] < 95.0
    gaps = {round(b - a, 6) for a, b in itertools.pairwise(times)}
    assert len(gaps) == 1


def test_the_running_time_is_rounded_before_the_moments_are_worked_out() -> None:
    """The running time is rounded to hundredths, half away from zero, before the moments: probes
    that disagree in the third decimal must agree here."""
    assert scene_duration(210.004) == 210.0
    assert scene_duration(210.006) == 210.01
    # Python rounds this DOWN to an even hundredth; the reference rounds up.
    assert scene_duration(0.125) == 0.13
    assert round(0.125, 2) == 0.12


def test_a_video_of_no_length_still_yields_moments_rather_than_an_error() -> None:
    """A video of no length yields zero moments rather than an error: the probe refuses it."""
    assert scene_frame_times(0.0) == (0.0,) * SCENE_FRAMES


def test_the_threshold_is_the_reference_selection_not_a_sort() -> None:
    """The threshold is the reference's selection, transcribed, odd lengths and single values
    included."""
    values = [float(value) for value in range(64)]
    assert _selected_median(values) == _median(list(values))
    assert _selected_median([1.0]) == 1.0
    assert _selected_median([3.0, 1.0, 2.0]) == 2.0


def test_a_still_stored_top_down_is_read_the_right_way_up() -> None:
    """A negative bitmap height means rows top-down; read the wrong way up it would fingerprint a
    different picture."""
    bottom_up = bitmap(8, 6, 0)
    top_down = bytearray(bottom_up)
    struct.pack_into("<i", top_down, 22, -6)
    rows = [bottom_up[54:][index * 24 : (index + 1) * 24] for index in range(6)]
    top_down[54:] = b"".join(reversed(rows))

    assert video_phash([bytes(top_down)] * SCENE_FRAMES) == video_phash([bottom_up] * SCENE_FRAMES)


def test_a_still_that_is_not_a_plain_bitmap_is_refused() -> None:
    """A still that is not 24-bit is refused rather than misread."""
    thirty_two_bit = bytearray(bitmap(4, 4, 0))
    struct.pack_into("<H", thirty_two_bit, 28, 32)
    with pytest.raises(ValueError, match="24-bit"):
        video_phash([bytes(thirty_two_bit)] * SCENE_FRAMES)
