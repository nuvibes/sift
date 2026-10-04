# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether two files share a song, from their fingerprints alone. No file is opened here.

Two steps, and each exists because the other cannot do the whole job.

## The keys: which files are worth comparing at all

Comparing every file with every other is arithmetic over the whole library for each new file, and
almost every comparison answers no. So each fingerprint is also written down as the set of its
KEYS: the high 20 bits of every value, once each (`keys_of`). The low twelve bits are where a
re-encode, a change of loudness or talking over the music flips bits; the high twenty survive
often enough that two files carrying the same song share a hundred keys or more, while
two unrelated files share only what chance puts in common in a space of about a million.

A file becomes a CANDIDATE for another when they share enough distinct keys, and "enough" has two
parts (`is_candidate`): at least `CANDIDATE_KEYS`, and at least `KEYS_PER_SECOND` for every second
of the SHORTER of the two. The second part is what lets a clip in. Chance grows with how much of
two fingerprints there is to collide, and so does what a real shared song leaves behind, so one
number for every length either admits two unrelated full tracks or turns away every short clip of
a song, and a clip from anywhere in a song is the case this feature exists for.

Calibrated on thirteen real fingerprints (ten full-length music videos and three short clips). Of
the 78 pairs among them:

* the six that share a song (four editors' cuts of one track) share **108 to 273 keys**
  (273, 187, 165, 127, 115, 108): at least 0.59 keys per second of the shorter file;
* the 72 that do not share **at most 8** (median 0; the three clips of 7 to 12 seconds at most 4).

And cut from those same files, 20,496 excerpts of 8 to 30 seconds were each set against every
whole fingerprint: with the floor at 5 and 0.2 a second, **no excerpt of an unrelated file was a
candidate**, and of the excerpts that really did hold the other file's song (verified under the
line below) the floor kept 87% at 8 s, 93% at 10 s, 97% at 12 s, 99% at 15 s and all of them at
20 and 30 s. A floor of 4 kept a few more short excerpts and let an unrelated clip in; a floor of
8 turned away 37% of the true 8-second excerpts.

The evidence is small (thirteen files, one shared song) and says so. A wrong candidate costs one
verification of a few milliseconds and changes no answer, because the verification decides; a
missed candidate is a pair nobody sees. So the floor errs low. `tests/test_matching.py` reads the
calibration fingerprints where a machine holds a copy and holds all of it; the tree ships no
fingerprint of anybody's music, so anywhere else that test is skipped and says so.

## The verification: the calibrated rule, ported rather than redesigned

A candidate is only a file worth looking at. Whether it really shares the song is decided by the
calibrated procedure (`compare`, a port of its `ber.py`): windows of thirty seconds of one
fingerprint, stepped fifteen seconds, each slid over every offset of the other where the whole
window overlaps it; a window's score is the share of its bits that differ at the best offset (the
bit error rate); a pair MATCHES when its best window scores **under 0.25**.

0.25 rather than the literature's 0.35, because a search over every offset of a long fingerprint
lets two unrelated files reach 0.35 by chance: on the calibration set the best unrelated pair of
full tracks scored 0.413 and the six true pairs 0.064 to 0.180. A fingerprint shorter than the
window is used whole and flagged (`Match.short`): its chance floor is higher than a thirty-second
window's, so 0.25 is not calibrated for it, and the row says so (`windows = 1`) rather than claiming
more than was measured.

Both directions are tried and the better kept (`pair`), which is how the calibration figures were
taken: one direction's windows can all straddle an edit where the other direction's cannot.

Time: **a few milliseconds for one three-minute pair**, both directions, and well under half a
second for all 78 pairs of the thirteen, each scoring exactly what the
reference `ber.py` scores. So the verification is never the cost; a file with a handful of
candidates settles in tens of milliseconds, on a thread.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

#: Which way a fingerprint is cut into keys. Stored on every fingerprint row that has been indexed
#: (`audio_fingerprints.indexed_scheme`), so a later change to `keys_of` is a different number, and
#: every row indexed under the old one is simply not indexed any more: the next run reindexes it
#: instead of comparing keys cut two different ways.
KEY_SCHEME = 1

#: How many low bits of a value are dropped to make its key. See the module docstring.
KEY_SHIFT = 12

#: The fewest distinct keys two files must share to be compared at all, however short they are.
#: Calibrated. See the module docstring for the measurement and `tests/test_matching.py` for the
#: test that holds it.
CANDIDATE_KEYS = 5

#: And at least this many for every second of the shorter of the two. See the module docstring.
KEYS_PER_SECOND = 0.2

#: The most candidates one file is verified against, the ones sharing the most keys first.
#:
#: A bound on the work, not part of the rule. Keys are not spread evenly (speech and near-silence
#: land on the same few), so two LONG files with no song in common share more than chance in a
#: million would say: a 15-minute and a 29-minute scene can share over two hundred keys, clear the
#: floor, and still score around 0.41, not a pair. Each such verification is a fraction of a
#: second, so a library of long scenes could hand one file thousands of them. Past this many, the
#: file with the fewest shared keys is the least likely partner; and a song set to more clips than
#: this is still found through the group's one hop, from the clips that do pair.
CANDIDATE_LIMIT = 200

#: Seconds per value: Chromaprint gives one every 1,365 samples at 11,025 Hz
#: (`kernel/chromaprint.py`, `_SAMPLES_PER_VALUE` and `_SAMPLE_RATE`).
SECONDS_PER_VALUE = 1365 / 11025

#: The window, and how far it steps. The calibration's figures, and the line was drawn for these.
WINDOW_SECONDS = 30.0
STEP_SECONDS = 15.0

#: A pair matches when its best window's bit error rate is below this. See the module docstring.
MATCH = 0.25

_BITS = 32


@dataclass(frozen=True, slots=True)
class Match:
    """What comparing two fingerprints found.

    `offset_s` is where the song sits in the SECOND fingerprint minus where it sits in the first,
    in seconds, at the best window. `windows` is how many windows were tried and `matching` how
    many of them scored under the line; a query shorter than one window is used whole, so it has
    one window and `short` is set.
    """

    ber: float
    offset_s: float
    windows: int
    matching: int
    short: bool

    @property
    def matches(self) -> bool:
        """Whether this is the same song, by the calibrated line."""
        return self.ber < MATCH


def keys_of(values: Sequence[int]) -> set[int]:
    """The distinct keys of a fingerprint: the high 20 bits of every value, once each."""
    return {int(value) >> KEY_SHIFT for value in values}


def is_candidate(shared: int, first_ms: int, second_ms: int) -> bool:
    """Whether two files sharing this many distinct keys are worth verifying. The one statement of
    the floor: the database's search asks only for files over `CANDIDATE_KEYS`, and this decides
    the rest, so the rule is written once."""
    shorter_seconds = min(first_ms, second_ms) / 1000
    return shared >= max(CANDIDATE_KEYS, math.ceil(KEYS_PER_SECOND * shorter_seconds))


def as_array(values: Sequence[int]) -> np.ndarray:
    """A fingerprint's values as the unsigned 32-bit array the comparison works on."""
    # Imported here rather than at the top: see `kernel/content/perceptual.py` for the two
    # reasons: a module most of Sift loads on every boot, and a coverage run that loads the
    # test plugin first, where numpy refuses to be loaded twice.
    import numpy as np

    return np.asarray(values, dtype=np.uint32)


def _popcount_rows(x: np.ndarray) -> np.ndarray:
    """The set bits in each row of a 2-D uint32 array. `bitwise_count` is numpy 2's, which the
    project pins."""
    import numpy as np

    return np.bitwise_count(x).sum(axis=1, dtype=np.int64)


def _best_offset(window: np.ndarray, reference: np.ndarray) -> tuple[float, int]:
    """The best bit error rate of `window` over every full-overlap position in `reference`, and
    that position in values. A reference shorter than the window is slid inside it instead, and
    the position comes back negated so it still reads as reference minus window."""
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
    """The calibrated rule, one direction: `query` in windows against the whole of `reference`.

    A port of `ber.py`'s `compare`, and deliberately not a redesign of it: the line at 0.25 was
    measured for exactly this procedure. An empty fingerprint on either side is no match at all:
    there is nothing to compare, and a score of zero errors over zero bits is not a song.
    """
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
    """Both directions, and the better one, which is how the calibration took its figures.

    The answer reads from `first` to `second` whichever direction won: `offset_s` is where the
    song sits in `second` minus where it sits in `first`.
    """
    forward = compare(first, second)
    backward = compare(second, first)
    if backward.ber < forward.ber:
        return Match(
            ber=backward.ber,
            # `or 0.0` so a song at the same place in both reads 0.0 rather than -0.0.
            offset_s=-backward.offset_s or 0.0,
            windows=backward.windows,
            matching=backward.matching,
            short=backward.short,
        )
    return forward
