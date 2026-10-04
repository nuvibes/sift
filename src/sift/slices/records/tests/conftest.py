# SPDX-License-Identifier: AGPL-3.0-or-later
"""An application and a session. Nothing is seeded, and nothing needs to be.

This slice answers with a description of what a field IS, never with any thing's value, so there is
no library for its answer to depend on. An empty one is the honest fixture.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.testing.auth import establish_session, give_pin

PASSWORD = "A-Records-Test-Passw0rd!"


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


def db_path(client: TestClient) -> Path:
    return client.app.state.database.path  # type: ignore[attr-defined,no-any-return]


def sign_in(client: TestClient, role: str = "admin") -> str:
    """Become somebody. Returns their user id."""
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"records-{role}", password=PASSWORD
    )
    give_pin(db_path(client), user_id)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id
