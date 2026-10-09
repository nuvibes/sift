# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether two files share a song, from their fingerprints alone: shared keys pick candidates,
the bit error rate decides."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

#: Stored per row (`audio_fingerprints.indexed_scheme`); a change here reindexes every row.
KEY_SCHEME = 1

#: The low bits are what a re-encode or talk-over flips.
KEY_SHIFT = 12

#: Calibrated: true pairs shared 108 to 273 keys, unrelated ones at most 8.
CANDIDATE_KEYS = 5

#: Per second of the shorter file, so a short clip can still be a candidate.
KEYS_PER_SECOND = 0.2

#: A bound on the work: long unrelated scenes can clear the floor; fewest shared keys drop first.
CANDIDATE_LIMIT = 200

#: Chromaprint gives one value every 1,365 samples at 11,025 Hz.
SECONDS_PER_VALUE = 1365 / 11025

#: The calibration's figures; `MATCH` was drawn for these.
WINDOW_SECONDS = 30.0
STEP_SECONDS = 15.0

#: Not the usual 0.35: searching every offset lets unrelated tracks reach about 0.41.
MATCH = 0.25

_BITS = 32


@dataclass(frozen=True, slots=True)
class Match:
    """What comparing two fingerprints found; `offset_s` is second minus first, in seconds."""

    ber: float
    offset_s: float
    windows: int
    matching: int
    short: bool

    @property
    def matches(self) -> bool:
        return self.ber < MATCH


def keys_of(values: Sequence[int]) -> set[int]:
    return {int(value) >> KEY_SHIFT for value in values}


def is_candidate(shared: int, first_ms: int, second_ms: int) -> bool:
    """Whether two files sharing this many distinct keys are worth verifying."""
    shorter_seconds = min(first_ms, second_ms) / 1000
    return shared >= max(CANDIDATE_KEYS, math.ceil(KEYS_PER_SECOND * shorter_seconds))


def as_array(values: Sequence[int]) -> np.ndarray:
    # Imported late, as in `kernel/content/perceptual.py`.
    import numpy as np

    return np.asarray(values, dtype=np.uint32)


def _popcount_rows(x: np.ndarray) -> np.ndarray:
    """The set bits in each row of a 2-D uint32 array."""
    import numpy as np

    return np.bitwise_count(x).sum(axis=1, dtype=np.int64)


def _best_offset(window: np.ndarray, reference: np.ndarray) -> tuple[float, int]:
    """The best bit error rate of `window` anywhere in `reference`, and that position in values."""
    from numpy.lib.stride_tricks import sliding_window_view

    size = len(window)
    if len(reference) < size:
        ber, position = _best_offset(reference, window)
        return ber, -position
    views = sliding_window_view(reference, size)
    errors = _popcount_rows(views ^ window[None, :])
    best = int(errors.argmin())
    return float(errors[best]) / (_BITS * size), best


def compare(query: np.ndarray, reference: np.ndarray) -> Match:
    """The calibrated rule, one direction: `query` in windows against the whole of `reference`."""
    if not len(query) or not len(reference):
        return Match(ber=1.0, offset_s=0.0, windows=0, matching=0, short=False)
    width = round(WINDOW_SECONDS / SECONDS_PER_VALUE)
    step = round(STEP_SECONDS / SECONDS_PER_VALUE)
    short = len(query) < width
    starts = [0] if short else range(0, len(query) - width + 1, step)
    scored: list[tuple[float, float]] = []
    for start in starts:
        window = query if short else query[start : start + width]
        ber, position = _best_offset(window, reference)
        scored.append((ber, (position - start) * SECONDS_PER_VALUE))
    best_ber, best_offset = min(scored)
    return Match(
        ber=round(best_ber, 4),
        offset_s=round(best_offset, 1),
        windows=len(scored),
        matching=sum(1 for ber, _ in scored if ber < MATCH),
        short=short,
    )


def pair(first: np.ndarray, second: np.ndarray) -> Match:
    """Both directions, and the better one; the answer reads from `first` to `second`."""
    forward = compare(first, second)
    backward = compare(second, first)
    if backward.ber < forward.ber:
        return Match(
            ber=backward.ber,
            offset_s=-backward.offset_s or 0.0,
            windows=backward.windows,
            matching=backward.matching,
            short=backward.short,
        )
    return forward
