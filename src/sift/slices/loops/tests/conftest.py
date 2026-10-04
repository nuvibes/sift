# SPDX-License-Identifier: AGPL-3.0-or-later
"""One video somebody can see, one they cannot, and a running application over both.

Over HTTP rather than against the service, for the reason the other slices give: a loop's whole
permission story is a JOIN in the access layer and an ownership check in the router, and neither is
in the service object. A test of the service alone would exercise the half that was never in doubt.

Two videos rather than one, and that is the load-bearing part of this fixture: a loop reaches an
user only through the file it points at, so proving that needs a file the user cannot reach.
With one video every scoping test passes by having nothing to hide.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.kernel.jobs import WorkerPool
from sift.main import create_app
from sift.testing.auth import establish_session, give_pin

_EPOCH = 1_700_000_000

PASSWORD = "A-Loops-Test-Passw0rd!"

#: Well-formed, and never minted. The control every refusal is compared against.
NEVER_EXISTED = "01HX0000000000000000000099"

#: How long each video runs. A mark's end may not be past it, and the refusal needs a real number to
#: be past.
DURATION_MS = 60_000

_INSERT_ASSET = """
INSERT INTO assets
    (id, identity, media_type, width, height, duration_ms, size_bytes, original_filename, added_at)
VALUES (?, ?, 'video', 1920, 1080, ?, ?, ?, ?)
"""

_INSERT_LOCATION = """
INSERT INTO asset_locations
    (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_SHARE_ITEM = """
INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)
VALUES (?, 'item', ?, ?, 'share', 0)
ON CONFLICT DO NOTHING
"""


@dataclass(frozen=True, slots=True)
class Videos:
    """Two videos in one folder: one that gets shared, and one that never is."""

    root: str
    folder: str
    shared: str
    private: str


def write(db_path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    """Run writes on a connection of this helper's own, on its own loop."""

    async def run() -> None:
        database = Database(db_path, readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                for sql, params in statements:
                    await connection.execute(sql, params)
        finally:
            await database.close()

    asyncio.run(run())


def read(db_path: Path, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
    async def run() -> list[dict[str, object]]:
        database = Database(db_path, readers=1)
        await database.connect()
        try:
            rows = await database.fetch_all(sql, params)
            return [dict(row) for row in rows]
        finally:
            await database.close()

    return asyncio.run(run())


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))

    # The pool is kept from doing work, and it is not tidiness. These tests are about what marking
    # a moment PUTS IN the queue; nothing here needs the queue drained, because a still is written
    # straight to disk by `build_still`, the way the job would. Left running, the pool claims the
    # thumbnail job and finishes it, and `dedupe` then correctly queues a SECOND one for the next
    # mark at the same moment, since it only collapses onto work still waiting. A test asserting
    # that one moment queues one job would read that as the collapse failing. It is timing, so it
    # would only show under a loaded parallel run.
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
    """Become somebody, holding a PIN. Returns their user id."""
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"loops-{role}-{who}", password=PASSWORD
    )
    give_pin(db_path(client), user_id)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


@pytest.fixture
def videos(client: TestClient, tmp_path: Path) -> Videos:
    """Two videos with real bytes, in one folder."""
    media = tmp_path / "media" / "clips"
    media.mkdir(parents=True, exist_ok=True)

    root, folder = new_id(), new_id()
    statements: list[tuple[str, tuple[object, ...]]] = [
        (
            "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
            (root, "library", str(tmp_path / "media"), _EPOCH),
        ),
        (
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (folder, root, None, "clips", "clips"),
        ),
    ]

    made: list[str] = []
    for offset, name in enumerate(("shared", "private")):
        asset_id = new_id()
        made.append(asset_id)
        payload = f"bytes of {name}".encode()
        (media / f"{name}.mp4").write_bytes(payload)
        statements.append(
            (
                _INSERT_ASSET,
                (
                    asset_id,
                    f"digest-{name}",
                    DURATION_MS,
                    len(payload),
                    f"{name}.mp4",
                    _EPOCH + offset,
                ),
            )
        )
        statements.append(
            (
                _INSERT_LOCATION,
                (
                    new_id(),
                    asset_id,
                    root,
                    folder,
                    f"clips/{name}.mp4",
                    f"{name}.mp4",
                    _EPOCH,
                    _EPOCH,
                ),
            )
        )

    write(db_path(client), statements)
    return Videos(root=root, folder=folder, shared=made[0], private=made[1])


def share(client: TestClient, asset_id: str, user_id: str) -> None:
    write(db_path(client), [(_SHARE_ITEM, (new_id(), asset_id, user_id))])


def mark(client: TestClient, asset_id: str, start: int = 1_000, end: int = 4_000, **extra: object):  # type: ignore[no-untyped-def]
    """Save a stretch. Answered with the mark, or with the refusal."""
    return client.post(
        "/api/loops", json={"asset_id": asset_id, "start_ms": start, "end_ms": end, **extra}
    )


def loop_ids(client: TestClient) -> list[str]:
    return [entry["id"] for entry in client.get("/api/loops").json()["items"]]


def forget(client: TestClient, loop_id: str) -> dict[str, int]:
    """Forget one mark the one way there is (the bulk route), and answer what the wall reads: how
    many went, how many were skipped."""
    done = client.post("/api/loops/forget", json={"loop_ids": [loop_id]})
    assert done.status_code == 200, done.text
    body = done.json()
    return {"changed": body["changed"], "skipped": body["skipped"]}
