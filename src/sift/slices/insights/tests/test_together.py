# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rank correlation, on pairs whose coefficient is known by hand."""

from __future__ import annotations

from fractions import Fraction

import pytest

from sift.slices.insights.together import doubled_ranks, spearman

pytestmark = pytest.mark.unit

#: (x, y, rho): a textbook table with no ties, both directions, and a tie worked by hand.
KNOWN = [
    ([1, 2, 3, 4, 5], [2, 4, 6, 8, 10], 1.0),
    ([1, 2, 3, 4, 5], [10, 8, 6, 4, 2], -1.0),
    (
        [106, 100, 86, 101, 99, 103, 97, 113, 112, 110],
        [7, 27, 2, 50, 28, 29, 20, 12, 6, 17],
        -29 / 165,
    ),
    ([1, 2, 2, 3], [1, 2, 3, 4], 4.5 / (22.5**0.5)),
]


@pytest.mark.parametrize(("xs", "ys", "rho"), KNOWN)
def test_the_coefficient_is_the_known_one(xs: list[int], ys: list[int], rho: float) -> None:
    found = spearman(xs, ys)
    assert found is not None
    assert found.n == len(xs)
    assert found.rho == pytest.approx(rho)


def test_a_tie_takes_the_average_of_its_ranks_doubled() -> None:
    assert doubled_ranks([30, 10, 20, 20]) == [8, 2, 5, 5]
    assert doubled_ranks([]) == []


def test_the_bar_is_decided_in_integers_at_its_edge() -> None:
    """Over five, a squared rank difference of 8 is exactly 0.6 and of 10 is 0.5."""
    exactly = spearman([1, 2, 3, 4, 5], [3, 2, 1, 4, 5])
    under = spearman([1, 2, 3, 4, 5], [3, 2, 1, 5, 4])
    assert exactly is not None and under is not None
    assert exactly.at_least(Fraction(3, 5))
    assert not under.at_least(Fraction(3, 5))
    falling = spearman([1, 2, 3], [3, 2, 1])
    assert falling is not None and not falling.at_least(Fraction(3, 5))


def test_a_run_that_never_moves_has_no_coefficient() -> None:
    assert spearman([4, 4, 4], [1, 2, 3]) is None
    assert spearman([1], [1]) is None
    with pytest.raises(ValueError, match="different lengths"):
        spearman([1, 2], [1])
