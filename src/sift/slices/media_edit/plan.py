# SPDX-License-Identifier: AGPL-3.0-or-later
"""Working out what a compression can and cannot do, before anything is encoded.

Everything here is arithmetic over what is already known about a file: how long it runs, how big
the picture is, how fast it moves, whether it has sound. Nothing in this module opens a file,
spawns anything or waits on anything, which is what lets a panel covering four hundred files answer
immediately, and what lets every rule in it be tested without encoding a frame.

Three questions are answered, and they are the same question at different moments:

    can this target be met at all          before the panel offers a button
    which attempt should be made first     so a four-minute encode is not spent finding out
    was that good enough, and what next    after each attempt, from what it actually weighed

The last one is why the ladder is navigated here rather than inside the job. A job that decides its
own next step while holding an open file is a decision nobody can test; a function from "what has
been measured so far" to "what to try next" is one that can be tested exhaustively, and is.
"""

from __future__ import annotations

from dataclasses import dataclass

from sift.slices.media_edit import tuning
from sift.slices.media_edit.tuning import Rung


@dataclass(frozen=True, slots=True)
class SourceFacts:
    """What is known about a file without opening it. Straight off the indexed row.

    Every field is optional except the ones arithmetic cannot proceed without, and those are
    checked rather than assumed: a file can be indexed with no duration or no dimensions, and the
    honest answer for one of those is "Sift cannot tell", not a number derived from a guess.
    """

    width: int | None
    height: int | None
    duration_ms: int | None
    fps: float | None
    size_bytes: int | None
    vcodec: str | None
    acodec: str | None
    container: str | None
    #: Whether the picture is HDR, so the encode maps it to SDR rather than washing it out.
    hdr: bool = False

    @property
    def duration_seconds(self) -> float | None:
        if self.duration_ms is None or self.duration_ms <= 0:
            return None
        return self.duration_ms / 1000.0

    @property
    def has_audio(self) -> bool:
        return bool(self.acodec)

    @property
    def measurable(self) -> bool:
        """Whether there is enough here to predict anything at all.

        A file with no duration or no dimensions is not refused: it is simply not predicted for,
        and the panel says so rather than showing a number it made up.
        """
        return (
            self.duration_seconds is not None
            and self.width is not None
            and self.width > 0
            and self.height is not None
            and self.height > 0
        )


def floor_height(source: SourceFacts) -> int:
    """The smallest picture this file may be reduced to.

    The floor, or the file's own height when it is already under it. A source below the floor is
    neither refused nor scaled up: it is compressed at the size it already is, because making it
    bigger would produce a larger file that looks worse, which is the opposite of the job.
    """
    if source.height is None or source.height <= 0:
        return tuning.MINIMUM_HEIGHT
    return min(tuning.MINIMUM_HEIGHT, source.height)


def output_height(rung: Rung, source: SourceFacts) -> int:
    """How tall this rung's output is: the rung's cap, or the source, whichever is smaller."""
    source_height = source.height if source.height and source.height > 0 else 0
    if rung.height is None:
        return source_height
    if source_height <= 0:
        return rung.height
    return min(rung.height, source_height)


def output_width(rung: Rung, source: SourceFacts) -> int:
    """The matching width, keeping the shape and rounded to an even number.

    Odd dimensions are refused outright by the encoder, so the rounding is a requirement rather
    than a tidiness.
    """
    if not source.width or not source.height or source.height <= 0:
        return 0
    scaled = round(source.width * output_height(rung, source) / source.height)
    return max(2, scaled - (scaled % 2))


def output_fps(rung: Rung, source: SourceFacts) -> float:
    """The output's frame rate: the rung's cap, or the source's, whichever is slower."""
    source_fps = source.fps if source.fps and source.fps > 0 else 30.0
    if rung.fps is None:
        return source_fps
    return min(rung.fps, source_fps)


def allowed_rungs(source: SourceFacts) -> tuple[Rung, ...]:
    """The rungs this file may actually be taken down, best first.

    **This is where the floor is enforced, and it is the only place.** A rung whose output would be
    shorter than `floor_height` is not attempted, is not counted as reachable, and is not offered
    as an alternative, so a target that only fits below the floor is reported as not fitting,
    which is the whole point of having one.
    """
    limit = floor_height(source)
    return tuple(rung for rung in tuning.RUNGS if output_height(rung, source) >= limit)


def predicted_bytes(rung: Rung, source: SourceFacts) -> int | None:
    """What this rung is expected to weigh. None when the file cannot be predicted for.

    A frame costs what its pixels cost, a second costs what its frames cost, and the sound rides
    along at whatever it already was, which is assumed rather than read, because the sound is
    copied untouched and reading it per file would mean opening every file to draw a panel.

    It is an estimate and it is treated as one everywhere it is used: it decides what to attempt,
    never what the answer is. What decides the answer is the encode, weighed.
    """
    seconds = source.duration_seconds
    if not source.measurable or seconds is None:
        return None
    pixels = output_width(rung, source) * output_height(rung, source)
    video_bits = rung.bits_per_pixel * pixels * output_fps(rung, source)
    audio_bits = tuning.ASSUMED_AUDIO_BITS_PER_SECOND if source.has_audio else 0
    total_bits = (video_bits + audio_bits) * seconds * (1 + tuning.CONTAINER_OVERHEAD)
    return int(total_bits / 8)


def smallest_reachable_bytes(source: SourceFacts) -> int | None:
    """The smallest file this source can honestly be turned into. None when unpredictable.

    The last rung the floor allows. Everything below it exists in the table and is not offered.
    """
    rungs = allowed_rungs(source)
    if not rungs:
        return None
    return predicted_bytes(rungs[-1], source)


def best_rung_under(target_bytes: int, source: SourceFacts) -> int | None:
    """Which rung to attempt first: the best-quality one expected to fit. None if none is.

    Walking from the top and taking the first that fits gives the highest quality the estimate
    believes in, which is the right place to start: the ladder then corrects the estimate with
    measurements rather than trusting it.
    """
    rungs = allowed_rungs(source)
    for index, rung in enumerate(rungs):
        predicted = predicted_bytes(rung, source)
        if predicted is not None and predicted <= target_bytes:
            return index
    return None


# --- what the panel is told -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether one file can meet one target, and what to say about it.

    `reason` is filled in only when something has to be said, and it says what about THIS file
    makes the target impossible rather than that it might not work out. A warning somebody cannot
    act on is a warning they learn to click past.
    """

    reachable: bool
    #: What the first attempt is expected to weigh, when there is one worth attempting.
    predicted_bytes: int | None = None
    #: The smallest target this file could actually meet, when the asked-for one is out of reach.
    smallest_reachable_bytes: int | None = None
    reason: str | None = None
    #: True when nothing has to be re-encoded at all: the file already meets the target and, if
    #: compatibility was asked for, is already in a form that plays anywhere.
    copy_only: bool = False


def _describe(source: SourceFacts) -> str:
    """The file, in the terms that make a refusal make sense: how long, and how big a picture.

    Whole minutes above a minute, because the seconds are noise next to the point being made, and
    seconds below one: a short clip at an enormous picture size can miss a small target, and
    "0m of 7680x4320 video" is a sentence that makes somebody distrust the rest of it.
    """
    seconds = int(source.duration_seconds or 0)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        length = f"{hours}h {minutes}m"
    elif minutes:
        length = f"{minutes}m"
    else:
        length = f"{seconds}s"
    return f"{length} of {source.width}x{source.height} video"


def judge(
    source: SourceFacts,
    *,
    target_bytes: int | None,
    compatibility: bool,
) -> Verdict:
    """Everything the panel needs to say about one file, before anything is encoded.

    With no size target this is only ever about compatibility, and the answer is either "nothing to
    do" or "rewrap it". With one, it is the reachability question: can any rung the floor allows
    land under the number, and if not, what is the smallest number that would.
    """
    needs_encode = compatibility and not _already_compatible(source)
    if target_bytes is None:
        if not needs_encode:
            return Verdict(reachable=True, copy_only=True, predicted_bytes=source.size_bytes)
        return Verdict(reachable=True, predicted_bytes=source.size_bytes)

    if source.size_bytes is not None and source.size_bytes <= target_bytes and not needs_encode:
        # It already fits and it already plays. Encoding it would spend minutes making it worse.
        return Verdict(reachable=True, copy_only=True, predicted_bytes=source.size_bytes)

    if not source.measurable:
        # Nothing is known about the file's shape, so nothing can be predicted. Not a refusal: the
        # encode is allowed to find out, and the panel says a prediction is not available.
        return Verdict(reachable=True)

    index = best_rung_under(target_bytes, source)
    if index is not None:
        rungs = allowed_rungs(source)
        return Verdict(reachable=True, predicted_bytes=predicted_bytes(rungs[index], source))

    smallest = smallest_reachable_bytes(source)
    return Verdict(
        reachable=False,
        smallest_reachable_bytes=smallest,
        reason=_unreachable_reason(source, target_bytes=target_bytes, smallest=smallest),
    )


def _unreachable_reason(source: SourceFacts, *, target_bytes: int, smallest: int | None) -> str:
    """Why this file cannot meet this target, in terms of the file.

    Two facts do the work: what the file is, and what the smallest honest version of it weighs.
    Together they say "the target is below the floor" without using the word floor, which is the
    sentence somebody can act on.
    """
    described = _describe(source)
    wanted = _megabytes(target_bytes)
    if smallest is None:  # pragma: no cover - measurable sources always predict
        return f"This is {described} \u2014 {wanted} is not reachable."
    return (
        f"This is {described}. The smallest Sift will make it is about {_megabytes(smallest)}, "
        f"because it will not reduce the picture below {tuning.MINIMUM_HEIGHT}p \u2014 going under "
        f"{wanted} would mean going under that."
    )


def _megabytes(value: int) -> str:
    """A byte count as somebody reads it. Whole numbers above ten, one decimal below."""
    megabytes = value / (1024 * 1024)
    if megabytes >= 10:
        return f"{round(megabytes)} MB"
    return f"{megabytes:.1f} MB"


def _already_compatible(source: SourceFacts) -> bool:
    """Whether the file already plays anywhere, so compatibility asks nothing of it."""
    return (
        source.container == tuning.COMPATIBLE_CONTAINER
        and source.vcodec == tuning.COMPATIBLE_VCODEC
        and (not source.has_audio or source.acodec in tuning.COMPATIBLE_ACODECS)
    )


def needs_audio_conversion(source: SourceFacts) -> bool:
    """Whether the compatibility target has to touch the sound, which it otherwise never does.

    True only when the file has sound the widely-supported container cannot carry. Everywhere else
    (and on every size target, without exception) the sound is copied through untouched.
    """
    return source.has_audio and source.acodec not in tuning.COMPATIBLE_ACODECS


def rewrap_is_enough(source: SourceFacts, *, target_bytes: int | None) -> bool:
    """Whether a compatibility conversion is a stream copy rather than an encode.

    The common case, and the one worth getting right: a file that is already H.264 in a container
    that is merely the wrong one needs its packets moved, not its pixels rebuilt. That is seconds
    instead of minutes and it loses nothing.

    A size target that the file already meets does not change the answer; one it does not meet does,
    because then the picture has to shrink and a copy cannot shrink anything.
    """
    if source.vcodec != tuning.COMPATIBLE_VCODEC or needs_audio_conversion(source):
        return False
    if target_bytes is None:
        return True
    return source.size_bytes is not None and source.size_bytes <= target_bytes


# --- navigating the ladder with real measurements ---------------------------------------------


@dataclass(frozen=True, slots=True)
class Attempt:
    """One rung that was actually encoded, and what the file weighed when it was."""

    index: int
    size_bytes: int


def next_attempt(
    attempts: tuple[Attempt, ...],
    *,
    target_bytes: int,
    source: SourceFacts,
) -> int | None:
    """Which rung to encode next, or None when the ladder is done.

    **Best fit, not first fit.** An estimate can be wrong in either direction, so landing under the
    target is not the end: a rung above the one that fit may fit too, and it would look better. The
    range between "known to fit" and "known not to fit" is halved each time until nothing is left
    between them, and whichever fitting rung ends up highest is what is kept.

    Bounded by `MAX_ATTEMPTS`, because every step here is a full pass over the file. Running out of
    attempts is not a failure: the best fitting rung found so far is the answer, and the caller
    keeps it.
    """
    rungs = allowed_rungs(source)
    if not rungs:
        return None
    if len(attempts) >= tuning.MAX_ATTEMPTS:
        return None

    if not attempts:
        # The estimate's pick, or the last rung when the estimate says nothing fits: the caller
        # only reaches here having been told the target is out of reach and having said go anyway,
        # and the smallest rung is the closest it can come to what was asked for.
        picked = best_rung_under(target_bytes, source)
        return len(rungs) - 1 if picked is None else picked

    fitting = [each.index for each in attempts if each.size_bytes <= target_bytes]
    over = [each.index for each in attempts if each.size_bytes > target_bytes]
    tried = {each.index for each in attempts}

    # Above the best fit lies better quality that may also fit; below the worst overshoot lies
    # nothing worth having. The gap between the two is what is left to search.
    upper = min(fitting) if fitting else len(rungs)
    lower = max(over) + 1 if over else 0

    candidate = (lower + upper - 1) // 2 if lower < upper else None
    if candidate is None or candidate in tried or not 0 <= candidate < len(rungs):
        return None
    return candidate


def best_result(attempts: tuple[Attempt, ...], *, target_bytes: int) -> Attempt | None:
    """The attempt to keep: the highest-quality one that came in under the target.

    None when nothing did, which is the caller's cue to keep the smallest it managed and say so:
    a person who overrode an unreachable target gets the closest Sift could get, not nothing.
    """
    fitting = [each for each in attempts if each.size_bytes <= target_bytes]
    if not fitting:
        return None
    return min(fitting, key=lambda each: each.index)
