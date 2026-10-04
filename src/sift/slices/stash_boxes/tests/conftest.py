# SPDX-License-Identifier: AGPL-3.0-or-later
"""The fixtures the stash-box tests share: the running application, and the service and jobs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sift.slices.workbench.schema  # noqa: F401 (the ledger's table registers itself)
from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import (
    JobContext,
    JobQueue,
    SystemCapabilities,
    register_handler,
)
from sift.kernel.secret_store import SecretStore
from sift.main import create_app
from sift.slices.stash_boxes.jobs import (
    STASH_ENRICH,
    STASH_LINK_INVENTED,
    STASH_SCAN,
    STASH_SWEEP,
)
from sift.slices.stash_boxes.service import StashBoxService
from sift.slices.stash_boxes.tests.jobs_support import (
    A_KEY,
    _Adapter,
    _never_called,
    _Secrets,
)


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as running:
        yield running


@pytest.fixture
async def service(temp_db: Database) -> StashBoxService:
    await temp_db.initialize_schema()
    return StashBoxService(temp_db, SecretStore(temp_db), _Adapter())  # type: ignore[arg-type]


@pytest.fixture
def handlers(clean_handlers: None) -> None:
    """Claim the two types, so the queue will take a job of either.

    The handlers registered here are never the ones called: every test drives `scan` or `sweep`
    itself with the stand-ins it needs. What registering buys is a queue that accepts the job at
    all, and a job the queue really claimed: progress, notes and fan-out are all writes fenced on
    that claim, and for a job the queue never heard of every one of them does nothing.
    """
    register_handler(STASH_SCAN, _never_called, name="Asking a stash-box")
    register_handler(STASH_SWEEP, _never_called, name="Scanning the library")
    register_handler(STASH_ENRICH, _never_called, name="Asking about a batch")
    register_handler(STASH_LINK_INVENTED, _never_called, name="Linking what boxes invented")


@pytest.fixture
def context_for(
    job_queue: JobQueue, content_store: object, library_store: object, handlers: None
) -> Callable[..., Awaitable[JobContext]]:
    """A handler's context, for a job that is really queued and really claimed.

    A hand-built job would be simpler and would quietly ruin every test using it: progress, notes
    and fan-out are all writes fenced on the claim, and for a job the queue never heard of every one
    of them does nothing at all.
    """

    async def build(
        job_type: str, payload: dict[str, object], *, key: bytes | None = A_KEY
    ) -> JobContext:
        capabilities = SystemCapabilities(
            content=content_store,  # type: ignore[arg-type]
            library=library_store,  # type: ignore[arg-type]
            secrets=_Secrets(key),
        )
        job_id = await job_queue.enqueue(job_type, payload)
        worker_id = new_id()
        while True:
            job = await job_queue.claim(worker_id)
            assert job is not None, "the queue lost a job this test enqueued"
            if job.id == job_id:
                return JobContext(
                    job=job, worker_id=worker_id, queue=job_queue, capabilities=capabilities
                )

    return build
