# SPDX-License-Identifier: AGPL-3.0-or-later
"""Time left priced as the import's work over the pool, the range, its words and its record."""

from __future__ import annotations

import pytest

from sift.kernel.jobs import time_left
from sift.kernel.jobs.time_left import ANY_KIND, Import, Said, Steady

PRICES = {
    ("probe", "image"): 2.0,
    ("probe", ANY_KIND): 2.0,
    ("thumbnail", "image"): 1.0,
    ("thumbnail", "video"): 4.0,
    ("thumbnail", ANY_KIND): 2.0,
}


def test_work_is_items_times_their_price_and_a_kind_nobody_counted_takes_its_types() -> None:
    assert time_left.work({("thumbnail", "image"): 10, ("thumbnail", "gif"): 5}, PRICES) == 20.0
    assert time_left.work({("preview", "video"): 1}, PRICES) is None
    assert time_left.work({}, PRICES) == 0.0


def _job(**changed: object) -> Import:
    asked: dict[str, object] = {
        "read": {("probe", "image"): 100},
        "passes": {("thumbnail", "image"): 100, ("thumbnail", "video"): 50},
        "prices": PRICES,
        "workers": 10,
        "read_pace": 2.0,
        **changed,
    }
    return Import(**asked)  # type: ignore[arg-type]


def test_after_the_read_the_passes_take_their_work_over_every_worker() -> None:
    assert time_left.seconds_left(_job(read={})) == (None, 30.0)
    assert time_left.seconds_left(_job(read={}, passes={("preview", "video"): 1})) == (None, None)
    assert time_left.seconds_left(_job(read={}, workers=0)) == (None, 300.0)


def test_a_read_with_no_price_or_no_pace_says_nothing_of_either() -> None:
    assert time_left.seconds_left(_job(read={("walk", "image"): 1})) == (None, None)
    assert time_left.seconds_left(_job(read_pace=None)) == (None, None)


def test_a_read_bound_by_its_share_is_its_own_pace_and_the_passes_follow_it() -> None:
    # 200 worker seconds at 2 a second: 100 s; all the work over 10 workers: 50 s.
    assert time_left.seconds_left(_job(pool_bound=False)) == (100.0, 100.0)
    queued = _job(pool_bound=False, queued={"thumbnail": 30})
    assert time_left.seconds_left(queued) == (100.0, 106.0)


def test_a_read_bound_by_the_pool_is_no_sooner_than_all_the_work_less_what_is_queued() -> None:
    slow = _job(read_pace=20.0, queued={"thumbnail": 30})
    assert time_left.seconds_left(slow) == (44.0, 50.0)
    assert time_left.seconds_left(_job(read_pace=20.0)) == (50.0, 50.0)


def test_a_stalled_read_leaves_the_passes_their_own_work_over_the_pool() -> None:
    assert time_left.seconds_left(_job(pool_bound=False, read_stalled=True)) == (100.0, 50.0)


def test_passes_held_for_the_walk_start_when_the_read_is_done() -> None:
    assert time_left.seconds_left(_job(held=True)) == (100.0, 130.0)


def test_passes_with_a_kind_unpriced_are_no_sooner_than_the_read_and_the_queue() -> None:
    unpriced = {("preview", "video"): 3}
    assert time_left.seconds_left(_job(passes=unpriced, queued={"thumbnail": 5})) == (100.0, 101.0)
    assert time_left.seconds_left(_job(passes=unpriced, held=True)) == (100.0, None)


def test_the_range_is_the_ratio_either_side() -> None:
    assert time_left.band(160.0) == pytest.approx((100.0, 256.0))


@pytest.mark.parametrize(
    ("quick", "slow", "window"),
    [
        (10, 50, (0, 60)),
        (100, 300, (0, 300)),
        (400, 1000, (300, 1200)),
        (1000, 2000, (900, 2700)),
        (8000, 20000, (7200, 21600)),
        (90000, 160000, (86400, 162000)),
        (200000, 300000, (172800, 345600)),
    ],
)
def test_the_window_is_rounded_out_as_the_words_are(
    quick: int, slow: int, window: tuple[int, int]
) -> None:
    assert time_left.window_of(quick, slow) == window


def test_a_shown_range_counts_down_and_moves_only_when_the_figure_stayed_off() -> None:
    steady = Steady()
    assert steady.show("scan", 0.0, 100.0, 200.0) == (100.0, 200.0)
    # Within the margin of what is shown by now: the shown range, counted down.
    assert steady.show("scan", 10.0, 95.0, 185.0) == (90.0, 190.0)
    # Off, but not for a minute yet.
    assert steady.show("scan", 20.0, 200.0, 400.0) == (80.0, 180.0)
    assert steady.show("scan", 30.0, 72.0, 170.0) == (70.0, 170.0)
    assert steady.show("scan", 40.0, 200.0, 400.0) == (60.0, 160.0)
    assert steady.show("scan", 100.0, 200.0, 400.0) == (200.0, 400.0)


def test_a_shown_range_that_ran_out_moves_at_once_and_a_forgotten_one_starts_again() -> None:
    steady = Steady()
    steady.show("scan", 0.0, 10.0, 20.0)
    assert steady.show("scan", 30.0, 50.0, 90.0) == (50.0, 90.0)
    steady.forget("scan")
    assert steady.show("scan", 31.0, 500.0, 900.0) == (500.0, 900.0)


def test_a_run_is_scored_in_its_words_with_a_minute_either_side_and_by_third() -> None:
    said = [
        Said(at=0, low=300, high=600, left=9),  # ends at 900: 300 s late, wrong
        Said(at=300, low=300, high=600, left=6),  # 600 left: right
        Said(at=600, low=0, high=240, left=3),  # 300 left: right within the minute
        Said(at=700, low=None, high=None, left=2, stalled=True),
        Said(at=800, low=None, high=None, left=1),
    ]
    assert time_left.score(said, 900) == {
        "minutes": 3,
        "right": 67,
        "thirds": [0, 100, 100],
        "stalled": 1,
    }
    assert time_left.score([], 900) is None
    stalled = [Said(at=0, low=None, high=None, left=1, stalled=True)]
    assert time_left.score(stalled, 60) == {
        "minutes": 0,
        "right": None,
        "thirds": [None, None, None],
        "stalled": 1,
    }
