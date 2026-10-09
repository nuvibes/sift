# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which frames to look at, and how many: the picks are baked into stored hashes and previews."""

from __future__ import annotations

from dataclasses import dataclass

from sift.kernel.log import get_logger

log = get_logger(__name__)

#: The most frames anything here will ever ask for.
MAX_FRAMES = 30

#: Up to this many seconds, this many frames per second; first match wins.
LADDER: tuple[tuple[float, float], ...] = (
    (20.0, 1.0),
    (60.0, 1.0 / 3.0),
    (300.0, 1.0 / 10.0),
)


def picture_span(duration_ms: int | None, video_duration_ms: int | None) -> int:
    """How much of a file has pictures to sample: the shorter of the container and the picture."""
    whole = max(duration_ms or 0, 0)
    picture = video_duration_ms or 0
    if picture <= 0:
        return whole
    if whole <= 0:
        return picture
    return min(whole, picture)


def frame_rate(duration_ms: int) -> float:
    """Frames per second to sample a video of this length at."""
    if duration_ms <= 0:
        return LADDER[0][1]

    seconds = duration_ms / 1000
    for limit, rate in LADDER:
        if seconds <= limit:
            return rate
    return MAX_FRAMES / seconds


def sample_frames(duration_ms: int) -> tuple[int, ...]:
    """The timestamps to sample at, starting at zero so playing never needs a seek."""
    if duration_ms <= 0:
        return (0,)

    step_ms = 1000 / frame_rate(duration_ms)
    timestamps: list[int] = []
    index = 0
    while True:
        # Multiplied out from the index, never accumulated: a float sum drifts into a hash.
        timestamp = round(index * step_ms)
        if timestamp >= duration_ms:
            break
        timestamps.append(timestamp)
        index += 1
    return tuple(timestamps)


@dataclass(frozen=True, slots=True)
class Piece:
    """One stretch of a video that a hover preview is cut from."""

    start_ms: int
    length_ms: int


@dataclass(frozen=True, slots=True)
class PreviewShape:
    """How long a hover preview runs, and how often it cuts to the next moment."""

    key: str
    label: str
    total_seconds: float
    segment_seconds: float

    def __post_init__(self) -> None:
        """Refuse a length that is not a whole number of cuts, which the menu would misstate."""
        total_ms = round(self.total_seconds * 1000)
        piece_ms = round(self.segment_seconds * 1000)
        if total_ms <= 0 or piece_ms <= 0 or total_ms % piece_ms:
            raise ValueError(
                f"a preview of {self.total_seconds}s cannot be cut evenly every "
                f"{self.segment_seconds}s"
            )


#: Two shapes, as each is a separate cache and a library rebuild; both cut every 1.5 seconds.
PREVIEW_SHAPES: tuple[PreviewShape, ...] = (
    PreviewShape("brief", "6 seconds", 6.0, 1.5),
    PreviewShape("full", "12 seconds", 12.0, 1.5),
)

DEFAULT_PREVIEW_SHAPE = "full"

#: Here because the settings screen, the job and the rebuild all read it.
PREVIEW_SHAPE_SETTING = "performance.preview_shape"


def preview_shape(key: str) -> PreviewShape:
    """The shape stored under this name, or the default so previews keep being built."""
    for shape in PREVIEW_SHAPES:
        if shape.key == key:
            return shape
    return next(shape for shape in PREVIEW_SHAPES if shape.key == DEFAULT_PREVIEW_SHAPE)


def preview_segments(duration_ms: int, shape: PreviewShape) -> tuple[Piece, ...]:
    """A hover preview's stretches: a fixed budget, starting at zero where the still is."""
    total_ms = max(0, round(shape.total_seconds * 1000))
    piece_ms = max(1, round(shape.segment_seconds * 1000))

    # An untimed file: take what is there from the start; ffmpeg stops when the file does.
    if duration_ms <= 0:
        return (Piece(0, total_ms),)

    count = round(total_ms / piece_ms)
    if duration_ms < 2 * total_ms or count <= 1:
        return (Piece(0, min(total_ms, duration_ms)),)

    step = duration_ms / count
    # Multiplied out from the index, never accumulated, as everywhere here.
    return tuple(Piece(round(index * step), piece_ms) for index in range(count))


#: Browsers refuse to decode a JPEG past a few thousand pixels a side.
MAX_SPRITE_FRAMES = 400

#: Its own ladder, finer than the fingerprint's, by clip length; first match wins.
SPRITE_LADDER: tuple[tuple[float, float], ...] = (
    (20.0, 2.0),  # under ~20s: twice a second
    (120.0, 1.0),  # under two minutes: every second
    (600.0, 1.0 / 3.0),  # under ten minutes: every three seconds
)

#: Past the last rung: a frame every ten seconds, until the cap above takes over.
SPRITE_LONG_RATE = 1.0 / 10.0


def sprite_frames(duration_ms: int) -> tuple[int, ...]:
    """The scrub sheet's timestamps, kept apart from `LADDER`, which fingerprints ride on."""
    if duration_ms <= 0:
        return (0,)

    seconds = duration_ms / 1000
    rate = SPRITE_LONG_RATE
    for limit, candidate in SPRITE_LADDER:
        if seconds <= limit:
            rate = candidate
            break

    wanted = min(MAX_SPRITE_FRAMES, max(1, int(seconds * rate)))
    step_ms = duration_ms / wanted

    # Multiplied out from the index, never accumulated, as in `sample_frames`.
    return tuple(round(index * step_ms) for index in range(wanted))


def hash_frames(duration_ms: int) -> tuple[int, ...]:
    """A fingerprint's timestamps: always MAX_FRAMES, evenly spread, so every file compares."""
    if duration_ms <= 0:
        return (0,)

    step_ms = duration_ms / MAX_FRAMES
    return tuple(round(index * step_ms) for index in range(MAX_FRAMES))


#: Twice the fingerprint's: somebody briefly on screen is found by more moments.
MAX_FACE_FRAMES = 60

FACE_DENSE_SECONDS = 20.0

FACE_SPREAD_SECONDS = 300.0

FACE_DENSE_MOMENTS = 20

#: Bumped when the moments change: a scan records the density asked for, not the moments.
FACE_SAMPLING_VERSION = 3


def face_moments(duration_ms: int) -> int:
    """How many moments a face pass looks at, a count that never falls with length."""
    if duration_ms <= 0:
        return 1

    seconds = duration_ms / 1000
    if seconds <= FACE_DENSE_SECONDS:
        count = round(seconds)
    elif seconds <= FACE_SPREAD_SECONDS:
        through = (seconds - FACE_DENSE_SECONDS) / (FACE_SPREAD_SECONDS - FACE_DENSE_SECONDS)
        count = round(FACE_DENSE_MOMENTS + through * (MAX_FRAMES - FACE_DENSE_MOMENTS))
    else:
        count = MAX_FRAMES
    return max(1, min(MAX_FRAMES, count))


def face_frames(duration_ms: int, *, density: float = 1.0) -> tuple[int, ...]:
    """The moments a face pass looks at, with `density` scaling the count."""
    if duration_ms <= 0:
        return (0,)
    if density <= 0:
        raise ValueError("a density of zero would look at no frames at all")

    wanted = max(1, min(MAX_FACE_FRAMES, round(face_moments(duration_ms) * density)))
    step = duration_ms / wanted
    # Multiplied out from the index, never accumulated.
    return tuple(round(index * step) for index in range(wanted))
