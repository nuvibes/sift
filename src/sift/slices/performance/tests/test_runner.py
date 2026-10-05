# SPDX-License-Identifier: AGPL-3.0-or-later
"""The runner and the rates it keeps, per hardware profile: a whole run, one storage, the GPU's
previews and the models, and what a restart reads back."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.kernel.hardware import HardwareReport
from sift.kernel.media import FFmpegError
from sift.slices.performance import measure_encoder, measure_models, measure_together, selftest
from sift.slices.performance.measure_encoder import CardCurve, CardLevel
from sift.slices.performance.measure_models import ModelCurve, ModelLevel, ModelPass
from sift.slices.performance.rates import (
    MachineRates,
    RatesStore,
    StorageRate,
    lengths_from_json,
    measurement_to_json,
    more_from_json,
    together_from_json,
)
from sift.slices.performance.runner import NO_FOLDER, SelfTestRunner
from sift.slices.performance.selftest import (
    Decode,
    Level,
    Measurement,
    StorageCurve,
    StorageLevel,
)

pytestmark = pytest.mark.integration


def a_machine(cores: int = 8) -> HardwareReport:
    return HardwareReport(
        cpu_count=cores,
        total_ram_bytes=None,
        worker_concurrency=4,
        cuda=False,
        rocm=False,
        transcode_encoders=(),
        warnings=(),
    )


def a_measurement() -> Measurement:
    return Measurement(
        cores=8,
        levels=(
            Level(
                at_once=2,
                seconds=4.0,
                finished=2,
                worst_lag_seconds=0.0,
                worst_wait_seconds=0.0,
            ),
        ),
        decode=Decode(frames_per_second=300.0, seek_seconds=0.05),
        storages=(
            StorageCurve(
                storage="\\\\nas\\a\\",
                label="Videos",
                remote=True,
                levels=(
                    StorageLevel(at_once=1, seconds=2.0, bytes_read=40 << 20, seeks=12),
                    StorageLevel(at_once=2, seconds=2.0, bytes_read=80 << 20, seeks=24),
                    StorageLevel(at_once=4, seconds=4.0, bytes_read=80 << 20, seeks=48),
                ),
            ),
            StorageCurve(storage="\\\\nas\\b\\", label="Photos", remote=True, failed="too few"),
        ),
    )


async def test_the_rates_are_read_once_per_profile_until_a_measurement_replaces_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runner asks per task (once per file of a Build), and each ask would otherwise be a
    read of the rates table and a thread hop. The answer moves only when a measurement is saved."""
    store = await a_store(tmp_path)
    reads = 0
    real = cast(Any, store._db.fetch_one)

    async def counting(*args: object, **kwargs: object) -> object:
        nonlocal reads
        reads += 1
        return await real(*args, **kwargs)

    monkeypatch.setattr(store._db, "fetch_one", counting)

    assert await store.load("profile-a") is None
    assert await store.load("profile-a") is None
    assert reads == 1, "the second ask was answered from memory"

    await store.save(
        MachineRates(
            profile="profile-a", measured_at=1, decode_fps=30.0, seek_seconds=0.1, storages={}
        )
    )
    rates = await store.load("profile-a")
    assert rates is not None and rates.decode_fps == 30.0
    assert reads == 2, "a measurement saved is read back once"


async def test_the_rates_table_is_made_once_and_kept_across_boots(tmp_path: Path) -> None:
    """A saved measurement survives the next start: the schema step leaves the table be."""
    from sift.slices.performance import rates

    store = await a_store(tmp_path)
    await store.save(
        MachineRates(
            profile="profile-a", measured_at=1, decode_fps=30.0, seek_seconds=0.1, storages={}
        )
    )
    async with store._db.write() as connection:
        await rates.initialize(connection, rates.VERSION)

    loaded = await store.load("profile-a")
    assert loaded is not None and loaded.decode_fps == 30.0


async def test_the_storages_to_measure_are_grouped_by_what_serves_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nine folders on one share are one share: it is the share that has a number of readers it
    can serve, and the folders on it ride together under one label."""
    import importlib
    from types import SimpleNamespace

    from sift.slices.performance.runner import storages_to_measure

    def storage(path: Path) -> Any:
        remote = path.as_posix().startswith("//nas/")
        return SimpleNamespace(key="\\\\nas\\a\\" if remote else "C:\\", remote=remote)

    monkeypatch.setattr(
        importlib.import_module("sift.slices.performance.runner"), "storage_of", storage
    )

    class Library:
        async def roots(self) -> list[Any]:
            return [
                SimpleNamespace(name="Photos", abs_path="//nas/a/photos"),
                SimpleNamespace(name="Local", abs_path="C:/library"),
                SimpleNamespace(name="Video", abs_path="//nas/a/video"),
            ]

    grouped = await storages_to_measure(Library())

    assert [(one.label, one.remote, len(one.roots)) for one in grouped] == [
        ("Photos, Video", True, 2),
        ("Local", False, 1),
    ]


#: Every database `a_store` opened, closed after each test by the package's conftest. An open one
#: holds a thread that keeps the interpreter alive, and a failing test keeps its store reachable
#: through the traceback, so a red run would otherwise never exit.
OPENED: list[Database] = []


async def a_store(tmp_path: Path) -> RatesStore:
    database = Database(tmp_path / "rates.sqlite3")
    await database.connect()
    OPENED.append(database)
    await database.initialize_schema()
    return RatesStore(database)


async def close_the_stores() -> None:
    while OPENED:
        await OPENED.pop().close()


ASKED: list[str | None] = []


async def _ask(requested_by: str | None) -> str | None:
    ASKED.append(requested_by)
    return "job-1"


def a_runner(
    tmp_path: Path,
    store: RatesStore,
    *,
    machine: HardwareReport | None = None,
    on: tuple[selftest.StorageToMeasure, ...] = (),
    **more: Any,
) -> SelfTestRunner:
    async def current() -> dict[str, int]:
        return {}

    async def storages() -> list[selftest.StorageToMeasure]:
        return list(on)

    return SelfTestRunner(
        settings=Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
        hardware=machine or a_machine(),
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
        current=current,
        storages=storages,
        rates=store,
        ask=_ask,
        **more,
    )


# --- the rates ------------------------------------------------------------------------------------


def test_the_rates_are_read_off_the_best_level_of_each_share() -> None:
    rates = MachineRates.from_measurement("abc", a_measurement(), now=1000)

    assert rates.decode_fps == 300.0
    assert rates.seek_seconds == 0.05
    assert list(rates.storages) == ["\\\\nas\\a\\"], "a share that could not be measured is absent"
    share = rates.storages["\\\\nas\\a\\"]
    assert share.at_once == 2, "the knee, not the widest level tried"
    assert share.megabytes_per_second == pytest.approx(80 * 1.048576 / 2, rel=1e-3)
    assert share.seek_seconds == pytest.approx(2.0 * 2 / 24)


def test_a_measurement_with_no_decoder_keeps_no_decode_rate_rather_than_zero() -> None:
    rates = MachineRates.from_measurement("abc", Measurement(cores=8), now=1)
    assert rates.decode_fps is None and rates.seek_seconds is None
    assert rates.storages == {}


async def test_the_rates_round_trip_through_the_store(tmp_path: Path) -> None:
    store = await a_store(tmp_path)
    kept = MachineRates(
        profile="abc",
        measured_at=1234,
        decode_fps=250.5,
        seek_seconds=0.042,
        storages={
            "\\\\nas\\a\\": StorageRate(at_once=2, megabytes_per_second=80.0, seek_seconds=0.2)
        },
    )

    assert await store.load("abc") is None
    await store.save(kept)

    assert await store.load("abc") == kept
    assert await store.load("other") is None, "one row per profile, and only that profile's"


async def test_a_later_run_replaces_the_profiles_rates(tmp_path: Path) -> None:
    store = await a_store(tmp_path)
    first = MachineRates(profile="abc", measured_at=1, decode_fps=100.0, seek_seconds=0.1)
    second = MachineRates(profile="abc", measured_at=2, decode_fps=200.0, seek_seconds=0.2)
    await store.save(first)
    await store.save(second)
    assert await store.load("abc") == second


# --- the runner -----------------------------------------------------------------------------------


async def test_a_finished_run_files_the_rates_under_this_hardware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def instant(**_kwargs: object) -> Measurement:
        return a_measurement()

    monkeypatch.setattr(selftest, "measure", instant)
    store = await a_store(tmp_path)
    runner = a_runner(tmp_path, store)

    assert await runner.measured() is False
    await runner.run()

    assert await runner.measured() is True
    rates = await runner.rates()
    assert rates is not None and rates.profile == a_machine().profile
    assert rates.decode_fps == 300.0
    assert runner.state.finished_at is not None and runner.state.running is False
    assert runner.state.measurement == a_measurement(), "the screen shows the same run"


async def test_each_kinds_last_length_is_kept_and_survives_a_later_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def instant(**_kwargs: object) -> Measurement:
        return a_measurement()

    monkeypatch.setattr(selftest, "measure", instant)
    store = await a_store(tmp_path)
    runner = a_runner(tmp_path, store)
    await runner.keep_length("whole", 724.04)
    assert await runner.lasted("whole") is None, "nothing is kept before a measurement is"

    await runner.run()
    await runner.keep_length("whole", 724.04)
    await runner.keep_length("storage", 1.2)
    await runner.run()

    again = a_runner(tmp_path, RatesStore(store._db))
    assert await again.lasted("whole") == 724.0
    assert await again.lasted("storage") == 1.2


async def test_the_stalls_a_run_causes_are_counted_apart_from_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = [5, 2]

    async def stalling(**_kwargs: object) -> Measurement:
        seen[0] += 21
        seen[1] += 3
        return a_measurement()

    monkeypatch.setattr(selftest, "measure", stalling)
    runner = a_runner(tmp_path, await a_store(tmp_path), stalls=lambda: (seen[0], seen[1]))
    await runner.run()
    seen[0] += 4

    assert runner.caused == (21, 3), "what stalled before and after the run is not its"


async def test_a_run_that_could_not_measure_files_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A machine whose encoder could not run is not a measured machine: the next Build asks again
    rather than reading a row that says nothing."""

    async def refused(**_kwargs: object) -> Measurement:
        return Measurement(cores=8, failed="the video encoder could not be run")

    monkeypatch.setattr(selftest, "measure", refused)
    store = await a_store(tmp_path)
    runner = a_runner(tmp_path, store)

    await runner.run()

    assert await runner.measured() is False
    assert runner.state.measurement is not None and runner.state.measurement.failed


async def test_a_second_ask_joins_the_run_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The screen's press and a Build's first step are one run, not two measuring each other."""
    started = 0
    release = asyncio.Event()

    async def slow(**_kwargs: object) -> Measurement:
        nonlocal started
        started += 1
        await release.wait()
        return a_measurement()

    monkeypatch.setattr(selftest, "measure", slow)
    store = await a_store(tmp_path)
    runner = a_runner(tmp_path, store)

    assert runner.start() is True
    assert runner.start() is False, "already going"
    waiting = asyncio.create_task(runner.run())
    await asyncio.sleep(0)
    release.set()
    await waiting

    assert started == 1
    assert await runner.measured() is True


async def test_the_rates_are_per_hardware_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run on one shape of machine does not make another shape measured."""

    async def instant(**_kwargs: object) -> Measurement:
        return a_measurement()

    monkeypatch.setattr(selftest, "measure", instant)
    store = await a_store(tmp_path)
    await a_runner(tmp_path, store, machine=a_machine(cores=8)).run()

    assert await a_runner(tmp_path, store, machine=a_machine(cores=4)).measured() is False


# --- the rates as the kernel's read rule takes them ----------------------------------------------


def test_the_rates_for_reading_carry_the_files_share_when_it_was_measured() -> None:
    rates = MachineRates.from_measurement("abc", a_measurement(), now=1)

    local = rates.for_reading(None)
    assert local is not None and local.storage is None
    assert local.decode_fps == 300.0 and local.seek_seconds == 0.05

    on_share = rates.for_reading("\\\\nas\\a\\")
    assert on_share is not None and on_share.storage is not None
    assert on_share.storage.seek_seconds == pytest.approx(2.0 * 2 / 24)

    unmeasured_share = rates.for_reading("\\\\nas\\b\\")
    assert unmeasured_share is not None and unmeasured_share.storage is None, (
        "a share that could not be measured is read as a local disk would be, not refused"
    )


def test_a_machine_whose_decoder_never_ran_has_no_read_rates() -> None:
    rates = MachineRates.from_measurement("abc", Measurement(cores=8), now=1)
    assert rates.for_reading(None) is None


async def test_the_runner_answers_read_rates_for_a_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def instant(**_kwargs: object) -> Measurement:
        return a_measurement()

    monkeypatch.setattr(selftest, "measure", instant)
    store = await a_store(tmp_path)
    runner = a_runner(tmp_path, store)
    assert await runner.read_rates(tmp_path / "clip.mp4") is None, "never measured"

    await runner.run()

    read = await runner.read_rates(tmp_path / "clip.mp4")
    assert read is not None and read.decode_fps == 300.0 and read.storage is None


# --- the reading kept across a restart ------------------------------------------------------------


async def test_the_whole_measurement_round_trips_through_the_store(tmp_path: Path) -> None:
    """What the share advice is worked out from is kept, not only held in memory."""
    store = await a_store(tmp_path)
    kept = MachineRates.from_measurement("abc", a_measurement(), now=1234)

    await store.save(kept)

    loaded = await store.load("abc")
    assert loaded is not None
    assert loaded.measurement == a_measurement()


async def test_a_restarted_screen_gives_the_share_advice_from_the_kept_reading(
    tmp_path: Path,
) -> None:
    """The share advice draws after a restart, not only once the test has run in THIS process."""
    store = await a_store(tmp_path)
    await store.save(MachineRates.from_measurement(a_machine().profile, a_measurement(), now=1))
    runner = a_runner(tmp_path, store)
    before = runner.state.measurement

    await runner.recall()

    assert before is None, "nothing is on the screen until it is recalled"
    assert runner.state.measurement == a_measurement()
    assert runner.state.finished_at is not None
    keys = [one.key for one in runner.state.recommendations]
    assert selftest.SHARE_READS_KEY in keys, "the share advice is back without measuring again"


async def test_a_different_machine_recalls_nothing(tmp_path: Path) -> None:
    """A row belongs to one hardware profile; a changed machine is a different machine."""
    store = await a_store(tmp_path)
    await store.save(MachineRates.from_measurement("another-machine", a_measurement(), now=1))
    runner = a_runner(tmp_path, store)

    await runner.recall()

    assert runner.state.measurement is None
    assert runner.state.recommendations == []


async def test_a_row_kept_before_the_reading_was_stored_recalls_nothing(tmp_path: Path) -> None:
    store = await a_store(tmp_path)
    await store.save(
        MachineRates(profile=a_machine().profile, measured_at=1, decode_fps=30.0, seek_seconds=0.1)
    )
    runner = a_runner(tmp_path, store)

    await runner.recall()

    assert runner.state.measurement is None


def test_a_kept_reading_this_version_cannot_read_is_none_not_an_error() -> None:
    from sift.slices.performance.rates import measurement_from_json

    assert measurement_from_json('{"cores": 8, "levels": [{"at_once": "x"}]}') is None
    assert measurement_from_json("not json") is None


# --- asked, and one storage alone -----------------------------------------------------------------


async def test_a_build_s_measure_only_asks_for_the_queued_run(tmp_path: Path) -> None:
    runner = a_runner(tmp_path, await a_store(tmp_path))
    ASKED.clear()

    await runner.measure()

    assert ASKED == [None]
    assert runner.state.running is False and runner.state.measurement is None


async def test_a_run_with_no_library_folder_says_no_share_was_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def instant(**_kwargs: object) -> Measurement:
        return a_measurement()

    monkeypatch.setattr(selftest, "measure", instant)
    store = await a_store(tmp_path)
    alone = a_runner(tmp_path, store)
    await alone.run()
    assert alone.notes == [NO_FOLDER]

    with_folder = a_runner(tmp_path, store, on=(_new_share(tmp_path),))
    await with_folder.run()
    assert with_folder.notes == []


def _new_share(tmp_path: Path) -> selftest.StorageToMeasure:
    return selftest.StorageToMeasure(
        storage="\\\\nas\\c\\", label="Clips", remote=True, roots=(tmp_path,)
    )


def _measured_share(levels: tuple[StorageLevel, ...]) -> Any:
    async def measured(one: selftest.StorageToMeasure, **_kwargs: object) -> StorageCurve:
        return StorageCurve(storage=one.storage, label=one.label, remote=True, levels=levels)

    return measured


async def test_one_storage_measured_alone_is_kept_beside_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = await a_store(tmp_path)
    await store.save(MachineRates.from_measurement(a_machine().profile, a_measurement(), now=5))
    levels = (
        StorageLevel(at_once=1, seconds=2.0, bytes_read=20 << 20, seeks=12),
        StorageLevel(at_once=2, seconds=2.0, bytes_read=60 << 20, seeks=24),
    )
    monkeypatch.setattr(selftest, "measure_storage", _measured_share(levels))
    share = _new_share(tmp_path)
    runner = a_runner(tmp_path, store, on=(share,))

    curve = await runner.measure_storage(share.storage)

    assert curve is not None and curve.best is not None and curve.best.at_once == 2
    rates = await runner.rates()
    assert rates is not None
    assert set(rates.storages) == {"\\\\nas\\a\\", share.storage}, "the share before is kept"
    assert rates.storages[share.storage].at_once == 2
    assert rates.decode_fps == 300.0, "the device's own rates are kept"
    assert rates.measurement is not None
    assert [one.storage for one in rates.measurement.storages] == [
        "\\\\nas\\a\\",
        "\\\\nas\\b\\",
        share.storage,
    ]
    assert runner.state.running is False and runner.state.finished_at is not None


async def test_a_storage_measured_on_a_device_never_measured_keeps_no_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row holding only a share would make the device read as measured, so none is written."""
    levels = (StorageLevel(at_once=1, seconds=2.0, bytes_read=20 << 20, seeks=12),)
    monkeypatch.setattr(selftest, "measure_storage", _measured_share(levels))
    share = _new_share(tmp_path)
    runner = a_runner(tmp_path, await a_store(tmp_path), on=(share,))

    assert await runner.measure_storage(share.storage) is not None
    assert await runner.measured() is False
    assert await runner.measure_storage("\\\\elsewhere\\") is None, "no folder is on it"


# --- the GPU's previews and the models --------------------------------------------------------------


def a_card() -> CardCurve:
    return CardCurve(
        encoder="h264_nvenc",
        decodes_on_card=True,
        levels=(
            CardLevel(at_once=1, seconds=2.0, finished=1),
            CardLevel(at_once=2, seconds=2.0, finished=2),
            CardLevel(at_once=4, seconds=3.9, finished=4, busy=True),
        ),
    )


def a_model(family: str = "identify", *, share: bool = True) -> ModelCurve:
    return ModelCurve(
        name="Faces",
        family=family,
        device="nvidia",
        levels=(
            ModelLevel(at_once=1, seconds=4.0, finished=1),
            ModelLevel(at_once=2, seconds=4.0, finished=2),
            ModelLevel(at_once=4, seconds=8.0, finished=4),
        ),
        share_key=measure_models.RECOGNITION_SHARE_KEY if share else None,
        memory_bytes=10,
    )


def _measured_more(monkeypatch: pytest.MonkeyPatch, *, clip: bool = True) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def instant(**_kwargs: object) -> Measurement:
        return a_measurement()

    async def source(into: Path, _settings: Settings) -> Path:
        if not clip:
            raise FFmpegError("no encoder")
        return into / "clip.mp4"

    async def card(**kwargs: Any) -> CardCurve:
        seen["card_source"] = kwargs["source"]
        return a_card()

    async def model(one: ModelPass, **kwargs: Any) -> ModelCurve:
        seen["model_source"] = kwargs["source"]
        return a_model(one.family)

    monkeypatch.setattr(selftest, "measure", instant)
    monkeypatch.setattr(measure_encoder, "build_source", source)
    monkeypatch.setattr(measure_encoder, "measure_card", card)
    monkeypatch.setattr(measure_models, "measure_pass", model)
    return seen


def a_model_pass() -> ModelPass:
    return ModelPass(
        name="Faces",
        family="identify",
        device="nvidia",
        installed=lambda _weight: True,
        runner=lambda: cast(Any, None),
        asks=(),
        picture=(8, 8),
    )


async def _passes() -> list[ModelPass]:
    return [a_model_pass()]


async def test_a_run_measures_the_gpus_previews_and_each_model_and_keeps_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _measured_more(monkeypatch)
    store = await a_store(tmp_path)
    runner = a_runner(tmp_path, store, preview=cast(Any, object()), passes=_passes)

    await runner.run()

    assert runner.card == a_card() and runner.models == (a_model(),)
    assert seen["card_source"].name == "clip.mp4" and seen["model_source"] == seen["card_source"]
    assert runner.notes == [NO_FOLDER, *a_card().said()]
    advice = {one.key: one for one in runner.state.recommendations}
    previews = advice[selftest.GENERATION_LIMIT_KEY]
    assert previews.suggested == 2 and "on the GPU" in previews.reason
    share = advice[measure_models.RECOGNITION_SHARE_KEY]
    assert share.suggested == 50, "two files at once of the four tasks"
    assert await runner.prices() == {"identify": 4.0}
    kept = await runner.rates()
    assert kept is not None and kept.card == a_card() and kept.models == (a_model(),)

    again = a_runner(tmp_path, RatesStore(store._db))
    await again.recall()
    assert again.card == a_card() and again.models == (a_model(),)
    assert again.notes == a_card().said()
    assert again.state.recommendations == runner.state.recommendations


async def test_no_clip_is_handed_on_as_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _measured_more(monkeypatch, clip=False)
    runner = a_runner(
        tmp_path, await a_store(tmp_path), preview=cast(Any, object()), passes=_passes
    )
    await runner.run()
    assert seen == {"card_source": None, "model_source": None}


async def test_without_a_gpu_or_models_the_advice_is_the_cpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _measured_more(monkeypatch)
    runner = a_runner(tmp_path, await a_store(tmp_path))
    await runner.run()
    assert runner.card is None and runner.models == ()
    assert runner.state.recommendations == selftest.recommend(a_measurement(), current={})
    assert runner.recommend(Measurement(cores=8), current={}) == []


async def test_a_storage_run_keeps_the_kept_gpu_and_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _measured_more(monkeypatch)
    share = selftest.StorageToMeasure(storage="\\\\nas\\a\\", label="Videos", remote=True, roots=())
    store = await a_store(tmp_path)
    await a_runner(tmp_path, store, preview=cast(Any, object()), passes=_passes).run()
    monkeypatch.setattr(
        selftest,
        "measure_storage",
        _measured_share((StorageLevel(at_once=1, seconds=2.0, bytes_read=20 << 20, seeks=12),)),
    )
    later = a_runner(tmp_path, RatesStore(store._db), on=(share,))
    await later.measure_storage(share.storage)
    kept = await later.rates()
    assert later.card == a_card() and kept is not None and kept.card == a_card()


async def test_the_kept_gpu_and_models_survive_an_unreadable_reading() -> None:
    assert more_from_json("{not json") == (None, ())
    assert more_from_json(measurement_to_json(Measurement(cores=8))) == (None, ())


def test_the_kept_run_lengths_survive_an_unreadable_reading() -> None:
    kept = measurement_to_json(Measurement(cores=8), lengths={"storage": 12.5})
    assert lengths_from_json(kept) == {"storage": 12.5}
    for unreadable in ("{not json", "[1]", '{"lengths": [1]}', '{"lengths": {"storage": "x"}}'):
        assert lengths_from_json(unreadable) == {}, unreadable


async def test_a_gpu_with_no_model_passes_measures_the_previews_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _measured_more(monkeypatch)
    runner = a_runner(tmp_path, await a_store(tmp_path), preview=cast(Any, object()))
    await runner.run()
    assert runner.card == a_card() and runner.models == ()
    assert "model_source" not in seen


def a_together(*, behind: bool) -> measure_together.Together:
    plan = measure_together.Plan(tasks=3, previews=2, on_card=False)
    lag = 0.5 if behind else 0.0
    return measure_together.Together(
        windows=(measure_together.Window(plan, 20.0, worst_lag_seconds=lag),), seconds=20.0
    )


async def test_the_combined_run_is_last_and_steps_the_advice_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _measured_more(monkeypatch)
    store = await a_store(tmp_path)
    seen: dict[str, Any] = {}

    async def together(plan: measure_together.Plan, machine: Any, **kwargs: Any) -> Any:
        kept = await RatesStore(store._db).load(a_machine().profile)
        seen.update(plan=plan, kept=kept, clip=machine.clip, passes=kwargs["passes"])
        return a_together(behind=True)

    runner = a_runner(tmp_path, store, passes=_passes, together=together)
    await runner.run()

    assert seen["kept"] is None, "nothing is kept until the whole run ends"
    assert seen["plan"].tasks == 4 and seen["clip"].name == selftest.CLIP_NAME
    assert [one.name for one in seen["passes"]] == ["Faces"]
    advice = {one.key: one.suggested for one in runner.state.recommendations}
    assert advice[selftest.WORKER_COUNT_KEY] == 2 and advice[selftest.GENERATION_LIMIT_KEY] == 1
    assert runner.notes[-2:] == a_together(behind=True).said()[-2:]
    kept = await runner.rates()
    assert kept is not None and kept.together == a_together(behind=True)

    again = a_runner(tmp_path, RatesStore(store._db))
    await again.recall()
    assert again.together == a_together(behind=True)
    assert again.state.recommendations == runner.state.recommendations


async def test_a_combined_run_that_breaks_keeps_and_shows_the_separate_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _measured_more(monkeypatch)

    async def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("a slot broke")

    runner = a_runner(tmp_path, await a_store(tmp_path), together=broken)
    with pytest.raises(RuntimeError):
        await runner.run()

    kept = await runner.rates()
    assert kept is not None and kept.measurement == a_measurement() and kept.together is None
    assert runner.state.measurement == a_measurement() and runner.state.finished_at is not None


async def test_a_combined_run_that_kept_up_changes_no_advice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _measured_more(monkeypatch)

    async def together(*_args: Any, **_kwargs: Any) -> Any:
        return a_together(behind=False)

    runner = a_runner(tmp_path, await a_store(tmp_path), together=together)
    await runner.run()
    assert runner.state.recommendations == selftest.recommend(a_measurement(), current={})


def test_the_combined_run_round_trips_and_an_unreadable_one_is_none() -> None:
    kept = measurement_to_json(a_measurement(), together=a_together(behind=True))
    assert together_from_json(kept) == a_together(behind=True)
    assert together_from_json(measurement_to_json(a_measurement())) is None
    assert together_from_json('{"together": {"windows": [{}]}}') is None
