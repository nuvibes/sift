# SPDX-License-Identifier: AGPL-3.0-or-later
"""The benchmark job, and what Sift runs by itself when a library folder is added."""

from __future__ import annotations

import asyncio
import json
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.access import Role
from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.jobs import (
    JobContext,
    JobFailedPermanently,
    JobQueue,
    JobState,
    register_handler,
)
from sift.kernel.jobs.worker_pool import WorkerPool
from sift.kernel.vocabulary import VIA_BENCHMARK
from sift.kernel.workbench import DOER, Recorded
from sift.main import create_app
from sift.slices.performance import benchmark, measure_encoder, measure_together, selftest
from sift.slices.performance.benchmark import (
    BENCHMARK,
    PRESSED,
    RECEIPTS,
    STORAGE,
    BenchmarkReceipts,
    FirstBenchmark,
    FirstFolder,
    ThenScan,
    ask_for_run,
    drain,
    run_benchmark,
    undrain,
)
from sift.slices.performance.rates import MachineRates
from sift.slices.performance.selftest import (
    Measurement,
    StorageCurve,
    StorageLevel,
    StorageToMeasure,
)
from sift.slices.performance.tests.test_runner import (
    a_machine,
    a_measurement,
    a_runner,
    a_store,
)
from sift.slices.settings_hub.service import SettingsService
from sift.testing.auth import establish_session
from sift.testing.fixtures import create_user, png_bytes

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("first_folder_benchmarks")]

ROOTS = "/api/library/roots"


# --- the trigger ---------------------------------------------------------------------------------


async def _nothing(_context: JobContext) -> None:
    return None


class Folders:
    """How many library folders there are, as the trigger reads it."""

    def __init__(self, count: int) -> None:
        self.count = count

    async def __call__(self) -> int:
        return self.count


#: A share the kept measurement has a number for, and one it does not (`a_measurement`).
KNOWN_SHARE = "\\\\nas\\a\\"
NEW_SHARE = "\\\\nas\\c\\"


async def a_trigger(
    tmp_path: Path,
    queue: JobQueue,
    *,
    folders: int = 1,
    measured: bool = False,
    on: str = "C:\\",
    remote: bool = False,
) -> tuple[FirstFolder, FirstBenchmark]:
    register_handler(BENCHMARK, _nothing, name="Benchmarking this device")
    store = await a_store(tmp_path)
    runner = a_runner(tmp_path, store)
    if measured:
        await store.save(MachineRates.from_measurement(a_machine().profile, a_measurement(), now=0))
    first = FirstBenchmark()

    async def storage(root_id: str) -> StorageToMeasure | None:
        if root_id == "gone":
            return None
        return StorageToMeasure(storage=on, label="Clips", remote=remote, roots=(tmp_path,))

    trigger = FirstFolder(
        runner=runner, queue=queue, roots=Folders(folders), first=first, storage=storage
    )
    return trigger, first


async def _waiting(queue: JobQueue, job_type: str) -> int:
    return (await queue.unfinished_by_type()).get(job_type, 0)


async def test_the_first_folder_on_a_device_never_measured_queues_the_benchmark(
    tmp_path: Path, job_queue: JobQueue
) -> None:
    trigger, first = await a_trigger(tmp_path, job_queue)

    assert await trigger("root-1", "admin-1", True) is True

    assert await _waiting(job_queue, BENCHMARK) == 1
    assert first.run is not None and first.run.state == "waiting"
    job = await job_queue.get(first.run.job_id)
    assert job is not None and job.payload == {"root_id": "root-1", "scan": True}
    assert job.requested_by == "admin-1", "the run is the press of whoever added the folder"


async def test_a_second_folder_does_not_queue_it(tmp_path: Path, job_queue: JobQueue) -> None:
    trigger, first = await a_trigger(tmp_path, job_queue, folders=2)

    assert await trigger("root-2", "admin-1", True) is False
    assert await _waiting(job_queue, BENCHMARK) == 0
    assert first.run is None


async def test_a_measured_device_does_not_queue_it(tmp_path: Path, job_queue: JobQueue) -> None:
    """A library restored with a measurement for this hardware is a measured device too."""
    trigger, _first = await a_trigger(
        tmp_path, job_queue, measured=True, on=KNOWN_SHARE, remote=True
    )

    assert await trigger("root-1", "admin-1", True) is False
    assert await _waiting(job_queue, BENCHMARK) == 0


async def test_nobody_s_press_and_a_run_already_waiting_do_not_queue_it(
    tmp_path: Path, job_queue: JobQueue
) -> None:
    trigger, _first = await a_trigger(tmp_path, job_queue)

    assert await trigger("root-1", None, True) is False
    assert await trigger("root-1", "admin-1", True) is True
    assert await trigger("root-1", "admin-1", True) is False, (
        "one run, not two measuring each other"
    )
    assert await _waiting(job_queue, BENCHMARK) == 1


@pytest.mark.parametrize(
    ("on", "remote"), [(NEW_SHARE, True), ("C:\\", False)], ids=["share", "local disk"]
)
async def test_a_folder_on_a_storage_never_measured_queues_a_run_of_that_storage(
    tmp_path: Path, job_queue: JobQueue, on: str, remote: bool
) -> None:
    """A device measured with no folder measures a storage when its first folder is added."""
    trigger, first = await a_trigger(
        tmp_path, job_queue, folders=2, measured=True, on=on, remote=remote
    )

    assert await trigger("root-2", "admin-1", True) is True

    assert first.run is not None
    assert (first.run.state, first.run.said) == ("waiting", benchmark.MEASURING_STORAGE)
    job = await job_queue.get(first.run.job_id)
    assert job is not None
    assert job.payload == {"root_id": "root-2", "scan": True, STORAGE: True}


@pytest.mark.parametrize(
    ("on", "remote", "root"),
    [(KNOWN_SHARE, True, "root-2"), (NEW_SHARE, True, "gone")],
    ids=["share measured before", "folder gone"],
)
async def test_a_folder_on_a_measured_storage_or_gone_queues_nothing(
    tmp_path: Path, job_queue: JobQueue, on: str, remote: bool, root: str
) -> None:
    trigger, first = await a_trigger(tmp_path, job_queue, measured=True, on=on, remote=remote)

    assert await trigger(root, "admin-1", True) is False
    assert await _waiting(job_queue, BENCHMARK) == 0 and first.run is None


async def test_a_press_queues_one_whole_run_even_behind_a_storage_run(
    job_queue: JobQueue,
) -> None:
    register_handler(BENCHMARK, _nothing, name="Benchmarking this device")
    await job_queue.enqueue(BENCHMARK, {"root_id": "r", "scan": True, STORAGE: True})

    asked = await ask_for_run(job_queue, requested_by="admin-1")

    assert asked is not None
    job = await job_queue.get(asked)
    assert job is not None and job.payload == {PRESSED: True} and job.requested_by == "admin-1"
    assert await ask_for_run(job_queue, requested_by="admin-1") is None, "one whole run coming"
    assert await _waiting(job_queue, BENCHMARK) == 2


# --- through the route: the order against the scan -----------------------------------------------


@pytest.fixture
def idle_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_folder_benchmarks: None
) -> Iterator[TestClient]:
    """The application with no workers, so what is WAITING in the queue can be read."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()

    async def _no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", _no_workers)
    app: FastAPI = create_app()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def _sign_in(client: TestClient, role: str) -> str:
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        db_path, role=role, username=f"bench-{role}", password="Bench-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def _queued(client: TestClient) -> list[tuple[str, str]]:
    db = client.app.state.database.path  # type: ignore[attr-defined]
    with sqlite3.connect(db) as connection:  # nosemgrep: sift-no-database-driver-outside-kernel
        return [
            (str(kind), str(state))
            for kind, state in connection.execute(
                "SELECT type, state FROM jobs WHERE type IN (?, 'scan') ORDER BY created_at, id",
                (BENCHMARK,),
            )
        ]


def test_the_first_folder_s_scan_waits_behind_the_benchmark_and_a_second_s_does_not(
    idle_app: TestClient, tmp_path: Path
) -> None:
    """Today a folder's scan is queued as it is added. The first folder on a device never measured
    queues the benchmark instead, and its scan is queued once the benchmark has settled, so the
    scan reads under the settings the benchmark chose. A second folder scans at once."""
    _sign_in(idle_app, "admin")
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()

    first = idle_app.post(ROOTS, json={"abs_path": str(tmp_path / "one")})
    assert first.status_code == 201, first.text
    assert _queued(idle_app) == [(BENCHMARK, "queued")], "the benchmark, and no scan yet"

    second = idle_app.post(ROOTS, json={"abs_path": str(tmp_path / "two")})
    assert second.status_code == 201, second.text
    assert _queued(idle_app) == [(BENCHMARK, "queued"), ("scan", "queued")]

    run = idle_app.get("/api/performance/benchmark").json()
    assert run["state"] == "waiting" and run["said"] == benchmark.RUNNING

    # Settled (here, stopped from Activity): the first folder's own scan is queued after it.
    cancelled = idle_app.post(f"/api/jobs/{run['job_id']}/cancel")
    assert cancelled.status_code == 204, cancelled.text
    assert [one for one in _queued(idle_app) if one[0] == "scan"] == [
        ("scan", "queued"),
        ("scan", "queued"),
    ]
    stopped = idle_app.get("/api/performance/benchmark").json()
    assert stopped["state"] == "failed"
    assert stopped["said"] == benchmark.failed_sentence(benchmark.STOPPED)


def test_a_press_queues_the_run_and_reads_as_going_while_it_waits(idle_app: TestClient) -> None:
    _sign_in(idle_app, "admin")

    first = idle_app.post("/api/performance/self-test")
    again = idle_app.post("/api/performance/self-test")

    assert first.status_code == again.status_code == 202
    assert first.json()["running"] is True, "queued and not begun is still going"
    assert first.json()["notes"] == []
    assert _queued(idle_app) == [(BENCHMARK, "queued")], "one run, not two"


def test_a_guest_cannot_add_a_folder_so_never_queues_it(
    idle_app: TestClient, tmp_path: Path
) -> None:
    _sign_in(idle_app, "guest")
    (tmp_path / "one").mkdir()

    refused = idle_app.post(ROOTS, json={"abs_path": str(tmp_path / "one")})

    assert refused.status_code == 403
    assert _queued(idle_app) == []
    assert idle_app.get("/api/performance/benchmark").status_code == 403


# --- the run, the apply and the record ------------------------------------------------------------


class Measures:
    """A runner whose measurement is planted: what is checked is what the run does with it."""

    def __init__(self, measurement: Measurement | None) -> None:
        self.measurement = measurement
        self.state = selftest.SelfTest()
        self.is_measured = False
        self.notes: list[str] = []
        self.lengths: dict[str, float] = {}

    async def measured(self) -> bool:
        return self.is_measured

    async def lasted(self, kind: str) -> float | None:
        return self.lengths.get(kind)

    async def keep_length(self, kind: str, seconds: float) -> None:
        self.lengths[kind] = seconds

    def recommend(
        self, measurement: Measurement, *, current: dict[str, int]
    ) -> list[selftest.Recommendation]:
        return selftest.recommend(measurement, current=current)

    async def run(self) -> None:
        self.state.measurement = self.measurement
        self.is_measured = self.measurement is not None and self.measurement.failed is None

    async def measure_storage(self, storage: str) -> StorageCurve | None:
        self.state.measurement = self.measurement
        found = self.measurement.storages if self.measurement is not None else ()
        return next((one for one in found if one.storage == storage), None)


async def a_run(
    temp_db: Database,
    job_queue: JobQueue,
    measurement: Measurement | None,
    runner: Measures | None = None,
    payload: dict[str, Any] | None = None,
    on: str = KNOWN_SHARE,
) -> tuple[FirstBenchmark, SettingsService, list[set[str]], Any]:
    register_handler(BENCHMARK, _nothing, name="Benchmarking this device")
    await job_queue.enqueue(
        BENCHMARK, payload or {"root_id": "root-1", "scan": True}, requested_by=None
    )
    claimed = await job_queue.claim("worker-one")
    assert claimed is not None
    hub = SettingsService(temp_db)
    told: list[set[str]] = []

    async def notify(changed: set[str]) -> None:
        told.append(changed)

    first = FirstBenchmark()
    runner = runner or Measures(measurement)

    async def storage_of(root_id: str) -> StorageToMeasure | None:
        if root_id == "gone":
            return None
        return StorageToMeasure(storage=on, label="Clips", remote=True, roots=())

    async def go() -> None:
        from sift.slices.performance.runner import current_settings

        await run_benchmark(
            JobContext(job=claimed, worker_id="worker-one", queue=job_queue),
            runner=runner,  # type: ignore[arg-type]
            first=first,
            saves=hub,
            current=lambda: current_settings(hub),
            notify=notify,
            storage_of=storage_of,
        )

    return first, hub, told, go


async def _receipts(temp_db: Database) -> list[dict[str, Any]]:
    rows = await temp_db.fetch_all(
        "SELECT id, title, payload, actor_kind, actor_id, verb FROM workbench_decisions"
        " WHERE queue = ?",
        (RECEIPTS,),
    )
    return [dict(row) for row in rows]


async def test_the_automatic_run_sets_what_it_found_as_one_receipt_sift_took(
    temp_db: Database, job_queue: JobQueue
) -> None:
    first, hub, told, go = await a_run(temp_db, job_queue, a_measurement())

    await go()

    expected = [
        one for one in selftest.recommend(a_measurement(), current={}) if one.changes_anything
    ]
    for one in expected:
        assert await hub.get_app(one.key) == one.suggested, one.key
    assert told == [{one.key for one in expected}], "the reactions a press would ask for"
    receipts = await _receipts(temp_db)
    assert len(receipts) == 1, "one act, one line"
    line = receipts[0]
    assert (line["actor_kind"], line["actor_id"], line["verb"]) == ("sift", VIA_BENCHMARK, "edited")
    assert line["title"].startswith("Sift set how many tasks run at the same time to ")
    assert "(it was automatic)" in line["title"]
    assert line["title"].endswith("from the benchmark of this device")
    assert first.run is not None and first.run.state == "set"
    assert first.run.said == benchmark.set_sentence(len(expected))
    assert first.run.receipt_id == line["id"]
    assert [one.after for one in first.run.changes] == [one.suggested for one in expected]


async def test_the_receipt_is_worded_from_what_it_recorded(
    temp_db: Database, job_queue: JobQueue
) -> None:
    """History draws the line from the changes the receipt recorded, each setting by the name it
    has now, rather than from the title stored on the day. A row holding no change this build can
    read keeps its stored title (None)."""
    _first, hub, _told, go = await a_run(temp_db, job_queue, a_measurement())
    await go()
    (line,) = await _receipts(temp_db)

    async def notify(keys: set[str]) -> None:
        return None

    receipts = BenchmarkReceipts(hub, notify)
    recorded = Recorded(
        id=line["id"], queue=RECEIPTS, payload=line["payload"], title="", detail="", decided_at=0
    )
    worded = receipts.worded(recorded)

    assert worded is not None
    doer, said = worded.said
    assert doer is DOER
    assert isinstance(said, str)
    # By the name the setting has on Settings today, which is what a reader can go and find.
    assert said.startswith(" set tasks at the same time to ")
    assert "(it was automatic)" in said
    assert said.endswith(" from the benchmark of this device")
    unread = Recorded(id="x", queue=RECEIPTS, payload="{}", title="", detail="", decided_at=0)
    assert receipts.worded(unread) is None


async def test_undo_puts_back_each_value_nobody_changed_since(
    temp_db: Database, job_queue: JobQueue
) -> None:
    _first, hub, _told, go = await a_run(temp_db, job_queue, a_measurement())
    await go()
    admin = await create_user(temp_db, Role.ADMIN)
    # Somebody changes one of them by hand after the run: that value is theirs now.
    await hub.apply(admin, {selftest.GENERATION_LIMIT_KEY: 1})
    told: list[set[str]] = []

    async def notify(changed: set[str]) -> None:
        told.append(changed)

    line = (await _receipts(temp_db))[0]
    undone = await BenchmarkReceipts(hub, notify).reverse(admin, line["id"], line["payload"])

    assert undone is True
    assert await hub.get_app(selftest.WORKER_COUNT_KEY) == 0, "back to automatic"
    assert await hub.get_app(selftest.GENERATION_LIMIT_KEY) == 1, "changed since, so kept"
    assert selftest.GENERATION_LIMIT_KEY not in told[0]
    payload = json.loads(line["payload"])
    assert {one["key"] for one in payload["changes"]} >= {selftest.WORKER_COUNT_KEY}


async def test_a_pressed_run_writes_no_setting(temp_db: Database, job_queue: JobQueue) -> None:
    """Pressed on the Performance screen, the run suggests and waits for Apply."""
    first, hub, told, go = await a_run(temp_db, job_queue, a_measurement(), payload={PRESSED: True})

    await go()

    assert first.run is None, "the toasts are the automatic run's"
    assert told == [] and await _receipts(temp_db) == []
    assert await hub.get_app(selftest.WORKER_COUNT_KEY) == 0
    [job] = (await job_queue.list(job_type=BENCHMARK)).jobs
    assert job.note == benchmark.SUGGESTED


async def test_a_pressed_run_that_could_not_measure_fails_with_why(
    temp_db: Database, job_queue: JobQueue
) -> None:
    failed = Measurement(cores=8, failed="the video encoder couldn't be run")
    _first, _hub, _told, go = await a_run(temp_db, job_queue, failed, payload={PRESSED: True})

    with pytest.raises(JobFailedPermanently) as raised:
        await go()

    assert str(raised.value) == benchmark.failed_sentence("the video encoder couldn't be run")


async def test_a_storage_run_keeps_its_number_and_sets_nothing_while_shares_read_as_measured(
    temp_db: Database, job_queue: JobQueue
) -> None:
    first, hub, told, go = await a_run(
        temp_db,
        job_queue,
        a_measurement(),
        payload={"root_id": "root-2", "scan": True, STORAGE: True},
    )

    await go()

    assert first.run is not None and first.run.state == "agreed"
    assert told == []
    assert await hub.get_app(selftest.SHARE_READS_KEY) == 0
    assert await hub.get_app(selftest.WORKER_COUNT_KEY) == 0, "the device's own numbers stay"
    assert await _receipts(temp_db) == []


async def test_a_storage_run_of_a_local_disk_ends_done_with_nothing_suggested(
    temp_db: Database, job_queue: JobQueue
) -> None:
    disk = StorageCurve(
        storage="C:\\",
        label="Clips",
        remote=False,
        levels=(
            StorageLevel(at_once=1, seconds=1.0, bytes_read=400 << 20),
            StorageLevel(at_once=2, seconds=1.0, bytes_read=410 << 20),
        ),
    )
    runner = Measures(Measurement(cores=8, storages=(disk,)))
    first, _hub, told, go = await a_run(
        temp_db,
        job_queue,
        None,
        runner=runner,
        payload={"root_id": "root-2", "scan": True, STORAGE: True},
        on="C:\\",
    )

    await go()

    assert first.run is not None
    assert (first.run.state, first.run.said) == ("agreed", benchmark.STORAGE_KEPT)
    assert told == [] and await _receipts(temp_db) == []
    assert benchmark.STORAGE in runner.lengths, "its length is kept for the next run's note"


@pytest.mark.parametrize(
    ("storage", "root", "why"),
    [
        ("\\\\nas\\b\\", "root-2", "too few"),
        (NEW_SHARE, "root-2", benchmark.GONE),
        (NEW_SHARE, "gone", benchmark.GONE),
    ],
    ids=["could not read it", "no folder on it", "its folder removed"],
)
async def test_a_storage_run_that_could_not_measure_says_why(
    temp_db: Database, job_queue: JobQueue, storage: str, root: str, why: str
) -> None:
    first, _hub, _told, go = await a_run(
        temp_db,
        job_queue,
        a_measurement(),
        payload={"root_id": root, "scan": True, STORAGE: True},
        on=storage,
    )

    with pytest.raises(JobFailedPermanently):
        await go()

    assert first.run is not None and first.run.said == benchmark.failed_sentence(why)


async def test_a_storage_run_on_a_share_that_recommends_nothing_is_too_busy(
    temp_db: Database, job_queue: JobQueue
) -> None:
    empty = StorageCurve(storage=NEW_SHARE, label="Clips", remote=True)
    first, _hub, _told, go = await a_run(
        temp_db,
        job_queue,
        Measurement(cores=8, storages=(empty,)),
        payload={"root_id": "root-2", "scan": True, STORAGE: True},
        on=NEW_SHARE,
    )

    with pytest.raises(JobFailedPermanently):
        await go()

    assert first.run is not None
    assert first.run.said == benchmark.failed_sentence(benchmark.TOO_BUSY)


async def test_a_run_that_could_not_measure_says_why_and_sets_nothing(
    temp_db: Database, job_queue: JobQueue
) -> None:
    failed = Measurement(cores=8, failed="the video encoder couldn't be run")
    first, hub, told, go = await a_run(temp_db, job_queue, failed)

    with pytest.raises(JobFailedPermanently) as raised:
        await go()

    said = benchmark.failed_sentence("the video encoder couldn't be run")
    assert str(raised.value) == said
    assert said == (
        "Sift couldn't benchmark this device because the video encoder couldn't be run. "
        "You can run it from Settings > Performance."
    )
    assert first.run is not None and (first.run.state, first.run.said) == ("failed", said)
    assert told == [] and await _receipts(temp_db) == []
    assert await hub.get_app(selftest.WORKER_COUNT_KEY) == 0


async def test_a_run_too_busy_to_measure_says_so(temp_db: Database, job_queue: JobQueue) -> None:
    busy = Measurement(cores=8)  # nothing finished in time: no level to recommend from
    first, _hub, _told, go = await a_run(temp_db, job_queue, busy)

    with pytest.raises(JobFailedPermanently):
        await go()

    assert first.run is not None
    assert first.run.said == benchmark.failed_sentence(benchmark.TOO_BUSY)


async def test_a_device_measured_while_the_run_waited_is_not_measured_again(
    temp_db: Database, job_queue: JobQueue
) -> None:
    """A press on the Performance screen measured it first: the run says so and sets nothing."""
    measured = Measures(a_measurement())
    measured.is_measured = True
    first, hub, told, go = await a_run(temp_db, job_queue, None, runner=measured)

    await go()

    assert first.run is not None
    assert (first.run.state, first.run.said) == ("already", benchmark.ALREADY)
    assert told == [] and await _receipts(temp_db) == []
    assert await hub.get_app(selftest.WORKER_COUNT_KEY) == 0


class Raises(Measures):
    """A runner whose measuring breaks part way, as an encoder that crashes would."""

    async def run(self) -> None:
        raise RuntimeError("the encoder went away")


@pytest.mark.parametrize("runner", [Raises(None), Measures(None)], ids=["raised", "nothing"])
async def test_a_run_that_broke_or_left_no_measurement_says_something_went_wrong(
    temp_db: Database, job_queue: JobQueue, runner: Measures
) -> None:
    """Whether the measuring raised or simply came back with nothing, the run ends failed with a
    sentence a person can act on, and nothing is set."""
    first, hub, told, go = await a_run(temp_db, job_queue, None, runner=runner)

    with pytest.raises(JobFailedPermanently) as raised:
        await go()

    said = benchmark.failed_sentence(benchmark.WENT_WRONG)
    assert str(raised.value) == said
    assert first.run is not None and (first.run.state, first.run.said) == ("failed", said)
    assert told == [] and await _receipts(temp_db) == []
    assert await hub.get_app(selftest.WORKER_COUNT_KEY) == 0


async def test_a_run_whose_settings_already_suit_the_device_changes_nothing_and_says_so(
    temp_db: Database, job_queue: JobQueue
) -> None:
    first, hub, told, go = await a_run(temp_db, job_queue, a_measurement())
    admin = await create_user(temp_db, Role.ADMIN)
    await hub.apply(
        admin, {one.key: one.suggested for one in selftest.recommend(a_measurement(), current={})}
    )

    await go()

    assert first.run is not None
    assert (first.run.state, first.run.said) == ("agreed", benchmark.AGREED)
    assert told == [] and await _receipts(temp_db) == []


async def test_a_run_that_is_no_longer_queued_starts_no_scan(job_queue: JobQueue) -> None:
    """Cleared from Activity before it settled: there is no job to say which folder to read."""
    scans: list[tuple[str, str | None]] = []

    async def scan(root_id: str, by: str | None) -> None:
        scans.append((root_id, by))

    await ThenScan(queue=job_queue, first=FirstBenchmark(), scan=scan)("no-such-job")

    assert scans == []


async def test_a_receipt_this_build_cannot_read_is_left_with_its_stored_title(
    temp_db: Database, job_queue: JobQueue
) -> None:
    """A payload that is not JSON, values that are not, a setting this build does not know or a
    value that is not a number: each is None, so History keeps the title written on the day."""
    hub = SettingsService(temp_db)

    async def notify(keys: set[str]) -> None:
        return None

    receipts = BenchmarkReceipts(hub, notify)
    known = selftest.WORKER_COUNT_KEY
    payloads = [
        "not json",
        json.dumps({"changes": [{"key": known, "before": "{", "after": "2"}]}),
        json.dumps({"changes": [{"key": "no.such.setting", "before": "0", "after": "2"}]}),
        json.dumps({"changes": [{"key": known, "before": "0", "after": '"two"'}]}),
    ]

    for payload in payloads:
        recorded = Recorded(
            id="x", queue=RECEIPTS, payload=payload, title="", detail="", decided_at=0
        )
        assert receipts.worded(recorded) is None, payload
    admin = await create_user(temp_db, Role.ADMIN)
    assert await receipts.pictures_of(admin, payloads[1]) == ()


async def test_undo_with_every_value_changed_since_puts_nothing_back(
    temp_db: Database, job_queue: JobQueue
) -> None:
    _first, hub, _told, go = await a_run(temp_db, job_queue, a_measurement())
    await go()
    admin = await create_user(temp_db, Role.ADMIN)
    line = (await _receipts(temp_db))[0]
    # Each put back by hand to what it was, so none still says what Sift set.
    changed = {
        one["key"]: json.loads(one["before"]) for one in json.loads(line["payload"])["changes"]
    }
    await hub.apply(admin, changed)
    told: list[set[str]] = []

    async def notify(keys: set[str]) -> None:
        told.append(keys)

    undone = await BenchmarkReceipts(hub, notify).reverse(admin, line["id"], line["payload"])

    assert undone is False and told == []
    for key, value in changed.items():
        assert await hub.get_app(key) == value, key


async def test_the_scan_follows_the_settled_run_named_for_who_added_the_folder(
    job_queue: JobQueue,
) -> None:
    register_handler(BENCHMARK, _nothing, name="Benchmarking this device")
    job_id = await job_queue.enqueue(
        BENCHMARK, {"root_id": "root-1", "scan": True}, requested_by="admin-1"
    )
    scans: list[tuple[str, str | None]] = []

    async def scan(root_id: str, by: str | None) -> None:
        scans.append((root_id, by))

    first = FirstBenchmark()
    then = ThenScan(queue=job_queue, first=first, scan=scan)

    await then(job_id)

    assert scans == [("root-1", "admin-1")]


async def test_a_folder_added_without_a_scan_gets_none_after_the_run(
    job_queue: JobQueue,
) -> None:
    """The setup steps add a folder without reading it yet; the benchmark does not read it either."""
    register_handler(BENCHMARK, _nothing, name="Benchmarking this device")
    job_id = await job_queue.enqueue(
        BENCHMARK, {"root_id": "root-1", "scan": False}, requested_by="admin-1"
    )
    scans: list[tuple[str, str | None]] = []

    async def scan(root_id: str, by: str | None) -> None:
        scans.append((root_id, by))

    await ThenScan(queue=job_queue, first=FirstBenchmark(), scan=scan)(job_id)

    assert scans == []


# --- the queue to itself, and a real first folder with files -------------------------------------


async def test_an_ordinary_job_queued_beside_a_running_benchmark_stays_queued(
    job_queue: JobQueue,
) -> None:
    """The benchmark measures the machine, so nothing runs beside it: a job queued ahead of it
    waits, and so does one queued while it runs, until it has settled."""
    register_handler(BENCHMARK, _nothing, name="Benchmarking this device", exclusive=True)
    register_handler("ordinary", _nothing, name="Doing something ordinary")
    ahead = await job_queue.enqueue("ordinary", {})
    bench = await job_queue.enqueue(BENCHMARK, {})

    first = await job_queue.claim("worker-one")
    assert first is not None and first.id == bench, "it goes first, ahead of older work"
    assert await job_queue.claim("worker-two") is None, "nothing beside it while it runs"
    await job_queue.enqueue("ordinary", {"later": True})
    assert await job_queue.claim("worker-two") is None

    await job_queue.complete(bench, "worker-one")
    after = await job_queue.claim("worker-two")
    assert after is not None and after.id == ahead


def _files(client: TestClient) -> int:
    db = client.app.state.database.path  # type: ignore[attr-defined]
    with sqlite3.connect(db) as connection:  # nosemgrep: sift-no-database-driver-outside-kernel
        return int(connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0])


def _worked(client: TestClient) -> dict[str, str]:
    """Every job other than the benchmark that a worker has taken (running, done or failed), by id."""
    db = client.app.state.database.path  # type: ignore[attr-defined]
    with sqlite3.connect(db) as connection:  # nosemgrep: sift-no-database-driver-outside-kernel
        return {
            str(job_id): f"{kind}:{state}"
            for job_id, kind, state in connection.execute(
                "SELECT id, type, state FROM jobs"
                " WHERE type != ? AND state NOT IN ('queued', 'paused')",
                (BENCHMARK,),
            )
        }


@pytest.fixture
def booted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_folder_benchmarks: None
) -> Iterator[tuple[TestClient, threading.Event]]:
    """The application with its workers, and a planted measurement the test lets end."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    release = threading.Event()

    async def measuring(**_kwargs: object) -> Measurement:
        await asyncio.to_thread(release.wait, 60)
        return a_measurement()

    async def no_clip(*_args: object) -> Path:
        raise OSError("no clip here")

    async def apart(*_args: object, **_kwargs: object) -> measure_together.Together:
        return measure_together.Together()

    monkeypatch.setattr(selftest, "measure", measuring)
    monkeypatch.setattr(measure_encoder, "build_source", no_clip)
    monkeypatch.setattr(measure_together, "run", apart)
    with TestClient(create_app()) as client:
        try:
            yield client, release
        finally:
            # Let the planted run end before the application stops, or a failing test waits on it.
            release.set()
    get_settings.cache_clear()


def _until(check: Callable[[], bool], seconds: float = 30) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return False


def test_nothing_of_the_first_folder_is_read_until_the_benchmark_settles(
    booted: tuple[TestClient, threading.Event], tmp_path: Path
) -> None:
    """The add also queues `library_reconcile`, which beside the benchmark would import the folder
    under it and measure the device busy."""
    client, release = booted
    _sign_in(client, "admin")
    folder = tmp_path / "first"
    folder.mkdir()
    for index in range(3):
        (folder / f"still-{index}.png").write_bytes(png_bytes(bytes([0, index, 7, 9])))

    before = set(_worked(client))  # the boot's own upkeep, taken before the folder was added
    added = client.post(ROOTS, json={"abs_path": str(folder)})
    assert added.status_code == 201, added.text
    assert _until(lambda: client.get("/api/performance/benchmark").json()["state"] == "running")

    held = client.get("/api/performance/benchmark").json()
    assert held["held"] == benchmark.HELD
    time.sleep(2)  # long enough for a free worker to have taken the reconcile, were it allowed
    assert _files(client) == 0, "nothing of the folder is read while the device is measured"
    beside = {key: one for key, one in _worked(client).items() if key not in before}
    assert beside == {}, "no job is taken beside the benchmark"

    release.set()
    assert _until(lambda: client.get("/api/performance/benchmark").json()["state"] == "set")
    assert _until(lambda: _files(client) == 3), "the files arrive once the run has settled"
    assert client.get("/api/performance/benchmark").json()["held"] is None


# --- the queue drained ---------------------------------------------------------------------------


async def test_a_run_pauses_what_is_running_and_starts_it_again_after(
    job_queue: JobQueue,
) -> None:
    """A job that hears the pause stops and is started again after; one that does not is named,
    and the seconds the run waited are kept."""
    register_handler(BENCHMARK, _nothing, name="Benchmarking this device", exclusive=True)
    register_handler("listens", _nothing, name="Listening for a pause")
    register_handler("ignores", _nothing, name="Ignoring a pause")
    listens = await job_queue.enqueue("listens", {})
    ignores = await job_queue.enqueue("ignores", {})
    for worker in ("worker-a", "worker-b"):
        assert await job_queue.claim(worker) is not None
    bench_id = await job_queue.enqueue(BENCHMARK, {PRESSED: True})
    bench = await job_queue.claim("worker-c")
    assert bench is not None and bench.id == bench_id
    context = JobContext(job=bench, worker_id="worker-c", queue=job_queue)
    listener = await job_queue.get(listens)
    assert listener is not None and listener.claimed_by is not None

    async def hears_it() -> None:
        await asyncio.sleep(0.3)
        await job_queue.pause_running(listens, str(listener.claimed_by), "asked")

    heard = asyncio.create_task(hears_it())
    drained = await drain(context, wait=1.0)
    await heard

    assert sorted(drained.asked) == sorted([listens, ignores])
    assert drained.paused == 1 and drained.kept_running == ["Ignoring a pause"]
    assert 0.3 <= drained.waited < 3
    assert drained.said() == [
        f"Sift paused 1 task while it measured, waited {round(drained.waited)} s for the ones "
        "running to stop, and started it again after.",
        "1 task couldn't be paused and ran beside it: Ignoring a pause.",
    ]

    await undrain(job_queue, drained)

    started_again = await job_queue.get(listens)
    still = await job_queue.get(ignores)
    assert started_again is not None and started_again.state is JobState.QUEUED
    assert still is not None and still.state is JobState.RUNNING, "its pause is withdrawn"


async def test_what_a_run_paused_is_started_by_the_boot_after_a_run_cut_short(
    job_queue: JobQueue,
) -> None:
    register_handler(BENCHMARK, _nothing, name="Benchmarking this device", exclusive=True)
    register_handler("listens", _nothing, name="Listening for a pause")
    listens = await job_queue.enqueue("listens", {})
    assert await job_queue.claim("worker-a") is not None
    await job_queue.enqueue(BENCHMARK, {PRESSED: True})
    bench = await job_queue.claim("worker-c")
    assert bench is not None

    await drain(JobContext(job=bench, worker_id="worker-c", queue=job_queue), wait=0)
    await job_queue.pause_running(listens, "worker-a", "asked")

    assert await job_queue.resume_after_benchmark() == [listens]


async def test_a_run_pauses_the_waiting_work_it_holds_back_and_its_end_starts_it_again(
    job_queue: JobQueue,
) -> None:
    register_handler(BENCHMARK, _nothing, name="Benchmarking this device", exclusive=True)
    register_handler("ordinary", _nothing, name="Doing something ordinary")
    waiting = await job_queue.enqueue("ordinary", {"n": 1})
    later = await job_queue.enqueue("ordinary", {"n": 2}, run_after=int(time.time()) + 3600)
    theirs = await job_queue.enqueue("ordinary", {"n": 3})
    assert await job_queue.pause(theirs)
    await job_queue.enqueue(BENCHMARK, {PRESSED: True})
    bench = await job_queue.claim("worker-c")
    assert bench is not None

    drained = await drain(JobContext(job=bench, worker_id="worker-c", queue=job_queue), wait=0)

    async def state(job_id: str) -> JobState | None:
        row = await job_queue.get(job_id)
        return None if row is None else row.state

    assert drained.paused == 1 and await state(waiting) is JobState.PAUSED
    assert drained.said() == ["Sift paused 1 task while it measured and started it again after."]
    assert drained.holding() == "Sift paused 1 task until it's done."
    assert await state(later) is JobState.QUEUED
    await undrain(job_queue, drained)
    assert await state(waiting) is JobState.QUEUED
    assert await state(theirs) is JobState.PAUSED, "a person's pause is theirs"


def test_the_note_says_how_long_the_last_run_of_its_kind_took_here() -> None:
    assert benchmark.running_said(storage=False, last=724.0) == (
        "Benchmarking this device so Sift can make the best use of it. "
        "The last one here took about 12 minutes."
    )
    assert benchmark.running_said(storage=True, last=1.2).endswith("took under a minute.")
    assert benchmark.running_said(storage=False, last=None) == benchmark.RUNNING
    assert "One to three minutes" not in benchmark.RUNNING


@pytest.mark.parametrize("finished", [True, False], ids=["finished", "failed"])
async def test_a_finished_run_keeps_its_length_and_one_that_failed_does_not(
    temp_db: Database, job_queue: JobQueue, finished: bool
) -> None:
    failed = None if finished else "the video encoder couldn't be run"
    runner = Measures(a_measurement() if finished else Measurement(cores=8, failed=failed))
    _first, _hub, _told, go = await a_run(temp_db, job_queue, None, runner=runner)
    try:
        await go()
    except JobFailedPermanently:
        assert not finished
    assert (benchmark.WHOLE_RUN in runner.lengths) is finished


async def test_a_run_with_nothing_running_waits_for_nothing(job_queue: JobQueue) -> None:
    register_handler(BENCHMARK, _nothing, name="Benchmarking this device", exclusive=True)
    await job_queue.enqueue(BENCHMARK, {PRESSED: True})
    bench = await job_queue.claim("worker-c")
    assert bench is not None

    drained = await drain(JobContext(job=bench, worker_id="worker-c", queue=job_queue))

    assert drained.asked == [] and drained.said() == [] and drained.waited < 1
