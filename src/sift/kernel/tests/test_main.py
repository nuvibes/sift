# SPDX-License-Identifier: AGPL-3.0-or-later
"""Boot tests for the application assembly."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator

from sift import client as client_module
from sift.kernel import lifecycle, wiring
from sift.kernel.access import Repository, Role, Viewer
from sift.kernel.config import get_settings
from sift.kernel.content import ContentStore
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.jobs import JobSwitchedOff
from sift.kernel.jobs.quiet_hours import WHEN_WORK
from sift.kernel.jobs.schedules import when_key
from sift.kernel.wire import Refused
from sift.main import (
    HSTS_HEADER_NAME,
    HSTS_HEADER_VALUE,
    _add_error_handlers,
    create_app,
    security_headers,
)
from sift.slices import semantic
from sift.slices.auth.crypto import derive_csrf_token
from sift.slices.performance.selftest import Measurement, Recommendation, SelfTest
from sift.testing.auth import establish_session, signed_in_id
from sift.wiring.lifespan import _quieten_a_reset_at_teardown

pytestmark = pytest.mark.integration


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Boot the real app against temp directories, configured through the environment as a real
    start-up is; the process-wide settings cache is cleared on both sides."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()

    with TestClient(create_app()) as c:
        yield c

    get_settings.cache_clear()


def test_app_boots_and_answers_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_hides_the_hardware_from_anonymous_callers(client: TestClient) -> None:
    """Liveness is public, the hardware fingerprint is not: an anonymous caller gets the status."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "hardware" not in body


def test_health_says_which_run_of_the_server_is_answering(client: TestClient) -> None:
    """The boot id tells a screen waiting out a restart that it happened: the old server answers for
    a moment after the ask, so "does it reply" is not enough. Admin-only, as a restart is."""
    _sign_in(client, "admin")

    body = client.get("/health").json()

    assert body["boot"] == lifecycle.BOOT_ID
    assert body["boot"]


def test_an_oversized_json_body_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A huge body is refused before FastAPI buffers it, so a stranger cannot post gigabytes."""
    monkeypatch.setattr("sift.main.MAX_REQUEST_BODY_BYTES", 10)
    response = client.post("/api/auth/login", json={"username": "x", "password": "y" * 100})
    assert response.status_code == 413


def test_a_small_json_body_is_not_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sift.main.MAX_REQUEST_BODY_BYTES", 10_000)
    response = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
    assert response.status_code != 413


def test_a_multipart_upload_is_exempt_from_the_json_body_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A large multipart upload is not turned into a 413 by the small JSON ceiling."""
    monkeypatch.setattr("sift.main.MAX_REQUEST_BODY_BYTES", 10)
    response = client.post("/api/auth/login", files={"file": ("big.bin", b"x" * 100)})
    assert response.status_code != 413


def _sign_in(client: TestClient, role: str) -> None:
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    _, token, _ = establish_session(
        db_path, role=role, username=f"health-{role}", password="Health-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)


def test_health_hides_the_hardware_from_a_guest(client: TestClient) -> None:
    """A guest has no business sizing up the host: admin-gated, not merely authenticated."""
    _sign_in(client, "guest")
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "hardware" not in body


def test_health_shows_the_hardware_to_an_admin(client: TestClient) -> None:
    """A signed-in admin is shown which encoder is in use, and no one else."""
    _sign_in(client, "admin")
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "hardware" in body


def test_health_says_what_the_machines_sqlite_can_do(client: TestClient) -> None:
    """Which SQLite the machine has is the first question a database problem asks; admin-only."""
    _sign_in(client, "admin")
    sqlite = client.get("/health").json()["sqlite"]

    assert sqlite["version"]
    assert sqlite["fts5"] is True
    assert "load_extension" in sqlite


def test_health_hides_the_sqlite_detail_from_a_guest(client: TestClient) -> None:
    _sign_in(client, "guest")

    assert "sqlite" not in client.get("/health").json()


def test_health_says_how_well_the_loop_has_kept_up_for_an_admin(client: TestClient) -> None:
    """How well the loop kept up answers "why did playback stutter"."""
    _sign_in(client, "admin")
    loop = client.get("/health").json()["loop"]

    assert loop["worst_lag_seconds"] >= 0
    assert loop["held_count"] >= 0


def test_health_says_how_long_work_is_waiting_for_a_thread_for_an_admin(client: TestClient) -> None:
    """A held loop and a full thread pool feel alike and are fixed by opposite things."""
    _sign_in(client, "admin")
    threads = client.get("/health").json()["threads"]

    assert threads["worst_wait_seconds"] >= 0
    assert threads["full_count"] >= 0
    assert threads["waiting_seconds"] >= 0


# --- what a stranger and a guest may learn from /health
#
# The WHOLE body is asserted, so anything ever added to an admin's answer is covered the moment it
# exists, without somebody remembering a test for it.


def test_a_stranger_learns_only_that_sift_is_up(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_a_guest_learns_only_that_sift_is_up(client: TestClient) -> None:
    _sign_in(client, "guest")

    assert client.get("/health").json() == {"status": "ok"}


# --- the budget the two heavy passes share
#
# Against the running pool: the failure is a share nothing ever reads, a control that looks like it
# works. These reach the app's own objects ON THE APP'S LOOP: on the runner's loop the database's
# write lock fails with `bound to a different event loop` once it has to wait.


def on_the_apps_loop[T](client: TestClient, work: Coroutine[Any, Any, T]) -> T:
    return client.portal.call(lambda: work)  # type: ignore[union-attr,no-any-return]


def test_describing_a_library_is_capped_by_the_running_pool(client: TestClient) -> None:
    """The running pool caps describing a library."""
    pool = client.app.state.pool  # type: ignore[attr-defined]

    assert "semantic_describe" in pool.limits


def test_a_pass_with_nobody_competing_gets_the_whole_machine(client: TestClient) -> None:
    """With nothing else working, describing is capped at the worker count, read back through the
    pool's own configuration reader, the path a Performance change takes."""
    pool = client.app.state.pool  # type: ignore[attr-defined]

    workers, limits = on_the_apps_loop(client, pool._read_config())

    assert limits["semantic_describe"] == workers


def test_a_pass_gives_way_once_another_one_has_work(client: TestClient) -> None:
    """Real rows in the real queue move a real cap: a pass gives way once another has work.

    The recognition jobs are queued far in the future so none is claimed mid-read, and recognition
    is set to run as work arrives: work held for quiet hours asks for none of the machine.
    """
    app = client.app
    pool = app.state.pool  # type: ignore[attr-defined]
    queue = app.state.queue  # type: ignore[attr-defined]

    on_the_apps_loop(
        client,
        app.state.settings_hub.apply(  # type: ignore[attr-defined]
            _an_admin(client),
            {
                "faces.enabled": True,
                "faces.machine_budget": "share",
                "faces.core_share": 50,
                when_key("faces"): WHEN_WORK,
            },
        ),
    )
    queue.forget_quiet_hours()
    _, alone = on_the_apps_loop(client, pool._read_config())

    for _ in range(3):
        on_the_apps_loop(
            client, queue.enqueue("face_scan", {"asset_id": "nothing"}, run_after=_LONG_FROM_NOW)
        )
    _, sharing = on_the_apps_loop(client, pool._read_config())

    assert sharing["semantic_describe"] < alone["semantic_describe"]


def test_the_pool_runs_a_share_of_the_device_while_somebody_is_at_the_keyboard(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A quarter of the workers by default, every kind's share and each tool's threads held to it;
    the whole device again once nobody is here."""
    import os

    from sift.kernel import attention, budget, media
    from sift.kernel import subprocess as tools

    pool = client.app.state.pool  # type: ignore[attr-defined]
    full, _ = on_the_apps_loop(client, pool._read_config())
    since = {"seconds": 1.0}
    monkeypatch.setattr(attention, "ATTENTION", attention.Attention(lambda: since["seconds"]))
    settings = get_settings()
    cores = os.cpu_count() or 1

    workers, limits = on_the_apps_loop(client, pool._read_config())

    assert workers == budget.stepped_workers(full, budget.STEP_BACK_SHARE)
    assert limits["semantic_describe"] <= workers
    # A one-worker pool cannot step back, so it is the whole device.
    percent = budget.STEP_BACK_SHARE if workers < full else budget.WHOLE_DEVICE
    threads = budget.tool_threads(cores, running=workers, percent=percent)
    assert media.background_threads(settings) == threads
    held = budget.processor_rate(threads, cores) if workers < full else None
    assert tools.background_rate() == held

    since["seconds"] = 600.0
    workers, _ = on_the_apps_loop(client, pool._read_config())

    assert workers == full
    # On the whole device a tool shares the cores with the tools actually running: none here, so
    # the one about to start gets the machine (the threads follow what runs, not the worker count).
    assert media.background_threads(settings) == cores
    assert tools.background_rate() is None


def test_recognition_switched_off_is_paused_rather_than_capped(client: TestClient) -> None:
    """Recognition switched off is a cap of zero, which the pool must accept: refusing it would stop
    every other live setting with it."""
    pool = client.app.state.pool  # type: ignore[attr-defined]

    _, limits = on_the_apps_loop(client, pool._read_config())

    assert limits["face_scan"] == 0


#: Far enough out that a queued job stays queued, in the queue's seconds since the epoch.
_LONG_FROM_NOW = 4_000_000_000


def _an_admin(client: TestClient) -> Viewer:
    """A user who EXISTS, for a settings write: the record names a user the database must know."""
    _sign_in(client, "admin")
    return Viewer(id=signed_in_id(client), role=Role.ADMIN)


def test_boot_creates_the_directories_it_owns(client: TestClient, tmp_path: Path) -> None:
    """Boot creates the directories it owns, so a broken mount fails the start."""
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "cache" / "transcode").is_dir()
    assert (tmp_path / "data" / "quarantine").is_dir()


def test_the_content_store_is_wired_into_the_app(client: TestClient) -> None:
    """Boot builds a real store on a real database."""
    store = client.app.state.content  # type: ignore[attr-defined]
    assert isinstance(store, ContentStore)


def test_the_access_repository_is_wired_into_the_app(client: TestClient) -> None:
    repository = client.app.state.access  # type: ignore[attr-defined]
    assert isinstance(repository, Repository)


# --- the standing offer to measure this machine
#
# Three answers: "not now" differs from "no", because the offer is worth making again once a
# library exists.


def _admin_client(client: TestClient) -> TestClient:
    """Signed in, with the CSRF header: sync over HTTP, since `TestClient` runs the app on its own
    loop."""
    _sign_in(client, "admin")
    held = client.cookies.get(SESSION_COOKIE_NAME) or ""
    client.headers.update({CSRF_HEADER_NAME: derive_csrf_token(held)})
    return client


def test_a_guest_is_never_told_about_the_measurement(client: TestClient) -> None:
    """It works the machine hard and returns instance-wide settings: refused, not hidden."""
    _sign_in(client, "guest")

    assert client.get("/api/performance/self-test").status_code == 403


def test_a_recommendation_is_compared_against_the_setting_as_it_is_now(
    client: TestClient,
) -> None:
    """A recommendation is compared with the settings live, so once applied it offers nothing."""
    signed_in = _admin_client(client)
    # Planted rather than measured: the comparison is under test, not ffmpeg.
    signed_in.app.state.self_test_runner.state = SelfTest(  # type: ignore[attr-defined]
        running=False,
        finished_at=1.0,
        measurement=Measurement(cores=8, levels=()),
        recommendations=[
            Recommendation(
                key="performance.generation_limit",
                label="How many previews are built at the same time",
                current=0,
                suggested=4,
                reason="measured",
            )
        ],
    )

    before = signed_in.get("/api/performance/self-test").json()["recommendations"][0]
    assert before["changes_anything"] is True

    response = signed_in.put("/api/settings", json={"values": {"performance.generation_limit": 4}})
    assert response.status_code in (200, 204), response.text
    after = signed_in.get("/api/performance/self-test").json()["recommendations"][0]

    assert after["current"] == 4
    assert after["changes_anything"] is False


# --- everything that is not the API, answered by the one catch-all route


def test_an_address_that_is_only_the_word_api_inside_something_else_is_a_page(
    client: TestClient,
) -> None:
    """`/apiary` is a page address, matched as a segment: the client answers it, not the API."""
    answer = client.get("/apiary")

    assert answer.status_code != 404
    assert answer.headers["content-type"].split(";")[0] in ("text/html", "application/json")
    if answer.headers["content-type"].startswith("application/json"):
        assert "not built" in answer.json()["detail"]


def test_a_checkout_with_no_client_says_so_rather_than_refusing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no client built, a page address answers 503, not 404: the address is fine and the app
    is temporarily missing, and 404 is this app's refusal."""
    monkeypatch.setattr(client_module, "CLIENT_DIR", Path("/nowhere"))
    monkeypatch.setattr(client_module, "INDEX", Path("/nowhere/index.html"))

    answer = client.get("/browse")

    assert answer.status_code == 503
    assert "not built" in answer.json()["detail"]
    assert "/api" in answer.json()["detail"]


def test_a_page_address_is_answered_with_the_client(client: TestClient) -> None:
    """A link to a filtered view survives being opened cold."""
    if not client_module.is_built():
        pytest.skip("no client built in this checkout")

    answer = client.get("/asset/01HX0000000000000000000A01")

    assert answer.status_code == 200
    assert answer.headers["cache-control"] == client_module.PAGE
    assert "<!doctype html>" in answer.text.lower()


# --- the headers every answer carries


def test_a_request_that_arrived_over_https_is_told_to_keep_using_it(client: TestClient) -> None:
    """HSTS only over https: on plain http it would lock a LAN install out of its only address."""
    plain = client.get("/health")
    assert HSTS_HEADER_NAME not in plain.headers

    forwarded = client.get("/health", headers={"x-forwarded-proto": "https"})
    assert forwarded.headers[HSTS_HEADER_NAME] == HSTS_HEADER_VALUE


def test_a_page_answer_carries_the_same_headers_a_route_answer_does(
    client: TestClient,
) -> None:
    """The API's middleware and everyone else's write the same headers."""
    if not client_module.is_built():
        pytest.skip("no client built in this checkout")

    answer = client.get("/browse", headers={"x-forwarded-proto": "https"})

    for name in security_headers():
        assert name in answer.headers, name
    assert answer.headers[HSTS_HEADER_NAME] == HSTS_HEADER_VALUE


def test_every_answer_carries_sifts_mark_including_a_refusal(client: TestClient) -> None:
    """Every answer carries `x-sift`, a sign-in refusal included: the desktop shell trusts an
    address only once it does, so another host answering 200 or 401 is not trusted."""
    assert client.get("/health").headers["x-sift"] == "1"
    refused = client.get("/api/settings")
    assert refused.status_code == 401
    assert refused.headers["x-sift"] == "1"


def test_a_handler_that_raises_is_answered_generically_and_still_dressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 500 synthesized past the header middleware is dressed like every other answer, with a fixed
    generic body: the detail carries a traceback and any path in it."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    app = create_app()

    @app.get("/api/deliberately-broken")
    async def broken() -> None:
        raise RuntimeError("a path nobody should see: /home/somebody/holiday.mp4")

    # Ahead of the catch-all, which is registered last and would answer its own 404.
    app.router.routes.insert(0, app.router.routes.pop())

    try:
        with TestClient(app, raise_server_exceptions=False) as raising:
            answer = raising.get("/api/deliberately-broken", headers={"x-forwarded-proto": "https"})
            # Over plain http the same answer must NOT carry HSTS.
            plain = raising.get("/api/deliberately-broken")
    finally:
        get_settings.cache_clear()

    assert plain.status_code == 500
    assert HSTS_HEADER_NAME not in plain.headers
    assert answer.status_code == 500
    assert answer.json() == {"detail": "Something went wrong."}
    assert "holiday.mp4" not in answer.text
    assert "RuntimeError" not in answer.text
    for name in security_headers():
        assert name in answer.headers, name
    assert answer.headers[HSTS_HEADER_NAME] == HSTS_HEADER_VALUE
    # The correlation id ties the generic sentence to the log line with the detail.
    assert answer.headers["x-correlation-id"]


def test_a_machine_that_cannot_load_the_vector_add_on_still_boots_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """Sift starts where the meaning-search add-on cannot load, and the boot log says why."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    monkeypatch.setattr(
        semantic.VectorStore, "available", property(lambda self: False), raising=True
    )

    try:
        with TestClient(create_app()) as without:
            assert without.get("/health").json()["status"] == "ok"
    finally:
        get_settings.cache_clear()

    # `capfd`: the log handler needs a real file descriptor.
    assert "semantic.unavailable" in capfd.readouterr().out


def test_a_retired_environment_variable_is_named_once_at_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """A variable Sift no longer reads is named at boot, with what to do instead, and does not stop
    the boot."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SIFT_SESSION_TTL_SECONDS", "1209600")
    get_settings.cache_clear()

    try:
        with TestClient(create_app()) as booted:
            assert booted.get("/health").json()["status"] == "ok"
    finally:
        get_settings.cache_clear()

    said = capfd.readouterr().out
    assert "config.retired" in said
    assert "SIFT_SESSION_TTL_SECONDS" in said
    assert "Privacy screen" in said


def test_nothing_is_said_when_no_retired_variable_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """An ordinary boot prints no such warning: one printed every time is one nobody sees."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("SIFT_SESSION_TTL_SECONDS", raising=False)
    monkeypatch.delenv("SIFT_PUBLIC_BASE_URL", raising=False)
    get_settings.cache_clear()

    try:
        with TestClient(create_app()) as booted:
            assert booted.get("/health").json()["status"] == "ok"
    finally:
        get_settings.cache_clear()

    assert "config.retired" not in capfd.readouterr().out


async def test_a_reset_while_a_connection_is_closing_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tab closing mid-response is not logged as a fault: asyncio's proactor transport raises
    `ConnectionResetError` shutting a socket the other end left, with no `await` to receive it."""
    _quieten_a_reset_at_teardown()
    handler = asyncio.get_running_loop().get_exception_handler()
    assert handler is not None

    seen: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "default_exception_handler", seen.append)

    handler(
        loop,
        {
            "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
            "exception": ConnectionResetError(10054, "forcibly closed by the remote host"),
        },
    )

    assert seen == [], "the ordinary end of a connection was reported as a fault"


async def test_every_other_failure_still_reaches_the_default_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only that reset is quietened: a different exception under the same message, and the same one
    under another, still reach the default handler."""
    _quieten_a_reset_at_teardown()
    handler = asyncio.get_running_loop().get_exception_handler()
    assert handler is not None

    seen: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "default_exception_handler", seen.append)

    handler(
        loop,
        {
            "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
            "exception": ValueError("something genuinely wrong"),
        },
    )
    handler(
        loop,
        {
            "message": "Task exception was never retrieved",
            "exception": ConnectionResetError(10054, "forcibly closed by the remote host"),
        },
    )

    assert len(seen) == 2, "a fault outside the one shape being quietened was swallowed"


async def test_every_diagnostic_watch_is_stopped_when_the_app_shuts_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every background task started at boot is stopped at shutdown.

    One left running wakes after the database closes and raises an unretrieved exception. Asserted
    on the TASKS, not the log, so silencing the printer cannot pass it.
    """
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app):
            watchdog = wiring.part_of_app(app, wiring.WATCHDOG)
            threads = wiring.part_of_app(app, wiring.THREADS)
            reads = wiring.part_of_app(app, wiring.READS)
            backlog = wiring.part_of_app(app, wiring.BACKLOG)
            # Known positive: all four run while the app is up.
            running = [watchdog._task, threads._task, reads._task, backlog._task]
            assert [one is not None for one in running] == [True] * 4

        assert [watchdog._task, threads._task, reads._task, backlog._task] == [None] * 4, (
            "a diagnostic watch outlived the application, and its next tick asks a closed database"
        )
    finally:
        get_settings.cache_clear()


def test_a_mistyped_api_address_gets_an_api_answer(client: TestClient) -> None:
    """A mistyped API address gets an API 404, never the client's HTML with a 200."""
    for path in ("/api", "/api/", "/api/nothing-here"):
        answer = client.get(path)
        assert answer.status_code == 404, path
        assert answer.json() == {"detail": "Not found."}


async def test_asking_for_work_that_is_switched_off_is_a_409_with_its_sentence(
    client: TestClient,
) -> None:
    handler = client.app.exception_handlers[JobSwitchedOff]  # type: ignore[attr-defined]
    answer = await handler(None, JobSwitchedOff("Reading watermarks is switched off."))
    assert answer.status_code == 409
    assert json.loads(answer.body) == {"detail": "Reading watermarks is switched off."}


class _Named(BaseModel):
    """A body whose one field refuses in a sentence written for the person who typed it."""

    name: str

    @field_validator("name")
    @classmethod
    def _with_a_letter(cls, value: str) -> str:
        raise Refused("A name needs a letter in it.")


def test_a_refusal_written_for_a_person_is_said_under_its_field_and_nothing_typed_is_echoed() -> (
    None
):
    """A form puts the sentence under the field the header names, and the typed value never comes
    back."""
    app = FastAPI()
    _add_error_handlers(app)

    @app.post("/named")
    async def named(body: _Named) -> dict[str, str]:
        return {"name": body.name}

    answer = TestClient(app).post("/named", json={"name": "s3cret-typed"})

    assert answer.status_code == 422
    assert answer.json() == {"detail": "A name needs a letter in it."}
    assert answer.headers["Sift-Field"] == "name"
    assert "s3cret-typed" not in answer.text


def test_the_look_for_a_quiet_moment_runs_with_the_server_and_is_told_to_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sift.slices import performance

    stops: list[asyncio.Event] = []

    async def looking(_self: object, stop: asyncio.Event, *, every: float = 0.0) -> None:
        stops.append(stop)
        await stop.wait()

    monkeypatch.setattr(performance.WhenQuiet, "keep_looking", looking)
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    with TestClient(create_app()) as booted:
        assert booted.get("/health").status_code == 200
        assert [stop.is_set() for stop in stops] == [False]
    get_settings.cache_clear()
    assert stops[0].is_set()
