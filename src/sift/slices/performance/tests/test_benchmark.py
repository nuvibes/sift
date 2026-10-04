# SPDX-License-Identifier: AGPL-3.0-or-later
"""The benchmark Sift runs by itself on the first library folder, and what it sets.

What is checked: the first folder on a device never measured queues it and every other case does
not (a second folder, a measured device, a run already waiting, nobody's press, a guest); the
folder's scan waits behind it and is queued once it settles; the automatic run sets what it found
through the settings' own door, as one receipt on History whose Undo puts the values back, where a
pressed run writes nothing; and a run that could not measure says why in the sentence the toasts
read.
"""

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
    register_handler,
)
from sift.kernel.jobs.worker_pool import WorkerPool
from sift.kernel.vocabulary import VIA_BENCHMARK
from sift.kernel.workbench import DOER, Recorded
from sift.main import create_app
from sift.slices.performance import benchmark, selftest
from sift.slices.performance.benchmark import (
    BENCHMARK,
    RECEIPTS,
    BenchmarkReceipts,
    FirstBenchmark,
    FirstFolder,
    ThenScan,
    run_benchmark,
)
from sift.slices.performance.rates import MachineRates
from sift.slices.performance.selftest import Measurement
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


async def a_trigger(
    tmp_path: Path, queue: JobQueue, *, folders: int = 1, measured: bool = False
) -> tuple[FirstFolder, FirstBenchmark]:
    register_handler(BENCHMARK, _nothing, name="Benchmarking this device")
    store = await a_store(tmp_path)
    runner = a_runner(tmp_path, store)
    if measured:
        await store.save(MachineRates.from_measurement(a_machine().profile, a_measurement(), now=0))
    first = FirstBenchmark()
    return FirstFolder(runner=runner, queue=queue, roots=Folders(folders), first=first), first


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
    trigger, _first = await a_trigger(tmp_path, job_queue, measured=True)

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

    async def measured(self) -> bool:
        return self.is_measured

    async def measure(self) -> None:
        self.state.measurement = self.measurement
        self.is_measured = self.measurement is not None and self.measurement.failed is None


async def a_run(
    temp_db: Database,
    job_queue: JobQueue,
    measurement: Measurement | None,
    runner: Measures | None = None,
) -> tuple[FirstBenchmark, SettingsService, list[set[str]], Any]:
    register_handler(BENCHMARK, _nothing, name="Benchmarking this device")
    await job_queue.enqueue(BENCHMARK, {"root_id": "root-1", "scan": True}, requested_by=None)
    claimed = await job_queue.claim("worker-one")
    assert claimed is not None
    hub = SettingsService(temp_db)
    told: list[set[str]] = []

    async def notify(changed: set[str]) -> None:
        told.append(changed)

    first = FirstBenchmark()
    runner = runner or Measures(measurement)

    async def go() -> None:
        from sift.slices.performance.runner import current_settings

        await run_benchmark(
            JobContext(job=claimed, worker_id="worker-one", queue=job_queue),
            runner=runner,  # type: ignore[arg-type]
            first=first,
            saves=hub,
            current=lambda: current_settings(hub),
            notify=notify,
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

    expected = selftest.recommend(a_measurement(), current={})
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


async def test_a_pressed_run_writes_no_setting(
    tmp_path: Path, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pressed on the Performance screen, the run suggests and waits for Apply."""

    async def instant(**_kwargs: object) -> Measurement:
        return a_measurement()

    monkeypatch.setattr(selftest, "measure", instant)
    await temp_db.initialize_schema()
    hub = SettingsService(temp_db)
    runner = a_runner(tmp_path, await a_store(tmp_path))

    await runner.measure()

    assert runner.state.recommendations, "it suggests"
    assert await hub.get_app(selftest.WORKER_COUNT_KEY) == 0
    assert await _receipts(temp_db) == []


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

    async def measure(self) -> None:
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
                "SELECT id, type, state FROM jobs WHERE type != ? AND state != 'queued'",
                (BENCHMARK,),
            )
        }


@pytest.fixture
def booted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_folder_benchmarks: None
) -> Iterator[tuple[TestClient, threading.Event]]:
    """The application with its workers, and a benchmark that measures until the test lets it.

    The measurement is planted (`selftest.measure`), so what is checked is what the queue lets run
    around it; the run itself, the runner, the apply and the scan after it are the real ones.
    """
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    release = threading.Event()

    async def measuring(**_kwargs: object) -> Measurement:
        await asyncio.to_thread(release.wait, 60)
        return a_measurement()

    monkeypatch.setattr(selftest, "measure", measuring)
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
    """The folder's add queues `library_reconcile` as well as the scan, and a reconcile run
    beside the benchmark would import the folder under it and measure the device busy. While the
    run waits or runs, the folder has no file rows and no job but the benchmark has been taken;
    once it settles, the files arrive."""
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
