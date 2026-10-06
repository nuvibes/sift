# SPDX-License-Identifier: AGPL-3.0-or-later
"""The performance settings: 0 means automatic, and any value that should never arrive becomes a
safe number rather than a crash in the worker pool's reconfigure."""

from __future__ import annotations

import pytest

from sift.kernel.hardware import HardwareReport
from sift.kernel.settings_registry import Scope, get_registered
from sift.slices import performance


def _hardware(worker_concurrency: int) -> HardwareReport:
    return HardwareReport(
        cpu_count=worker_concurrency + 1,
        total_ram_bytes=None,
        worker_concurrency=worker_concurrency,
        cuda=False,
        rocm=False,
        transcode_encoders=(),
        warnings=(),
    )


# --- worker count -----------------------------------------------------------------------------


def test_a_set_worker_count_is_used_as_given() -> None:
    assert performance.resolve_worker_count(3, _hardware(8)) == 3


def test_zero_workers_means_the_automatic_answer_not_none() -> None:
    """The whole sentinel. Zero is "let Sift decide", which is the hardware figure, never no
    workers, which would stop the library dead."""
    assert performance.resolve_worker_count(0, _hardware(6)) == 6


@pytest.mark.parametrize("bad", [-1, True, False, "4", 2.0, None])
def test_a_nonsense_worker_count_falls_back_to_automatic(bad: object) -> None:
    """A nonsense worker count falls back to automatic."""
    assert performance.resolve_worker_count(bad, _hardware(5)) == 5


# --- generation limit -------------------------------------------------------------------------


def test_a_set_generation_limit_is_used_over_the_automatic_half() -> None:
    # Explicit wins even when it differs from half the workers.
    assert performance.resolve_generation_limit(2, 16) == 2


@pytest.mark.parametrize("automatic", [0, -3, True, False, "2", 1.5, None])
def test_an_automatic_generation_limit_is_half_the_effective_workers(automatic: object) -> None:
    """0 (and any non-value) means "let Sift decide", which is half the workers ACTUALLY running,
    so raising the worker count raises this with it, matching the screen's promise."""
    assert performance.resolve_generation_limit(automatic, 8) == 4
    assert performance.resolve_generation_limit(automatic, 3) == 1  # floor
    assert performance.resolve_generation_limit(automatic, 1) == 1  # never below one


# --- scan limit -------------------------------------------------------------------------------


def test_a_set_scan_limit_is_used() -> None:
    assert performance.resolve_scan_limit(4) == 4


@pytest.mark.parametrize("automatic", [0, -1, True, False, "4", 4.0, None])
def test_an_unset_scan_limit_is_none_meaning_no_separate_cap(automatic: object) -> None:
    """None, not zero: no entry in the limits dictionary, so scans are bounded only by the worker
    count. Zero would mean never scan, which is nobody's intent."""
    assert performance.resolve_scan_limit(automatic) is None


# --- the declarations themselves --------------------------------------------------------------


def test_every_performance_setting_is_registered_app_scoped_in_its_section() -> None:
    """Registration is a side effect of importing the slice, and it is what puts these on the
    screen. A guest must not be able to change how hard the whole instance works, so every one is
    app-scoped, checked here rather than trusted."""
    # Each key is asserted on the screen that draws it.
    on_performance = (
        performance.WORKER_COUNT_KEY,
        performance.GENERATION_LIMIT_KEY,
        performance.SCAN_LIMIT_KEY,
        performance.SHARE_READS_KEY,
    )
    on_importing = (
        performance.GENERATE_PREVIEWS_KEY,
        performance.GENERATE_SPRITES_KEY,
        performance.GENERATE_FINGERPRINTS_KEY,
        performance.PREVIEW_SHAPE_KEY,
    )
    for section, keys in (("Performance", on_performance), ("Importing", on_importing)):
        for key in keys:
            declared = get_registered(key)
            assert declared is not None, f"{key} is not registered"
            assert declared.scope is Scope.APP
            assert declared.section == section, f"{key} is drawn on {declared.section}"


def test_the_numeric_settings_default_to_automatic_and_are_bounded() -> None:
    """0 is the stored default for the three counts, so a fresh install behaves exactly as it did
    before these existed. The bounds are what the screen constrains its input to."""
    for key in (
        performance.WORKER_COUNT_KEY,
        performance.GENERATION_LIMIT_KEY,
        performance.SCAN_LIMIT_KEY,
    ):
        declared = get_registered(key)
        assert declared is not None
        assert declared.default == performance.AUTOMATIC
        assert declared.minimum == performance.AUTOMATIC
        assert declared.maximum == performance.MAX_MANUAL_WORKERS


def test_the_generate_switches_default_on() -> None:
    """Every derivative is built by default: turning the feature off must be a choice, not the
    state a fresh install boots into. Booleans, so the screen draws them as switches."""
    for key in (
        performance.GENERATE_PREVIEWS_KEY,
        performance.GENERATE_SPRITES_KEY,
    ):
        declared = get_registered(key)
        assert declared is not None
        assert declared.default is True


def test_the_step_back_setting_defaults_on_and_lives_on_performance() -> None:
    """Stepping back while the computer is in use is what somebody wants without asking, so it is
    on for a fresh install, drawn on Settings > Performance, and shared by the whole install."""
    declared = get_registered(performance.STEP_BACK_KEY)
    assert declared is not None
    assert declared.default is True
    assert declared.section == "Performance"
    assert declared.scope is Scope.APP
    assert declared.label == "Use less system resources while you're working"


def test_the_step_back_share_is_a_quarter_on_every_install_and_lives_on_performance() -> None:
    """The share the step back keeps to: a quarter unless somebody chose otherwise, a percent the
    screen draws as a slider beside the switch it belongs to."""
    declared = get_registered(performance.STEP_BACK_SHARE_KEY)
    assert declared is not None
    assert declared.default == 25
    assert declared.section == "Performance"
    assert declared.scope is Scope.APP
    assert declared.unit == "%"
    assert (declared.minimum, declared.maximum) == (10, 100)
    assert declared.label == "System resource usage in eco mode"
    assert (
        "eco mode while you're working, a video is playing or other programs are busy"
        in declared.help
    )


@pytest.mark.parametrize(("raw", "share"), [(25, 25), (60, 60), (100, 100), (250, 100)])
def test_a_stored_step_back_share_is_used(raw: int, share: int) -> None:
    assert performance.resolve_step_back_share(raw) == share


@pytest.mark.parametrize("bad", [None, True, "25", 0, 5])
def test_an_unreadable_step_back_share_is_a_quarter(bad: object) -> None:
    assert performance.resolve_step_back_share(bad) == 25


# --- the shares the long passes divide the machine by --------------------------------------------
#
# Each long pass's share of the machine, from the worker count.


def test_a_scan_gets_a_quarter_of_the_workers_by_default() -> None:
    assert performance.resolve_scan_share(0, 8) == 2


def test_a_scan_can_be_given_an_explicit_share() -> None:
    assert performance.resolve_scan_share(3, 8) == 3


def test_a_scan_share_never_exceeds_the_workers_there_are() -> None:
    """A cap above the pool is not a cap, and it would read as one on the screen that shows it."""
    assert performance.resolve_scan_share(99, 8) == 8


def test_describing_gets_half_the_workers() -> None:
    assert performance.resolve_describe_share(8) == 4


def test_compressing_gets_half_the_workers_like_describing() -> None:
    """A peer of the other long passes, not a privileged one and not the smallest."""
    assert performance.resolve_compress_share(8) == 4
    assert performance.resolve_compress_share(8) == performance.resolve_describe_share(8)


def test_every_share_survives_a_one_worker_machine() -> None:
    """A share that rounded to nothing would stop a pass rather than slow it, and a library
    part-way through would sit unfinished with nothing able to explain why."""
    assert performance.resolve_scan_share(0, 1) == 1
    assert performance.resolve_describe_share(1) == 1
    assert performance.resolve_compress_share(1) == 1


def test_the_share_reads_setting_defaults_to_automatic_and_is_bounded_by_the_lanes() -> None:
    """Its ceiling is the lanes' own, not the worker ceiling: the two are different resources."""
    from sift.kernel.lanes import MAX_READS_AT_ONCE

    declared = get_registered(performance.SHARE_READS_KEY)
    assert declared is not None
    assert declared.default == performance.AUTOMATIC
    assert declared.minimum == performance.AUTOMATIC
    assert declared.maximum == MAX_READS_AT_ONCE


def test_automatic_share_reads_leaves_each_share_at_its_measured_number() -> None:
    assert performance.resolve_share_reads(0) == 0
    assert performance.resolve_share_reads(None) == 0
    assert performance.resolve_share_reads(True) == 0


def test_a_set_share_reads_is_used_and_capped() -> None:
    from sift.kernel.lanes import MAX_READS_AT_ONCE

    assert performance.resolve_share_reads(6) == 6
    assert performance.resolve_share_reads(10_000) == MAX_READS_AT_ONCE


def test_a_typed_scan_limit_is_fixed_and_automatic_is_a_share() -> None:
    """A typed scan limit is a fixed limit; automatic stays a share."""
    assert performance.scan_limit_is_fixed(1) is True
    assert performance.scan_limit_is_fixed(performance.AUTOMATIC) is False
    assert performance.scan_limit_is_fixed(True) is False
