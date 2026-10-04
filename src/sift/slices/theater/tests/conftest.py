# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real application and two users, which is the whole of what these tests need.

No library is seeded. Nothing in this slice reads an asset, resolves a permission or streams a byte:
a cell's source is a query, and running it is the search route's job under the resolver that
already decides who may see what. What is under test here is a saved wall: whose it is, whether it
adds up, and whether one user can reach another's.

Two users, because half of what this slice promises is only visible with a second one in the
database. A route scoped by `user_id` looks identical to an unscoped one until somebody else has a
row for it to return by mistake.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.jobs import WorkerPool
from sift.main import create_app
from sift.testing.auth import establish_session

PASSWORD = "A-Theater-Test-Passw0rd!"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """The application, with the worker pool held idle: nothing here queues any work."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))

    async def _no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", _no_workers)
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as running:
        yield running


def db_path(client: TestClient) -> Path:
    return client.app.state.database.path  # type: ignore[attr-defined,no-any-return]


def sign_in(client: TestClient, role: str = "admin", *, who: str = "one") -> str:
    """Become somebody, replacing whoever the client was. Returns their user id."""
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"theater-{role}-{who}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def cell(
    source: str = "",
    *,
    media_kind: str = "video_gif",
    ordering: str = "shuffle",
    end_behaviour: str = "once",
    timer_seconds: int | None = None,
    volume: int = 100,
    sort: str | None = None,
    aspect: str = "dynamic",
) -> dict[str, object]:
    """One cell, as the client sends it. Named arguments so a test says only what it is about."""
    return {
        "source": source,
        "media_kind": media_kind,
        "ordering": ordering,
        "end_behaviour": end_behaviour,
        "timer_seconds": timer_seconds,
        "volume": volume,
        "sort": sort,
        "aspect": aspect,
    }


def wall(
    name: str = "My wall",
    layout: str = "side_by_side",
    cells: list[dict[str, object]] | None = None,
    shape: dict[str, object] | None = None,
) -> dict[str, object]:
    """A whole arrangement, defaulting to the smallest one that adds up.

    No shape by default, which is deliberately the older spelling: a wall saved before walls could
    be built carries a preset name and nothing else, and most of these tests are about the parts
    that did not change.
    """
    body: dict[str, object] = {
        "name": name,
        "layout": layout,
        "cells": cells if cells is not None else [cell(), cell()],
    }
    if shape is not None:
        body["shape"] = shape
    return body


def row(*widths: int) -> dict[str, object]:
    """A shape of one row, with a cell of each width across it."""
    slots = []
    col = 0
    for width in widths:
        slots.append({"row": 0, "col": col, "row_span": 1, "col_span": width})
        col += width
    return {"rows": 1, "cols": col, "slots": slots}
