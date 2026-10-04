# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every stage and every request in the finished app is self-timing, and every timing record is
safe to share.

The timing hooks live in the kernel (the pool times a job, a middleware times a request, each
expensive step calls the hook by name), so a feature cannot forget them; this proves it. It walks
the real application: every job type runs through the pool's timing seam, every route emits a
request-timing record, the named stages are emitted by their owners, every record is safe for a
public bug report (no path, URL, filename, hostname or email, even with redaction off), and no
slice logs outside the kernel logger. Each check is paired with a proof that it can fail.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sift.slices
from sift.kernel.config import get_settings
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobContext, JobQueue, WorkerPool, registered_handlers
from sift.kernel.jobs.queue import Job, JobState
from sift.kernel.log import configure_logging, get_logger
from sift.main import create_app
from tests.gates.test_authz_matrix import (
    WEBSOCKET,
    _leaf_routes,
    _methods_of,
    fill,
    mounted_routes,
)

pytestmark = [pytest.mark.gate, pytest.mark.integration]


# --- capturing what the app writes to its log


#: The event that proves the capture can hear the application, named so it can be taken back out.
_HEARD = "diagnostics.capture_can_hear_the_app"


@contextmanager
def capture_events() -> Iterator[list[dict[str, object]]]:
    """The structured events the app emits, parsed back from the JSON it would write out.

    The rendered output, because what leaves the machine has been through redaction. A fresh
    handler on the root logger, added after the app has started: booting reinstalls the root
    handlers.
    """
    events: list[dict[str, object]] = []
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    # `logging.root` attaches a sink to read; it makes no logger to write through.
    root = logging.root
    root.addHandler(handler)

    # A KNOWN POSITIVE: one record through the app's path must come back, or every later "never
    # emitted" would be the capture going deaf (a replaced handler list, a raised level, a logger
    # bound under an older configuration), not the slice it names.
    get_logger(__name__).info(_HEARD)
    if _HEARD not in buffer.getvalue():
        root.removeHandler(handler)
        raise AssertionError(
            "the log capture is not receiving what the application writes, so every check in this "
            "file would report the app as silent. Something earlier in this run left the logging "
            "pipeline unable to deliver records: a replaced handler list, a raised level, or a "
            "logger bound under an earlier configuration."
        )
    buffer.seek(0)
    buffer.truncate(0)

    try:
        yield events
    finally:
        root.removeHandler(handler)
        for line in buffer.getvalue().splitlines():
            line = line.strip()
            if line:
                with suppress(json.JSONDecodeError):
                    events.append(json.loads(line))


def timing_records(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """The stage-timing records: what a `timing_hook(...)` block emits when it closes."""
    return [event for event in events if event.get("event") == "timing"]


def request_records(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """The request-timing records: one per request the middleware saw."""
    return [event for event in events if event.get("event") == "http.request"]


# --- what a timing record is allowed to carry
#
# The frozen contract: a field not listed here fails, so whether a new field is safe to paste into
# a public issue is decided in writing before it ships in an export.

#: Fields on a stage-timing record (`event == "timing"`), across every stage that emits one.
SAFE_TIMING_FIELDS = frozenset(
    {
        # structlog's own envelope.
        "event",
        "level",
        "timestamp",
        # on every timing record.
        "stage",
        "duration_ms",
        # Only where a block declared a wait (`Timing.acquired`): a queue must not read as a slow
        # statement.
        "waited_ms",
        "ran_ms",
        "failed",
        # the job wrapper.
        "job_type",
        "job_id",
        # hashing.
        "file_size",
        "file_type",
        # `statement` is a NAME (declared, or a verb, a table and a digest), never the text.
        "statement",
        "component",
        "to_version",
        # probe, thumbnail, preview, sprite.
        "asset_id",
        "media_type",
        "encoder",
        "frames",
        # a library scan.
        "root_id",
        # How many rows a read handed back.
        "rows",
        # How far behind the loop's queue was, while something watches it.
        "loop_backlog_ms",
    }
)

#: Fields on a request-timing record (`event == "http.request"`).
SAFE_REQUEST_FIELDS = frozenset(
    {
        "event",
        "level",
        "timestamp",
        "method",
        "route",
        "status",
        "duration_ms",
        "correlation_id",
    }
)

#: Values that are templates by design, exempt from the value scan (a route like
#: `/api/assets/{asset_id}/hls/index.m3u8`); the field allowlist still governs them.
_TEMPLATE_KEYS = frozenset({"route"})

#: What must never appear in a value: a home directory, a URL, a media filename, or an email.
_LEAKS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/home/|/usr/home/|/Users/|/Volumes/", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\Users\\", re.IGNORECASE),
    re.compile(r"\\\\[^\\]+\\"),
    re.compile(r"\bhttps?://|\bftp://"),
    re.compile(r"\.(?:mp4|mkv|mov|avi|webm|m4v|jpe?g|png|gif|webp)\b", re.IGNORECASE),
    re.compile(r"[^\s@]+@[^\s@]+\.[A-Za-z]{2,}"),
)


def leaks_in(record: dict[str, object]) -> list[str]:
    """Any value in a record that looks like a place on disk, a URL, a filename or an email."""
    found: list[str] = []
    for key, value in record.items():
        if key in _TEMPLATE_KEYS or not isinstance(value, str):
            continue
        if any(pattern.search(value) for pattern in _LEAKS):
            found.append(f"{key}={value!r}")
    return found


# --- driving the app


def _planted_job(job_type: str) -> Job:
    """A job row to hand a handler, with no payload and no capabilities behind it: the handler fails
    at once, and the timing block records on the way out either way."""
    return Job(
        id=new_id(),
        parent_id=None,
        type=job_type,
        state=JobState.RUNNING,
        priority=0,
        payload={},
        progress=0.0,
        attempts=1,
        max_attempts=1,
        claimed_by="conformance",
        heartbeat_at=None,
        error=None,
        note=None,
        run_after=None,
        created_at=0,
        updated_at=0,
    )


@pytest.fixture
def booted_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """The real application, booted, with the worker pool's own loop stood down, so no worker picks
    up seeded work; this file drives handlers by hand."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))

    async def _no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", _no_workers)
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


def sweep_routes(client: TestClient, app: FastAPI) -> set[tuple[str, str]]:
    """Hit every mounted HTTP route once, and report which ones were asked.

    Any placeholder value matches the path pattern, and the record is written whatever the answer.
    WebSockets are left out: a socket is not a request.
    """
    asked: set[tuple[str, str]] = set()
    for method, path in mounted_routes(app):
        if method == WEBSOCKET:
            continue
        filled = fill(path, {name: new_id() for name in _params_in(path)})
        with suppress(Exception):
            client.request(method, filled)
        asked.add((method, path))
    return asked


_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::[^{}]+)?\}")


def _params_in(path: str) -> set[str]:
    return {match.group(1) for match in _PLACEHOLDER.finditer(path)}


# --- 1. every job type is run through the timing seam


async def test_every_registered_job_type_emits_a_timing_record(booted_app: FastAPI) -> None:
    """Every registered handler, run the way the pool runs it with an empty payload, leaves a
    timing record: work run off the queue would leave none."""
    # Asserted after the app has shut down: a failure inside the lifespan would deadlock shutdown.
    handler_count = 0
    untimed: list[str] = []
    bad_duration: list[str] = []
    async with booted_app.router.lifespan_context(booted_app):
        handlers = registered_handlers()
        handler_count = len(handlers)

        queue = JobQueue(booted_app.state.database)
        pool = WorkerPool(queue, concurrency=1)

        for job_type, handler in handlers.items():
            context = JobContext(
                job=_planted_job(job_type), worker_id="conformance", queue=queue, capabilities=None
            )
            with capture_events() as events, suppress(BaseException):
                # The worker loop's exact call, bounded: a handler that blocks is cancelled.
                await asyncio.wait_for(pool._invoke(handler, context), timeout=30)
            recorded = [
                record
                for record in timing_records(events)
                if record.get("stage") == "job" and record.get("job_type") == job_type
            ]
            if not recorded:
                untimed.append(job_type)
            elif not isinstance(recorded[0].get("duration_ms"), int | float):
                bad_duration.append(job_type)

    assert handler_count, "no job handlers registered; the app did not finish starting"
    assert not untimed, (
        f"these job types ran without a timing record: {sorted(untimed)}. A job is timed by the "
        "pool that runs it, so one that is not means the work reached a worker some other way."
    )
    assert not bad_duration, f"these job types recorded no duration: {sorted(bad_duration)}"


# --- 2. every route is timed


def route_targets(app: FastAPI) -> set[tuple[str, str]]:
    """(method, path) for every HTTP route, as the middleware records it: a prefixed router's short
    template, though the request goes to the full path."""
    targets: set[tuple[str, str]] = set()
    for _full, route in _leaf_routes(app.routes):
        path = getattr(route, "path", None)
        if not isinstance(path, str):
            continue
        for method in _methods_of(route):
            if method != WEBSOCKET:
                targets.add((method, path))
    return targets


def untimed_routes(
    targets: set[tuple[str, str]], events: list[dict[str, object]]
) -> set[tuple[str, str]]:
    """The routes that were asked but produced no request-timing record."""
    timed = {(record.get("method"), record.get("route")) for record in request_records(events)}
    return {target for target in targets if target not in timed}


def test_every_mounted_route_emits_a_request_timing_record(booted_app: FastAPI) -> None:
    """Every route the app mounts, hit once, leaves a request-timing record for its template.

    The client is entered before the capture: booting reinstalls the root log handlers.
    """
    with TestClient(booted_app) as client, capture_events() as events:
        sweep_routes(client, booted_app)

    missing = untimed_routes(route_targets(booted_app), events)
    assert not missing, (
        f"these routes answered without a request-timing record: {sorted(missing)}. Every route is "
        "timed by a middleware it inherits by being mounted, so one that is not was mounted outside "
        "it."
    )


# --- 3. the named stages exist, by name, in the module that owns each
#
# The slow-path analysis reads these names back; a rename fails here until both sides change.

NAMED_STAGES: tuple[tuple[str, str, str], ...] = (
    # The database stages go through `_judged`, which names a statement and its bar for every call.
    ("database read", "sift.kernel.db", '_judged("db.read"'),
    ("database write", "sift.kernel.db", '_judged("db.write"'),
    ("hashing", "sift.kernel.content.hashing", 'timing_hook("content.hash"'),
    ("ffprobe", "sift.slices.media_jobs.probing", 'timing_hook("probe.'),
    ("thumbnail", "sift.slices.media_jobs.thumbnails", 'timing_hook("thumbnail.render"'),
    ("preview", "sift.slices.media_jobs.previews", 'timing_hook("preview.encode"'),
    ("sprite", "sift.slices.media_jobs.sprites", 'timing_hook("sprite.render"'),
    ("transcode", "sift.slices.player.transcode", 'timing_hook(f"player.render.'),
    ("request waterfall", "sift.main", '"http.request"'),
)


@pytest.mark.parametrize(
    ("stage", "module", "marker"),
    NAMED_STAGES,
    ids=[stage for stage, _, _ in NAMED_STAGES],
)
def test_named_stage_is_emitted_by_its_owner(stage: str, module: str, marker: str) -> None:
    """Each stage name the analysis depends on is emitted by the module that owns it."""
    import importlib

    source = Path(importlib.import_module(module).__file__ or "").read_text(encoding="utf-8")
    assert marker in source, (
        f"the {stage!r} stage is no longer emitted as expected by {module}. If it was renamed, the "
        "slow-path analysis that reads this name back has to be updated in step, on purpose."
    )


# --- 4. every timing record is safe to share


def _collect_records(app: FastAPI) -> list[dict[str, object]]:
    """Every timing and request record a route sweep produces: what a running install logs."""
    with TestClient(app) as client, capture_events() as events:
        sweep_routes(client, app)
    return timing_records(events) + request_records(events)


def test_timing_records_carry_no_identifying_data(booted_app: FastAPI) -> None:
    """No record carries a path, URL, filename or email, and every field is in the contract."""
    records = _collect_records(booted_app)
    assert records, "the sweep produced no records to check"

    for record in records:
        allowed = (
            SAFE_REQUEST_FIELDS if record.get("event") == "http.request" else SAFE_TIMING_FIELDS
        )
        stray = set(record) - allowed
        assert not stray, (
            f"a {record.get('event')!r} record carries fields not in the contract: {sorted(stray)}. "
            "Add them to the allowlist only once you have decided the value is safe to share."
        )
        leaks = leaks_in(record)
        assert not leaks, f"a {record.get('event')!r} record carries identifying data: {leaks}"


def test_timing_records_are_clean_even_with_redaction_turned_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With redaction off the records are still clean: a record is safe because it never carries
    a name, not because a scrubber catches one."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SIFT_LOG_UNREDACTED", "true")

    async def _no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", _no_workers)
    get_settings.cache_clear()
    try:
        app = create_app()
        records = _collect_records(app)
        assert records, "the sweep produced no records to check"
        for record in records:
            leaks = leaks_in(record)
            assert not leaks, (
                f"with redaction off, a {record.get('event')!r} record carries identifying data: "
                f"{leaks}. The record should not contain it in the first place."
            )
    finally:
        get_settings.cache_clear()


# --- 5. no slice logs outside the kernel logger


def test_no_slice_logs_outside_the_kernel() -> None:
    """No slice uses `print` or a raw stdlib logger, which would write past redaction."""
    slices_dir = Path(sift.slices.__file__ or "").parent
    raw_print = re.compile(r"(?<![\w.])print\s*\(")

    offenders: list[str] = []
    for source_file in slices_dir.rglob("*.py"):
        if "tests" in source_file.parts:
            continue
        text = source_file.read_text(encoding="utf-8")
        if raw_print.search(text) or "logging.getLogger" in text:
            offenders.append(str(source_file.relative_to(slices_dir.parent)))

    assert not offenders, (
        f"these slice files log outside the kernel logger: {sorted(offenders)}. Use `get_logger` so "
        "the record goes through redaction like every other."
    )


# --- the checks can fail


async def test_a_job_run_off_the_seam_produces_no_record(job_queue: JobQueue) -> None:
    """A handler called directly, off the seam, leaves no record; run as the pool runs it, one."""
    configure_logging("INFO", redact_personal=True)

    async def handler(_: JobContext) -> None:
        return None

    context = JobContext(
        job=_planted_job("planted"), worker_id="conformance", queue=job_queue, capabilities=None
    )
    pool = WorkerPool(job_queue, concurrency=1)

    with capture_events() as through_the_seam:
        await pool._invoke(handler, context)
    assert any(
        record.get("job_type") == "planted" for record in timing_records(through_the_seam)
    ), "a handler run through the pool must record"

    with capture_events() as off_the_seam:
        await handler(context)
    assert not any(
        record.get("job_type") == "planted" for record in timing_records(off_the_seam)
    ), "a handler run off the seam must not record: if it does, the check cannot catch a bypass"


def test_the_route_check_catches_a_route_that_was_not_timed() -> None:
    """The route check reports a route with no record, and nothing once the record is there."""
    route = ("GET", "/planted")
    assert untimed_routes({route}, []) == {route}
    assert (
        untimed_routes({route}, [{"event": "http.request", "method": "GET", "route": "/planted"}])
        == set()
    )


def test_the_contract_catches_a_field_it_does_not_allow() -> None:
    """The contract flags a field it does not list and a value that looks like a home directory."""
    assert set({"event": "timing", "abs_path": "x"}) - SAFE_TIMING_FIELDS == {"abs_path"}
    assert leaks_in({"detail": "/home/kate/Videos/holiday.mp4"})
    assert not leaks_in({"stage": "job", "job_type": "probe", "duration_ms": 12})
    # A route template is exempt, even one carrying a literal that looks filename-shaped.
    assert not leaks_in({"event": "http.request", "route": "/api/assets/{asset_id}/hls/index.m3u8"})
