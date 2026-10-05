# SPDX-License-Identifier: AGPL-3.0-or-later
"""The self-test endpoints, with the measurement stood in for: who may ask, one run at a time,
recommendations compared with settings as they are now, and the scratch directory removed."""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.wiring import part_of_app
from sift.main import create_app
from sift.slices.auth.crypto import derive_csrf_token
from sift.slices.performance import measure_encoder, measure_together, selftest
from sift.slices.performance.rates import MachineRates
from sift.slices.performance.runner import SELF_TEST_RUNNER
from sift.slices.performance.selftest import Level, Measurement, Recommendation, SelfTest
from sift.testing.auth import establish_session

pytestmark = pytest.mark.integration


async def _no_clip(*_args: object) -> Path:
    raise OSError("no clip here")


async def _apart(*_args: object, **_kwargs: object) -> measure_together.Together:
    return measure_together.Together()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The real application, booted against temp directories.

    The route reads the settings hub, the library and the watchdogs off `app.state`, so a hand-built
    application would be a test of the stand-ins. Configured through the environment, which is the
    path a real start-up takes.
    """
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    # A planted measurement goes no further: no GPU ladder, no models, no combined run.
    monkeypatch.setattr(measure_encoder, "build_source", _no_clip)
    monkeypatch.setattr(measure_together, "run", _apart)

    with TestClient(create_app()) as booted:
        yield booted

    get_settings.cache_clear()


def _sign_in(client: TestClient, role: str) -> None:
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    _, token, _ = establish_session(
        db_path, role=role, username=f"tuning-{role}", password="Tuning-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)


def _when_it_has_finished(client: TestClient) -> Any:
    """Read the self-test back once the run it started has ended, waiting rather than sleeping."""
    deadline = time.monotonic() + 10
    while True:
        body = client.get("/api/performance/self-test").json()
        if body["running"] is False:
            return body
        assert time.monotonic() < deadline, "the self-test never finished"
        time.sleep(0.02)


def _as_admin(client: TestClient) -> TestClient:
    """Signed in with the CSRF header, through HTTP, since the client runs its own loop."""
    _sign_in(client, "admin")
    held = client.cookies.get(SESSION_COOKIE_NAME) or ""
    client.headers.update({CSRF_HEADER_NAME: derive_csrf_token(held)})
    return client


def a_level(at_once: int) -> Level:
    return Level(
        at_once=at_once,
        seconds=1.0,
        finished=at_once,
        worst_lag_seconds=0.0,
        worst_wait_seconds=0.0,
    )


def a_finished_run() -> SelfTest:
    """A run that has already happened, planted rather than measured."""
    return SelfTest(
        running=False,
        finished_at=1.0,
        measurement=Measurement(cores=8, levels=(a_level(1), a_level(2))),
        recommendations=[
            Recommendation(
                key=selftest.GENERATION_LIMIT_KEY,
                label="How many previews are built at once",
                current=0,
                suggested=4,
                reason="measured",
            )
        ],
    )


# --- who may ask -----------------------------------------------------------------------------


def test_a_guest_may_not_read_the_measurement(client: TestClient) -> None:
    """Refused at the route, not only hidden in the client: it hands back instance-wide settings."""
    _sign_in(client, "guest")

    assert client.get("/api/performance/self-test").status_code == 403


def test_a_guest_may_not_start_one(client: TestClient) -> None:
    _sign_in(client, "guest")
    held = client.cookies.get(SESSION_COOKIE_NAME) or ""
    client.headers.update({CSRF_HEADER_NAME: derive_csrf_token(held)})

    assert client.post("/api/performance/self-test").status_code == 403


def test_a_signed_out_caller_is_refused(client: TestClient) -> None:
    assert client.get("/api/performance/self-test").status_code == 401


# --- before anything has run ------------------------------------------------------------------


def test_an_instance_that_has_never_measured_says_so_rather_than_failing(
    client: TestClient,
) -> None:
    """The run object is made on first use, so the first read must not be the one that breaks."""
    body = _as_admin(client).get("/api/performance/self-test").json()

    assert body["running"] is False
    assert body["finished"] is False
    assert body["measurement"] is None
    assert body["recommendations"] == []


def test_a_process_that_queued_no_benchmark_of_its_own_says_none(client: TestClient) -> None:
    """What every admin window's toasts read: nothing to say rather than a failure."""
    body = _as_admin(client).get("/api/performance/benchmark").json()

    assert body["state"] == "none" and body["job_id"] is None
    assert body["measured"] is False


# --- starting -----------------------------------------------------------------------------------


def test_starting_answers_at_once_and_leaves_the_work_running(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request returns while the test is still going, or it would read as a hung server."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_measure(**_: object) -> Measurement:
        started.set()
        await release.wait()
        return Measurement(cores=8, levels=(a_level(1),))

    monkeypatch.setattr(selftest, "measure", slow_measure)
    signed_in = _as_admin(client)

    response = signed_in.post("/api/performance/self-test")

    assert response.status_code == 202
    assert response.json()["running"] is True
    # And reading it back while it is going says so rather than blocking on the run.
    assert signed_in.get("/api/performance/self-test").json()["running"] is True

    release.set()


def test_a_second_request_joins_the_run_rather_than_starting_another(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second press is not an error: it answers with the run already in flight."""
    runs = 0
    release = asyncio.Event()
    # Set from the application's own loop and read from this thread, so it has to be a threading
    # primitive rather than an `asyncio` one.
    started = threading.Event()

    async def counting(**_: object) -> Measurement:
        nonlocal runs
        runs += 1
        started.set()
        await release.wait()
        return Measurement(cores=8, levels=(a_level(1),))

    monkeypatch.setattr(selftest, "measure", counting)
    signed_in = _as_admin(client)

    first = signed_in.post("/api/performance/self-test")
    # Waited for: on a loaded machine the run may not have begun yet.
    assert started.wait(10), "the first press never started a measurement"

    second = signed_in.post("/api/performance/self-test")

    assert (first.status_code, second.status_code) == (202, 202)
    assert second.json()["running"] is True
    assert runs == 1, f"the second press started another measurement (runs={runs})"

    release.set()


def test_a_finished_run_reports_what_it_measured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole path, with only the encoder stood in for: start it, let it finish, read it back."""

    async def quick(**_: object) -> Measurement:
        return Measurement(cores=8, levels=(a_level(1), a_level(2)))

    monkeypatch.setattr(selftest, "measure", quick)
    signed_in = _as_admin(client)

    signed_in.post("/api/performance/self-test")
    body = _when_it_has_finished(signed_in)

    assert body["running"] is False
    assert body["finished"] is True
    assert body["measurement"] is not None
    assert body["measurement"]["cores"] == 8
    assert [level["at_once"] for level in body["measurement"]["levels"]] == [1, 2]
    # Something was recommended, which is the only reason the measurement is taken at all.
    assert body["recommendations"] != []


def test_a_run_in_flight_shows_its_rungs_apart_from_the_result_it_will_replace(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rungs finished so far are `progress`; `measurement` stays the last result until the run
    ends, so a screen opened mid-run shows both, and a run not yet measuring shows no rungs."""
    release = asyncio.Event()

    async def one_rung_then_a_pause(**kwargs: object) -> Measurement:
        report = kwargs["report"]
        assert callable(report)
        report(Measurement(cores=8, levels=(a_level(1),)))
        await release.wait()
        return Measurement(cores=8, levels=(a_level(1), a_level(2)))

    runner = part_of_app(client.app, SELF_TEST_RUNNER)  # type: ignore[arg-type]
    runner.state = a_finished_run()
    monkeypatch.setattr(selftest, "measure", one_rung_then_a_pause)
    signed_in = _as_admin(client)

    pressed = signed_in.post("/api/performance/self-test").json()
    assert pressed["running"] is True and pressed["progress"] is None
    assert [level["at_once"] for level in pressed["measurement"]["levels"]] == [1, 2]

    part_way: dict[str, Any] | None = None
    deadline = time.monotonic() + 5
    while part_way is None and time.monotonic() < deadline:
        body = dict(signed_in.get("/api/performance/self-test").json())
        if body["progress"] is not None:
            part_way = body
    portal = client.portal
    assert portal is not None
    portal.call(release.set)

    assert part_way is not None, "a rung that has been measured is visible before the end"
    assert [level["at_once"] for level in part_way["progress"]["levels"]] == [1]
    assert part_way["finished"] is True and part_way["recommendations"] != []
    assert [level["at_once"] for level in part_way["measurement"]["levels"]] == [1, 2]

    finished = _when_it_has_finished(signed_in)
    assert finished["progress"] is None


def test_a_measurement_that_could_not_run_is_reported_as_such(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A test that could not run must not read as a machine that is slow. Those call for
    completely different things from the person reading the screen."""

    async def refused(**_: object) -> Measurement:
        return Measurement(cores=8, failed="Sift could not build a clip to measure with.")

    monkeypatch.setattr(selftest, "measure", refused)
    signed_in = _as_admin(client)

    signed_in.post("/api/performance/self-test")
    body = _when_it_has_finished(signed_in)

    assert body["measurement"]["failed"] == "Sift could not build a clip to measure with."
    assert body["measurement"]["levels"] == []


def test_the_scratch_directory_is_taken_away_even_when_the_run_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It writes clips into a directory of its own under the system temp area. Left behind on every
    failure, a machine somebody measures a few times fills up with them."""
    seen: list[Path] = []

    async def noting(*, workspace: Path, **_: object) -> Measurement:
        seen.append(workspace)
        raise RuntimeError("the encoder fell over")

    monkeypatch.setattr(selftest, "measure", noting)
    signed_in = _as_admin(client)

    signed_in.post("/api/performance/self-test")
    body = _when_it_has_finished(signed_in)

    assert len(seen) == 1
    assert not seen[0].exists(), "the run left its scratch directory behind"
    # And the run is not left claiming to still be going, which would refuse every later attempt.
    assert body["running"] is False


# --- what a recommendation is compared against ----------------------------------------------------


def test_a_recommendation_is_compared_against_the_setting_as_it_is_now(
    client: TestClient,
) -> None:
    """So an applied recommendation stops being offered as a change."""
    signed_in = _as_admin(client)
    signed_in.app.state.self_test_runner.state = a_finished_run()  # type: ignore[attr-defined]

    before = signed_in.get("/api/performance/self-test").json()["recommendations"][0]
    assert before["current"] == 0
    assert before["changes_anything"] is True

    applied = signed_in.put("/api/settings", json={"values": {selftest.GENERATION_LIMIT_KEY: 4}})
    assert applied.status_code in (200, 204), applied.text
    after = signed_in.get("/api/performance/self-test").json()["recommendations"][0]

    assert after["current"] == 4
    assert after["changes_anything"] is False


def test_the_gpus_previews_and_each_model_reach_the_screen(client: TestClient) -> None:
    from sift.slices.performance.measure_encoder import CardCurve, CardLevel
    from sift.slices.performance.measure_models import ModelCurve, ModelLevel

    signed_in = _as_admin(client)
    runner = signed_in.app.state.self_test_runner  # type: ignore[attr-defined]
    runner.state = a_finished_run()
    runner.card = CardCurve(
        encoder="h264_nvenc", decodes_on_card=True, levels=(CardLevel(2, 4.0, 2),)
    )
    runner.models = (
        ModelCurve(
            name="Faces", family="identify", device="nvidia", levels=(ModelLevel(1, 2.0, 1),)
        ),
    )

    body = signed_in.get("/api/performance/self-test").json()

    assert body["card"]["encoder"] == "h264_nvenc" and body["card"]["best_at_once"] == 2
    assert [one["name"] for one in body["models"]] == ["Faces"]


def test_a_setting_that_is_not_a_number_does_not_take_the_screen_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A value from a restored row that slipped the decoder falls back to what the run recorded,
    rather than raising on a page whose whole job is to tell somebody how to fix their settings."""
    signed_in = _as_admin(client)
    signed_in.app.state.self_test_runner.state = a_finished_run()  # type: ignore[attr-defined]
    hub = signed_in.app.state.settings_hub  # type: ignore[attr-defined]

    async def unusable(key: str) -> object:
        return "not a number"

    monkeypatch.setattr(hub, "get_app", unusable)

    body = signed_in.get("/api/performance/self-test").json()

    assert body["recommendations"][0]["current"] == 0


# --- what the shares are actually being read at -----------------------------------------------


def test_the_screen_is_told_how_many_files_a_share_is_read_at_once(client: TestClient) -> None:
    """The number the reads are running on, resolved on the server.

    The setting holds zero for "automatic" and `resolve_share_reads` turns it into the real figure,
    so the effective figure goes on the wire and never the raw one.
    """
    signed_in = _as_admin(client)

    automatic = signed_in.get("/api/performance/self-test").json()

    assert automatic["share_reads_now"] == 0

    applied = signed_in.put("/api/settings", json={"values": {selftest.SHARE_READS_KEY: 5}})
    assert applied.status_code in (200, 204), applied.text

    chosen = signed_in.get("/api/performance/self-test").json()

    assert chosen["share_reads_now"] == 5


# --- the retired offer ---------------------------------------------------------------------------


def test_the_offer_to_measure_is_retired(client: TestClient) -> None:
    """`performance.tune_prompt` ("Offer to measure this device") is a removed setting.

    A write to the key is refused as a key nobody declares, and the answer carries no `offer`
    field for a screen to half-read.
    """
    signed_in = _as_admin(client)

    assert "offer" not in signed_in.get("/api/performance/self-test").json()
    refused = signed_in.put("/api/settings", json={"values": {"performance.tune_prompt": "done"}})
    assert refused.status_code == 400, refused.text


# --- what this machine is ----------------------------------------------------------------------


def test_the_machine_table_reports_what_the_startup_probe_found(client: TestClient) -> None:
    """The Performance pane's table, read live from the hardware report rather than stored.

    Asserted against the report's own answer rather than numbers written out here: the route must
    describe the same machine the rest of the application sizes itself for.
    """
    signed_in = _as_admin(client)

    response = signed_in.get("/api/performance/hardware")

    assert response.status_code == 200
    body = response.json()
    report = client.app.state.hardware  # type: ignore[attr-defined]
    assert body["cpu_count"] == report.cpu_count
    assert body["cpu_model"] == report.cpu_model
    assert body["total_ram_bytes"] == report.total_ram_bytes
    assert body["worker_concurrency"] == report.worker_concurrency
    assert body["gpu_name"] == report.gpu_name
    assert body["gpu_driver"] == report.gpu_driver
    assert body["cuda"] == report.cuda
    assert body["rocm"] == report.rocm
    assert body["transcode_encoders"] == list(report.transcode_encoders)
    assert body["warnings"] == list(report.warnings)


def test_the_machine_table_is_admin_only(client: TestClient) -> None:
    """The make of the processor and the name of the card are how a stranger tells one machine
    from another, which is why this is not part of the health answer."""
    _sign_in(client, "guest")

    assert client.get("/api/performance/hardware").status_code == 403


# --- the scan history, one row per PASS ----------------------------------------------------------


def test_a_finished_runs_report_can_be_read_by_an_admin_only(client: TestClient) -> None:
    """The block of text a person copies. Seeded through the ledger the application built, so what
    is read back is what a real run writes. A run says how many files a library holds and how long
    this machine takes over them, and neither is a guest's business."""

    from sift.kernel.jobs.families import Family
    from sift.kernel.jobs.ledger import CURRENT_FAMILY
    from sift.kernel.wiring import LEDGER, part_of_app

    book = part_of_app(client.app, LEDGER)  # type: ignore[arg-type]
    book.started("probe")
    token = CURRENT_FAMILY.set(Family.SCAN)
    try:
        book.stage("probe.fingerprint", 250.0)
    finally:
        CURRENT_FAMILY.reset(token)
    book.finished("probe", duration_ms=1200, ok=True, media_type="video", size_bytes=5_000_000)
    run = book.open_run(Family.SCAN)
    assert run is not None
    run.started_at -= 120
    # On the application's loop, through the client's portal: the ledger writes through the
    # database's lock, which binds to the first loop that contends for it. `asyncio.run` would make
    # a second loop, and once an earlier test has bound the lock the write here would be refused.
    portal = client.portal
    assert portal is not None
    portal.call(functools.partial(book.settle, {}, settings={"jobs at once": 4}))

    # Probing's own family is recorded beside the scan; the scan is the run under test.
    row = next(one for one in portal.call(book.recent) if one.family == "scan")
    assert row.jobs_done == 1 and row.files_total == 1

    _sign_in(client, "guest")
    assert client.get(f"/api/performance/runs/{row.id}/report").status_code == 403

    _sign_in(client, "admin")
    report = client.get(f"/api/performance/runs/{row.id}/report")
    assert report.status_code == 200, report.text
    text = report.json()["text"]
    assert "Scan run" in text and "Settings: jobs at once 4" in text
    assert "probe.fingerprint" in text
    assert client.get("/api/performance/runs/nope/report").status_code == 404


def test_a_run_of_a_family_this_build_no_longer_knows_reports_under_its_own_name(
    client: TestClient,
) -> None:
    """A row outlives the code that wrote it: a family retired from the enum is still a run that
    happened, and its report carries the name the row does rather than being refused."""
    from sift.kernel.jobs.families import Family
    from sift.kernel.wiring import LEDGER, part_of_app

    book = part_of_app(client.app, LEDGER)  # type: ignore[arg-type]
    book.started("probe")
    book.finished("probe", duration_ms=10, ok=True, media_type="video", size_bytes=1)
    assert book.open_run(Family.SCAN) is not None
    portal = client.portal
    assert portal is not None
    portal.call(functools.partial(book.settle, {}, settings={}))
    database = client.app.state.database  # type: ignore[attr-defined]
    portal.call(functools.partial(database.execute, "UPDATE work_runs SET family = 'mystery'", ()))

    row = next(one for one in portal.call(book.recent) if one.family == "mystery")
    _sign_in(client, "admin")
    report = client.get(f"/api/performance/runs/{row.id}/report")

    assert report.status_code == 200, report.text
    assert ", mystery run," in report.json()["text"].splitlines()[0]


def test_a_restarted_screen_reads_back_the_kept_measurement_without_measuring(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route's half of the recall: the kept reading for THIS machine is what the first read
    after a start answers with, and nothing is measured to get it. `test_runner` proves the
    runner recalls; this proves the read the Performance screen makes is what asks it to."""
    from sift.kernel.db import Database
    from sift.kernel.wiring import HARDWARE, part_of_app
    from sift.slices.performance.rates import MachineRates, RatesStore
    from sift.slices.performance.runner import SELF_TEST_RUNNER

    async def never(**_: object) -> Measurement:
        raise AssertionError("a read must not measure")

    monkeypatch.setattr(selftest, "measure", never)
    profile = part_of_app(client.app, HARDWARE).profile  # type: ignore[arg-type]
    kept = Measurement(cores=8, levels=(a_level(1), a_level(2)))

    async def keep() -> None:
        database = Database(client.app.state.database.path, readers=1)  # type: ignore[attr-defined]
        await database.connect()
        try:
            await RatesStore(database).save(MachineRates.from_measurement(profile, kept, now=1))
        finally:
            await database.close()

    asyncio.run(keep())
    # Written past the app's store, whose boot already cached "nothing measured".
    part_of_app(client.app, SELF_TEST_RUNNER)._rates._known.clear()  # type: ignore[arg-type]
    body = _as_admin(client).get("/api/performance/self-test").json()

    assert body["running"] is False
    assert body["finished"] is True
    assert body["measurement"] is not None and body["measurement"]["cores"] == 8
    assert [level["at_once"] for level in body["measurement"]["levels"]] == [1, 2]


def test_the_folders_on_each_storage_are_read_from_the_library_never_kept_with_the_run(
    client: TestClient,
) -> None:
    """A folder removed since the run is not listed, and one added since is."""
    runner = part_of_app(client.app, SELF_TEST_RUNNER)  # type: ignore[arg-type]
    runner.state = a_finished_run()
    runner.state.measurement = Measurement(
        cores=8,
        storages=(
            selftest.StorageCurve(storage="C:\\", label="Removed since", remote=False),
            selftest.StorageCurve(storage="D:\\", label="Also removed", remote=False),
        ),
    )

    async def now_on() -> list[selftest.StorageToMeasure]:
        return [
            selftest.StorageToMeasure(storage="C:\\", label="Added since", remote=False, roots=())
        ]

    runner._storages = now_on

    body = _as_admin(client).get("/api/performance/self-test").json()

    assert [one["folders"] for one in body["measurement"]["storages"]] == ["Added since", ""]


def test_a_first_part_is_said_until_the_full_run_and_toasts_keep_asking_for_it(
    client: TestClient,
) -> None:
    from sift.kernel.wiring import HARDWARE
    from sift.slices.performance.benchmark import FIRST_BENCHMARK, AutomaticRun

    app = cast(FastAPI, client.app)
    runner = part_of_app(app, SELF_TEST_RUNNER)
    profile = part_of_app(app, HARDWARE).profile
    quick = MachineRates.from_measurement(
        profile, Measurement(cores=8, levels=(a_level(1),)), now=1, first_part=True
    )
    asyncio.run(_kept_beside_the_app(client, quick))
    runner._rates._known.clear()
    admin = _as_admin(client)

    body = admin.get("/api/performance/self-test").json()
    assert (body["whole_to_come"], body["whole_due"]) == (True, True)
    part_of_app(app, FIRST_BENCHMARK).now(
        AutomaticRun(job_id="j", state="running", said="s", holds=False)
    )
    run = admin.get("/api/performance/benchmark").json()
    assert run["measured"] is False, "a window keeps asking until the full run"
    assert run["held"] is None, "no folder waits for the full run"

    asyncio.run(_kept_beside_the_app(client, dataclasses.replace(quick, whole_stopped=True)))
    runner._rates._known.clear()
    body = admin.get("/api/performance/self-test").json()
    assert (body["whole_to_come"], body["whole_due"]) == (True, False)


async def _kept_beside_the_app(client: TestClient, rates: MachineRates) -> None:
    from sift.kernel.db import Database
    from sift.slices.performance.rates import RatesStore

    database = Database(client.app.state.database.path, readers=1)  # type: ignore[attr-defined]
    await database.connect()
    try:
        await RatesStore(database).save(rates)
    finally:
        await database.close()
