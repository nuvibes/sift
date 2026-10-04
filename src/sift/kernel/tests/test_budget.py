# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dividing the machine between the long passes, as pure arithmetic: a switched-on feature with
nothing to do must not hold a share of the machine idle overnight."""

from __future__ import annotations

import pytest

from sift.kernel.budget import (
    STEP_BACK_SHARE,
    WHOLE_DEVICE,
    divide,
    processor_rate,
    stepped_workers,
    tool_threads,
    unfinished_by_type,
)

FACES = "face_scan"
DESCRIBE = "semantic_describe"
SCAN = "scan"

#: Eight workers: recognition and describing entitled to half each, scanning to a quarter.
EIGHT = {FACES: 4, DESCRIBE: 4, SCAN: 2}


def test_one_pass_with_work_gets_the_whole_machine() -> None:
    """The one pass with work gets the whole machine, not its quarter."""
    allowed = divide(workers=8, entitlements=EIGHT, busy={FACES: 12})

    assert allowed[FACES] == 8


def test_two_passes_with_work_get_their_shares() -> None:
    allowed = divide(workers=8, entitlements=EIGHT, busy={FACES: 12, DESCRIBE: 30})

    assert allowed[FACES] == 4
    assert allowed[DESCRIBE] == 4


def test_three_passes_with_work_never_ask_for_more_than_there_is() -> None:
    allowed = divide(workers=8, entitlements=EIGHT, busy={FACES: 5, DESCRIBE: 5, SCAN: 5})

    assert allowed[FACES] + allowed[DESCRIBE] + allowed[SCAN] <= 8


def test_a_share_comes_back_when_its_owner_has_work_again() -> None:
    """A share comes back when its owner has work again."""
    had_it_all = divide(workers=8, entitlements=EIGHT, busy={DESCRIBE: 30})
    faces_arrive = divide(workers=8, entitlements=EIGHT, busy={DESCRIBE: 30, FACES: 1})

    assert had_it_all[DESCRIBE] == 8
    assert faces_arrive[DESCRIBE] < had_it_all[DESCRIBE]


def test_a_pass_with_nothing_to_do_is_not_left_unable_to_start() -> None:
    """An idle claimant keeps a real cap: a cap is not a reservation, and zero would leave the first
    arriving job waiting for the next reconfigure."""
    allowed = divide(workers=8, entitlements=EIGHT, busy={FACES: 12})

    assert allowed[DESCRIBE] >= 1
    assert allowed[SCAN] >= 1


def test_nothing_anywhere_has_work() -> None:
    """Nothing anywhere has work: the empty case, where a slip divides by zero."""
    allowed = divide(workers=8, entitlements=EIGHT, busy={})

    assert allowed == {FACES: 8, DESCRIBE: 8, SCAN: 8}


def test_an_entitlement_of_zero_stays_zero() -> None:
    """An entitlement of zero (recognition outside its hours) stays zero."""
    allowed = divide(workers=8, entitlements={**EIGHT, FACES: 0}, busy={FACES: 99, DESCRIBE: 4})

    assert allowed[FACES] == 0


def test_a_paused_pass_does_not_take_a_share_from_the_others() -> None:
    """A paused pass does not squeeze the others."""
    paused = divide(workers=8, entitlements={**EIGHT, FACES: 0}, busy={FACES: 99, DESCRIBE: 30})

    assert paused[DESCRIBE] == 8


def test_a_share_that_would_round_to_nothing_is_still_one() -> None:
    """A share never rounds to nothing, or a library sits unfinished."""
    allowed = divide(workers=2, entitlements={FACES: 8, DESCRIBE: 1}, busy={FACES: 1, DESCRIBE: 1})

    assert allowed[DESCRIBE] == 1


def test_no_workers_means_nothing_runs() -> None:
    assert divide(workers=0, entitlements=EIGHT, busy={FACES: 1}) == {
        FACES: 0,
        DESCRIBE: 0,
        SCAN: 0,
    }


@pytest.mark.parametrize("workers", [1, 2, 3, 4, 8, 16, 64])
def test_no_allowance_ever_exceeds_the_workers(workers: int) -> None:
    """No allowance exceeds the workers."""
    entitlements = {FACES: workers, DESCRIBE: workers, SCAN: max(1, workers // 4)}

    allowed = divide(workers=workers, entitlements=entitlements, busy={FACES: 1, DESCRIBE: 1})

    assert all(value <= workers for value in allowed.values())


def test_a_kind_that_has_never_had_a_job_reads_as_idle_rather_than_missing() -> None:
    """A kind never seen reads as idle: absent and zero decide opposite things."""
    assert unfinished_by_type({FACES: 3}, EIGHT) == {FACES: 3, DESCRIBE: 0, SCAN: 0}


def test_work_outside_the_budget_is_not_carried_into_it() -> None:
    """Only the named kinds divide the machine: a queue of downloads shrinks nobody's share."""
    narrowed = unfinished_by_type({"thumbnail": 900, "download": 40, FACES: 2}, EIGHT)

    assert narrowed == {FACES: 2, DESCRIBE: 0, SCAN: 0}


# --- a number an admin typed is a limit, not an entitlement: only a SHARE is redistributed


def test_a_fixed_kind_alone_on_the_machine_gets_its_number_and_no_more() -> None:
    """A fixed kind alone on the machine gets its number and no more."""
    allowed = divide(workers=12, entitlements={**EIGHT, SCAN: 1}, busy={SCAN: 7}, fixed={SCAN})

    assert allowed[SCAN] == 1


def test_the_same_kind_as_a_share_still_takes_the_idle_machine() -> None:
    """The same kind as a share takes the idle machine, which keeps an import alone fast."""
    allowed = divide(workers=12, entitlements={**EIGHT, SCAN: 1}, busy={SCAN: 7})

    assert allowed[SCAN] == 12


def test_a_fixed_kind_is_not_cut_when_everything_competes() -> None:
    """An exact number holds while the machine is busy too."""
    allowed = divide(
        workers=8,
        entitlements={**EIGHT, FACES: 2},
        busy={FACES: 80, DESCRIBE: 30, SCAN: 5},
        fixed={FACES},
    )

    assert allowed[FACES] == 2


def test_the_shares_divide_what_the_fixed_kinds_with_work_leave() -> None:
    """The shares divide what the fixed kinds with work leave, so the screen's numbers add up."""
    allowed = divide(workers=8, entitlements=EIGHT, busy={SCAN: 3, FACES: 9}, fixed={SCAN})

    assert allowed == {FACES: 6, DESCRIBE: 3, SCAN: 2}


def test_an_idle_fixed_kind_reserves_nothing() -> None:
    """An idle fixed kind reserves nothing."""
    allowed = divide(workers=8, entitlements=EIGHT, busy={FACES: 9}, fixed={SCAN})

    assert allowed[FACES] == 8
    assert allowed[SCAN] == 2


def test_a_fixed_number_above_the_pool_is_the_pool() -> None:
    allowed = divide(workers=4, entitlements={FACES: 64}, busy={FACES: 1}, fixed={FACES})

    assert allowed[FACES] == 4


def test_a_fixed_zero_still_means_stop() -> None:
    allowed = divide(workers=8, entitlements={**EIGHT, FACES: 0}, busy={FACES: 9}, fixed={FACES})

    assert allowed[FACES] == 0


# --- work a person is waiting on: a first import's thumbnails must not wait behind every probe

PROBE = "probe"
THUMBNAIL = "thumbnail"


def test_work_a_person_waits_on_makes_room_and_is_never_capped() -> None:
    entitled = {PROBE: 4, THUMBNAIL: 4}

    alone = divide(workers=8, entitlements=entitled, busy={PROBE: 200}, waited_on={THUMBNAIL})
    waiting = divide(
        workers=8, entitlements=entitled, busy={PROBE: 200, THUMBNAIL: 3}, waited_on={THUMBNAIL}
    )

    assert alone == {PROBE: 8}
    assert waiting == {PROBE: 4}


# --- the step back: a share of the device


def test_twelve_workers_at_a_quarter_are_three() -> None:
    assert STEP_BACK_SHARE == 25
    assert stepped_workers(12, STEP_BACK_SHARE) == 3


@pytest.mark.parametrize(
    ("full", "percent", "stepped"),
    [(1, 25, 1), (2, 25, 1), (4, 25, 1), (5, 25, 2), (8, 25, 2), (12, 50, 6), (12, 100, 12)],
)
def test_the_share_of_the_workers_is_rounded_up_and_never_below_one(
    full: int, percent: int, stepped: int
) -> None:
    assert stepped_workers(full, percent) == stepped


def test_a_pool_of_one_never_steps_back() -> None:
    assert stepped_workers(1, STEP_BACK_SHARE) == 1
    assert stepped_workers(1, 10) == 1


def test_a_share_out_of_range_is_held_to_the_device() -> None:
    assert stepped_workers(12, 0) == 1
    assert stepped_workers(12, 400) == 12


def test_each_tool_gets_its_part_of_the_share_of_the_cores() -> None:
    # A quarter of twenty-four is six, shared by three.
    assert tool_threads(24, running=3, percent=STEP_BACK_SHARE) == 2
    # The whole device is the cores over the workers.
    assert tool_threads(24, running=12, percent=WHOLE_DEVICE) == 2
    assert tool_threads(24, running=4, percent=WHOLE_DEVICE) == 6
    # Never below one, however small the share or many the workers.
    assert tool_threads(4, running=8, percent=10) == 1


@pytest.mark.parametrize("cores", [1, 2, 4, 8, 12, 16, 24, 32, 64])
@pytest.mark.parametrize("full", [1, 2, 3, 6, 8, 12, 23, 48])
def test_the_workers_threads_together_stay_inside_the_share(cores: int, full: int) -> None:
    workers = stepped_workers(full, STEP_BACK_SHARE)
    usable = max(1, cores * STEP_BACK_SHARE // WHOLE_DEVICE)
    threads = tool_threads(cores, running=workers, percent=STEP_BACK_SHARE)
    # One thread each is the floor; the processor rate holds each tool to its part.
    assert workers * threads <= max(usable, workers)


def test_the_processor_rate_is_the_threads_part_of_the_machine() -> None:
    assert processor_rate(2, 24) == 833
    assert processor_rate(24, 24) == 10_000
    assert processor_rate(48, 24) == 10_000
    assert processor_rate(1, 100_000) == 1
