# SPDX-License-Identifier: AGPL-3.0-or-later
"""The recommended numbers run together: the plan, the windows, the step down and what is said."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.media import Encoder
from sift.slices.performance import measure_encoder, measure_models, selftest
from sift.slices.performance import measure_together as mt
from sift.slices.performance.measure_encoder import CardCurve, CardLevel
from sift.slices.performance.measure_models import ModelCurve, ModelLevel
from sift.slices.performance.selftest import Recommendation, StorageCurve, StorageLevel
from sift.slices.performance.tests.test_measure_models import Stand, a_pass


def advice(tasks: int = 8, previews: int = 4, **more: int) -> list[Recommendation]:
    wanted = {selftest.WORKER_COUNT_KEY: tasks, selftest.GENERATION_LIMIT_KEY: previews, **more}
    return [Recommendation(key, key, 0, n, "Measured.") for key, n in wanted.items()]


def a_model(name: str, best: int, *, share: bool = False) -> ModelCurve:
    return ModelCurve(
        name=name,
        family="identify",
        device="cpu",
        levels=tuple(ModelLevel(n, 1.0, n) for n in (1, 2, 4) if n <= best),
        share_key=measure_models.RECOGNITION_SHARE_KEY if share else None,
    )


A_CARD = CardCurve(encoder="h264_nvenc", decodes_on_card=True, levels=(CardLevel(2, 1.0, 2),))
A_SHARE = StorageCurve(
    storage="\\\\nas\\a\\",
    label="Films",
    remote=True,
    levels=(StorageLevel(1, 1.0, 10_000_000), StorageLevel(2, 1.0, 20_000_000)),
)


def test_the_plan_is_the_advice_as_it_would_run() -> None:
    plan = mt.plan_for(
        advice(8, 2, **{measure_models.RECOGNITION_SHARE_KEY: 25}),
        card=A_CARD,
        models=[a_model("Faces", 4, share=True), a_model("Smart Search", 4), a_model("None", 0)],
        storages=[A_SHARE, StorageCurve(storage="C:\\", label="Disk", remote=False)],
        current={},
    )
    assert plan is not None and plan.on_card
    assert plan.widths == (("Faces", 2), ("Smart Search", 4)), "a quarter of 8 tasks is 2 files"
    assert plan.models == (("Faces", 2), ("Smart Search", 4)) and plan.encodes == 0
    assert plan.reads == (mt.Reads("\\\\nas\\a\\", "Films", 2),)
    assert plan.described() == (
        "8 tasks at the same time (2 previews on the GPU, Faces 2 files and Smart Search 4 files), "
        "with reads from \\\\nas\\a at 2"
    )
    unset = mt.plan_for(
        advice(8, 2), card=None, models=[a_model("Faces", 4, share=True)], storages=[], current={}
    )
    assert unset is not None and unset.widths == (("Faces", 4),), "the setting's default share"
    assert (
        not unset.on_card
        and unset.described()
        == "8 tasks at the same time (2 previews, Faces 4 files and 2 encodes)"
    )
    assert mt.plan_for([], card=None, models=[], storages=[], current={}) is None


def test_a_step_down_takes_a_quarter_and_squeezes_the_models_last() -> None:
    plan = mt.Plan(tasks=8, previews=4, on_card=True, widths=(("Faces", 4),))
    assert plan.models == (("Faces", 4),) and plan.encodes == 0
    lower = plan.stepped()
    assert lower is not None and (lower.tasks, lower.previews) == (6, 3)
    assert lower.models == (("Faces", 3),)
    one = mt.Plan(tasks=2, previews=2, on_card=False).stepped()
    assert one is not None and (one.tasks, one.previews) == (1, 1)
    assert one.stepped() is None


def test_a_model_left_without_a_task_is_not_run() -> None:
    plan = mt.Plan(tasks=3, previews=2, on_card=False, widths=(("Faces", 4), ("Smart Search", 4)))
    assert plan.models == (("Faces", 1),) and plan.encodes == 0


class Counted:
    def __init__(self, *, failing: bool = False) -> None:
        self.failing = failing
        self.calls: list[str] = []

    async def preview(self, index: int) -> bool:
        self.calls.append(f"preview {index}")
        await asyncio.sleep(0)
        return not self.failing

    async def encode(self, index: int) -> bool:
        self.calls.append(f"encode {index}")
        await asyncio.sleep(0)
        return True

    async def model(self, name: str, index: int) -> bool:
        self.calls.append(f"{name} {index}")
        await asyncio.sleep(0)
        return True

    async def read(self, storage: str) -> int:
        self.calls.append(storage)
        await asyncio.sleep(0)
        return 0 if storage == "gone" else 2_000_000


def readings(lags: list[float]) -> mt.Readings:
    """The worst lag since the start, rising by each window's own lag."""
    seen, total = [0.0], 0.0
    for lag in lags:
        total += lag
        seen += [total, total]
    values = iter(seen)
    return mt.Readings(worst_lag=lambda: next(values), worst_wait=lambda: 0.0)


async def _measure(plan: mt.Plan, work: Counted, lags: list[float], **given: Any) -> mt.Together:
    clock = [0.0]

    async def sleep(seconds: float) -> None:
        clock[0] += seconds
        for _ in range(4):
            await asyncio.sleep(0)

    return await mt.measure(
        plan,
        work,
        readings=readings(lags),
        seconds=given.pop("seconds", 6),
        window=3,
        clock=lambda: clock[0],
        sleep=sleep,
        **{"busy": lambda: False, **given},
    )


async def test_a_plan_that_keeps_up_runs_its_minute_and_changes_nothing() -> None:
    plan = mt.Plan(
        tasks=3,
        previews=1,
        on_card=True,
        widths=(("Faces", 1),),
        reads=(mt.Reads("s", "Films", 1),),
    )
    work = Counted()
    together = await _measure(plan, work, [0.0, 0.0])
    assert [one.plan for one in together.windows] == [plan, plan] and together.seconds == 6
    first = together.windows[0]
    assert first.kept_up and first.done > 0 and first.megabytes > 0
    assert {"preview 0", "encode 0", "Faces 0", "s"} <= set(work.calls)
    assert together.settled == plan
    assert together.said()[1] == "Sift kept up, so nothing was stepped down."
    assert together.applied(advice(3, 1)) == advice(3, 1)


async def test_a_window_that_falls_behind_steps_the_plan_down_and_says_so() -> None:
    plan = mt.Plan(tasks=8, previews=4, on_card=True)
    work = Counted()
    together = await _measure(plan, work, [0.4, 0.4, 0.4], seconds=9)
    assert [one.plan.tasks for one in together.windows] == [8, 6, 5]
    assert together.settled is not None and together.settled.tasks == 4
    assert together.said()[1:] == [
        "At 8 tasks, Sift stopped responding for 0.40 s.",
        "So tasks were stepped down to 4 and previews to 1, which wasn't run again.",
    ]
    moved = {one.key: one for one in together.applied(advice(8, 4))}
    assert moved[selftest.WORKER_COUNT_KEY].suggested == 4
    assert moved[selftest.GENERATION_LIMIT_KEY].suggested == 1
    assert moved[selftest.WORKER_COUNT_KEY].reason.endswith(
        "Run together with everything else recommended, Sift fell behind at 8, so this is 4."
    )
    calls = len(work.calls)
    await asyncio.sleep(0)
    assert len(work.calls) == calls, "every slot stopped with the run"


async def test_a_step_that_keeps_up_is_where_it_settles() -> None:
    together = await _measure(mt.Plan(tasks=8, previews=4, on_card=False), Counted(), [0.4, 0.1])
    assert together.settled is not None and together.settled.tasks == 6
    assert (
        together.said()[-1]
        == "So tasks were stepped down to 6 and previews to 3, where it kept up."
    )


async def test_a_failed_task_is_falling_behind_and_its_slot_waits_for_the_next_window() -> None:
    work = Counted(failing=True)
    together = await _measure(mt.Plan(tasks=1, previews=1, on_card=True), work, [0.0])
    assert together.windows[0].failed == 1 and work.calls == ["preview 0"]
    assert together.said()[1] == "Even at 1 task, 1 of the tasks failed."
    again = await _measure(
        mt.Plan(tasks=2, previews=1, on_card=True), Counted(failing=True), [0.0, 0.0]
    )
    assert [one.failed for one in again.windows] == [1, 1], "tried again in the next window"


async def test_a_window_behind_while_others_were_busy_is_given_one_more_and_marked() -> None:
    busy = iter([True, True])
    plan = mt.Plan(tasks=2, previews=1, on_card=True)
    together = await _measure(plan, Counted(), [0.4, 0.4], busy=lambda: next(busy))
    assert len(together.windows) == 1 and together.windows[0].busy
    assert together.said()[-1] == "Part of it was measured while other programs were busy."


async def test_a_storage_that_stops_answering_ends_only_its_reader() -> None:
    plan = mt.Plan(tasks=1, previews=1, on_card=True, reads=(mt.Reads("gone", "Films", 2),))
    together = await _measure(plan, Counted(), [0.0], seconds=3)
    assert together.windows[0].kept_up and together.windows[0].megabytes == 0


def test_the_other_reasons_for_falling_behind_are_said() -> None:
    plan = mt.Plan(tasks=1, previews=1, on_card=True)
    assert mt.Window(plan, 1.0, worst_wait_seconds=0.3).why() == "work waited 0.30 s for a thread"
    assert mt.Window(plan, 1.0, fell_behind=2).why() == "Sift fell behind 2 times"
    assert mt.Window(plan, 1.0, fell_behind=1).why() == "Sift fell behind once"
    assert not mt.Window(plan, 1.0, fell_behind=2).kept_up, "under the worst since the start"
    assert mt.Together().said() == [] and mt.Together().settled is None
    assert mt.Together().applied(advice()) == advice()
    assert mt.Together(failed="no GPU").said() == [
        "The recommended numbers weren't run together: no GPU."
    ]


# --- the work as Sift does it --------------------------------------------------------------------


def a_machine(tmp_path: Path, *, card: CardCurve | None = A_CARD, **more: Any) -> mt.Machine:
    return mt.Machine(
        clip=tmp_path / "clip.mp4",
        source=more.pop("source", tmp_path / "source.mp4"),
        workspace=tmp_path,
        settings=Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
        hardware=HardwareReport(
            cpu_count=4,
            total_ram_bytes=None,
            worker_concurrency=4,
            cuda=False,
            rocm=False,
            transcode_encoders=(),
            warnings=(),
        ),
        preview_command=more.pop("preview", lambda *_a, **_k: []),
        card=card,
    )


def _recording(seen: list[Any]) -> Callable[..., Any]:
    async def done(*args: Any, **kwargs: Any) -> bool:
        seen.append((args, kwargs))
        return True

    return done


async def test_the_machine_runs_previews_encodes_and_models_as_the_ladders_did(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previews: list[Any] = []
    encodes: list[Any] = []
    files: list[Any] = []
    monkeypatch.setattr(measure_encoder, "_encode", _recording(previews))
    monkeypatch.setattr(selftest, "_encode_once", _recording(encodes))
    monkeypatch.setattr(measure_models, "one_file", _recording(files))

    machine = a_machine(tmp_path)
    assert await machine.preview(3) and await machine.encode(1)
    assert previews[0][1]["encoder"] is Encoder.NVENC and previews[0][0][3] == 3
    assert encodes[0][0][2:] == (1, machine.settings, selftest.THREADS_PER_ENCODE)

    on_cpu = a_machine(tmp_path, card=None)
    assert await on_cpu.preview(2) and encodes[-1][0][2] == 10_002

    stand = Stand()
    plan = mt.Plan(tasks=2, previews=1, on_card=True, widths=(("Faces", 1),))
    await machine.open(plan, [a_pass(stand)], [])
    assert (
        await machine.model("Faces", 0) and files[0][1]["seconds"] == measure_encoder.SOURCE_SECONDS
    )
    await machine.close()
    assert stand.unloaded and machine.ready == {}
    no_source = a_machine(tmp_path, source=None)
    no_source.ready = {"Faces": (measure_models._load(a_pass()), a_pass())}
    assert await no_source.model("Faces", 0) is False


async def test_the_machine_reads_each_storages_largest_files(tmp_path: Path) -> None:
    folder = tmp_path / "films"
    folder.mkdir()
    for name in ("a.bin", "b.bin"):
        with open(folder / name, "wb") as handle:
            handle.truncate(selftest.SAMPLE_FLOOR_BYTES)
    machine = a_machine(tmp_path)
    share = selftest.StorageToMeasure(storage="s", label="Films", remote=True, roots=(folder,))
    empty = selftest.StorageToMeasure(
        storage="e", label="Empty", remote=True, roots=(tmp_path / "x",)
    )
    await machine.open(mt.Plan(tasks=1, previews=1, on_card=False), [], [share, empty])
    assert set(machine.samples) == {"s"}
    assert await machine.read("s") == selftest.SEEKS_PER_FILE * selftest.BYTES_PER_SEEK
    (folder / "a.bin").unlink()
    (folder / "b.bin").unlink()
    assert await machine.read("s") == 0


async def test_a_run_loads_what_the_plan_runs_and_ends_it_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand = Stand()
    planned: list[mt.Plan] = []

    async def measured(plan: mt.Plan, _work: Any, **_kwargs: Any) -> mt.Together:
        planned.append(plan)
        return mt.Together()

    monkeypatch.setattr(mt, "measure", measured)
    plan = mt.Plan(
        tasks=3,
        previews=1,
        on_card=False,
        widths=(("Faces", 1), ("Unknown", 1)),
        reads=(mt.Reads("s", "Films", 1),),
    )
    quiet = mt.Readings(worst_lag=lambda: 0.0, worst_wait=lambda: 0.0)
    await mt.run(plan, a_machine(tmp_path), passes=[a_pass(stand)], to_read=[], readings=quiet)
    assert planned[0].widths == (("Faces", 1),) and planned[0].reads == ()
    assert stand.unloaded

    await mt.run(
        plan, a_machine(tmp_path, source=None), passes=[a_pass()], to_read=[], readings=quiet
    )
    assert planned[1].widths == ()

    def refuses() -> Any:
        raise RuntimeError("the device went away.")

    broken = replace(a_pass(), runner=refuses)
    failed = await mt.run(plan, a_machine(tmp_path), passes=[broken], to_read=[], readings=quiet)
    assert failed.failed == "the device went away"


async def test_a_run_canceled_while_its_models_load_ends_the_ones_already_up(
    tmp_path: Path,
) -> None:
    stand, later = Stand(), Stand()
    loading, release = threading.Event(), threading.Event()

    def slow() -> Stand:
        loading.set()
        release.wait(5)
        return later

    plan = mt.Plan(tasks=3, previews=1, on_card=False, widths=(("Faces", 1), ("Later", 1)))
    machine = a_machine(tmp_path)
    passes = [a_pass(stand), replace(a_pass(), name="Later", runner=slow)]
    quiet = mt.Readings(worst_lag=lambda: 0.0, worst_wait=lambda: 0.0)
    running = asyncio.create_task(mt.run(plan, machine, passes=passes, to_read=[], readings=quiet))
    await asyncio.to_thread(loading.wait, 5)
    running.cancel()
    await asyncio.sleep(0.05)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert stand.unloaded and later.unloaded and machine.ready == {}


class Endless(Counted):
    def __init__(self) -> None:
        super().__init__()
        self.going = 0
        self.all_going = asyncio.Event()

    async def encode(self, index: int) -> bool:
        self.going += 1
        if self.going == 3:
            self.all_going.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.going -= 1
        return True


async def test_a_canceled_combined_run_ends_every_slot_at_once() -> None:
    work = Endless()
    measuring = asyncio.create_task(
        mt.measure(mt.Plan(tasks=3, previews=0, on_card=False), work, readings=readings([0.0]))
    )
    await asyncio.wait_for(work.all_going.wait(), 5)
    measuring.cancel()
    with pytest.raises(asyncio.CancelledError):
        await measuring
    assert work.going == 0, "no encode outlives the run"
