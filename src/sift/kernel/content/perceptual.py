# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fingerprints that survive a re-encode, by structure rather than bytes.

The values are stored, so a golden test pins them: a change here silently stops every match."""

from __future__ import annotations

import math
import struct
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

#: Which generation of these fingerprints a stored value is; raise it whenever any hash changes,
#: so old rows are found by a pass instead of a re-decode. One number for all four hashes.
FINGERPRINT_VERSION = 1

#: Large enough that the corner carries structure, small enough that the transform is trivial.
HASH_FRAME_SIZE = 32

#: Eight squared, less the brightness term, is the hash's 63 bits.
HASH_CORNER_SIZE = 8

PHASH_BITS = HASH_CORNER_SIZE * HASH_CORNER_SIZE - 1

_HEX_WIDTH = (PHASH_BITS + 7) // 8 * 2

#: Bits one frame may differ by and still be the same picture: short of unrelated photographs.
NEAR_FRAME_BITS = 11

#: The same edge as a share of the bits, so it reads alike of one frame and of a video's thirty.
NEAR_SHARE = NEAR_FRAME_BITS / (_HEX_WIDTH * 4)


def _cosine_table(size: int, corner: int) -> tuple[tuple[float, ...], ...]:
    """The transform's constants, built once: cos((2x+1) * u * pi / 2N)."""
    return tuple(
        tuple(math.cos((2 * x + 1) * u * math.pi / (2 * size)) for x in range(size))
        for u in range(corner)
    )


_COSINES = _cosine_table(HASH_FRAME_SIZE, HASH_CORNER_SIZE)


def phash(frame: bytes, *, size: int = HASH_FRAME_SIZE) -> str:
    """The fingerprint of one frame of grayscale pixels: the coarse DCT corner, median-split.

    The brightness term is dropped, so frames lit alike do not match for it."""
    expected = size * size
    if len(frame) != expected:
        raise ValueError(f"a {size}x{size} frame is {expected} bytes, and this one is {len(frame)}")
    if size != HASH_FRAME_SIZE:
        raise ValueError(f"the cosine table is built for {HASH_FRAME_SIZE}px frames, not {size}")

    rows = [frame[offset : offset + size] for offset in range(0, expected, size)]

    # Separable: rows, then columns, a fraction of the direct form's work.
    by_row = [
        [sum(pixel * cosines[x] for x, pixel in enumerate(row)) for cosines in _COSINES]
        for row in rows
    ]
    corner = [
        [sum(by_row[y][u] * cosines[y] for y in range(size)) for u in range(HASH_CORNER_SIZE)]
        for cosines in _COSINES
    ]

    coefficients = [value for row in corner for value in row][1:]
    median = _median(coefficients)
    bits = 0
    for index, value in enumerate(coefficients):
        if value > median:
            bits |= 1 << index
    return f"{bits:0{_HEX_WIDTH}x}"


def videohash(frame_hashes: Sequence[str]) -> str | None:
    """A video's fingerprint: its frames' fingerprints joined, so differing bits measure it."""
    if not frame_hashes:
        return None
    return "".join(frame_hashes)


def distance(first: str, second: str) -> int | None:
    """How many bits differ, or None when the lengths differ and the question does not apply."""
    if len(first) != len(second) or not first:
        return None
    try:
        return (int(first, 16) ^ int(second, 16)).bit_count()
    except ValueError:
        return None


def apart(first: str, second: str) -> float | None:
    """The share of bits that differ, 0 to 1, so a frame and a video can be ranked alike."""
    bits = distance(first, second)
    if bits is None:
        return None
    return bits / (len(first) * 4)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


# The fingerprint public stash-boxes share, transcribed bit for bit, oddities included: never
# improve it, as a better answer is a different number. A golden value pins it.

SCENE_COLUMNS = 5
SCENE_ROWS = 5
SCENE_SHOT_WIDTH = 160

SCENE_FRAMES = SCENE_COLUMNS * SCENE_ROWS

_SCENE_EDGE = 0.05
_SCENE_SPAN = 1.0 - 2 * _SCENE_EDGE

_SCENE_SIZE = 64
_SCENE_CORNER = 8


def scene_duration(seconds: float) -> float:
    """The running time in hundredths of a second, rounding half away from zero as others do."""
    return math.floor(seconds * 100 + 0.5) / 100


def scene_frame_times(seconds: float) -> tuple[float, ...]:
    """The twenty-five moments the grid is built from, in seconds, neither clamped nor deduped."""
    duration = scene_duration(seconds)
    offset = _SCENE_EDGE * duration
    step = (_SCENE_SPAN * duration) / float(SCENE_FRAMES)
    return tuple(offset + index * step for index in range(SCENE_FRAMES))


def _decode_bmp(data: bytes) -> np.ndarray:
    """One still as ffmpeg's uncompressed 24-bit bitmap, as (h, w, 3) RGB; a JPEG would vary."""
    # Lazy: numpy is heavy at boot, and a second import under a test plugin makes it refuse.
    import numpy as np

    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    depth = struct.unpack_from("<H", data, 28)[0]
    if depth != 24:
        raise ValueError(f"expected a 24-bit bitmap and got {depth}-bit")
    upside_down = height > 0
    height = abs(height)
    stride = (width * 3 + 3) & ~3
    flat = np.frombuffer(data, dtype=np.uint8, count=stride * height, offset=offset)
    rows = flat.reshape(height, stride)[:, : width * 3].reshape(height, width, 3)
    if upside_down:
        rows = rows[::-1]
    # Blue first in the bitmap, and later steps need a contiguous array.
    picture: np.ndarray = np.ascontiguousarray(rows[:, :, ::-1])
    return picture


def _grid(shots: Sequence[bytes]) -> np.ndarray:
    """The stills laid out 5 across and 5 down, on an opaque canvas."""
    # Lazy: numpy is heavy at boot, and a second import under a test plugin makes it refuse.
    import numpy as np

    pictures = [_decode_bmp(shot) for shot in shots]
    height, width = pictures[0].shape[:2]
    canvas = np.zeros((height * SCENE_ROWS, width * SCENE_COLUMNS, 4), dtype=np.uint8)
    for index, picture in enumerate(pictures):
        left = width * (index % SCENE_COLUMNS)
        top = height * (index // SCENE_ROWS)
        # Clipped like the reference, so a resolution change mid-file does not raise.
        tall = min(picture.shape[0], canvas.shape[0] - top)
        wide = min(picture.shape[1], canvas.shape[1] - left)
        canvas[top : top + tall, left : left + wide, :3] = picture[:tall, :wide]
        canvas[top : top + tall, left : left + wide, 3] = 255
    return canvas


def _shrink_weights(count: int, scale: float) -> tuple[np.ndarray, np.ndarray]:
    """One axis's triangle-filter weights out of 256, truncated as the reference truncates."""
    # Lazy: numpy is heavy at boot, and a second import under a test plugin makes it refuse.
    import numpy as np

    length = 2 * int(max(math.ceil(scale), 1))
    factor = min(1.0 / scale, 1.0)
    weights = np.zeros((count, length), dtype=np.int64)
    starts = np.zeros(count, dtype=np.int64)
    for out in range(count):
        centre = scale * (float(out) + 0.5) - 0.5
        start = int(centre) - length // 2 + 1
        starts[out] = start
        centre -= float(start)
        for tap in range(length):
            distance_from_centre = abs((centre - float(tap)) * factor)
            triangle = 1.0 - distance_from_centre if distance_from_centre <= 1 else 0.0
            weights[out, tap] = int(triangle * 256)
    return weights, starts


def _shrink_axis(source: np.ndarray, weights: np.ndarray, starts: np.ndarray) -> np.ndarray:
    """Shrink along the second axis and transpose, as the reference's two passes do."""
    # Lazy: numpy is heavy at boot, and a second import under a test plugin makes it refuse.
    import numpy as np

    taps = weights.shape[1]
    index = np.clip(starts[:, None] + np.arange(taps)[None, :], 0, source.shape[1] - 1)
    gathered = source[:, index, :].astype(np.int64)
    weighted: np.ndarray = (gathered * weights[None, :, :, None]).sum(axis=2)
    totals = weights.sum(axis=1)
    shrunk = np.clip(weighted // totals[None, :, None], 0, 255).astype(np.uint8)
    transposed: np.ndarray = shrunk.transpose(1, 0, 2)
    return transposed


def _shrink_to_square(canvas: np.ndarray) -> np.ndarray:
    """The grid reduced to 64x64."""
    height, width = canvas.shape[:2]
    across = _shrink_axis(canvas, *_shrink_weights(_SCENE_SIZE, width / _SCENE_SIZE))
    return _shrink_axis(across, *_shrink_weights(_SCENE_SIZE, height / _SCENE_SIZE))


# Written out, as a cosine differing in its last bit between machines changes the fingerprint.
_DCT64_DIVISORS = (
    1.9993976373924083,
    1.9945809133573804,
    1.9849590691974202,
    1.9705552847778824,
    1.9514042600770571,
    1.9275521315908797,
    1.8990563611860733,
    1.8659855976694777,
    1.8284195114070614,
    1.7864486023910306,
    1.7401739822174227,
    1.6897071304994142,
    1.6351696263031674,
    1.5766928552532127,
    1.5144176930129691,
    1.448494165902934,
    1.3790810894741339,
    1.3063456859075537,
    1.2304631811612539,
    1.151616382835691,
    1.0699952397741948,
    0.9857963844595683,
    0.8992226593092132,
    0.8104826280099796,
    0.7197900730699766,
    0.627363480797783,
    0.5334255149497968,
    0.43820248031373954,
    0.3419237775206027,
    0.24482135039843256,
    0.1471291271993349,
    0.049082457045824535,
)
_DCT32_DIVISORS = (
    1.9975909124103448,
    1.978353019929562,
    1.9400625063890882,
    1.8830881303660416,
    1.8079785862468867,
    1.7154572200005442,
    1.6064150629612899,
    1.4819022507099182,
    1.3431179096940369,
    1.191398608984867,
    1.0282054883864435,
    0.8551101868605644,
    0.6737797067844401,
    0.48596035980652796,
    0.2934609489107235,
    0.09813534865483627,
)
_DCT16_DIVISORS = (
    1.9903694533443936,
    1.9138806714644176,
    1.76384252869671,
    1.546020906725474,
    1.2687865683272912,
    0.9427934736519956,
    0.5805693545089246,
    0.19603428065912154,
)
_DCT8_DIVISORS = (
    1.9615705608064609,
    1.6629392246050907,
    1.1111404660392046,
    0.3901806440322566,
)
_DCT4_DIVISORS = (1.8477590650225735, 0.7653668647301797, 1.4142135623730951)


def _transform(
    values: list[float],
    divisors: tuple[float, ...],
    half_step: Callable[[list[float]], None],
) -> None:
    """One halving step of the transform in place, in the reference's order of additions."""
    size = len(values)
    half = size // 2
    temp = [0.0] * size
    for index in range(half):
        first, second = values[index], values[size - 1 - index]
        temp[index] = first + second
        temp[index + half] = (first - second) / divisors[index]

    lower, upper = temp[:half], temp[half:]
    half_step(lower)
    half_step(upper)
    temp = lower + upper

    for index in range(half - 1):
        values[index * 2 + 0] = temp[index]
        values[index * 2 + 1] = temp[index + half] + temp[index + half + 1]
    values[size - 2], values[size - 1] = temp[half - 1], temp[size - 1]


def _transform4(values: list[float]) -> None:
    first, last = values[0], values[3]
    second, third = values[1], values[2]
    a = first + last
    b = second + third
    c = (first - last) / _DCT4_DIVISORS[0]
    d = (second - third) / _DCT4_DIVISORS[1]
    a, b = a + b, (a - b) / _DCT4_DIVISORS[2]
    c, d = c + d, (c - d) / _DCT4_DIVISORS[2]
    values[0], values[1], values[2], values[3] = a, c + d, b, d


def _transform8(values: list[float]) -> None:
    sums = [values[i] + values[7 - i] for i in range(4)]
    differences = [(values[i] - values[7 - i]) / _DCT8_DIVISORS[i] for i in range(4)]
    _transform4(sums)
    _transform4(differences)
    values[0], values[1] = sums[0], differences[0] + differences[1]
    values[2], values[3] = sums[1], differences[1] + differences[2]
    values[4], values[5] = sums[2], differences[2] + differences[3]
    values[6], values[7] = sums[3], differences[3]


def _transform16(values: list[float]) -> None:
    _transform(values, _DCT16_DIVISORS, _transform8)


def _transform32(values: list[float]) -> None:
    _transform(values, _DCT32_DIVISORS, _transform16)


def _transform64(values: list[float]) -> None:
    _transform(values, _DCT64_DIVISORS, _transform32)


def _selected_median(values: Sequence[float]) -> float:
    """The reference's threshold step for step: a quick-select averaged with its left neighbour."""
    sequence = list(values)
    low, high = 0, len(sequence) - 1
    middle = len(sequence) // 2
    if low == high:
        return sequence[middle]

    while low < high:
        # Halved separately and then added, which picks the reference's pivot.
        pivot = low // 2 + high // 2
        pivot_value = sequence[pivot]
        store = low
        sequence[pivot], sequence[high] = sequence[high], sequence[pivot]
        for index in range(low, high):
            if sequence[index] < pivot_value:
                sequence[store], sequence[index] = sequence[index], sequence[store]
                store += 1
        sequence[high], sequence[store] = sequence[store], sequence[high]
        if middle <= store:
            high = store
        else:
            low = store + 1

    if len(sequence) % 2 == 0:
        return sequence[middle - 1] / 2 + sequence[middle] / 2
    return sequence[middle]


def _square_hash(square: np.ndarray) -> str:
    """The fingerprint of the 64x64 square, as sixteen hex characters."""
    # Lazy: numpy is heavy at boot, and a second import under a test plugin makes it refuse.
    import numpy as np

    grey = (
        0.299 * square[:, :, 0].astype(np.float64)
        + 0.587 * square[:, :, 1].astype(np.float64)
        + 0.114 * square[:, :, 2].astype(np.float64)
    )
    rows = [grey[row].tolist() for row in range(_SCENE_SIZE)]
    for row in rows:
        _transform64(row)

    corner = [0.0] * (_SCENE_CORNER * _SCENE_CORNER)
    for across in range(_SCENE_CORNER):
        column = [rows[down][across] for down in range(_SCENE_SIZE)]
        _transform64(column)
        for down in range(_SCENE_CORNER):
            corner[_SCENE_CORNER * down + across] = column[down]

    threshold = _selected_median(corner)
    bits = 0
    for index, value in enumerate(corner):
        if value > threshold:
            # The first coefficient is the highest bit, the reverse of `phash`.
            bits |= 1 << (64 - index - 1)
    return f"{bits:016x}"


def video_phash(shots: Sequence[bytes]) -> str | None:
    """A whole video's fingerprint from twenty-five stills, or None if any is missing."""
    if len(shots) != SCENE_FRAMES or not all(shots):
        return None
    return _square_hash(_shrink_to_square(_grid(shots)))
