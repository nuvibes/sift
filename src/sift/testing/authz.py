# SPDX-License-Identifier: AGPL-3.0-or-later
"""One booted application and one seeded library, shared by the read-only cases of a test module.

Booting the application takes a couple of seconds; seeding a library for it takes far longer,
because every seed helper opens a database of its own. A module that asks hundreds of questions of
the same library pays that once with `shared_client`. A case that writes (deletes a row, locks a
session, plants a route) boots its own app with `booted` and says why in its fixture: its writes
would otherwise change what every case after it is answered.

ONE APP IS RUNNING AT A TIME. A boot takes over parts of the whole process: the thread pools, the
log file, the landing step and the change bus belong to the last app started, and its shutdown
closes them. So booting any app stops the shared one first, and the shared client refuses to ask
anything after that. A module puts its cases that boot their own app after the ones that share.

Each boot and test: registries empty then handed back, a pool taking no job, a quiet index refresh.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from sift.kernel.config import get_settings
from sift.kernel.jobs import WorkerPool, worker_pool
from sift.kernel.wiring import DATABASE, part_of_app
from sift.main import create_app
from sift.slices.search import jobs as search_jobs
from sift.testing.fixtures import JOB_REGISTRIES

#: A seed: handed the booted app's database file and the directory the app keeps everything under.
Seed = Callable[[Path, Path], None]


#: How many apps this process has started. A shared client remembers the count it started at.
_boots = 0

#: The shared app that is running, as the stack that stops it. None when none is.
_running_shared: ExitStack | None = None


@contextmanager
def _registries_emptied() -> Iterator[None]:
    """Empty job registries for one boot, and what was there before handed back afterwards."""
    held = {name: getattr(worker_pool, name).copy() for name in JOB_REGISTRIES}
    for name in JOB_REGISTRIES:
        getattr(worker_pool, name).clear()
    try:
        yield
    finally:
        for name, before in held.items():
            registry = getattr(worker_pool, name)
            registry.clear()
            registry.update(before)


async def _idle(self: WorkerPool) -> None:
    """The pool's start, doing nothing: cases ask about routes; a never-started pool stops safely."""


async def _no_refresh(**_: object) -> None:
    """The search index refresh a boot schedules, not scheduled."""


@contextmanager
def _conditions(directory: Path) -> Iterator[None]:
    """What every boot here runs under, for as long as its registries and settings must hold.

    The worker pool never takes a job. Left running it picks up whatever a route enqueues (the
    player's transcode of a seeded clip that is not really a video, say) and the retries write to
    the database under the cases' feet, which reads now and then as a session that was refused.
    """
    with pytest.MonkeyPatch.context() as patch, _registries_emptied():
        patch.setenv("SIFT_DATA_DIR", str(directory / "data"))
        patch.setenv("SIFT_CACHE_DIR", str(directory / "cache"))
        patch.setattr(WorkerPool, "start", _idle)
        patch.setattr(search_jobs, "ensure_scheduled", _no_refresh)
        get_settings.cache_clear()
        try:
            yield
        finally:
            get_settings.cache_clear()


@contextmanager
def _started(directory: Path, seed: Seed | None, client: type[TestClient]) -> Iterator[TestClient]:
    """The application started over `directory` and seeded, and stopped on the way out."""
    global _boots
    _boots += 1
    app = create_app()
    with client(app) as running:
        if seed is not None:
            seed(part_of_app(app, DATABASE).path, directory)
        yield running


def _stop_the_shared_app() -> None:
    """Stop the shared app, if one is running, so the next boot has the process to itself."""
    global _running_shared
    running, _running_shared = _running_shared, None
    if running is not None:
        running.close()


@contextmanager
def booted(directory: Path, seed: Seed | None = None) -> Iterator[TestClient]:
    """The real application over `directory`, started, seeded, and stopped on the way out."""
    _stop_the_shared_app()
    with _conditions(directory), _started(directory, seed, TestClient) as client:
        yield client


class _SharedClient(TestClient):
    """A client of the shared app that refuses to ask anything once another app has booted."""

    def __init__(self, app: FastAPI, **kwargs: Any) -> None:
        super().__init__(app, **kwargs)
        self._booted_as = _boots

    def _still_running(self) -> None:
        if _boots != self._booted_as:
            raise RuntimeError(
                "the shared app was stopped when another app booted after it; put the module's "
                "cases that boot their own app after its cases that use the shared one"
            )

    def request(self, *args: Any, **kwargs: Any) -> httpx.Response:
        self._still_running()
        answer: httpx.Response = super().request(*args, **kwargs)
        return answer

    def websocket_connect(self, *args: Any, **kwargs: Any) -> WebSocketTestSession:
        self._still_running()
        return super().websocket_connect(*args, **kwargs)


def shared_client(seed: Seed, *, name: str) -> object:
    """A module-scoped fixture called `name`: one app, booted and seeded once, for the module's
    cases that only read. Assign it to a module-level name and ask for it by `name`."""

    @pytest.fixture(scope="module", name=name)
    def fixture(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
        global _running_shared
        _stop_the_shared_app()
        directory = tmp_path_factory.mktemp(name)
        # The conditions hold to the module's end; a later boot may stop the app sooner, and the
        # registries it filled are handed back only here, outside every test's own keeping of them.
        with _conditions(directory), ExitStack() as running:
            client = running.enter_context(_started(directory, seed, _SharedClient))
            _running_shared = running
            try:
                yield client
            finally:
                if _running_shared is running:
                    _running_shared = None

    return fixture
