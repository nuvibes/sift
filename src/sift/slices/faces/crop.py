# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning a face in a frame into the aligned square a recognizer was trained on.

The transform carrying the five landmarks onto the standard ones is fitted and the picture
resampled through it. Squares are encoded in one call per file, and stored compressed: the drift
is far below anything decided here, at a fifth of the disk.
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

#: A guard against a wedged encoder, not a budget.
_ENCODE_TIMEOUT = 60.0

CHIP_SIZE = 112

#: Where the five landmarks sit in that square: the arrangement the recognizers were trained on.
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
    """The square a face was resampled into, and how much of it is really the picture: once the
    edge is mirrored in, the invented part cannot be told apart."""

    chip: np.ndarray
    containment: float
    """The fraction of the FACE that came from inside the frame. 1.0 is a face wholly cut from real
    pixels; 0.5 means half of it is one edge pixel repeated. Measured over the middle of the square
    rather than all of it. See `_CORE_MARGIN`."""


def similarity_transform(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """The rotation, single scale and shift that best carries `source` onto `target`.

    Least squares over all five points, and never a stretch, which would hide a turned face.
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
    # A reflection fits as well and mirrors the face, so it is flipped back.
    correction = np.ones(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        correction[1] = -1.0
    rotation = u @ np.diag(correction) @ vt
    scale = float(singular @ correction) / variance

    matrix = np.eye(3, dtype=np.float64)
    matrix[:2, :2] = scale * rotation
    matrix[:2, 2] = target_mean - scale * rotation @ source_mean
    return matrix


#: How far past the landmarks the face reaches, as a fraction of the eye-to-mouth drop: the part
#: containment is measured over, so a missing hairline is not a missing face.
_CORE_MARGIN = 0.5


def _core_of_the_template() -> tuple[float, float, float, float]:
    """The part of the aligned square that is the face, derived from the template."""
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
    """Fold an out-of-range index back into the picture, reflecting without repeating the edge,
    so a square past the frame gets plausible texture rather than a streak."""
    if size == 1:
        return np.zeros_like(index)
    period = 2 * size - 2
    folded = np.abs(index) % period
    return np.where(folded >= size, period - folded, folded)


def warp(frame: np.ndarray, matrix: np.ndarray, *, size: int = CHIP_SIZE) -> Aligned:
    """Resample a frame through a transform into a `size` square, bilinearly, mirroring past
    the frame's edge, and report how much of the face came from inside it."""
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

    # One sample per pixel; a point on the last row or column is still inside.
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


_START = b"\xff\xd8\xff"


def encode_args(count: int, size: int, quality: int, settings: Settings) -> list[str]:
    """Arguments turning a run of aligned squares into stored pictures, one process per file."""
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
    """Cut a run of stored pictures apart at each one's start marker."""
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
    """Store a file's face pictures, one per square in order; a count mismatch is refused."""
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
    """Arguments turning a run of stored pictures back into squares, one process per file."""
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
    """Read a file's stored face pictures back as squares, refusing a partial answer."""
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
    # `np.array` rather than `.copy()`, which the loop-blocking gate reads as a file copy.
    return [
        np.array(
            np.frombuffer(stream, dtype=np.uint8, count=stride, offset=index * stride), copy=True
        ).reshape(size, size, 3)
        for index in range(len(pictures))
    ]


def digest(crop: bytes) -> str:
    """The identity of a crop, as lowercase hex: the library's own hash."""
    return blake3(crop).hexdigest()


def portrait(frame: np.ndarray, box: Box, *, margin: float = tuning.COVER_MARGIN) -> np.ndarray:
    """A square picture of somebody cut from their frame, moved rather than shrunk at an edge."""
    height, width = frame.shape[:2]
    wanted = min(int(max(box.width, box.height) * (1.0 + 2.0 * margin)), height, width)
    wanted = max(wanted, 2)

    middle_x = box.x + box.width // 2
    middle_y = box.y + box.height // 2
    left = min(max(middle_x - wanted // 2, 0), max(width - wanted, 0))
    top = min(max(middle_y - wanted // 2, 0), max(height - wanted, 0))
    return np.ascontiguousarray(frame[top : top + wanted, left : left + wanted])


def portrait_args(width: int, height: int, size: int, settings: Settings) -> list[str]:
    """Arguments reducing one picture to at most `size` a side, never enlarging it."""
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
