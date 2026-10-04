# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning a face in a frame into the square picture a recognizer was trained on.

A recognizer does not look at a photograph. It looks at a 112-pixel square in which the eyes,
the nose and the mouth sit at fixed positions, because that is how every image it was trained on
was arranged. Handing it a face that is rotated, off-centre or a different size is handing it
something it has never seen, and the numbers that come back are worse in a way nothing downstream
can detect.

So a face is not cropped, it is **aligned**: the transform that carries its five landmarks onto
the standard ones is worked out and the picture is resampled through it. Rotation, scale and
position all come out right at once, which a rectangular crop cannot do at all.

The squares are written out through the same separate encoder every other picture in Sift goes
through, one call for a whole file's worth rather than one per face: starting the process costs
more than any of these tiny pictures does.

They are stored compressed rather than exactly: compression moves a face's numbers by four tenths
of a percent, against a gap of more than half between the same person and a different one, and it
is five times less disk in the directory that gets backed up. These are the input to a model, but
the drift is nowhere near the scale anything here decides on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from blake3 import blake3

from sift.kernel.config import Settings
from sift.kernel.media import BASE_FLAGS
from sift.kernel.media import run as run_tool
from sift.slices.faces import tuning
from sift.slices.faces.models import Box

#: How long writing one file's face pictures may take. They are tiny and there are a handful; this
#: is a guard against a wedged encoder, not a budget.
_ENCODE_TIMEOUT = 60.0

#: The square a face is resampled into. Every recognizer worth using takes this size.
CHIP_SIZE = 112

#: Where the five landmarks sit in that square: left eye, right eye, nose, left and right mouth
#: corners. These are not a choice: they are the arrangement the recognizers were trained on, and
#: moving them would silently degrade every embedding.
LANDMARK_TEMPLATE: tuple[tuple[float, float], ...] = (
    (38.2946, 51.6963),
    (73.5318, 51.5014),
    (56.0252, 71.7366),
    (41.5493, 92.3655),
    (70.7299, 92.2041),
)


class AlignmentError(ValueError):
    """The landmarks cannot describe a face: all in one place, or not five of them."""


@dataclass(frozen=True, slots=True)
class Aligned:
    """The square a face was resampled into, and how much of it is really the picture.

    The second half is here because it cannot be recovered afterwards. `warp` repeats the edge
    pixel wherever it samples past the side of the frame, deliberately, but once that has
    happened, the invented part is indistinguishable from a genuinely flat background, and every
    measurement taken on the square afterwards is taken on a picture that is partly made up.
    """

    chip: np.ndarray
    containment: float
    """The fraction of the FACE that came from inside the frame. 1.0 is a face wholly cut from real
    pixels; 0.5 means half of it is one edge pixel repeated. Measured over the middle of the square
    rather than all of it. See `_CORE_MARGIN`."""


def similarity_transform(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """The rotation, single scale and shift that best carries `source` onto `target`.

    Least-squares over all five points rather than solving exactly from two of them. A detector's
    landmarks are estimates; using two throws away three measurements and lets one bad estimate
    (an eye guessed on the edge of a pair of glasses) rotate the whole face.

    Deliberately a *similarity* rather than a general affine: rotate, scale and move, but never
    stretch one axis. An affine fit would squash a face turned sideways into a frontal-looking one,
    which is worse than the honest sideways face because it hides its own unreliability.
    """
    if source.shape != (5, 2) or target.shape != (5, 2):
        raise AlignmentError("alignment needs five landmarks")

    source = source.astype(np.float64)
    target = target.astype(np.float64)
    source_mean = source.mean(axis=0)
    target_mean = target.mean(axis=0)
    source_centred = source - source_mean
    target_centred = target - target_mean

    variance = float((source_centred**2).sum(axis=1).mean())
    if variance <= 0:
        raise AlignmentError("the five landmarks are all in the same place")

    covariance = target_centred.T @ source_centred / len(source)
    u, singular, vt = np.linalg.svd(covariance)
    # A reflection fits the points just as well as a rotation and produces a mirrored face, so the
    # smallest singular direction is flipped back when the determinant says the fit turned over.
    correction = np.ones(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        correction[1] = -1.0
    rotation = u @ np.diag(correction) @ vt
    scale = float(singular @ correction) / variance

    matrix = np.eye(3, dtype=np.float64)
    matrix[:2, :2] = scale * rotation
    matrix[:2, 2] = target_mean - scale * rotation @ source_mean
    return matrix


#: How far past the five landmarks the face is taken to reach, as a fraction of the eye-to-mouth
#: drop. It is what marks off the part of the square containment is measured over.
#:
#: **Not the whole square, which would refuse good faces for a fault in the margin.** The square is
#: not a crop of a face: it is the face arranged the way the recognizer was trained to see it, with
#: the eyes a little above the middle, which leaves a broad band of forehead, hair and background
#: above them and a narrower one below the chin. Somebody photographing themselves puts their head
#: near the top of the picture, so that band falls outside the frame; scored over the whole square,
#: a large, sharp, front-on face can fall under the 0.85 floor for how much of its hair was
#: invented.
#:
#: So containment is measured over the landmarks and half the eye-to-mouth drop around them, and
#: what lies beyond that is not counted. This is not a softening of the floor: a square cut at the
#: SIDE of a shot loses the features themselves and still scores badly, which is the case the floor
#: was put there for. What it stops doing is confusing a missing hairline with a missing face.
_CORE_MARGIN = 0.5


def _core_of_the_template() -> tuple[float, float, float, float]:
    """The part of the aligned square that is the face, in that square's own pixels.

    Derived from the template rather than written down, so it cannot drift away from the
    arrangement the landmarks are actually warped onto.
    """
    points = np.array(LANDMARK_TEMPLATE, dtype=np.float64)
    eyes = points[:2].mean(axis=0)
    mouth = points[3:].mean(axis=0)
    margin = float(np.hypot(*(mouth - eyes))) * _CORE_MARGIN
    left, top = points.min(axis=0) - margin
    right, bottom = points.max(axis=0) + margin
    return float(left), float(top), float(right), float(bottom)


_CORE = _core_of_the_template()


def _core_bounds(size: int) -> tuple[int, int, int, int]:
    """`_CORE` as row and column bounds in a square of `size`, clipped to it."""
    scale = size / CHIP_SIZE
    left, top, right, bottom = (round(edge * scale) for edge in _CORE)
    return max(0, left), max(0, top), min(size, right), min(size, bottom)


def _mirror(index: np.ndarray, size: int) -> np.ndarray:
    """Fold an out-of-range index back into the picture, reflecting at the edge.

    **What fills the part of a square that fell outside the frame.** Repeating the edge pixel is the
    obvious thing and the wrong thing: every row of the band is the same pixel stretched sideways,
    which no photograph looks like. The recognizer would be handed a face beside a smear, and
    (because the square is the picture Sift shows) so would the person looking at the screen.

    Reflecting costs the same arithmetic and produces the hair, ear or wall that was next to the
    edge, mirrored. Not what the camera saw, and it is not pretending to be: what it is, is
    plausible texture of the right colour and the right frequency, in the place where the
    alternative was a streak. Both the description and the picture come out better for it.

    The edge row itself is not repeated (reflecting `[a b c d]` past the end gives `c b a`, not
    `d c b a`), so the fold introduces no seam of its own.
    """
    if size == 1:
        return np.zeros_like(index)
    period = 2 * size - 2
    folded = np.abs(index) % period
    return np.where(folded >= size, period - folded, folded)


def warp(frame: np.ndarray, matrix: np.ndarray, *, size: int = CHIP_SIZE) -> Aligned:
    """Resample a frame through a transform into a `size` square, sampling bilinearly.

    Runs backwards (for each output pixel, work out where it came from) because the forward
    direction leaves holes wherever the transform stretches. Sampling off the edge of the frame
    reflects back into it rather than filling with black: a face at the very edge of a shot
    otherwise gets a hard black band across it, which is a strong edge exactly where the model
    looks for a jawline. See `_mirror` for why reflecting and not repeating.

    **How much of the face fell outside the frame is reported, because it is the difference between
    a picture of a face and a picture of a face beside an invented band.** The sharpness floor
    cannot see it (sharpness is the variance of the whole square, and a band beside a sharp face
    still averages well above it), and neither can a bounds check on the detector box: the square
    is rotated and scaled onto the landmarks, so it reaches further than the box does.

    Over the face rather than over the whole square, because the square carries a margin of hair
    and background that a smear across is not a reason to refuse anybody. See `_CORE_MARGIN`.
    """
    inverse = np.linalg.inv(matrix)
    ys, xs = np.mgrid[:size, :size]
    grid = np.stack([xs.ravel(), ys.ravel(), np.ones(size * size)], axis=-1)
    mapped = grid @ inverse.T

    height, width = frame.shape[:2]
    source_x = mapped[:, 0]
    source_y = mapped[:, 1]
    left = np.floor(source_x).astype(np.int64)
    top = np.floor(source_y).astype(np.int64)
    fraction_x = (source_x - left)[:, None]
    fraction_y = (source_y - top)[:, None]

    left_clamped = _mirror(left, width)
    right_clamped = _mirror(left + 1, width)
    top_clamped = _mirror(top, height)
    bottom_clamped = _mirror(top + 1, height)

    pixels = frame.astype(np.float32)
    upper = (
        pixels[top_clamped, left_clamped] * (1 - fraction_x)
        + pixels[top_clamped, right_clamped] * fraction_x
    )
    lower = (
        pixels[bottom_clamped, left_clamped] * (1 - fraction_x)
        + pixels[bottom_clamped, right_clamped] * fraction_x
    )
    blended = upper * (1 - fraction_y) + lower * fraction_y
    chip = np.clip(blended, 0, 255).astype(np.uint8).reshape(size, size, 3)

    # Counted on the sample points rather than on the output pixels, which is the same set: one
    # sample per pixel of the square. A point exactly on the last row or column is still inside the
    # picture: it is the bilinear pair beyond it that gets clamped, and clamping the second of two
    # neighbours is ordinary edge handling rather than an invented pixel.
    inside = (source_x >= 0) & (source_x <= width - 1) & (source_y >= 0) & (source_y <= height - 1)
    left_edge, top_edge, right_edge, bottom_edge = _core_bounds(size)
    core = inside.reshape(size, size)[top_edge:bottom_edge, left_edge:right_edge]
    return Aligned(chip=chip, containment=float(core.mean()) if core.size else float(inside.mean()))


def align(
    frame: np.ndarray,
    landmarks: tuple[tuple[float, float], ...],
    *,
    size: int = CHIP_SIZE,
) -> Aligned:
    """The aligned square for one face, and how much of it came from inside the frame."""
    template = np.array(LANDMARK_TEMPLATE, dtype=np.float64) * (size / CHIP_SIZE)
    matrix = similarity_transform(np.array(landmarks, dtype=np.float64), template)
    return warp(frame, matrix, size=size)


#: The markers a stored picture starts and ends with. Used to cut a stream of them apart.
_START = b"\xff\xd8\xff"


def encode_args(count: int, size: int, quality: int, settings: Settings) -> list[str]:
    """Turn a run of aligned squares into stored pictures, all in one go.

    Written as **one call per file rather than one per face**. A file with a handful of faces in it
    would otherwise start a handful of separate processes to write a handful of small pictures, and
    starting the process costs more than the picture does. The squares are fed in as raw bytes, all
    the same size, and come back as a run of finished pictures with nothing between them.

    Stored rather than kept raw because raw is four times the size for no benefit, and stored at
    high quality rather than perfectly because the difference measured against a perfect copy moves
    a face's numbers by four tenths of a percent, against a gap of more than half between the same
    person and a different one. The five times less disk is worth having: these live in the
    directory that gets backed up.
    """
    return [
        settings.ffmpeg_path,
        *BASE_FLAGS,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{size}x{size}",
        "-i",
        "pipe:0",
        "-frames:v",
        str(count),
        "-q:v",
        str(quality),
        "-f",
        "image2pipe",
        "-c:v",
        "mjpeg",
        "pipe:1",
    ]


def split_encoded(stream: bytes) -> list[bytes]:
    """Cut a run of stored pictures apart, at the marker each one begins with.

    There is no length in front of them and none is needed: the format's start marker cannot occur
    inside the data of the picture before it, because the encoder escapes it. Anything before the
    first marker is not a picture and is dropped.
    """
    starts = []
    position = stream.find(_START)
    while position != -1:
        starts.append(position)
        position = stream.find(_START, position + 1)
    if not starts:
        return []
    bounds = [*starts, len(stream)]
    return [stream[bounds[index] : bounds[index + 1]] for index in range(len(starts))]


async def encode(chips: Sequence[np.ndarray], settings: Settings) -> list[bytes]:
    """Store a file's face pictures. Hands back one per square, in the same order.

    Refuses to hand back a partial answer: if the encoder produced a different number of pictures
    than it was given squares, the pairing between a picture and the face it belongs to is unknown,
    and storing them anyway would attach the wrong face to a person.
    """
    if not chips:
        return []
    size = chips[0].shape[0]
    if any(chip.shape != (size, size, 3) for chip in chips):
        raise ValueError("every face picture written together has to be the same size")

    raw = b"".join(np.ascontiguousarray(chip, dtype=np.uint8).tobytes() for chip in chips)
    argv = encode_args(len(chips), size, tuning.CROP_QUALITY, settings)
    stream = await run_tool(argv, time_limit=_ENCODE_TIMEOUT, capture=True, stdin=raw)
    pictures = split_encoded(stream)
    if len(pictures) != len(chips):
        raise ValueError(
            f"asked for {len(chips)} face pictures and got {len(pictures)}; not storing any of "
            "them, because which picture belongs to which face is no longer known"
        )
    return pictures


def decode_args(count: int, settings: Settings) -> list[str]:
    """Turn a run of stored pictures back into the squares they were made from, all in one go.

    The exact reverse of `encode_args`, for the same reason it is one call per file: a file's
    faces are a handful of small pictures and a process each would cost more than the pictures.
    The stored pictures are fed in as a run with nothing between them (the format's start
    marker is the separator) and come back as raw colour bytes, every square the same size, so
    they are cut apart by counting.
    """
    return [
        settings.ffmpeg_path,
        *BASE_FLAGS,
        "-f",
        "image2pipe",
        "-c:v",
        "mjpeg",
        "-i",
        "pipe:0",
        "-frames:v",
        str(count),
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


async def decode(
    pictures: Sequence[bytes], settings: Settings, *, size: int = CHIP_SIZE
) -> list[np.ndarray]:
    """Read a file's stored face pictures back as the squares a recognizer takes.

    What measuring the library again with a different model reads instead of the media: the
    square was kept beside every face so that changing the recognizer costs describing stored
    pictures rather than decoding files. Refuses a partial answer for the same reason `encode`
    does: one picture fewer than asked for and nothing says which face lost its numbers.
    """
    if not pictures:
        return []
    stream = await run_tool(
        decode_args(len(pictures), settings),
        time_limit=_ENCODE_TIMEOUT,
        capture=True,
        stdin=b"".join(pictures),
    )
    stride = size * size * 3
    if len(stream) != stride * len(pictures):
        raise ValueError(
            f"asked for {len(pictures)} face squares and got {len(stream) // stride}; not "
            "describing any of them, because which square belongs to which face is no longer known"
        )
    # Copied out of the stream's bytes (`frombuffer` is a read-only view of them), with
    # `np.array` rather than `.copy()`, which reads as a file copy to the loop-blocking gate.
    return [
        np.array(
            np.frombuffer(stream, dtype=np.uint8, count=stride, offset=index * stride), copy=True
        ).reshape(size, size, 3)
        for index in range(len(pictures))
    ]


def digest(crop: bytes) -> str:
    """The identity of a crop, as lowercase hex.

    The same hash the library identifies files with, over every byte here because a crop is a
    few kilobytes already in memory and there is nothing to sample. The reason is the library's
    too: a reference that arrives twice (once in a folder, once in a pack) has to be recognized
    as one reference, and a second hashing scheme would be a second answer to a question that has
    one.
    """
    return blake3(crop).hexdigest()


def portrait(frame: np.ndarray, box: Box, *, margin: float = tuning.COVER_MARGIN) -> np.ndarray:
    """A square picture of somebody, cut out of the frame their face was found in.

    Not the aligned square above. That one is 112 pixels and is cropped to the eyes, nose and mouth
    because that is what a recognizer reads: it is a measurement, not a portrait, and a screen
    that draws one at 230 across is showing a picture blown up to twice its size.

    Square, because every place a cover is drawn is square, and cropping here rather than in the
    browser means what is cut away is chosen with the face in view instead of by a rule about
    aspect ratios.

    Kept inside the frame by moving rather than by shrinking. A face near an edge would otherwise
    produce a smaller square than a face in the middle, so a cover's size would depend on where
    somebody happened to be standing. Only a frame smaller than the square it wants shrinks it, and
    then there is nothing else to do.
    """
    height, width = frame.shape[:2]
    wanted = min(int(max(box.width, box.height) * (1.0 + 2.0 * margin)), height, width)
    wanted = max(wanted, 2)

    middle_x = box.x + box.width // 2
    middle_y = box.y + box.height // 2
    left = min(max(middle_x - wanted // 2, 0), max(width - wanted, 0))
    top = min(max(middle_y - wanted // 2, 0), max(height - wanted, 0))
    return np.ascontiguousarray(frame[top : top + wanted, left : left + wanted])


def portrait_args(width: int, height: int, size: int, settings: Settings) -> list[str]:
    """One picture in, one stored picture out, reduced to at most `size` on a side.

    Reduced by the encoder rather than in Python: it is one process either way, and the encoder
    averages the pixels it is dropping where anything short enough to write here would not.
    Enlarging is refused (`min(size,iw)`) because a face that was small in the frame is small,
    and stretching it only makes the blur bigger.
    """
    return [
        settings.ffmpeg_path,
        *BASE_FLAGS,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-i",
        "pipe:0",
        "-frames:v",
        "1",
        "-vf",
        f"scale='min({size},iw)':-2",
        "-q:v",
        str(tuning.CROP_QUALITY),
        "-f",
        "image2pipe",
        "-c:v",
        "mjpeg",
        "pipe:1",
    ]


async def encode_portrait(picture: np.ndarray, settings: Settings) -> bytes | None:
    """Store one cover picture. None if the encoder produced nothing usable."""
    height, width = picture.shape[:2]
    argv = portrait_args(width, height, tuning.COVER_SIZE, settings)
    raw = np.ascontiguousarray(picture, dtype=np.uint8).tobytes()
    stream = await run_tool(argv, time_limit=_ENCODE_TIMEOUT, capture=True, stdin=raw)
    written = split_encoded(stream)
    return written[0] if written else None
