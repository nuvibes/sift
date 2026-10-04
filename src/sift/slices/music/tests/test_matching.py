# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rule that says two files share a song: the keys, the floor, and the verification.

Synthetic fingerprints for the arithmetic, because a random array with bits flipped at a known
rate has a bit error rate everybody can work out by hand. And the real calibration fingerprints
where a machine holds them, because the floor is a CALIBRATION and a calibration is only held by the
data it was drawn from. The tree ships no fingerprint of anybody's music, so that test is skipped
everywhere else and says so.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from sift.slices.music import matching
from sift.slices.music.matching import (
    CANDIDATE_KEYS,
    MATCH,
    SECONDS_PER_VALUE,
    as_array,
    compare,
    is_candidate,
    keys_of,
    pair,
)
from sift.slices.music.tests import calibration

pytestmark = [pytest.mark.unit]

#: Where the thirteen raw calibration fingerprints are kept (little-endian uint32). See
#: `calibration`.
CALIBRATION = calibration.folder()

#: The four cuts of one song among them, by the tag each file's name starts with: six pairs.
SAME_SONG = frozenset({"P1", "P6", "L3", "L4"})


def _song(values: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 2**32, size=values, dtype=np.uint32)


def _flipped(song: np.ndarray, rate: float, seed: int) -> np.ndarray:
    """The song with each bit flipped at this rate: a bit error rate of about `rate`."""
    bits = np.random.default_rng(seed).random((len(song), 32)) < rate
    mask = (bits * (1 << np.arange(32, dtype=np.uint64))).sum(axis=1).astype(np.uint32)
    return song ^ mask


def test_a_key_is_the_high_twenty_bits_once_each() -> None:
    assert keys_of([0x12345678, 0x12345FFF, 0x00001000, 0x00000FFF]) == {0x12345, 0x1, 0x0}
    assert keys_of([]) == set()


def test_the_floor_is_an_absolute_count_and_a_rate_over_the_shorter_file() -> None:
    """Five keys however short the two are; and 0.2 a second of the SHORTER one, so two full
    tracks need thirty-odd where a twelve-second clip needs five."""
    assert CANDIDATE_KEYS == 5
    # Five seconds is one key by the rate, so the absolute floor is what decides.
    assert is_candidate(5, 5_000, 200_000)
    assert not is_candidate(4, 5_000, 200_000)
    # 150 s is the shorter: ceil(0.2 * 150) = 30.
    assert not is_candidate(29, 150_000, 180_000)
    assert is_candidate(30, 180_000, 150_000)


def test_a_copy_matches_at_nought_and_says_where() -> None:
    """The calibration's own self-test: a track against itself with its first 400 values cut off
    sits 400 values (49.5 s) later in the whole one, at no errors at all."""
    song = _song(1500, 1)
    found = compare(song[400:], song)
    assert found.ber == 0.0
    assert found.offset_s == round(400 * SECONDS_PER_VALUE, 1)
    assert found.matches
    assert found.windows == found.matching > 1
    assert not found.short


def test_a_copy_with_a_tenth_of_its_bits_flipped_still_matches() -> None:
    song = _song(1500, 2)
    found = pair(song, _flipped(song, 0.10, 3))
    assert 0.08 < found.ber < 0.12
    assert found.matches


def test_an_unrelated_fingerprint_does_not_match() -> None:
    """Random against random is about half the bits wrong, and a best offset found by searching
    every position pulls that down, never anywhere near the line."""
    found = pair(_song(1500, 4), _song(1500, 5))
    assert found.ber > 0.4
    assert not found.matches
    assert found.matching == 0


def test_the_line_is_below_a_quarter_not_at_it() -> None:
    assert matching.Match(ber=MATCH, offset_s=0.0, windows=1, matching=0, short=False).matches is (
        False
    )
    assert matching.Match(
        ber=MATCH - 0.0001, offset_s=0.0, windows=1, matching=1, short=False
    ).matches


def test_a_query_shorter_than_one_window_is_used_whole_and_flagged() -> None:
    song = _song(1500, 6)
    clip = song[700:790]
    found = compare(clip, song)
    assert found.short
    assert found.windows == 1
    assert found.ber == 0.0
    assert found.offset_s == round(700 * SECONDS_PER_VALUE, 1)
    # And the other way round: the long one's windows are wider than the clip, so the clip is slid
    # inside each window instead, and the offset still reads from the query to the reference.
    backwards = compare(song, clip)
    assert backwards.ber == 0.0
    assert backwards.offset_s == -round(700 * SECONDS_PER_VALUE, 1)


def test_a_pair_reads_from_first_to_second_whichever_direction_won() -> None:
    song = _song(1500, 7)
    clip = song[300:390]
    assert pair(clip, song).offset_s == round(300 * SECONDS_PER_VALUE, 1)
    assert pair(song, clip).offset_s == -round(300 * SECONDS_PER_VALUE, 1)


def test_an_empty_fingerprint_matches_nothing() -> None:
    """Nought errors over nought bits is not a song."""
    empty = as_array([])
    assert not compare(empty, _song(100, 8)).matches
    assert not compare(_song(100, 8), empty).matches


@pytest.mark.skipif(CALIBRATION is None, reason=calibration.WHY_SKIPPED)
def test_the_floor_and_the_line_on_the_thirteen_calibration_files() -> None:
    """Every pair of the four cuts of one song passes the floor AND the verification, and no other
    pair of the 78 does, and the key counts are the ones `matching`'s docstring states."""
    assert CALIBRATION is not None
    # Named by the tag at the front of each file's name, and nothing after it.
    fingerprints = {
        path.stem.split("-")[0]: np.fromfile(str(path), dtype="<u4")
        for path in CALIBRATION.glob("*.raw")
    }
    assert len(fingerprints) == 13
    keys = {name: keys_of(values.tolist()) for name, values in fingerprints.items()}

    def length_ms(name: str) -> int:
        return round(len(fingerprints[name]) * SECONDS_PER_VALUE * 1000)

    true_shared: list[int] = []
    other_shared: list[int] = []
    passed: set[frozenset[str]] = set()
    for first, second in itertools.combinations(sorted(fingerprints), 2):
        shared = len(keys[first] & keys[second])
        together = frozenset({first, second})
        (true_shared if together <= SAME_SONG else other_shared).append(shared)
        if not is_candidate(shared, length_ms(first), length_ms(second)):
            continue
        # Only a candidate is verified, as in the product: six comparisons, not 78.
        if pair(fingerprints[first], fingerprints[second]).matches:
            passed.add(together)
    assert passed == {frozenset(two) for two in itertools.combinations(SAME_SONG, 2)}
    assert sorted(true_shared) == [108, 115, 127, 165, 187, 273]
    assert max(other_shared) == 8


def test_a_pair_takes_the_better_direction_when_only_one_of_them_lines_up() -> None:
    """The one window of a short first file straddles noise and the song, so reading it into the
    second is only close; the second's own window lies wholly on the song, so reading the other way
    is exact. The pair keeps the exact reading, still said from the first file to the second."""
    song = _song(1500, 9)
    first = np.concatenate([_song(58, 10), song[121:363]])

    assert compare(first, song).ber > 0.0
    found = pair(first, song)
    assert found.ber == 0.0
    assert found.offset_s == round(63 * SECONDS_PER_VALUE, 1)
