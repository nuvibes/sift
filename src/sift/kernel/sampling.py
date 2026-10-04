# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which frames to look at, and how many.

Every feature that looks inside a video asks here (the hover preview, the scrub strip, the
fingerprint, the face pass), because the frames picked are baked into things that outlive the run
(a stored hash, a preview on disk). Four questions, four shapes: the fingerprint wants the same
count for every file, the scrub strip a fine rate, the face pass a count that never falls with
length, the preview a fixed budget of seconds. In the kernel because several features need it.

The fingerprint's ladder is a frame a second for short clips, becoming a budget of thirty frames
past five minutes, so the cost of looking never grows with length; thirty is ample to tell videos
apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from sift.kernel.log import get_logger

log = get_logger(__name__)

#: The most frames anything here will ever ask for. Past the last rung of the ladder the rate is
#: derived from this rather than the other way round, and a GIF is capped at it outright.
MAX_FRAMES = 30

#: The ladder: up to this many seconds, at this many frames per second. Read in order, first match
#: wins. Anything longer than the last rung gets MAX_FRAMES spread across its whole duration.
LADDER: tuple[tuple[float, float], ...] = (
    (20.0, 1.0),
    (60.0, 1.0 / 3.0),
    (300.0, 1.0 / 10.0),
)


def picture_span(duration_ms: int | None, video_duration_ms: int | None) -> int:
    """How much of a file there is to sample PICTURES from: the length every sampler here is handed.

    The container's length covers the sound too, so where sound outlasts picture, moments past the
    last frame sample nothing. So the shorter of the two where the picture's length is known (a
    stream stating more than the file plays has a tail nothing plays); otherwise the file's.
    """
    whole = max(duration_ms or 0, 0)
    picture = video_duration_ms or 0
    if picture <= 0:
        return whole
    if whole <= 0:
        return picture
    return min(whole, picture)


def frame_rate(duration_ms: int) -> float:
    """Frames per second to sample a video of this length at.

    A duration of zero or less is not an error: a file the probe could not time is still
    thumbnailed, from the start.
    """
    if duration_ms <= 0:
        return LADDER[0][1]

    seconds = duration_ms / 1000
    for limit, rate in LADDER:
        if seconds <= limit:
            return rate
    return MAX_FRAMES / seconds


def sample_frames(duration_ms: int) -> tuple[int, ...]:
    """The timestamps, in milliseconds, to sample a video of this length at.

    Starts at zero, always: the frame a preview opens on and a thumbnail is cut from, so playing
    never needs a seek. No cap is needed: every rung yields at most `MAX_FRAMES`, and past the last
    the rate is defined to yield exactly that.
    """
    if duration_ms <= 0:
        return (0,)

    step_ms = 1000 / frame_rate(duration_ms)
    timestamps: list[int] = []
    index = 0
    while True:
        # Multiplied out from the index, never accumulated: a float sum drifts, and these end up
        # in a filename and a hash.
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
        """Refuse a length that is not a whole number of cuts.

        A guard at import, in front of whoever typed the number, because `preview_segments` rounds
        silently and would give a montage that does not add up to the length the menu promised.
        In whole milliseconds, rounded as `preview_segments` rounds, so float dust does not reject
        an honest shape.
        """
        total_ms = round(self.total_seconds * 1000)
        piece_ms = round(self.segment_seconds * 1000)
        if total_ms <= 0 or piece_ms <= 0 or total_ms % piece_ms:
            raise ValueError(
                f"a preview of {self.total_seconds}s cannot be cut evenly every "
                f"{self.segment_seconds}s"
            )


#: The shapes a hover preview may have: two, not a pair of free numbers, because length and cut
#: rate are not independent and each combination is a separate cache and a library rebuild. Both
#: cut every 1.5 seconds (every second reads frantic, two sluggish), so the choice is only how long
#: to look, the longer exactly twice the shorter. Labels are the length alone, since the pace is
#: the same and stated in the setting's help, and the menu's narrow value column clips anything
#: longer. In the kernel because the settings screen and the encoding job both read them.
PREVIEW_SHAPES: tuple[PreviewShape, ...] = (
    PreviewShape("brief", "6 seconds", 6.0, 1.5),
    PreviewShape("full", "12 seconds", 12.0, 1.5),
)

#: What a fresh install gets, and what a stored value falling outside the list falls back to.
DEFAULT_PREVIEW_SHAPE = "full"

#: The preference's name, here because the settings screen, the job and the rebuild all read it
#: and none may import another.
PREVIEW_SHAPE_SETTING = "performance.preview_shape"


def preview_shape(key: str) -> PreviewShape:
    """The shape stored under this name, or the default.

    An unknown name (a hand-edited database, a shape no longer offered) gets the default rather
    than a raise, so previews keep being built.
    """
    for shape in PREVIEW_SHAPES:
        if shape.key == key:
            return shape
    return next(shape for shape in PREVIEW_SHAPES if shape.key == DEFAULT_PREVIEW_SHAPE)


def preview_segments(duration_ms: int, shape: PreviewShape) -> tuple[Piece, ...]:
    """The stretches a hover preview is built from: where each begins and how long it runs.

    A fixed BUDGET of seconds whatever the file, because previews are fetched by the hundred and
    their size is why they are built ahead. The shape comes from a `PreviewShape`.

    It starts at zero because the preview REPLACES the tile's still in the same rectangle, so any
    other first frame makes the tile jump; where the still was cut later (a black or faded opening)
    the job moves the first piece (`media_jobs.thumbnails.from_the_still`). Below twice the budget it is
    one piece from the start: a montage of a barely longer file skips nothing and encodes bigger.
    No piece is clamped to the end: past that floor the last one always ends inside the file, and
    the caller passes the picture's own length (`picture_span`).
    """
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


#: The most tiles one sprite sheet holds: browsers refuse to decode a JPEG past a few thousand
#: pixels a side, so an unbounded sheet is no scrubber at all. Past it the strip gets coarser.
MAX_SPRITE_FRAMES = 400

#: How often to grab a frame for the scrub strip, by clip length, first match wins. Its own ladder:
#: the fingerprint's thirty frames would show one picture for minutes, and a short clip being hunted
#: through wants fine detail where a long video wants a frame every few seconds.
SPRITE_LADDER: tuple[tuple[float, float], ...] = (
    (20.0, 2.0),  # under ~20s: twice a second
    (120.0, 1.0),  # under two minutes: every second
    (600.0, 1.0 / 3.0),  # under ten minutes: every three seconds
)

#: Past the last rung: a frame every ten seconds, until the cap above takes over.
SPRITE_LONG_RATE = 1.0 / 10.0


def sprite_frames(duration_ms: int) -> tuple[int, ...]:
    """The timestamps the scrub-preview sheet is built from.

    A third question ("what is at this point on the timeline") needing a density the others do not
    carry, and kept apart so `LADDER` and `MAX_FRAMES` never move: every stored fingerprint rides on
    them, and the golden file pins them.
    """
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
    """The timestamps a fingerprint is built from. Always MAX_FRAMES of them, evenly spread.

    Not the ladder: a fingerprint must be COMPARABLE, so the same count for every file, spread
    across its length. More samples buy little; what defeats a fingerprint is a shifted timeline,
    which is a matching problem, not a sampling one.
    """
    if duration_ms <= 0:
        return (0,)

    step_ms = duration_ms / MAX_FRAMES
    return tuple(round(index * step_ms) for index in range(MAX_FRAMES))


#: The most moments a face pass looks at in one file at any density: twice the fingerprint's, since
#: somebody briefly on screen is the one case where more moments find more.
MAX_FACE_FRAMES = 60

#: Up to here a face pass looks once a second, and the count is simply the length in seconds.
FACE_DENSE_SECONDS = 20.0

#: Past here the count stops growing: MAX_FRAMES moments spread across whatever length there is.
FACE_SPREAD_SECONDS = 300.0

#: What a file at `FACE_DENSE_SECONDS` gets, which is where the second stretch has to start from.
FACE_DENSE_MOMENTS = 20

#: Bumped when anything above changes what a pass would look at: a scan records the density asked
#: for, not the moments, so without it every scanned file would read as settled under a new shape.
FACE_SAMPLING_VERSION = 3


def face_moments(duration_ms: int) -> int:
    """How many moments a face pass looks at in a file of this length, before density.

    Not the fingerprint's ladder, whose count falls at every rung (a longer clip examined less than
    a shorter one). Three stretches: once a second to twenty seconds, climbing evenly to the
    thirty-moment budget at five minutes, then that budget spread across the length.
    """
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
    """The moments a face pass looks at, at a chosen density.

    `density` scales the count (half misses somebody seen once; triple finds them at triple the
    cost). Not a rate: the duration is already a count.
    """
    if duration_ms <= 0:
        return (0,)
    if density <= 0:
        raise ValueError("a density of zero would look at no frames at all")

    wanted = max(1, min(MAX_FACE_FRAMES, round(face_moments(duration_ms) * density)))
    step = duration_ms / wanted
    # Multiplied out from the index, never accumulated.
    return tuple(round(index * step) for index in range(wanted))
