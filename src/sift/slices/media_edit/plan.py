# SPDX-License-Identifier: AGPL-3.0-or-later
"""Working out what a compression can and cannot do, before anything is encoded.
Pure arithmetic over the indexed row, so the ladder's every step can be tested without encoding."""

from __future__ import annotations

from dataclasses import dataclass

from sift.slices.media_edit import tuning
from sift.slices.media_edit.tuning import Rung


@dataclass(frozen=True, slots=True)
class SourceFacts:
    """What is known about a file without opening it, straight off the indexed row."""

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
        """Whether there is enough here to predict anything at all."""
        return (
            self.duration_seconds is not None
            and self.width is not None
            and self.width > 0
            and self.height is not None
            and self.height > 0
        )


def floor_height(source: SourceFacts) -> int:
    """The smallest picture this file may be reduced to: the floor, or its own height when under it."""
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
    """The matching width, keeping the shape; the encoder refuses odd dimensions."""
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
    """The rungs this file may be taken down, best first. The only place the floor is enforced."""
    limit = floor_height(source)
    return tuple(rung for rung in tuning.RUNGS if output_height(rung, source) >= limit)


def predicted_bytes(rung: Rung, source: SourceFacts) -> int | None:
    """What this rung is expected to weigh, or None. An estimate: it picks what to attempt."""
    seconds = source.duration_seconds
    if not source.measurable or seconds is None:
        return None
    pixels = output_width(rung, source) * output_height(rung, source)
    video_bits = rung.bits_per_pixel * pixels * output_fps(rung, source)
    audio_bits = tuning.ASSUMED_AUDIO_BITS_PER_SECOND if source.has_audio else 0
    total_bits = (video_bits + audio_bits) * seconds * (1 + tuning.CONTAINER_OVERHEAD)
    return int(total_bits / 8)


def smallest_reachable_bytes(source: SourceFacts) -> int | None:
    """The smallest file this source can honestly be turned into. None when unpredictable."""
    rungs = allowed_rungs(source)
    if not rungs:
        return None
    return predicted_bytes(rungs[-1], source)


def best_rung_under(target_bytes: int, source: SourceFacts) -> int | None:
    """The best-quality rung expected to fit, or None. Measurements then correct the estimate."""
    rungs = allowed_rungs(source)
    for index, rung in enumerate(rungs):
        predicted = predicted_bytes(rung, source)
        if predicted is not None and predicted <= target_bytes:
            return index
    return None


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether one file can meet one target, and what about THIS file makes it impossible."""

    reachable: bool
    predicted_bytes: int | None = None
    smallest_reachable_bytes: int | None = None
    reason: str | None = None
    #: Already meets the target and, if asked, already plays anywhere.
    copy_only: bool = False


def _describe(source: SourceFacts) -> str:
    """The file as how long and how big a picture: whole minutes above a minute, seconds below."""
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
    """Everything the panel needs to say about one file, before anything is encoded."""
    needs_encode = compatibility and not _already_compatible(source)
    if target_bytes is None:
        if not needs_encode:
            return Verdict(reachable=True, copy_only=True, predicted_bytes=source.size_bytes)
        return Verdict(reachable=True, predicted_bytes=source.size_bytes)

    if source.size_bytes is not None and source.size_bytes <= target_bytes and not needs_encode:
        return Verdict(reachable=True, copy_only=True, predicted_bytes=source.size_bytes)

    if not source.measurable:
        # Nothing known to predict from; not a refusal, the encode finds out.
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
    """Why this file cannot meet this target, in terms of the file."""
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
    """Whether compatibility has to touch the sound, which it otherwise never does."""
    return source.has_audio and source.acodec not in tuning.COMPATIBLE_ACODECS


def rewrap_is_enough(source: SourceFacts, *, target_bytes: int | None) -> bool:
    """Whether a compatibility conversion is a stream copy rather than an encode."""
    if source.vcodec != tuning.COMPATIBLE_VCODEC or needs_audio_conversion(source):
        return False
    if target_bytes is None:
        return True
    return source.size_bytes is not None and source.size_bytes <= target_bytes


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
    """Which rung to encode next, or None when done: best fit by halving, within `MAX_ATTEMPTS`."""
    rungs = allowed_rungs(source)
    if not rungs:
        return None
    if len(attempts) >= tuning.MAX_ATTEMPTS:
        return None

    if not attempts:
        # After an override of an unreachable target the last rung is the closest it can come.
        picked = best_rung_under(target_bytes, source)
        return len(rungs) - 1 if picked is None else picked

    fitting = [each.index for each in attempts if each.size_bytes <= target_bytes]
    over = [each.index for each in attempts if each.size_bytes > target_bytes]
    tried = {each.index for each in attempts}

    upper = min(fitting) if fitting else len(rungs)
    lower = max(over) + 1 if over else 0

    candidate = (lower + upper - 1) // 2 if lower < upper else None
    if candidate is None or candidate in tried or not 0 <= candidate < len(rungs):
        return None
    return candidate


def best_result(attempts: tuple[Attempt, ...], *, target_bytes: int) -> Attempt | None:
    """The highest-quality attempt under the target, or None so the caller keeps its smallest."""
    fitting = [each for each in attempts if each.size_bytes <= target_bytes]
    if not fitting:
        return None
    return min(fitting, key=lambda each: each.index)
