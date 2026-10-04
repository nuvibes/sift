# SPDX-License-Identifier: AGPL-3.0-or-later
"""One row per Theater session, written from the wall's opening and closing reports.

Read back with a plain connection to the file, the way the player's tests read `plays`: there is no
route that returns a session, and adding one only for the tests would be a reader of a table written
before anything reads it, which is the point of gathering it now.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME
from sift.slices.theater.tests.conftest import db_path, sign_in

pytestmark = pytest.mark.unit


def _sessions(client: TestClient) -> list[dict[str, Any]]:
    """The session rows, read through the kernel's own handle (never the driver: a connection
    opened beside it misses the pragmas and the single-writer lock)."""

    async def run() -> list[dict[str, Any]]:
        database = Database(db_path(client), readers=1)
        await database.connect()
        try:
            rows = await database.fetch_all("SELECT * FROM theater_sessions ORDER BY id", ())
            return [dict(row) for row in rows]
        finally:
            await database.close()

    return asyncio.run(run())


def test_opening_and_closing_a_wall_is_one_row(client: TestClient) -> None:
    user_id = sign_in(client)

    opened = client.post("/api/theater/sessions/an-evening", json={})
    closed = client.post(
        "/api/theater/sessions/an-evening",
        json={
            "elapsed_ms": 3_600_000,
            "ended": True,
            "layout": "grid",
            "cells": 4,
            "arrangement": "a-saved-wall",
            "sources": ["", "in:holiday", "loops:any", "tags:beach"],
            "files": 37,
        },
    )

    assert (opened.status_code, closed.status_code) == (204, 204)
    (session,) = _sessions(client)
    assert session["user_id"] == user_id and session["session"] == "an-evening"
    assert (session["layout"], session["cells"], session["arrangement_id"]) == (
        "grid",
        4,
        "a-saved-wall",
    )
    assert json.loads(session["sources"]) == ["", "in:holiday", "loops:any", "tags:beach"]
    assert session["files"] == 37
    # Begun when the OPENING said, and ended when the closing landed.
    assert session["ended_at"] is not None and session["ended_at"] >= session["started_at"]


def test_a_lost_opening_still_writes_the_whole_session_from_its_close(client: TestClient) -> None:
    """The close carries how long the wall was open, so the start is derived rather than lost."""
    sign_in(client)

    client.post(
        "/api/theater/sessions/never-opened",
        json={"elapsed_ms": 600_000, "ended": True, "layout": "single", "cells": 1, "files": 3},
    )

    (session,) = _sessions(client)
    assert session["ended_at"] - session["started_at"] == 600
    assert session["files"] == 3


def test_a_late_opening_does_not_reopen_a_closed_session(client: TestClient) -> None:
    """Two keepalive requests can land in either order; the end, once written, stays written."""
    sign_in(client)

    client.post("/api/theater/sessions/out-of-order", json={"ended": True, "files": 5})
    client.post("/api/theater/sessions/out-of-order", json={})

    (session,) = _sessions(client)
    assert session["ended_at"] is not None and session["files"] == 5


def test_one_users_name_for_a_session_never_touches_anothers(client: TestClient) -> None:
    """The name is the browser's choice, so it is unique per user and not alone."""
    sign_in(client, who="one")
    client.post("/api/theater/sessions/same-name", json={"files": 2})
    sign_in(client, who="two")
    client.post("/api/theater/sessions/same-name", json={"ended": True, "files": 9})

    rows = _sessions(client)
    assert len(rows) == 2
    assert sorted(row["files"] for row in rows) == [2, 9]
    assert [row["ended_at"] is None for row in rows].count(True) == 1


def test_a_report_without_the_csrf_token_is_refused(client: TestClient) -> None:
    sign_in(client)
    del client.headers[CSRF_HEADER_NAME]

    assert client.post("/api/theater/sessions/any", json={}).status_code == 403
