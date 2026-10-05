# SPDX-License-Identifier: AGPL-3.0-or-later
"""One clock for a run: each stage's deadline, and a stage ended at it whatever it does."""

from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.slices.performance import selftest
from sift.slices.performance.budget import (
    DECODER,
    ENCODING,
    GRACE_SECONDS,
    MODELS,
    PREVIEWS,
    STORAGE,
    TOGETHER,
    WHOLE_SECONDS,
    WHOLE_SHARES,
    Budget,
    Deadline,
)
from sift.slices.performance.rates import MachineRates
from sift.slices.performance.runner import cut_said
from sift.slices.performance.selftest import Measurement
from sift.slices.performance.tests import test_measure_encoder, test_measure_models
from sift.slices.performance.tests.test_runner import a_machine, a_measurement, a_runner, a_store


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_each_stage_gets_its_share_of_what_is_left_and_unused_time_passes_on() -> None:
    clock = Clock()
    budget = Budget(WHOLE_SECONDS, WHOLE_SHARES, clock=clock)
    end = clock.now + WHOLE_SECONDS - GRACE_SECONDS
    total = sum(WHOLE_SHARES.values())

    encoding = budget.stage(ENCODING)
    assert encoding.at == pytest.approx(clock.now + (end - clock.now) * 60 / total)
    clock.now += 10  # done well inside its 60
    decoder = budget.stage(DECODER)
    assert decoder.at - clock.now > (end - 1000.0) * 10 / total, "the 50 s it left are shared on"

    clock.now = end - 5
    together = budget.stage(TOGETHER)
    budget.stage("not a stage")
    assert together.at <= end and budget.cut() == []
    clock.now = end + 30
    assert together.passed() and not decoder.cut
    assert budget.stage(MODELS).at == clock.now, "a stage begun past the end has nothing"
    assert budget.cut() == [TOGETHER]


def test_an_unlimited_budget_never_cuts() -> None:
    deadline = Budget().stage(STORAGE)
    assert math.isinf(deadline.at) and math.isinf(deadline.part(3).at)
    assert not deadline.passed()


async def test_a_stage_that_never_returns_is_ended_at_its_deadline() -> None:
    deadline = Deadline(ENCODING, time.monotonic() + 0.2)
    ended = asyncio.Event()

    async def forever() -> int:
        try:
            await asyncio.Event().wait()
        finally:
            ended.set()
        return 1

    started = time.monotonic()
    assert await asyncio.wait_for(deadline.within(forever()), 5) is None
    assert time.monotonic() - started < 1.0 and ended.is_set() and deadline.cut

    late = forever()
    assert await deadline.within(late) is None, "past its deadline nothing starts"
    with pytest.raises(RuntimeError, match="cannot reuse already awaited coroutine"):
        await late  # closed, never left unawaited

    waiting: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    assert await deadline.within(waiting) is None, "a step that is no coroutine is left as it is"
    assert not waiting.done()
    waiting.cancel()


async def test_a_step_done_in_time_is_kept_and_its_own_timeout_is_not_a_cut() -> None:
    deadline = Deadline(DECODER, time.monotonic() + 5)

    async def quick() -> int:
        return 7

    async def own_timeout() -> int:
        raise TimeoutError("the tool's own limit")

    assert await deadline.within(quick()) == 7
    with pytest.raises(TimeoutError, match="the tool's own limit"):
        await deadline.within(own_timeout())
    assert not deadline.cut and await Deadline(DECODER).within(quick()) == 7


def test_a_part_shares_what_is_left_of_its_stage() -> None:
    clock = Clock()
    stage = Deadline(STORAGE, clock.now + 30, clock)
    assert stage.part(3).at == clock.now + 10
    clock.now += 40
    assert stage.part(2).at == clock.now and stage.part(0).at == clock.now


# --- each ladder, and the run, past its deadline -----------------------------------------------


def _past(name: str) -> Deadline:
    return Deadline(name, time.monotonic() - 1)


async def test_a_ladder_past_its_deadline_keeps_what_it_has_and_says_why(
    tmp_path: Path, settings: Settings
) -> None:
    budget = Budget(1.0, {ENCODING: 1.0}, started=time.monotonic() - 10)
    measured = await selftest.measure(
        workspace=tmp_path,
        settings=settings,
        cores=8,
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
        budget=budget,
    )
    assert measured.failed == selftest.OUT_OF_TIME and budget.cut() == [ENCODING]

    one = selftest.StorageToMeasure(storage="s", label="Clips", remote=True, roots=())
    curve = await selftest.measure_storage(
        one, files=[Path(f"f{i}") for i in range(8)], deadline=_past(STORAGE)
    )
    assert curve.levels == () and curve.unmeasured == selftest.OUT_OF_TIME_UNTRIED

    card = await test_measure_encoder._measure(tmp_path, deadline=_past(PREVIEWS))
    assert card.levels == ()
    model = await test_measure_models._measure(
        tmp_path, test_measure_models.a_pass(), deadline=_past(MODELS)
    )
    assert model.levels == () and model.failed is None


async def test_a_storage_with_no_time_left_keeps_its_last_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def instant(**_kwargs: object) -> Measurement:
        return Measurement(cores=8, levels=(a_measurement().levels))

    monkeypatch.setattr(selftest, "measure", instant)
    store = await a_store(tmp_path)
    await store.save(MachineRates.from_measurement(a_machine().profile, a_measurement(), now=0))
    runner = a_runner(tmp_path, store)

    await runner.run()

    kept = await runner.rates()
    assert kept is not None and "\\\\nas\\a\\" in kept.storages
    assert runner.unsure == frozenset()


def test_what_was_cut_short_is_said_in_plain_words() -> None:
    assert cut_said([PREVIEWS]) == (
        "To finish in time, the benchmark stopped previews on the GPU early. What it measured "
        "there comes from fewer rounds, so Sift only suggests what it found."
    )
    assert "the decoder, the installed models and everything run together early" in cut_said(
        [DECODER, MODELS, TOGETHER]
    )
