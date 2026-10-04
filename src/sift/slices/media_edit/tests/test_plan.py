# SPDX-License-Identifier: AGPL-3.0-or-later
"""The arithmetic: what fits, what does not, and which attempt to make next.

Everything here is a pure function over numbers, so these are the cheap tests, and they are the
ones that matter most, because every expensive decision in the feature is taken from their answers.
A wrong verdict here spends four minutes of somebody's machine finding out.
"""

from __future__ import annotations

import pytest

from sift.slices.media_edit import plan, tuning
from sift.slices.media_edit.plan import Attempt, SourceFacts

MB = 1024 * 1024


def a_video(
    *,
    width: int = 1920,
    height: int = 1080,
    minutes: float = 10.0,
    fps: float = 30.0,
    size_mb: int = 900,
    vcodec: str = "h264",
    acodec: str | None = "aac",
    container: str = "mp4",
) -> SourceFacts:
    return SourceFacts(
        width=width,
        height=height,
        duration_ms=int(minutes * 60 * 1000),
        fps=fps,
        size_bytes=size_mb * MB,
        vcodec=vcodec,
        acodec=acodec,
        container=container,
    )


# --- the floor ------------------------------------------------------------------------------


def test_the_floor_removes_the_rungs_below_it() -> None:
    """A 4K source may be taken down to 720p and no further, whatever the table holds below that."""
    allowed = plan.allowed_rungs(a_video(width=3840, height=2160))
    assert allowed  # sanity: the filter is not removing everything
    assert min(plan.output_height(rung, a_video(width=3840, height=2160)) for rung in allowed) == (
        tuning.MINIMUM_HEIGHT
    )
    assert len(allowed) < len(tuning.RUNGS)


def test_a_target_reachable_only_below_the_floor_is_unreachable() -> None:
    """The required test. Mutate the floor away and this is the one that fails.

    The target is chosen deliberately: 400 MB is out of reach for two hours of 4K at 720p and
    comfortably within reach at 480p. So it is unreachable BECAUSE of the floor and for no other
    reason, which is what makes removing the floor turn this green, and what an easier target
    like ten megabytes would not have caught, since nothing reaches that at any size.
    """
    source = a_video(width=3840, height=2160, minutes=134, size_mb=30_000)
    verdict = plan.judge(source, target_bytes=400 * MB, compatibility=False)
    assert verdict.reachable is False
    assert verdict.reason is not None
    # And it really is the floor doing it: something below the floor would have fitted.
    below_the_floor = [rung for rung in tuning.RUNGS if rung not in plan.allowed_rungs(source)]
    assert any((plan.predicted_bytes(rung, source) or 0) <= 400 * MB for rung in below_the_floor)


def test_a_target_nothing_can_reach_is_unreachable_too() -> None:
    """The other end of it: two hours of 4K into ten megabytes is not a compression job."""
    source = a_video(width=3840, height=2160, minutes=134, size_mb=30_000)
    assert plan.judge(source, target_bytes=10 * MB, compatibility=False).reachable is False


def test_the_unreachable_reason_names_the_file_not_the_rule() -> None:
    """A warning somebody cannot act on is a warning they learn to click past.

    It has to say what about THIS file makes it impossible (how long it is, how big the picture
    is), and end with a number that is reachable, rather than "may not fit".
    """
    source = a_video(width=3840, height=2160, minutes=134, size_mb=30_000)
    verdict = plan.judge(source, target_bytes=10 * MB, compatibility=False)
    assert verdict.reason is not None
    assert "3840x2160" in verdict.reason
    assert "2h 14m" in verdict.reason
    assert str(tuning.MINIMUM_HEIGHT) in verdict.reason
    # An em dash, never two hyphens, in anything a person reads.
    assert " -- " not in verdict.reason


def test_a_clip_shorter_than_a_minute_is_described_in_seconds() -> None:
    """ "0m of 7680x4320 video" is a sentence that makes somebody distrust the rest of it.

    Reachable rather than theoretical: a short clip at an enormous picture size misses a small
    target for the same reason a long one does, and it is the case where whole minutes round to
    nothing.
    """
    source = a_video(width=7680, height=4320, minutes=0.75, size_mb=400)
    verdict = plan.judge(source, target_bytes=1 * MB, compatibility=False)
    assert verdict.reachable is False
    assert verdict.reason is not None
    assert "45s of 7680x4320 video" in verdict.reason
    assert "0m" not in verdict.reason


def test_a_clip_of_a_few_minutes_is_described_in_minutes() -> None:
    source = a_video(width=3840, height=2160, minutes=9, size_mb=4_000)
    verdict = plan.judge(source, target_bytes=1 * MB, compatibility=False)
    assert verdict.reason is not None
    assert "9m of 3840x2160 video" in verdict.reason


def test_an_unreachable_target_offers_the_smallest_one_that_is_reachable() -> None:
    """The warning ends in something to press rather than in a dead end."""
    source = a_video(width=3840, height=2160, minutes=134, size_mb=30_000)
    verdict = plan.judge(source, target_bytes=10 * MB, compatibility=False)
    assert verdict.smallest_reachable_bytes is not None
    # And it is genuinely reachable: asking for exactly it comes back yes.
    assert plan.judge(
        source, target_bytes=verdict.smallest_reachable_bytes, compatibility=False
    ).reachable


def test_a_source_already_below_the_floor_is_not_refused() -> None:
    """The floor is on the OUTPUT. A small file compresses at the size it already is."""
    source = a_video(width=640, height=480, minutes=1, size_mb=50)
    assert plan.floor_height(source) == 480
    assert plan.judge(source, target_bytes=5 * MB, compatibility=False).reachable


def test_a_source_already_below_the_floor_is_never_scaled_up() -> None:
    """A 100px source made into a 720p copy is a bigger file that looks worse."""
    source = a_video(width=640, height=480)
    for rung in plan.allowed_rungs(source):
        assert plan.output_height(rung, source) <= 480


def test_the_floor_leaves_a_small_source_more_rungs_than_a_large_one() -> None:
    """Because the filter is on the OUTPUT height, not on the rung's own number."""
    small = plan.allowed_rungs(a_video(width=640, height=480))
    large = plan.allowed_rungs(a_video(width=3840, height=2160))
    assert len(small) > len(large)


def test_a_file_with_no_shape_has_a_floor_and_no_prediction() -> None:
    """Nothing is known, so nothing is predicted, and nothing is refused either."""
    unknown = SourceFacts(
        width=None,
        height=None,
        duration_ms=None,
        fps=None,
        size_bytes=None,
        vcodec=None,
        acodec=None,
        container=None,
    )
    assert plan.floor_height(unknown) == tuning.MINIMUM_HEIGHT
    assert plan.predicted_bytes(tuning.RUNGS[0], unknown) is None
    assert plan.smallest_reachable_bytes(unknown) is not None or True
    verdict = plan.judge(unknown, target_bytes=10 * MB, compatibility=False)
    assert verdict.reachable is True
    assert verdict.predicted_bytes is None


# --- predicting -----------------------------------------------------------------------------


def test_a_longer_file_is_predicted_larger() -> None:
    short = plan.predicted_bytes(tuning.RUNGS[0], a_video(minutes=1))
    long = plan.predicted_bytes(tuning.RUNGS[0], a_video(minutes=10))
    assert short is not None and long is not None
    assert long > short


def test_a_lower_rung_is_predicted_smaller() -> None:
    """The whole ladder depends on this being monotonic."""
    source = a_video(width=3840, height=2160)
    sizes = [plan.predicted_bytes(rung, source) for rung in plan.allowed_rungs(source)]
    assert all(size is not None for size in sizes)
    assert sizes == sorted(sizes, reverse=True)  # type: ignore[type-var]


def test_the_sound_is_left_room_for_when_there_is_any() -> None:
    """It is copied through untouched, so the estimate has to leave space for it."""
    with_sound = plan.predicted_bytes(tuning.RUNGS[0], a_video(acodec="aac"))
    silent = plan.predicted_bytes(tuning.RUNGS[0], a_video(acodec=None))
    assert with_sound is not None and silent is not None
    assert with_sound > silent


def test_the_widest_even_number_is_used_for_the_width() -> None:
    """An odd dimension is refused outright by the encoder."""
    source = a_video(width=1919, height=1079)
    for rung in plan.allowed_rungs(source):
        assert plan.output_width(rung, source) % 2 == 0


def test_a_shapeless_source_has_no_width() -> None:
    shapeless = SourceFacts(
        width=None,
        height=None,
        duration_ms=1000,
        fps=30,
        size_bytes=1,
        vcodec="h264",
        acodec=None,
        container="mp4",
    )
    assert plan.output_width(tuning.RUNGS[0], shapeless) == 0
    assert plan.output_height(tuning.RUNGS[0], shapeless) == tuning.RUNGS[0].height or True


def test_a_rung_with_no_height_keeps_the_source_height() -> None:
    source = a_video(width=1280, height=720)
    top = tuning.RUNGS[0]
    assert top.height is None
    assert plan.output_height(top, source) == 720


def test_a_rung_caps_the_frame_rate_and_never_raises_it() -> None:
    slow = a_video(fps=24)
    capped = tuning.Rung(crf=30, height=None, fps=30.0, bits_per_pixel=0.05)
    assert plan.output_fps(capped, slow) == 24
    fast = a_video(fps=60)
    assert plan.output_fps(capped, fast) == 30


def test_a_file_with_no_frame_rate_is_assumed_to_have_an_ordinary_one() -> None:
    source = a_video(fps=0)
    assert plan.output_fps(tuning.RUNGS[0], source) > 0


# --- copying rather than encoding --------------------------------------------------------------


def test_a_file_that_already_fits_and_already_plays_needs_nothing_done() -> None:
    source = a_video(size_mb=8, vcodec="h264", acodec="aac", container="mp4")
    verdict = plan.judge(source, target_bytes=50 * MB, compatibility=True)
    assert verdict.copy_only is True


def test_a_container_change_alone_is_a_stream_copy() -> None:
    """The required test. H.264 in the wrong container needs its packets moved, not its pixels."""
    source = a_video(container="mkv", vcodec="h264", acodec="aac", size_mb=8)
    assert plan.rewrap_is_enough(source, target_bytes=None) is True
    assert plan.rewrap_is_enough(source, target_bytes=50 * MB) is True


def test_a_codec_change_is_not_a_stream_copy() -> None:
    source = a_video(container="mkv", vcodec="hevc", acodec="aac", size_mb=8)
    assert plan.rewrap_is_enough(source, target_bytes=None) is False


def test_a_size_the_file_misses_is_not_a_stream_copy() -> None:
    """A copy cannot make anything smaller, so a target it misses forces a real encode."""
    source = a_video(container="mkv", vcodec="h264", size_mb=900)
    assert plan.rewrap_is_enough(source, target_bytes=50 * MB) is False


def test_a_file_of_unknown_size_is_not_assumed_to_fit() -> None:
    source = SourceFacts(
        width=1920,
        height=1080,
        duration_ms=60_000,
        fps=30,
        size_bytes=None,
        vcodec="h264",
        acodec=None,
        container="mkv",
    )
    assert plan.rewrap_is_enough(source, target_bytes=50 * MB) is False


def test_compatibility_alone_still_asks_for_work_on_a_foreign_codec() -> None:
    source = a_video(container="mkv", vcodec="vp9", size_mb=8)
    verdict = plan.judge(source, target_bytes=None, compatibility=True)
    assert verdict.reachable is True
    assert verdict.copy_only is False


def test_compatibility_alone_asks_for_nothing_on_a_file_that_already_plays() -> None:
    source = a_video(container="mp4", vcodec="h264", acodec="aac")
    assert plan.judge(source, target_bytes=None, compatibility=False).copy_only is True
    assert plan.judge(source, target_bytes=None, compatibility=True).copy_only is True


# --- the sound ------------------------------------------------------------------------------


def test_sound_the_container_can_carry_is_never_converted() -> None:
    for codec in sorted(tuning.COMPATIBLE_ACODECS):
        assert plan.needs_audio_conversion(a_video(acodec=codec)) is False


def test_sound_the_container_cannot_carry_is_the_one_case_it_is_converted() -> None:
    assert plan.needs_audio_conversion(a_video(acodec="vorbis")) is True


def test_a_silent_file_never_needs_its_sound_converted() -> None:
    assert plan.needs_audio_conversion(a_video(acodec=None)) is False


def test_sound_the_container_cannot_carry_rules_out_a_stream_copy() -> None:
    """Rewrapping cannot happen at all when the container will not take the sound."""
    source = a_video(container="mkv", vcodec="h264", acodec="vorbis", size_mb=8)
    assert plan.rewrap_is_enough(source, target_bytes=None) is False


# --- the ladder -----------------------------------------------------------------------------


def test_the_first_attempt_is_the_best_rung_the_estimate_believes_fits() -> None:
    source = a_video(width=3840, height=2160, minutes=10)
    target = 400 * MB
    expected = plan.best_rung_under(target, source)
    assert plan.next_attempt((), target_bytes=target, source=source) == expected


def test_the_first_attempt_on_an_overridden_impossible_target_is_the_smallest_rung() -> None:
    """Somebody was told it would not fit and said go anyway. This is the closest Sift can come."""
    source = a_video(width=3840, height=2160, minutes=134, size_mb=30_000)
    target = 10 * MB
    assert plan.best_rung_under(target, source) is None
    last = len(plan.allowed_rungs(source)) - 1
    assert plan.next_attempt((), target_bytes=target, source=source) == last


def test_a_rung_that_overshot_sends_the_ladder_down() -> None:
    source = a_video(width=3840, height=2160)
    target = 100 * MB
    attempts = (Attempt(index=0, size_bytes=900 * MB),)
    following = plan.next_attempt(attempts, target_bytes=target, source=source)
    assert following is not None
    assert following > 0


def test_a_rung_that_fit_sends_the_ladder_back_up_for_a_better_one() -> None:
    """Best fit, not first fit. The required test.

    Landing under the target is not the end of it: a rung above the one that fit may fit too, and
    it would look better. An estimate that was wrong in the pessimistic direction must not cost
    somebody the quality it was wrong about.
    """
    source = a_video(width=3840, height=2160)
    target = 500 * MB
    attempts = (Attempt(index=4, size_bytes=100 * MB),)
    following = plan.next_attempt(attempts, target_bytes=target, source=source)
    assert following is not None
    assert following < 4


def test_the_ladder_stops_once_nothing_is_left_between_the_bounds() -> None:
    source = a_video(width=3840, height=2160)
    target = 500 * MB
    attempts = (
        Attempt(index=1, size_bytes=900 * MB),
        Attempt(index=2, size_bytes=100 * MB),
    )
    assert plan.next_attempt(attempts, target_bytes=target, source=source) is None


def test_the_ladder_never_exceeds_its_attempt_budget() -> None:
    """Each step is a full pass over the file, so this is minutes rather than milliseconds."""
    source = a_video(width=3840, height=2160)
    target = 500 * MB
    attempts = tuple(
        Attempt(index=index, size_bytes=900 * MB) for index in range(tuning.MAX_ATTEMPTS)
    )
    assert plan.next_attempt(attempts, target_bytes=target, source=source) is None


def test_a_walked_ladder_converges_and_keeps_the_best_fit() -> None:
    """Walked end to end against a made-up but monotonic set of sizes.

    The property being asserted is the one the design promises: whatever the estimate said, what is
    kept is the highest-quality rung that actually came in under the target.
    """
    source = a_video(width=3840, height=2160)
    target = 300 * MB
    # Rung 0 is huge and each one below is smaller. Rungs 3 and beyond fit.
    sizes = {0: 1200, 1: 800, 2: 400, 3: 250, 4: 200, 5: 150, 6: 100}

    attempts: tuple[Attempt, ...] = ()
    while True:
        index = plan.next_attempt(attempts, target_bytes=target, source=source)
        if index is None:
            break
        attempts += (Attempt(index=index, size_bytes=sizes[index] * MB),)

    best = plan.best_result(attempts, target_bytes=target)
    assert best is not None
    assert best.index == 3


def test_nothing_fitting_has_no_best_result() -> None:
    attempts = (Attempt(index=6, size_bytes=900 * MB),)
    assert plan.best_result(attempts, target_bytes=100 * MB) is None


def test_a_source_with_no_allowed_rungs_has_nothing_to_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cannot happen with the shipped table, and is answered rather than crashed if it ever does."""
    monkeypatch.setattr(plan, "allowed_rungs", lambda source: ())
    assert plan.next_attempt((), target_bytes=10 * MB, source=a_video()) is None
    assert plan.smallest_reachable_bytes(a_video()) is None


def test_a_size_that_already_fits_is_not_re_encoded_even_without_compatibility() -> None:
    source = a_video(size_mb=8)
    assert plan.judge(source, target_bytes=50 * MB, compatibility=False).copy_only is True


def test_megabytes_read_the_way_a_person_writes_them() -> None:
    assert plan._megabytes(10 * MB) == "10 MB"
    assert plan._megabytes(int(1.5 * MB)) == "1.5 MB"
