# SPDX-License-Identifier: AGPL-3.0-or-later
"""The watermark endpoints, called over HTTP against a real application.

Everything that reports on this feature or spends the machine's time and network on it takes an
admin, and that is checked as a raw request rather than through a screen: the failure worth catching
is a route that never asked.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.slices.watermarks import settings as watermark_settings
from sift.slices.watermarks.jobs import WATERMARK_FETCH_MODELS
from sift.testing.auth import establish_session
from sift.testing.settings import set_app_setting

pytestmark = pytest.mark.integration

PASSWORD = "A-Watermarks-Test-Passw0rd!"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    app: FastAPI = create_app()
    with TestClient(app) as running:
        yield running
    get_settings.cache_clear()


def _db(client: TestClient) -> Path:
    return client.app.state.database.path  # type: ignore[attr-defined,no-any-return]


def _sign_in(client: TestClient, role: str) -> None:
    _, token, csrf = establish_session(
        _db(client), role=role, username=f"marks-{role}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf


def _queued(client: TestClient) -> list[tuple[str, str]]:
    """Every job waiting, read out of the queue itself rather than through a stand-in."""
    path = _db(client)

    async def run() -> list[tuple[str, str]]:
        database = Database(path, readers=1)
        await database.connect()
        try:
            rows = await database.fetch_all("SELECT type, payload FROM jobs ORDER BY id")
            return [(str(row["type"]), str(row["payload"])) for row in rows]
        finally:
            await database.close()

    return asyncio.run(run())


def test_a_guest_is_refused_every_endpoint(client: TestClient) -> None:
    """There is no file a caller chooses in any of these, so refusing outright leaks nothing."""
    _sign_in(client, "guest")
    assert client.get("/api/watermarks/status").status_code == 403
    assert client.post("/api/watermarks/models/fetch").status_code == 403
    assert client.delete("/api/watermarks/reads").status_code == 403


def test_an_admin_is_answered_by_every_endpoint_in_the_order_a_fresh_install_meets_them(
    client: TestClient,
) -> None:
    """One application for the whole walk, because each one costs seconds to boot.

    Off: the status says so with no problem to report, a fetch is refused and queues nothing
    (downloading models for a feature nobody switched on is the network call the switch exists to
    prevent) and forgetting what was read still answers, because clearing what an earlier decision
    left behind is what somebody does after switching it off. On without models: the status says
    to fetch them rather than that something is broken, and a fetch hands back the job doing it.
    """
    _sign_in(client, "admin")

    body = client.get("/api/watermarks/status").json()
    assert (body["enabled"], body["ready"], body["problem"]) == (False, False, None)
    assert (body["read_files"], body["marks_found"], body["running_jobs"]) == (0, 0, 0)
    assert body["installed"] == []

    refused = client.post("/api/watermarks/models/fetch")
    assert refused.status_code == 409
    assert "switched off" in refused.text
    assert not any(kind == WATERMARK_FETCH_MODELS for kind, _ in _queued(client))

    forgot = client.delete("/api/watermarks/reads")
    assert forgot.status_code == 200
    assert forgot.json() == {"removed": 0}

    set_app_setting(_db(client), watermark_settings.ENABLED_KEY, "true")
    body = client.get("/api/watermarks/status").json()
    assert body["enabled"] is True and body["ready"] is False
    assert "have not been obtained" in body["problem"]

    fetching = client.post("/api/watermarks/models/fetch?again=true")
    assert fetching.status_code == 200
    fetches = [payload for kind, payload in _queued(client) if kind == WATERMARK_FETCH_MODELS]
    assert [json.loads(payload) for payload in fetches] == [{"again": True}]
    assert fetching.json()["job_id"]
