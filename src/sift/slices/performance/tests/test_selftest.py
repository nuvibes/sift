# SPDX-License-Identifier: AGPL-3.0-or-later
"""Working out what the machine can do, and what that means for the settings.

The unit of work is injected, so these measure something instant and deterministic rather than
really running a video encoder. What is being checked is the shape of the walk between levels and
the reading of the curve, which is where every decision is: whether ffmpeg works is not this
module's question and there is a conformance step in the image for it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.kernel.media import FFmpegError
from sift.slices.performance import selftest
from sift.slices.performance.selftest import Level, Measurement, recommend


def a_level(at_once: int, per_second: float, *, responsive: bool = True) -> Level:
    """A level with a chosen throughput. `finished` is the count and `seconds` makes the rate."""
    return Level(
        at_once=at_once,
        seconds=at_once / per_second,
        finished=at_once,
        worst_lag_seconds=0.0 if responsive else 1.0,
        worst_wait_seconds=0.0,
    )


# --- reading the curve ---------------------------------------------------------------------------


def test_a_machine_that_keeps_scaling_is_recommended_the_widest_level() -> None:
    measurement = Measurement(
        cores=16,
        levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 4.0)),
    )

    assert measurement.best is not None
    assert measurement.best.at_once == 4


def test_scaling_stops_where_more_at_once_stops_finishing_more() -> None:
    """The whole measurement. Four at once did no more work than two, so two is the answer, and
    the machine keeps what four would have taken."""
    measurement = Measurement(
        cores=16,
        levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 2.05), a_level(8, 2.1)),
    )

    assert measurement.best is not None
    assert measurement.best.at_once == 2


def test_the_curve_is_walked_rather_than_maximised() -> None:
    """A later level that scores highest does not win if the curve already flattened before it.

    Throughput wobbles. Taking the maximum of a wobbly curve reliably takes the peak of the noise,
    which on a saturated machine is the level that made everything else unusable.
    """
    measurement = Measurement(
        cores=16,
        levels=(a_level(1, 1.0), a_level(2, 1.02), a_level(4, 4.0)),
    )

    assert measurement.best is not None
    assert measurement.best.at_once == 1


def test_a_level_that_made_the_app_unresponsive_is_never_the_answer() -> None:
    """It finished the most work and it is still wrong: what was measured is a machine that stopped
    answering, and nobody wants a library indexed at the cost of not being able to use it."""
    measurement = Measurement(
        cores=16,
        levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 9.0, responsive=False)),
    )

    assert measurement.best is not None
    assert measurement.best.at_once == 2


def test_a_measurement_that_failed_has_no_best_level() -> None:
    assert Measurement(cores=8, failed="the video encoder could not be run").best is None


# --- what it recommends ---------------------------------------------------------------------------


def test_a_failed_measurement_recommends_nothing() -> None:
    """Half an answer from a test that did not run is worse than no answer, because it looks like
    one, and the two call for opposite things from whoever reads it."""
    failed = Measurement(cores=8, failed="the video encoder could not be run")

    assert recommend(failed, current={}) == []


def test_the_preview_cap_is_the_measured_level_itself() -> None:
    """Encoding is what was measured, and building previews is the encoding. No derivation between
    the number that was watched and the number that is suggested."""
    measurement = Measurement(cores=16, levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 4.0)))

    suggested = {one.key: one.suggested for one in recommend(measurement, current={})}

    assert suggested[selftest.GENERATION_LIMIT_KEY] == 4


def test_the_job_count_is_above_the_preview_cap_and_never_above_the_cores() -> None:
    """A job is not always encoding, so more jobs than encodes is right. More jobs than cores is
    not: past that they only take turns, and the disk is the thing that suffers."""
    measurement = Measurement(cores=6, levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 4.0)))

    suggested = {one.key: one.suggested for one in recommend(measurement, current={})}

    assert suggested[selftest.GENERATION_LIMIT_KEY] < suggested[selftest.WORKER_COUNT_KEY]
    assert suggested[selftest.WORKER_COUNT_KEY] <= 6


def test_the_preview_cap_is_never_above_the_job_count_it_is_recommended_beside() -> None:
    """The preview cap is never recommended above the job count.

    Building a preview is a job and the cap is a per-type limit inside the worker pool, so a cap
    above the worker count can never bind. A machine that keeps improving to the top of the ladder
    measures a level equal to its thread count while the job count keeps one thread back.
    """
    # Sixteen threads, still improving at sixteen at once: the shape of a run on an eight-core
    # machine with two threads a core.
    measurement = Measurement(
        cores=16,
        levels=(
            a_level(1, 1.0),
            a_level(2, 2.0),
            a_level(4, 4.0),
            a_level(8, 8.0),
            a_level(16, 16.0),
        ),
    )

    suggested = {one.key: one.suggested for one in recommend(measurement, current={})}

    assert suggested[selftest.WORKER_COUNT_KEY] == 15
    assert suggested[selftest.GENERATION_LIMIT_KEY] <= suggested[selftest.WORKER_COUNT_KEY]


def test_a_run_that_reached_the_top_of_the_ladder_says_so_rather_than_claiming_a_peak() -> None:
    """The words have to follow the difference between a peak and a floor.

    The ladder stops at one encode per thread, so a machine that keeps improving all the way up
    finishes with the widest rung it was ever offered and nothing above it was ever run. Saying
    "more than that finished no more work" there would be a claim about levels never measured.
    """
    ceiling = Measurement(
        cores=8,
        levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 4.0), a_level(8, 8.0)),
    )
    # Four at once did no more work than two, so the curve turned BEFORE the last rung run, which
    # is a peak that was measured rather than a ladder that ran out.
    turned = Measurement(
        cores=16,
        levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 2.1)),
    )

    assert ceiling.at_ceiling is True
    assert turned.at_ceiling is False

    words = " ".join(one.reason for one in recommend(ceiling, current={}))
    assert "as wide as the test goes" in words
    assert "finished no more work" not in words

    words = " ".join(one.reason for one in recommend(turned, current={}))
    assert "finished no more work" in words


def test_the_reasons_on_screen_use_a_real_dash_never_two_hyphens() -> None:
    """The reasons are read on the Performance pane, so they use a real dash, never two hyphens.
    Both shapes of run are asked, because the sentences are split between them: a ladder that ran
    out (the ceiling words and the preview cap held to the job count) and a peak under the job
    count (the margin above it)."""
    ceiling = Measurement(
        cores=8,
        levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 4.0), a_level(8, 8.0)),
    )
    turned = Measurement(cores=16, levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 2.1)))

    for measurement in (ceiling, turned):
        words = " ".join(one.reason for one in recommend(measurement, current={}))
        assert " -- " not in words
        assert "\u2014" in words


def test_the_numbers_are_called_threads_because_that_is_what_they_are() -> None:
    """`cpu_count` is LOGICAL processors, which the Performance screen labels "Threads" ten lines
    above these words, so these words say threads too: an eight-core machine with two threads a
    core is not to be told about "your 16 cores" beside a readout saying 16 threads."""
    measurement = Measurement(cores=16, levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 4.0)))

    words = " ".join(one.reason for one in recommend(measurement, current={}))

    assert "cores" not in words
    assert "threads" in words


def test_the_advice_says_device_and_task_as_the_screen_around_it_does() -> None:
    """The Performance screen says "this device" and "Tasks at once", and these sentences are drawn
    under it, so they use the same words. Both shapes of the curve, and both the margin and the
    preview cap."""
    turned = Measurement(cores=16, levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 2.1)))
    ceiling = Measurement(
        cores=8, levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 4.0), a_level(8, 8.0))
    )
    assert ceiling.at_ceiling is True

    for measurement in (turned, ceiling):
        for one in recommend(measurement, current={}):
            said = f"{one.label} {one.reason}"
            assert "machine" not in said, said
            assert " job" not in said and "Job" not in said, said
            assert " -- " not in said, said


def test_the_planned_rounds_are_the_rungs_this_machine_can_reach() -> None:
    """So a run in flight can say how far through it is. An upper bound rather than a total: a run
    still stops early when a level stops helping, which is why the screen says "up to"."""
    # A small machine is offered fewer rungs, which is the whole point: the run is shorter on the
    # machine least able to spare the time.
    assert selftest.planned_levels(24) == (1, 2, 4, 8, 16)
    assert selftest.planned_levels(4) == (1, 2, 4)
    # And never nothing: `measure` only breaks out once something has been done, so the first rung
    # is always run however small the machine.
    assert selftest.planned_levels(1) == (1,)


def test_every_recommendation_names_a_setting_that_exists() -> None:
    """The failure this is for is a recommendation nobody can apply: a key that looks right, has a
    label and a reason beside it, and matches nothing on any screen."""
    import sift.slices.faces
    import sift.slices.performance  # noqa: F401
    from sift.kernel.settings_registry import get_registered

    measurement = Measurement(cores=8, levels=(a_level(1, 1.0), a_level(2, 2.0)))

    for one in recommend(measurement, current={}):
        assert get_registered(one.key) is not None, one.key


def test_a_recommendation_says_whether_it_changes_anything() -> None:
    """So a screen can be quiet when the settings are already right, rather than presenting three
    rows that all say to leave things exactly as they are."""
    measurement = Measurement(cores=16, levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 4.0)))
    already = {one.key: one.suggested for one in recommend(measurement, current={})}

    again = recommend(measurement, current=already)

    assert not any(one.changes_anything for one in again)


# --- taking the measurement ------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


async def skip_decode(*_args: object, **_kwargs: object) -> selftest.Decode | None:
    """A decoder measurement that measures nothing, for the tests about the encode ladder."""
    return None


async def test_a_machine_with_no_working_encoder_says_so_rather_than_looking_slow(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two are opposite conclusions ("leave the settings alone" and "turn everything down")
    and a measurement of zero would be read as the second."""

    async def no_encoder(*args: object, **kwargs: object) -> Path:
        raise FFmpegError("ffmpeg: not found")

    monkeypatch.setattr(selftest, "build_clip", no_encoder)

    measured = await selftest.measure(
        workspace=tmp_path,
        settings=settings,
        cores=8,
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
    )

    assert measured.failed is not None
    assert measured.levels == ()


async def test_the_levels_are_walked_and_recorded(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    async def build(into: Path, _settings: Settings) -> Path:
        return into / "source.mp4"

    async def encode(
        _source: Path, _into: Path, index: int, _settings: Settings, _threads: int
    ) -> bool:
        seen.append(index)
        return True

    monkeypatch.setattr(selftest, "build_clip", build)

    measured = await selftest.measure(
        workspace=tmp_path,
        settings=settings,
        cores=64,
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
        levels=(1, 2, 4),
        encode=encode,
        repeats=1,
        measure_decoder=skip_decode,
    )

    assert [level.at_once for level in measured.levels] == [1, 2, 4]
    assert len(seen) == 1 + 2 + 4


async def test_each_rung_is_published_as_it_finishes(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ladder takes minutes and the screen has to be able to say where it has got to.

    Published one rung at a time, and each time as a whole `Measurement` rather than as a rung on
    its own, so the screen reads the same shape whether the run is half done or finished, and
    there is no second progress type for it to get wrong.
    """
    reported: list[list[int]] = []

    async def build(into: Path, _settings: Settings) -> Path:
        return into / "source.mp4"

    async def encode(*_args: object) -> bool:
        return True

    monkeypatch.setattr(selftest, "build_clip", build)

    measured = await selftest.measure(
        workspace=tmp_path,
        settings=settings,
        cores=64,
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
        levels=(1, 2, 4),
        encode=encode,
        repeats=1,
        measure_decoder=skip_decode,
        report=lambda so_far: reported.append([level.at_once for level in so_far.levels]),
    )

    assert reported == [[1], [1, 2], [1, 2, 4], [1, 2, 4]], (
        "one report per rung, each carrying all of them, then one more when the decoder is done"
    )
    assert [level.at_once for level in measured.levels] == [1, 2, 4]


async def test_a_level_wider_than_the_machine_is_not_run(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Encoding more at once than there are cores has never been the right answer, and running the
    wide levels anyway makes the test longest on the machines least able to afford it."""

    async def build(into: Path, _settings: Settings) -> Path:
        return into / "source.mp4"

    async def encode(*_args: object) -> bool:
        return True

    monkeypatch.setattr(selftest, "build_clip", build)

    measured = await selftest.measure(
        workspace=tmp_path,
        settings=settings,
        cores=2,
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
        levels=(1, 2, 4, 8),
        encode=encode,
        repeats=1,
        measure_decoder=skip_decode,
    )

    assert [level.at_once for level in measured.levels] == [1, 2]


async def test_the_walk_stops_once_the_machine_stops_keeping_up(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wider levels would only struggle more, and would keep somebody waiting to be told something
    already known."""
    lag = [0.0]

    async def build(into: Path, _settings: Settings) -> Path:
        return into / "source.mp4"

    async def encode(*_args: object) -> bool:
        lag[0] += 1.0  # the machine falls behind as soon as the first level runs
        return True

    monkeypatch.setattr(selftest, "build_clip", build)

    measured = await selftest.measure(
        workspace=tmp_path,
        settings=settings,
        cores=64,
        worst_lag=lambda: lag[0],
        worst_wait=lambda: 0.0,
        levels=(1, 2, 4, 8),
        encode=encode,
        repeats=1,
        measure_decoder=skip_decode,
    )

    assert [level.at_once for level in measured.levels] == [1]


async def test_each_level_is_judged_on_what_it_did_rather_than_on_the_history(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The readings are worst-since-start, so a machine that was already struggling before the test
    began would otherwise have every level blamed for it and every answer be "turn it all down"."""

    async def build(into: Path, _settings: Settings) -> Path:
        return into / "source.mp4"

    async def encode(*_args: object) -> bool:
        return True

    monkeypatch.setattr(selftest, "build_clip", build)

    measured = await selftest.measure(
        workspace=tmp_path,
        settings=settings,
        cores=64,
        worst_lag=lambda: 9.0,  # already terrible, and never getting worse
        worst_wait=lambda: 0.0,
        levels=(1, 2),
        encode=encode,
        repeats=1,
        measure_decoder=skip_decode,
    )

    assert all(level.responsive for level in measured.levels)


def test_the_job_count_is_a_margin_over_what_was_measured_not_a_multiple() -> None:
    """The job count is a margin over the measured level, never a multiple of it: a doubled level
    would be one the test never ran."""
    measurement = Measurement(
        cores=24,
        levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 4.0), a_level(8, 8.0)),
    )

    suggested = {one.key: one.suggested for one in recommend(measurement, current={})}

    assert suggested[selftest.GENERATION_LIMIT_KEY] == 8
    assert suggested[selftest.WORKER_COUNT_KEY] == 12  # 8 measured, plus half of it as margin


def test_the_job_count_always_leaves_a_core_for_everything_that_is_not_a_job() -> None:
    """The same rule the automatic answer already follows. A machine given every core to jobs is one
    whose interface stops answering while it indexes."""
    measurement = Measurement(cores=4, levels=(a_level(1, 1.0), a_level(2, 2.0), a_level(4, 4.0)))

    suggested = {one.key: one.suggested for one in recommend(measurement, current={})}

    assert suggested[selftest.WORKER_COUNT_KEY] <= 3


def test_the_measurement_is_long_enough_to_be_one() -> None:
    """A clip much shorter than this makes a level mostly process start-up and the curve noise.

    Each encode gets two threads rather than the whole machine, so the clip is real work.
    """
    assert selftest.CLIP_SECONDS >= 5


def test_widening_a_level_uses_more_of_the_machine() -> None:
    """The property the whole measurement rests on.

    Uncapped, one encode already fills a large machine and the curve flattens at once, into a
    recommendation far below what the machine can do. Capped at cores-over-level, every level
    uses the whole machine, so throughput is near flat by construction and it would recommend 1.
    Only a fixed share per encode makes "how many at once" the thing that varies.
    """
    assert selftest.cores_in_use(1) < selftest.cores_in_use(4) < selftest.cores_in_use(16)


def test_the_walk_stops_once_the_machine_is_thoroughly_oversubscribed() -> None:
    """Past roughly twice the machine the curve has already flattened, and every further level is
    slower for the same answer, on the machines least able to spare the time."""
    assert selftest.cores_in_use(16) > 4 * 2  # a 4-core box would stop well before level 16


# --- the two commands, against the ffmpeg that is really there ----------------------------------
#
# Everything above measures the walk between levels with the unit of work injected, which is the
# right shape for the decisions. These two are the opposite half: the exact argument lists, run for
# real, because an argument ffmpeg rejects is a self-test that reports a machine which cannot encode
# rather than a command that was wrong. Shortened to something that finishes instantly: what is
# being checked is that the commands are accepted and produce a file, not how fast the machine is.


@pytest.mark.integration
async def test_the_clip_every_level_encodes_is_really_built(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(selftest, "CLIP_SECONDS", 1)
    monkeypatch.setattr(selftest, "CLIP_WIDTH", 160)
    monkeypatch.setattr(selftest, "CLIP_HEIGHT", 120)

    clip = await selftest.build_clip(tmp_path, settings)

    assert clip.exists() and clip.stat().st_size > 0


@pytest.mark.integration
async def test_one_unit_of_the_work_really_encodes(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both thread caps go in, which is the part that reads as correct and can silently not be.

    ffmpeg has more than one thread pool: the flag before the input caps decoding and the filter
    graph, and the one in the output position caps the encoder, which is the pool doing the work
    being measured. A build that refused either would report an unresponsive machine.
    """
    monkeypatch.setattr(selftest, "CLIP_SECONDS", 1)
    monkeypatch.setattr(selftest, "CLIP_WIDTH", 160)
    monkeypatch.setattr(selftest, "CLIP_HEIGHT", 120)
    clip = await selftest.build_clip(tmp_path, settings)

    assert await selftest._encode_once(clip, tmp_path, 0, settings, threads=2) is True
    assert (tmp_path / "encoded-0.mp4").stat().st_size > 0

    # And with no cap at all, which is the other side of the branch and the state a levels walk
    # uses when nothing has said how many threads one encode may have.
    assert await selftest._encode_once(clip, tmp_path, 1, settings) is True


@pytest.mark.integration
async def test_an_encode_that_fails_is_an_answer_rather_than_a_crash(
    tmp_path: Path, settings: Settings
) -> None:
    """A level is allowed to contain work that did not finish: that is what the count is for. An
    exception here would abandon the whole measurement over one unit of it."""
    missing = tmp_path / "there-is-no-such-clip.mp4"

    assert await selftest._encode_once(missing, tmp_path, 0, settings) is False


def test_a_level_that_took_no_time_reports_no_throughput() -> None:
    """Dividing by it is the obvious hazard; reporting an infinite rate is the interesting one:
    it would win every comparison and be recommended."""
    assert a_level_of(seconds=0.0, finished=4).throughput == 0.0
    assert a_level_of(seconds=-1.0, finished=4).throughput == 0.0


def a_level_of(*, seconds: float, finished: int) -> Level:
    return Level(
        at_once=1,
        seconds=seconds,
        finished=finished,
        worst_lag_seconds=0.0,
        worst_wait_seconds=0.0,
    )


# --- the storage half ------------------------------------------------------------------------------


def a_storage_level(at_once: int, mb_per_second: float) -> selftest.StorageLevel:
    return selftest.StorageLevel(
        at_once=at_once, seconds=1.0, bytes_read=int(mb_per_second * 1_000_000)
    )


def a_share(*levels: selftest.StorageLevel, remote: bool = True) -> selftest.StorageCurve:
    return selftest.StorageCurve(
        storage="\\\\nas\\photos\\", label="Photos", remote=remote, levels=tuple(levels)
    )


def test_a_level_that_took_no_time_has_no_rate() -> None:
    """A clock that did not move is not an infinite share."""
    assert selftest.StorageLevel(at_once=1, seconds=0.0, bytes_read=1).megabytes_per_second == 0


def test_the_share_recommendation_says_when_nothing_wider_was_tried() -> None:
    """A share whose widest level tried was still its fastest has not been seen to collapse, and
    the reason says so rather than claiming a knee that was never found."""
    share = a_share(a_storage_level(1, 45), a_storage_level(2, 80))
    found = selftest.recommend_share_reads([share], current={})
    assert found is not None and found.suggested == 2
    assert "Nothing wider was tried" in found.reason


def test_a_share_that_keeps_delivering_more_is_read_to_its_widest_level() -> None:
    curve = a_share(a_storage_level(1, 40), a_storage_level(2, 75), a_storage_level(4, 120))
    assert curve.best is not None and curve.best.at_once == 4


def test_a_share_that_collapses_is_read_at_the_level_before_the_collapse() -> None:
    """The measurement this exists for: twelve readers delivered half of what two did."""
    curve = a_share(a_storage_level(1, 45), a_storage_level(2, 80), a_storage_level(4, 42))
    assert curve.best is not None and curve.best.at_once == 2


def test_a_share_that_stops_improving_is_not_pushed_wider() -> None:
    curve = a_share(a_storage_level(1, 80), a_storage_level(2, 84), a_storage_level(4, 86))
    assert curve.best is not None and curve.best.at_once == 1


def test_a_share_that_climbs_within_the_noise_is_not_told_it_delivered_less() -> None:
    """A share reading 22, 22, 23, 26 MB/s at one, two, four and eight readers.

    Eight readers really did deliver more than one (it is printed in the reason), so the
    sentence may not say the share delivered less. What it may say is why one reader is still the
    answer: no step of the ladder gained the margin a step has to gain.
    """
    share = a_share(
        a_storage_level(1, 22),
        a_storage_level(2, 22),
        a_storage_level(4, 23),
        a_storage_level(8, 26),
    )
    assert share.best is not None and share.best.at_once == 1
    assert share.collapsed is False
    found = selftest.recommend_share_reads([share], current={})
    assert found is not None and found.suggested == 1
    assert "no more than the noise between runs" in found.reason
    assert "delivered less" not in found.reason
    # Singular at one, and the four readings are on the screen beside the claim.
    assert "more than 1 file at once" in found.reason
    assert (
        "1 at once 22 MB/s, 2 at once 22 MB/s, 4 at once 23 MB/s, 8 at once 26 MB/s" in found.reason
    )


def test_a_share_with_no_readings_did_not_collapse() -> None:
    """Nothing was read, so nothing fell over: "delivered less" needs two readings to compare."""
    share = a_share()
    assert share.best is None
    assert share.collapsed is False


def test_a_share_that_really_fell_over_still_says_it_delivered_less() -> None:
    """The other half of the same branch: four readers delivered half of what two did."""
    share = a_share(a_storage_level(1, 45), a_storage_level(2, 80), a_storage_level(4, 42))
    assert share.collapsed is True
    found = selftest.recommend_share_reads([share], current={})
    assert found is not None and found.suggested == 2
    assert "delivered less, not more" in found.reason
    assert "more than 2 files at once" in found.reason


def test_a_share_that_could_not_be_measured_has_no_best_level() -> None:
    curve = selftest.StorageCurve(storage="x", label="x", remote=True, failed="too few files")
    assert curve.best is None


def test_the_share_recommendation_is_the_knee_of_the_weakest_share() -> None:
    """One setting governs every share, so the number safe for the weakest is the number."""
    strong = a_share(a_storage_level(1, 50), a_storage_level(2, 95), a_storage_level(4, 170))
    weak = selftest.StorageCurve(
        storage="\\\\old\\share\\",
        label="Old NAS",
        remote=True,
        levels=(a_storage_level(1, 30), a_storage_level(2, 50), a_storage_level(4, 20)),
    )
    found = selftest.recommend_share_reads([strong, weak], current={})
    assert found is not None
    assert found.key == selftest.SHARE_READS_KEY
    assert found.suggested == 2
    assert "Old NAS" in found.reason and "2 shares" in found.reason


def test_a_local_disk_recommends_nothing_about_shares() -> None:
    local = a_share(a_storage_level(1, 500), a_storage_level(2, 900), remote=False)
    assert selftest.recommend_share_reads([local], current={}) is None


def test_a_share_that_could_not_be_measured_recommends_nothing_rather_than_a_guess() -> None:
    failed = selftest.StorageCurve(storage="x", label="x", remote=True, failed="too few files")
    assert selftest.recommend_share_reads([failed], current={}) is None


def test_the_share_recommendation_rides_with_the_encoding_ones() -> None:
    measurement = Measurement(
        cores=16,
        levels=(a_level(1, 1.0), a_level(2, 2.0)),
        storages=(a_share(a_storage_level(1, 45), a_storage_level(2, 80), a_storage_level(4, 42)),),
    )
    keys = [one.key for one in recommend(measurement, current={})]
    assert keys == [
        selftest.WORKER_COUNT_KEY,
        selftest.GENERATION_LIMIT_KEY,
        selftest.SHARE_READS_KEY,
    ]


async def test_a_storage_is_read_wider_until_it_delivers_less() -> None:
    """The walk over a share: its own files at each level, and stopped at the first collapse."""
    asked: list[tuple[int, int]] = []

    async def read_level(files: list[Path], *, at_once: int, salt: int) -> selftest.StorageLevel:
        asked.append((at_once, len(files)))
        rate = {1: 40, 2: 75, 4: 30, 8: 10}[at_once]
        return a_storage_level(at_once, rate)

    one = selftest.StorageToMeasure(storage="s", label="Photos", remote=True, roots=(Path("."),))
    curve = await selftest.measure_storage(
        one, read_level=read_level, files=[Path(f"f{i}") for i in range(20)], repeats=1
    )

    assert [level.at_once for level in curve.levels] == [1, 2, 4]
    assert asked == [(1, 2), (2, 4), (4, 8)]
    assert curve.best is not None and curve.best.at_once == 2


async def test_a_storage_with_too_few_large_files_says_so() -> None:
    one = selftest.StorageToMeasure(storage="s", label="Photos", remote=True, roots=(Path("."),))
    curve = await selftest.measure_storage(one, files=[Path("only-one")])
    assert curve.failed is not None and curve.levels == ()


def test_the_sample_takes_the_largest_files_within_a_bounded_walk(tmp_path: Path) -> None:
    small = tmp_path / "small.bin"
    small.write_bytes(b"x" * 1024)
    big = tmp_path / "deep" / "big.bin"
    big.parent.mkdir()
    big.write_bytes(b"x" * (selftest.SAMPLE_FLOOR_BYTES + 1))
    assert selftest.sample_files([tmp_path]) == [big]


def test_the_sample_walk_stops_at_its_limit_and_steps_over_what_it_cannot_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bounded on entries looked at, not files found; a folder that went away between being
    listed and being opened, an entry that cannot be read, and one that is neither a file nor a
    folder are all stepped over rather than ending the walk."""
    import os
    from types import SimpleNamespace

    for name in ("a.bin", "b.bin", "c.bin"):
        (tmp_path / name).write_bytes(b"x" * (selftest.SAMPLE_FLOOR_BYTES + 1))
    monkeypatch.setattr(selftest, "SAMPLE_WALK_LIMIT", 2)
    assert len(selftest.sample_files([tmp_path])) == 1, "the limit counts entries looked at"

    monkeypatch.setattr(selftest, "SAMPLE_WALK_LIMIT", 4000)
    real_scandir = os.scandir

    def unreadable(*, follow_symlinks: bool = True) -> object:
        raise PermissionError("no")

    class Listing:
        def __init__(self, entries: list[object]) -> None:
            self._entries = entries

        def __enter__(self) -> list[object]:
            return self._entries

        def __exit__(self, *_: object) -> None:
            return None

    def listing(path: str | os.PathLike[str]) -> Listing:
        with real_scandir(path) as real:
            entries: list[object] = list(real)
        entries.append(
            SimpleNamespace(
                path=str(tmp_path / "pipe"),
                is_dir=lambda follow_symlinks=True: False,
                is_file=lambda follow_symlinks=True: False,
            )
        )
        entries.append(
            SimpleNamespace(
                path=str(tmp_path / "locked.bin"),
                is_dir=lambda follow_symlinks=True: False,
                is_file=lambda follow_symlinks=True: True,
                stat=unreadable,
            )
        )
        return Listing(entries)

    monkeypatch.setattr(os, "scandir", listing)
    found = selftest.sample_files([tmp_path / "vanished", tmp_path])
    assert sorted(one.name for one in found) == ["a.bin", "b.bin", "c.bin"]


async def test_a_level_reads_every_file_at_spread_places_and_steps_over_one_that_is_gone(
    tmp_path: Path,
) -> None:
    """The real reader: each file is opened once, seeked `SEEKS_PER_FILE` times at places spread
    across it, and the bytes that came back are the level's. A file gone since the sample was
    taken is logged and left out rather than failing the level.

    Two readers at once, deliberately: a total added to across an await would keep one file's
    bytes and lose the other's when two readers finish together, and one reader cannot show that."""
    files = [tmp_path / f"{index}.bin" for index in range(3)]
    for one in files:
        one.write_bytes(b"x" * (2 * selftest.BYTES_PER_SEEK + 4096))

    level = await selftest._read_level([*files, tmp_path / "gone.bin"], at_once=2, salt=1)

    assert level.at_once == 2
    assert level.seeks == 3 * selftest.SEEKS_PER_FILE
    assert level.bytes_read == 3 * selftest._seek_and_read(files[0], salt=1)
    assert 0 < level.bytes_read <= 3 * selftest.SEEKS_PER_FILE * selftest.BYTES_PER_SEEK


async def test_a_level_wider_than_the_sample_is_not_tried() -> None:
    """Eight readers over three files would be readers with nothing to read."""
    asked: list[int] = []

    async def read_level(files: list[Path], *, at_once: int, salt: int) -> selftest.StorageLevel:
        asked.append(at_once)
        return a_storage_level(at_once, 50 * at_once)

    one = selftest.StorageToMeasure(storage="s", label="Photos", remote=True, roots=(Path("."),))
    curve = await selftest.measure_storage(
        one,
        levels=(1, 8),
        read_level=read_level,
        files=[Path(f"f{i}") for i in range(3)],
        repeats=1,
    )

    assert asked == [1]
    assert [level.at_once for level in curve.levels] == [1]


async def test_each_storage_measured_is_reported_as_it_lands(
    tmp_path: Path, settings: Settings
) -> None:
    """The screen draws the storages as each is measured, not after the last."""
    seen: list[Measurement] = []

    async def one_storage(one: selftest.StorageToMeasure, *, repeats: int) -> selftest.StorageCurve:
        return selftest.StorageCurve(storage=one.storage, label=one.label, remote=one.remote)

    async def instant(*_args: object, **_kwargs: object) -> bool:
        return True

    await selftest.measure(
        workspace=tmp_path,
        settings=settings,
        cores=4,
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
        levels=(1,),
        encode=instant,
        repeats=1,
        measure_decoder=skip_decode,
        storages=[
            selftest.StorageToMeasure(storage="\\\\nas\\a\\", label="A", remote=True, roots=()),
            selftest.StorageToMeasure(storage="\\\\nas\\b\\", label="B", remote=True, roots=()),
        ],
        measure_one_storage=one_storage,
        report=seen.append,
    )

    assert [len(one.storages) for one in seen][-2:] == [1, 2]


async def test_only_network_storages_are_measured(tmp_path: Path, settings: Settings) -> None:
    measured: list[str] = []

    async def one_storage(one: selftest.StorageToMeasure, *, repeats: int) -> selftest.StorageCurve:
        measured.append(one.storage)
        return selftest.StorageCurve(storage=one.storage, label=one.label, remote=one.remote)

    async def instant(*_args: object, **_kwargs: object) -> bool:
        return True

    await selftest.measure(
        workspace=tmp_path,
        settings=settings,
        cores=4,
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
        levels=(1,),
        encode=instant,
        repeats=1,
        measure_decoder=skip_decode,
        storages=[
            selftest.StorageToMeasure(storage="C:\\", label="Local", remote=False, roots=()),
            selftest.StorageToMeasure(storage="\\\\nas\\a\\", label="NAS", remote=True, roots=()),
        ],
        measure_one_storage=one_storage,
    )
    assert measured == ["\\\\nas\\a\\"]


# --- three runs, the middle one kept ------------------------------------------------------------


async def test_each_level_is_run_three_times_and_the_middle_run_is_kept(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One noisy minute must not set the machine's numbers. The middle run of three is kept whole
    (its seconds and its readings together), rather than an average one bad run can pull."""
    import asyncio

    pauses = iter((0.0, 0.4, 0.15))

    async def build(into: Path, _settings: Settings) -> Path:
        return into / "source.mp4"

    async def encode(*_args: object) -> bool:
        await asyncio.sleep(next(pauses))
        return True

    monkeypatch.setattr(selftest, "build_clip", build)

    measured = await selftest.measure(
        workspace=tmp_path,
        settings=settings,
        cores=8,
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
        levels=(1,),
        encode=encode,
        measure_decoder=skip_decode,
    )

    assert selftest.REPEATS == 3
    (level,) = measured.levels
    assert 0.08 < level.seconds < 0.3, "the middle run, not the quickest or the slowest"


def test_the_middle_of_a_few_readings_is_the_median() -> None:
    assert selftest.middle([3.0, 1.0, 2.0]) == 2.0
    assert selftest.middle([5.0, 1.0]) == 1.0, "the lower of two, never a made-up mean"
    assert selftest.middle([7.0]) == 7.0


async def test_a_storage_level_is_the_middle_of_three_runs_on_distinct_places() -> None:
    """Each run reads its own places (a distinct salt), so no run measures the cache the run
    before it filled, and the level records the middle run by what it delivered."""
    salts: list[int] = []
    rates = iter((40.0, 100.0, 60.0))

    async def read_level(files: list[Path], *, at_once: int, salt: int) -> selftest.StorageLevel:
        salts.append(salt)
        return a_storage_level(at_once, next(rates))

    one = selftest.StorageToMeasure(storage="s", label="Photos", remote=True, roots=(Path("."),))
    curve = await selftest.measure_storage(
        one, levels=(2,), read_level=read_level, files=[Path(f"f{i}") for i in range(20)]
    )

    assert salts == [0, 1, 2]
    (level,) = curve.levels
    assert level.megabytes_per_second == 60.0


def test_a_storage_level_prices_one_seek_for_one_reader() -> None:
    """Two readers side by side for four seconds, twenty-four seeks between them: each reader
    made twelve in four seconds, a third of a second each."""
    level = selftest.StorageLevel(at_once=2, seconds=4.0, bytes_read=24 << 20, seeks=24)
    assert level.seconds_per_seek == pytest.approx(1 / 3)
    assert selftest.StorageLevel(at_once=2, seconds=4.0, bytes_read=0).seconds_per_seek == 0.0


# --- the decoder ---------------------------------------------------------------------------------


async def test_the_decoder_is_measured_as_the_middle_of_three(
    tmp_path: Path, settings: Settings
) -> None:
    decodes = iter((2.0, 1.0, 4.0))
    runs = iter((1.0, 3.0, 2.0))

    async def decode(_source: Path, _settings: Settings) -> float:
        return next(decodes)

    async def seek(_source: Path, ats: Sequence[float], _settings: Settings) -> float:
        assert len(ats) == len(selftest.SEEK_MOMENTS)
        return next(runs)

    found = await selftest.measure_decode(tmp_path / "clip.mp4", settings, decode=decode, seek=seek)

    assert found is not None
    assert found.frames_per_second == pytest.approx(selftest.CLIP_SECONDS * selftest.CLIP_RATE / 2)
    assert found.seek_seconds == pytest.approx(2.0 / len(selftest.SEEK_MOMENTS)), (
        "the middle run, per moment"
    )


async def test_a_decoder_that_cannot_run_is_no_rate_rather_than_a_slow_one(
    tmp_path: Path, settings: Settings
) -> None:
    async def decode(_source: Path, _settings: Settings) -> float:
        raise FFmpegError("no decoder")

    assert await selftest.measure_decode(tmp_path / "clip.mp4", settings, decode=decode) is None


async def test_the_decoder_is_measured_after_the_ladder_and_reported(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    reported: list[selftest.Decode | None] = []

    async def build(into: Path, _settings: Settings) -> Path:
        return into / "source.mp4"

    async def encode(*_args: object) -> bool:
        order.append("encode")
        return True

    async def decoder(_source: Path, _settings: Settings, *, repeats: int) -> selftest.Decode:
        order.append("decode")
        assert repeats == 1
        return selftest.Decode(frames_per_second=300.0, seek_seconds=0.05)

    monkeypatch.setattr(selftest, "build_clip", build)

    measured = await selftest.measure(
        workspace=tmp_path,
        settings=settings,
        cores=8,
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
        levels=(1,),
        encode=encode,
        repeats=1,
        measure_decoder=decoder,
        report=lambda so_far: reported.append(so_far.decode),
    )

    assert order == ["encode", "decode"]
    assert measured.decode == selftest.Decode(frames_per_second=300.0, seek_seconds=0.05)
    assert reported[-1] == measured.decode


@pytest.mark.integration
async def test_the_decoder_and_a_seek_are_really_run(tmp_path: Path, settings: Settings) -> None:
    """On the real tool: the clip is built, decoded, and seeked into, and the numbers are rates."""
    source = await selftest.build_clip(tmp_path, settings)
    found = await selftest.measure_decode(source, settings, repeats=1)
    assert found is not None
    assert found.frames_per_second > 0
    assert found.seek_seconds > 0
