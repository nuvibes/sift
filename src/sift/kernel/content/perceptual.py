# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fingerprints that survive a re-encode.

The digest of a file's bytes answers "are these the same file". It is exact, and that is the
point of it, but it means a video saved twice at different qualities, or a picture a phone
re-saved on its way out, reads as two unrelated things. That is the commonest kind of duplicate
there is, and the only kind a person would ever have to sort out by hand.

So each frame also gets a blurrier fingerprint. Shrink the picture until only its structure is
left, describe that structure as a number, and two pictures that *look* alike get numbers that
are *close*: close enough to measure, by counting the bits that differ.

What this is not: a summary, a description, or anything that knows what is in the picture. It
cannot tell a beach from a bedroom. It can tell that two files are the same beach.

Written here rather than taken from a library for one reason worth stating: ffmpeg has to decode
the frame either way, and it can hand back a small grey square as easily as a picture. Given that
square, all that is left is the arithmetic below, so a library would add a dependency, its
dependencies, and their upgrades, in exchange for thirty lines. The numbers it produces are
stored, so they are pinned by a golden test: a change here does not break anything visibly, it
just quietly stops matching every fingerprint taken before it.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

#: WHICH GENERATION OF THESE FINGERPRINTS A STORED VALUE IS.
#:
#: Written onto every row this module's output lands on (`assets.fingerprint_version`), and raised
#: by hand whenever anything here changes what a hash comes out as: the frame size, the corner,
#: the median rule, the moments sampled, the grid the whole-video hash is built from.
#:
#: The numbers are stored and compared against each other, so a change here does not break
#: anything visibly: it quietly stops matching every fingerprint taken before it, and the only way
#: to find which rows are which afterwards is to decode every file in the library again. The
#: version is what makes that a pass over the rows below it instead.
#:
#: ONE number for all four hashes, because one statement writes all four out of one decode. They
#: cannot be at different generations without somebody writing a second statement first, and the
#: day that happens is the day this becomes four columns rather than the day it is found out.
FINGERPRINT_VERSION = 1

#: The square a frame is reduced to before the transform. Thirty-two is the standard size for
#: this: large enough that the low-frequency corner carries real structure, small enough that the
#: transform is trivial.
HASH_FRAME_SIZE = 32

#: The side of the low-frequency corner kept from the transform. Eight squared, minus the one
#: term dropped as brightness, is the 63 bits of the hash.
HASH_CORNER_SIZE = 8

#: How many bits a single frame's fingerprint carries. One per kept coefficient, less the one
#: that is thrown away (see `phash`).
PHASH_BITS = HASH_CORNER_SIZE * HASH_CORNER_SIZE - 1

#: A fingerprint as text: PHASH_BITS rounded up to whole bytes, two hex characters each.
_HEX_WIDTH = (PHASH_BITS + 7) // 8 * 2

#: Bits of one frame's fingerprint that may differ before two frames are different pictures. The
#: near-duplicate scan's edge for a photograph: well short of the distance unrelated photographs
#: sit at, and within the distance its block search is complete to.
NEAR_FRAME_BITS = 11

#: The same edge as a share of a fingerprint's bits, so it reads the same of one frame's
#: fingerprint and of a video's thirty laid end to end (`videohash`): how much of the picture may
#: differ before two files stop being nearly the same.
NEAR_SHARE = NEAR_FRAME_BITS / (_HEX_WIDTH * 4)


def _cosine_table(size: int, corner: int) -> tuple[tuple[float, ...], ...]:
    """The transform's constants: cos((2x+1) * u * pi / 2N), for every pixel and kept frequency.

    Built once at import. It depends on nothing but two numbers that never change, and the
    alternative is recomputing a few hundred cosines for every frame of every file.
    """
    return tuple(
        tuple(math.cos((2 * x + 1) * u * math.pi / (2 * size)) for x in range(size))
        for u in range(corner)
    )


_COSINES = _cosine_table(HASH_FRAME_SIZE, HASH_CORNER_SIZE)


def phash(frame: bytes, *, size: int = HASH_FRAME_SIZE) -> str:
    """The fingerprint of one frame, given it as raw grayscale pixels, one byte each.

    The frame arrives already shrunk to a square and stripped of colour, because ffmpeg does both
    far faster than anything here could and has to touch the pixels anyway.

    What happens to it:

    A discrete cosine transform rewrites the square as a sum of patterns, from the broadest (the
    overall shading) to the finest. Only the coarsest corner is kept. That is the whole trick:
    the coarse patterns are what a picture still has after it has been re-encoded, resized, or run
    through a phone's compression, and the fine ones are exactly what those things throw away.

    The very first term is dropped. It is the average brightness of the whole frame, it is far
    larger than everything else, and it says nothing about structure: keeping it would mean two
    pictures of different things matching because they were lit the same way.

    Each remaining coefficient becomes one bit: is it above or below the median of them? The
    median rather than zero, so that exactly half the bits come out set whatever the picture is,
    which is what keeps two unrelated frames from agreeing by accident.
    """
    expected = size * size
    if len(frame) != expected:
        raise ValueError(f"a {size}x{size} frame is {expected} bytes, and this one is {len(frame)}")
    if size != HASH_FRAME_SIZE:
        raise ValueError(f"the cosine table is built for {HASH_FRAME_SIZE}px frames, not {size}")

    rows = [frame[offset : offset + size] for offset in range(0, expected, size)]

    # Separable: transform each row, then transform the columns of the result. The direct form is
    # the same arithmetic done size*size times over, and this is the same answer for a fraction of
    # the work: the difference between a few thousand multiplications per frame and a few
    # hundred thousand, on a path that runs for every file in a library.
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
    """A whole video's fingerprint: its frames' fingerprints, in order, joined.

    Joined rather than combined into a single number, because combining them would throw away the
    thing that makes this work. Two files are near-duplicates when *most of their frames* match:
    averaging thirty fingerprints into one produces a number that means nothing and matches
    nothing, whereas laying them end to end keeps every frame's answer intact and makes the count
    of differing bits across the whole string exactly what it should be: how much of the video
    differs.

    It reads as one long hex string, and comparing two of them is the same bit count it always
    was. They are only comparable at equal length, which is why the frames are sampled at a fixed
    count (see the sampler).

    None when there were no frames to hash. A file with nothing decodable in it has no
    fingerprint, and an empty string would be one that matched every other empty string.
    """
    if not frame_hashes:
        return None
    return "".join(frame_hashes)


def distance(first: str, second: str) -> int | None:
    """How many bits differ. None if the two are not comparable.

    Not comparable means different lengths, and different lengths mean one is a video and the
    other is a still, or they were fingerprinted by versions that disagreed. Neither is a
    duplicate, and neither is an error worth raising: the honest answer to "how alike are these"
    is that the question does not apply.
    """
    if len(first) != len(second) or not first:
        return None
    try:
        return (int(first, 16) ^ int(second, 16)).bit_count()
    except ValueError:
        return None


def apart(first: str, second: str) -> float | None:
    """The share of bits that differ, from 0 to 1. None if the two are not comparable.

    `distance` counts bits, and a count means a different thing on a 64-bit frame and on a video's
    thirty frames: 14 bits apart is two different photographs, and a whole video within 14 bits
    is the same video. A share of each fingerprint's own width is the one measure two kinds can be
    ranked by, and the one `NEAR_SHARE` is stated in.
    """
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


# --- the fingerprint the public stash-boxes share ---------------------------------------------
#
# Everything below computes ONE number for a whole video, and it is the only thing in Sift that has
# to agree bit for bit with software Sift did not write. That constraint runs the other way from
# every other decision in this file: nothing here may be improved, tidied or made more principled,
# because a "better" answer is a different number, and a different number matches nothing anybody
# else has ever recorded.
#
# So it is transcribed rather than designed, and the oddities are transcribed with it:
#
#   * The running time is rounded to hundredths of a second BEFORE the sample moments are worked
#     out, so every sample lands where the others land.
#   * Twenty-five stills are taken between 5% and 95% of the way through (skipping the opening
#     and closing tenth, which is where logos and credits live), scaled to 160 pixels wide, and
#     laid out as a 5x5 grid. That grid, not the video, is what gets fingerprinted.
#   * The grid is reduced to 64x64 by a specific filtered shrink with 8-bit integer weights, and a
#     different-but-equivalent shrink changes the answer.
#   * Grey is the old television weighting, on the 8-bit values.
#   * The cosine transform keeps the top-left 8x8 corner INCLUDING the brightness term, which the
#     hash above deliberately drops. Keeping it is what the others do.
#   * The threshold is a median that is not quite the median (see `_selected_median`).
#   * The first coefficient is the HIGHEST bit, which is the reverse of the hash above.
#
# It agrees exactly with the values public stash-boxes hold for the same files. That check needs
# the network and an account, so what guards this in the suite is a golden value pinned from a
# checked-in file. If that golden ever has to be "updated", the change that made it move is a bug,
# not an improvement.

#: The grid, and the width each still is scaled to.
SCENE_COLUMNS = 5
SCENE_ROWS = 5
SCENE_SHOT_WIDTH = 160

#: How many stills the grid holds.
SCENE_FRAMES = SCENE_COLUMNS * SCENE_ROWS

#: The fraction skipped at each end, and therefore the fraction sampled.
_SCENE_EDGE = 0.05
_SCENE_SPAN = 1.0 - 2 * _SCENE_EDGE

#: The square the grid is reduced to before the transform, and the corner kept from it.
_SCENE_SIZE = 64
_SCENE_CORNER = 8


def scene_duration(seconds: float) -> float:
    """The running time as the sample moments are worked out from: hundredths of a second.

    Rounded here rather than wherever the number came from, because the rounding is part of the
    agreement. Two people whose probes disagree in the third decimal must still choose the same
    twenty-five moments, or their fingerprints differ for a reason that has nothing to do with the
    pictures. Half rounds away from zero, which is what the others do and is not what Python's own
    `round` does.
    """
    return math.floor(seconds * 100 + 0.5) / 100


def scene_frame_times(seconds: float) -> tuple[float, ...]:
    """The twenty-five moments the grid is built from, in seconds.

    Evenly spread across the middle nine tenths. Not clamped and not deduplicated: a very short
    video asks for the same moment more than once, and the answer to that is twenty-five copies of
    one still, which is a perfectly good fingerprint of a video that is all one shot.
    """
    duration = scene_duration(seconds)
    offset = _SCENE_EDGE * duration
    step = (_SCENE_SPAN * duration) / float(SCENE_FRAMES)
    return tuple(offset + index * step for index in range(SCENE_FRAMES))


def _decode_bmp(data: bytes) -> np.ndarray:
    """One still, as ffmpeg hands it over: an uncompressed 24-bit bitmap. Returns (h, w, 3) RGB.

    Uncompressed on purpose. A JPEG would be smaller and would also mean the fingerprint depended
    on which JPEG encoder happened to be installed, which is exactly the kind of dependency this
    whole section exists to avoid.
    """
    # Imported here rather than at module scope, the same way the face runtime is. Two reasons,
    # and the second is not optional: it costs real time and memory to import for a module most of
    # Sift touches on every boot, and importing it while a test plugin is being loaded makes numpy
    # refuse: it will not be loaded twice in one process, and the coverage run does exactly that.
    import numpy as np

    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    depth = struct.unpack_from("<H", data, 28)[0]
    if depth != 24:
        raise ValueError(f"expected a 24-bit bitmap and got {depth}-bit")
    # A positive height means the rows are stored bottom to top, which is the ordinary case.
    upside_down = height > 0
    height = abs(height)
    stride = (width * 3 + 3) & ~3
    flat = np.frombuffer(data, dtype=np.uint8, count=stride * height, offset=offset)
    rows = flat.reshape(height, stride)[:, : width * 3].reshape(height, width, 3)
    if upside_down:
        rows = rows[::-1]
    # The bitmap stores blue first, and a reversed view is not contiguous, which every step after
    # this one is faster for, and one of them requires.
    picture: np.ndarray = np.ascontiguousarray(rows[:, :, ::-1])
    return picture


def _grid(shots: Sequence[bytes]) -> np.ndarray:
    """The stills laid out 5 across and 5 down, on an opaque canvas."""
    # Imported here rather than at module scope, the same way the face runtime is. Two reasons,
    # and the second is not optional: it costs real time and memory to import for a module most of
    # Sift touches on every boot, and importing it while a test plugin is being loaded makes numpy
    # refuse: it will not be loaded twice in one process, and the coverage run does exactly that.
    import numpy as np

    pictures = [_decode_bmp(shot) for shot in shots]
    height, width = pictures[0].shape[:2]
    canvas = np.zeros((height * SCENE_ROWS, width * SCENE_COLUMNS, 4), dtype=np.uint8)
    for index, picture in enumerate(pictures):
        left = width * (index % SCENE_COLUMNS)
        top = height * (index // SCENE_ROWS)
        # Clipped to the space it was given, which is what the reference does when a still comes
        # back a different size from the first one: a resolution that changes partway through a
        # file. Left to broadcast, that case raises instead, and an exception here would cost the
        # file its thumbnail and its codecs as well as this fingerprint.
        tall = min(picture.shape[0], canvas.shape[0] - top)
        wide = min(picture.shape[1], canvas.shape[1] - left)
        canvas[top : top + tall, left : left + wide, :3] = picture[:tall, :wide]
        canvas[top : top + tall, left : left + wide, 3] = 255
    return canvas


def _shrink_weights(count: int, scale: float) -> tuple[np.ndarray, np.ndarray]:
    """The filter weights for one axis of the shrink, as whole numbers out of 256.

    A triangle filter, widened in proportion to how far the picture is being shrunk, with the
    weights truncated to integers. The truncation is not sloppiness: the reference does its
    arithmetic in 8-bit integers, and rounding instead would produce a slightly different picture
    and therefore a different fingerprint.
    """
    # Imported here rather than at module scope, the same way the face runtime is. Two reasons,
    # and the second is not optional: it costs real time and memory to import for a module most of
    # Sift touches on every boot, and importing it while a test plugin is being loaded makes numpy
    # refuse: it will not be loaded twice in one process, and the coverage run does exactly that.
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
    """Shrink along the second axis and hand the result back transposed.

    Transposed because the reference makes two passes of one routine rather than writing a second
    one for the other direction, and the integer truncation in between is visible in the result.
    """
    # Imported here rather than at module scope, the same way the face runtime is. Two reasons,
    # and the second is not optional: it costs real time and memory to import for a module most of
    # Sift touches on every boot, and importing it while a test plugin is being loaded makes numpy
    # refuse: it will not be loaded twice in one process, and the coverage run does exactly that.
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


# The constants the transform divides by: twice the cosine of each half-step. Written out rather
# than computed, because a cosine that differs in its last bit between two machines' maths
# libraries is a fingerprint that differs between two machines.
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
    """One halving step of the cosine transform, in place.

    Split into sums and differences, transform each half, and interleave. Written this way rather
    than as a matrix multiply for one reason: floating-point addition is not associative, so the
    ORDER the numbers are added in changes the last bits of the answer, and a coefficient that
    lands a hair either side of the threshold is a bit that flips. Matching the reference means
    matching its order of operations, not merely its mathematics.
    """
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
    """The threshold: what the reference's median function actually does, step for step.

    It is a quick-select (it finds the middle value without sorting the rest), and then averages
    that with whatever is sitting one place to its left. In a sorted list that neighbour is the
    value below; here it is merely *some* value that is no larger, so the two are not obviously the
    same function.

    Twenty thousand random inputs failed to find a case where they disagree, so this may well be
    the plain median in every case that matters. It is transcribed rather than replaced with a sort
    anyway, because "may well be" is not a thing to bet a fingerprint on: if the two ever diverge,
    the cost is a bit that flips, in a value whose entire worth is that it equals what everybody
    else computed.
    """
    sequence = list(values)
    low, high = 0, len(sequence) - 1
    middle = len(sequence) // 2
    if low == high:
        return sequence[middle]

    while low < high:
        # Halved separately and then added, which is not the same as halving the sum when the two
        # are odd. It picks a different pivot, and a different pivot leaves a different neighbour.
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
    # Imported here rather than at module scope, the same way the face runtime is. Two reasons,
    # and the second is not optional: it costs real time and memory to import for a module most of
    # Sift touches on every boot, and importing it while a test plugin is being loaded makes numpy
    # refuse: it will not be loaded twice in one process, and the coverage run does exactly that.
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
            # The first coefficient is the highest bit, which is the opposite way round from
            # `phash` above. Both are arbitrary; only one of them has to match anybody else.
            bits |= 1 << (64 - index - 1)
    return f"{bits:016x}"


def video_phash(shots: Sequence[bytes]) -> str | None:
    """A whole video's fingerprint, from the twenty-five stills of it. None if there are not enough.

    None rather than a hash of what turned up: a grid with holes in it is a fingerprint of a
    different picture, it is indistinguishable from a real one, and it would be sent to a stash-box
    as though it meant something. A video that will not give up twenty-five frames has no
    fingerprint, and saying so is the honest answer.
    """
    if len(shots) != SCENE_FRAMES or not all(shots):
        return None
    return _square_hash(_shrink_to_square(_grid(shots)))
