# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each installed model, timed through its own process: the ladder, what is not measured, the
memory, the share it advises and the price it gives a fresh library."""

from __future__ import annotations

import asyncio
import ctypes
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sift.kernel import device_load, media
from sift.kernel.config import Settings
from sift.kernel.ml.runtime import DeviceUnavailable, Loaded
from sift.kernel.ml.weights import Weight
from sift.slices.performance import measure_models as mm
from sift.slices.performance import selftest
from sift.slices.performance.models import model_views


def a_weight(name: str = "finder") -> Weight:
    return Weight(
        id=name,
        role=name,
        family="small",
        revision="1",
        url="https://models.invalid/finder.onnx",
        digest="0" * 64,
        size_bytes=1,
        archive_member=None,
        licence="MIT",
    )


class Stand:
    """A model process that counts what it was asked."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, tuple[int, ...]]] = []
        self.unloaded = False

    def load(self, weight: Weight) -> Loaded:
        return Loaded(weight=weight, session=None, inputs=("in",), outputs=("out",), device="cpu")

    def run(
        self, loaded: Loaded, blob: np.ndarray, *, outputs: Sequence[str] | None = None
    ) -> list[np.ndarray]:
        self.runs.append((loaded.weight.id, blob.shape))
        return [blob]

    def unload(self) -> None:
        self.unloaded = True


def a_pass(
    stand: Stand | None = None, *, installed: bool = True, share: bool = False
) -> mm.ModelPass:
    held = stand or Stand()
    return mm.ModelPass(
        name="Faces",
        family="identify",
        device="cpu",
        installed=lambda _weight: installed,
        runner=lambda: held,
        asks=(mm.Ask(a_weight(), (1, 3, 8, 8), times=3), mm.Ask(a_weight("reader"), (2, 3, 4, 4))),
        picture=(16, 8),
        moments=4,
        share_key=mm.RECOGNITION_SHARE_KEY if share else None,
    )


class Quiet:
    def start(self) -> None:
        pass

    def stop(self) -> tuple[int | None, int | None]:
        return 1_000_000, 2_000_000


def a_settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


async def _measure(tmp_path: Path, one: mm.ModelPass, **given: Any) -> mm.ModelCurve:
    async def file(*_args: Any, **_kwargs: Any) -> bool:
        return True

    options: dict[str, Any] = {
        "busy": lambda: False,
        "watch": Quiet,
        "file": file,
        "repeats": 1,
    }
    options.update(given)
    return await mm.measure_pass(
        one, source=tmp_path / "clip.mp4", seconds=30, settings=a_settings(tmp_path), **options
    )


async def test_a_model_not_installed_is_not_measured_and_nothing_is_fetched(tmp_path: Path) -> None:
    curve = await _measure(tmp_path, a_pass(installed=False))
    assert curve.failed == mm.NOT_INSTALLED
    assert curve.levels == ()
    assert curve.said() == ["Faces wasn't measured: not installed."]


async def test_no_clip_says_so(tmp_path: Path) -> None:
    curve = await mm.measure_pass(a_pass(), source=None, seconds=30, settings=a_settings(tmp_path))
    assert curve.failed == mm.NO_SOURCE


async def test_a_device_that_will_not_load_is_the_answer(tmp_path: Path) -> None:
    def refused(_one: mm.ModelPass) -> Any:
        raise DeviceUnavailable("The GPU could not open a model.")

    curve = await _measure(tmp_path, a_pass(), load=refused)
    assert curve.failed == "The GPU could not open a model"


async def test_each_width_is_timed_through_one_process_and_the_process_is_ended(
    tmp_path: Path,
) -> None:
    stand = Stand()
    curve = await _measure(tmp_path, a_pass(stand))
    assert [level.at_once for level in curve.levels] == list(mm.MODEL_LEVELS)
    assert all(level.finished == level.at_once and not level.failed for level in curve.levels)
    assert curve.memory_bytes == 1_000_000 and curve.card_memory_bytes == 2_000_000
    assert curve.levels[0].memory_bytes == 1_000_000
    assert stand.unloaded


async def test_a_width_whose_files_fail_is_not_counted_and_said(tmp_path: Path) -> None:
    calls = {"n": 0}

    async def file(*_args: Any, **_kwargs: Any) -> bool:
        calls["n"] += 1
        return calls["n"] < 5

    curve = await _measure(tmp_path, a_pass(), file=file)
    four = curve.levels[-1]
    assert four.failed == 4 - four.finished and four.failed > 0
    assert curve.best is not None and curve.best.at_once < 4
    assert curve.said() == [
        f"Faces at 4 at the same time: {four.failed} of its files failed, so it wasn't counted."
    ]


async def test_a_busy_width_is_taken_once_and_marked(tmp_path: Path) -> None:
    answers = iter([True, False, False])
    curve = await _measure(tmp_path, a_pass(), busy=lambda: next(answers))
    assert [level.busy for level in curve.levels] == [True, False, False]
    assert curve.said() == [
        "Faces at 1 at the same time was measured while other programs were busy."
    ]


async def test_steady_takes_a_level_once_and_marks_it_busy() -> None:
    taken: list[int] = []

    async def take() -> int:
        taken.append(1)
        return len(taken)

    assert await selftest.steady(take, lambda n: -n, lambda: False) == 1
    assert await selftest.steady(take, lambda n: -n, lambda: True) == -2


def test_the_device_load_reader_says_whether_others_are_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(device_load.READER, "latest", None)
    assert not selftest.others_busy()
    monkeypatch.setattr(device_load.READER, "latest", device_load.Load(processor=90.0, own=0.0))
    assert selftest.others_busy()
    monkeypatch.setattr(device_load.READER, "latest", device_load.Load(processor=1.0, own=0.0))
    assert not selftest.others_busy()


async def test_one_file_decodes_its_frames_then_makes_its_model_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: dict[str, Any] = {}

    async def frames(_source: Path, moments: Sequence[media.Moment], **kwargs: Any) -> list[bytes]:
        asked.update(kwargs, moments=moments)
        return [b"x"] * len(moments)

    monkeypatch.setattr(media, "raw_moments", frames)
    stand = Stand()
    one = a_pass(stand)
    ready = mm._load(one)
    done = await mm.one_file(ready, one, source=tmp_path, seconds=30, settings=a_settings(tmp_path))
    assert done
    assert asked["filters"] == "scale=16:8" and asked["frame_bytes"] == 16 * 8 * 3
    assert [m.seek for m in asked["moments"]][:2] == [(), ("-ss", media.seconds(7500))]
    assert stand.runs == [("finder", (1, 3, 8, 8))] * 3 + [("reader", (2, 3, 4, 4))]


async def test_a_file_with_a_missing_frame_or_a_refusal_did_not_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def missing(*_args: Any, **_kwargs: Any) -> list[bytes | None]:
        return [None]

    async def refused(*_args: Any, **_kwargs: Any) -> list[bytes]:
        raise media.FFmpegError("no")

    one = a_pass()
    ready = mm._load(one)
    settings = a_settings(tmp_path)
    for answer in (missing, refused):
        monkeypatch.setattr(media, "raw_moments", answer)
        assert not await mm.one_file(ready, one, source=tmp_path, seconds=30, settings=settings)


def test_memory_is_how_far_free_fell_and_the_card_rose() -> None:
    free = iter([900, 700, 800, 850])
    card = iter([100, 400, 300, 200])
    watch = mm.MemoryWatch(free=lambda: next(free), card=lambda: next(card))
    watch.EVERY_SECONDS = 60.0
    watch.start()
    watch._read()
    watch._read()
    assert watch.stop() == (200, 300)
    blind = mm.MemoryWatch(free=lambda: None, card=lambda: None)
    blind.start()
    assert blind.stop() == (None, None)


def test_a_dip_between_the_ends_is_caught_by_the_sampler() -> None:
    sampled = threading.Event()
    free = iter([900, 500])

    def reading() -> int:
        found = next(free, 880)
        if found == 500:
            sampled.set()
        return found

    watch = mm.MemoryWatch(free=reading, card=lambda: None)
    watch.EVERY_SECONDS = 0.001
    watch.start()
    assert sampled.wait(5)
    assert watch.stop() == (400, None)


def test_a_watch_stopped_before_it_started_measured_nothing() -> None:
    assert mm.MemoryWatch(free=lambda: 900, card=lambda: 100).stop() == (None, None)


class Kernel32:
    def __init__(self, answers: bool) -> None:
        self.answers = answers

    def GlobalMemoryStatusEx(self, status: Any) -> int:
        status._obj.ullAvailPhys = 3 << 30
        return int(self.answers)


@pytest.mark.parametrize(("answers", "free"), [(True, 3 << 30), (False, None)])
def test_free_memory_is_what_windows_answers_and_none_on_a_refusal(
    monkeypatch: pytest.MonkeyPatch, answers: bool, free: int | None
) -> None:
    windll = type("WinDLL", (), {"kernel32": Kernel32(answers)})()
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)
    assert mm._windows_free() == free


def test_the_cards_memory_is_none_without_an_nvidia_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_driver(_name: str) -> Any:
        raise OSError("nvml.dll could not be found")

    monkeypatch.setattr(mm, "_WINDOWS", True)
    monkeypatch.setattr(ctypes, "CDLL", no_driver)
    assert mm.card_used() is None
    monkeypatch.setattr(ctypes, "CDLL", lambda _name: Nvml())
    assert mm.card_used() == 5 << 20


def test_free_memory_is_read_on_this_system() -> None:
    found = mm.free_memory()
    assert found is None or found > 0
    used = mm.card_used()
    assert used is None or used >= 0


class Nvml:
    def __init__(self, *refusals: int) -> None:
        self.refusals = [*refusals, 0, 0, 0]
        self.shut = False

    def nvmlInit_v2(self) -> int:
        return self.refusals.pop(0)

    def nvmlDeviceGetHandleByIndex_v2(self, _index: int, _handle: Any) -> int:
        return self.refusals.pop(0)

    def nvmlDeviceGetMemoryInfo(self, _handle: Any, memory: Any) -> int:
        memory._obj.used = 5 << 20
        return self.refusals.pop(0)

    def nvmlShutdown(self) -> int:
        self.shut = True
        return 0


def test_the_cards_memory_comes_from_nvml_and_a_refusal_is_none() -> None:
    assert mm._nvml_used(Nvml()) == 5 << 20
    assert mm._nvml_used(Nvml(1)) is None
    refused = Nvml(0, 1)
    assert mm._nvml_used(refused) is None and refused.shut
    assert mm._nvml_used(Nvml(0, 0, 1)) is None


def a_level(at_once: int, per_second: float, *, failed: int = 0) -> mm.ModelLevel:
    return mm.ModelLevel(
        at_once=at_once,
        seconds=at_once / per_second,
        finished=at_once - failed,
        failed=failed,
        low=per_second * 0.98,
        high=per_second,
    )


def a_curve(*levels: mm.ModelLevel, family: str = "identify", share: bool = True) -> mm.ModelCurve:
    return mm.ModelCurve(
        name="Faces",
        family=family,
        device="nvidia",
        levels=levels,
        share_key=mm.RECOGNITION_SHARE_KEY if share else None,
    )


def test_recognition_share_gives_the_files_at_once_its_model_did_best_at() -> None:
    curve = a_curve(a_level(1, 1.0), a_level(2, 1.9), a_level(4, 1.95))
    found = mm.recommend_share([curve], tasks=20, current={mm.RECOGNITION_SHARE_KEY: 50})
    assert found is not None
    assert (found.key, found.current, found.suggested) == (mm.RECOGNITION_SHARE_KEY, 50, 10)
    assert "at 2 at the same time" in found.reason and "added under 5%" in found.reason
    assert "10% of them is 2" in found.reason
    gaining = a_curve(a_level(1, 1.0), a_level(4, 3.0))
    assert mm.recommend_share([gaining], tasks=6, current={}) is None
    assert gaining.said() == [
        "Faces was still getting quicker at 4 at the same time, the most this run tries, so no "
        "share is suggested for it."
    ]


def test_a_small_share_is_held_to_the_settings_floor() -> None:
    found = mm.recommend_share([a_curve(a_level(1, 1.0), a_level(2, 1.0))], tasks=40, current={})
    assert found is not None and found.suggested == mm.SHARE_FLOOR


def test_a_width_with_a_failed_file_is_never_the_answer() -> None:
    curve = a_curve(a_level(1, 1.0), a_level(2, 1.9), a_level(4, 3.0, failed=1))
    assert curve.best is not None and curve.best.at_once == 2


def test_no_share_is_advised_without_a_measured_recognition() -> None:
    assert mm.recommend_share([], tasks=8, current={}) is None
    assert mm.recommend_share([a_curve(a_level(1, 1.0), share=False)], tasks=8, current={}) is None
    assert mm.recommend_share([a_curve(a_level(1, 1.0))], tasks=0, current={}) is None


def test_a_fresh_library_gets_a_price_per_file_for_each_family() -> None:
    faces = a_curve(a_level(1, 0.5), a_level(2, 0.9))
    marks = a_curve(a_level(1, 4.0), family="identify", share=False)
    search = a_curve(a_level(1, 2.0), family="semantic", share=False)
    unmeasured = mm.ModelCurve(name="x", family="other", device="cpu", failed="not installed")
    assert faces.seconds_per_file == pytest.approx(2 / 0.9)
    assert mm.prices([faces, marks, search, unmeasured]) == pytest.approx(
        {"identify": 2 / 0.9 + 0.25, "semantic": 0.5}
    )


def test_the_wire_carries_each_model_in_megabytes() -> None:
    curve = mm.ModelCurve(
        name="Smart Search",
        family="semantic",
        device="nvidia",
        levels=(mm.ModelLevel(at_once=1, seconds=2.0, finished=1, memory_bytes=3_000_000),),
        memory_bytes=1_257_000_000,
        card_memory_bytes=None,
    )
    failed = mm.ModelLevel(at_once=1, seconds=0.0, finished=0, failed=1)
    view = model_views((curve, a_curve(failed)))
    assert view[0].megabytes == 1257 and view[0].card_megabytes is None
    assert view[0].levels[0].megabytes == 3 and view[0].best_at_once == 1
    assert view[0].seconds_per_file == 2.0
    assert view[1].best_at_once is None and view[1].seconds_per_file is None
    assert failed.files_per_second == 0.0


async def test_a_cancel_while_the_model_loads_ends_the_process_once_it_is_up() -> None:
    stand = Stand()
    up = threading.Event()
    asked = threading.Event()

    def slow(one: mm.ModelPass) -> Any:
        asked.set()
        up.wait(10)
        return mm._load(one)

    loading = asyncio.create_task(mm.loaded(a_pass(stand), slow))
    await asyncio.to_thread(asked.wait, 10)
    loading.cancel()
    await asyncio.sleep(0.05)
    up.set()
    with pytest.raises(asyncio.CancelledError):
        await loading
    assert stand.unloaded, "the model process a canceled run started is ended"


async def test_a_cancel_while_the_models_load_stops_the_loading_watch(tmp_path: Path) -> None:
    watches: list[str] = []
    loading, release = threading.Event(), threading.Event()

    class Counting(Quiet):
        def stop(self) -> tuple[int | None, int | None]:
            watches.append("stopped")
            return super().stop()

    def slow(one: mm.ModelPass) -> Any:
        loading.set()
        release.wait(5)
        return mm._load(one)

    measuring = asyncio.create_task(_measure(tmp_path, a_pass(), watch=Counting, load=slow))
    await asyncio.to_thread(loading.wait, 5)
    measuring.cancel()
    await asyncio.sleep(0.05)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await measuring
    assert watches == ["stopped"]


async def test_a_width_canceled_mid_run_stops_its_memory_watch(tmp_path: Path) -> None:
    watches: list[str] = []
    timing = asyncio.Event()

    class Counting(Quiet):
        def start(self) -> None:
            watches.append("started")

        def stop(self) -> tuple[int | None, int | None]:
            watches.append("stopped")
            return super().stop()

    async def file(*_args: Any, **_kwargs: Any) -> bool:
        if len(watches) > 2:
            timing.set()
            await asyncio.Event().wait()
        return True

    measuring = asyncio.create_task(_measure(tmp_path, a_pass(), watch=Counting, file=file))
    await asyncio.wait_for(timing.wait(), 5)
    measuring.cancel()
    with pytest.raises(asyncio.CancelledError):
        await measuring
    assert watches.count("started") == watches.count("stopped") == 2
