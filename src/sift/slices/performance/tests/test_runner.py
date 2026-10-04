# SPDX-License-Identifier: AGPL-3.0-or-later
"""The runner and the rates it keeps.

What is checked: a finished run files this hardware's rates under its profile and a later run
replaces them; a run that could not measure files nothing; the rates round-trip through the
store; and the Build's two questions (measured?, measure) are answered by the same run the
screen would show.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.kernel.hardware import HardwareReport
from sift.slices.performance import selftest
from sift.slices.performance.rates import MachineRates, RatesStore, StorageRate
from sift.slices.performance.runner import SelfTestRunner
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
    """The schema step is a no-op on a database that already has the table: a saved measurement
    survives the next start."""
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


def a_runner(
    tmp_path: Path, store: RatesStore, *, machine: HardwareReport | None = None
) -> SelfTestRunner:
    async def current() -> dict[str, int]:
        return {}

    async def storages() -> list[selftest.StorageToMeasure]:
        return []

    return SelfTestRunner(
        settings=Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
        hardware=machine or a_machine(),
        worst_lag=lambda: 0.0,
        worst_wait=lambda: 0.0,
        current=current,
        storages=storages,
        rates=store,
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
    await runner.measure()

    assert await runner.measured() is True
    rates = await runner.rates()
    assert rates is not None and rates.profile == a_machine().profile
    assert rates.decode_fps == 300.0
    assert runner.state.finished_at is not None and runner.state.running is False
    assert runner.state.measurement == a_measurement(), "the screen shows the same run"


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

    await runner.measure()

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
    waiting = asyncio.create_task(runner.measure())
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
    await a_runner(tmp_path, store, machine=a_machine(cores=8)).measure()

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

    await runner.measure()

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
