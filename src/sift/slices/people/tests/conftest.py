# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real application, a real database, and two files with real bytes on disk.

These run over HTTP rather than against the service object. Attribution is a permission surface
and a promise about the filesystem, and both of those live in the router, the dependencies and the
access layer together. A test of the service alone would exercise the half that was never in
question.

The bytes on disk are not decoration. The load-bearing claim here is that assigning a person moves
nothing, and the only way to assert that is to have something real to compare before and after.

Everything is seeded through a connection of its own rather than the running app's handle. The
test client drives the application on its own event loop, and a write issued from the test's loop
meets a lock held on the app's, which fails as "bound to a different event loop" and has nothing
to do with what is being tested.
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
from sift.main import create_app
from sift.testing.auth import establish_session, give_pin

_EPOCH = 1_700_000_000

PASSWORD = "A-People-Test-Passw0rd!"

#: Well-formed, and never minted. The control every refusal is compared against.
NEVER_EXISTED = "01HX0000000000000000000099"

_INSERT_ASSET = """
INSERT INTO assets
    (id, identity, media_type, width, height, duration_ms, size_bytes, original_filename, added_at)
VALUES (?, ?, 'video', 1920, 1080, 4000, ?, ?, ?)
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

_GRANT_ON_OBJECT = """
INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)
VALUES (?, ?, ?, ?, ?, 0)
ON CONFLICT DO NOTHING
"""


@dataclass(frozen=True, slots=True)
class Library:
    """Two assets in one folder, and the ids needed to talk about them."""

    root: str
    folder: str
    shared: str
    private: str
    media: Path

    def path_of(self, name: str) -> Path:
        return self.media / "clips" / f"{name}.mp4"


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
    """Read back on a connection of this helper's own, for the same reason."""

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
    """Become somebody, holding a PIN. Returns their user id.

    The PIN is here because the vault refuses to conceal anything for a user without one:
    there would be nothing to open it with again. That refusal is tested where it is enforced; a
    test in this file that vaults something is asking what concealment does, not whether the
    precondition holds, so the user arrives already able to meet it.
    """
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"people-{role}-{who}", password=PASSWORD
    )
    give_pin(db_path(client), user_id)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


@pytest.fixture
def library(client: TestClient, tmp_path: Path) -> Library:
    """A root with a folder and two assets in it, with real bytes on disk."""
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

    ids: list[str] = []
    for name in ("shared", "private"):
        asset_id = new_id()
        ids.append(asset_id)
        payload = f"bytes of {name}".encode()
        (media / f"{name}.mp4").write_bytes(payload)
        statements.append(
            (_INSERT_ASSET, (asset_id, f"digest-{name}", len(payload), f"{name}.mp4", _EPOCH))
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
    return Library(
        root=root, folder=folder, shared=ids[0], private=ids[1], media=tmp_path / "media"
    )


def share(client: TestClient, asset_id: str, user_id: str) -> None:
    """Share one asset with one person, at the item level."""
    write(db_path(client), [(_SHARE_ITEM, (new_id(), asset_id, user_id))])


def grant_on(
    client: TestClient, object_type: str, object_id: str, user_id: str, effect: str = "share"
) -> None:
    """Point a grant at an object, which is what makes forgetting one on delete matter.

    Takes the kind the APPLICATION says, which is the kind the column holds: `acl_grants` was
    rebuilt at the access component's version 4 so its CHECK names the same words the wire does.
    This goes straight into the table rather than through the repository, so a kind the CHECK does
    not name is refused here rather than anywhere friendlier.
    """
    write(
        db_path(client),
        [(_GRANT_ON_OBJECT, (new_id(), object_type, object_id, user_id, effect))],
    )


def grants_naming(client: TestClient, object_type: str, object_id: str) -> list[dict[str, object]]:
    """Every grant on one object, read straight off the table, so it asks in the stored word."""
    return read(
        db_path(client),
        "SELECT * FROM acl_grants WHERE object_type = ? AND object_id = ?",
        (object_type, object_id),
    )


def make_person(client: TestClient, name: str, *, vault: bool = False) -> str:
    """Create a person through the API and return their id. Signed in as an admin already."""
    response = client.post("/api/people", json={"name": name, "vault": vault})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def make_username(client: TestClient, site: str, name: str) -> str:
    """Record where a file came from, the way a download does. Returns the row's id.

    Written straight to the tables rather than through an endpoint, because there is none: a
    username is an internal record of where a download came from, not an entity with screens of
    its own. The record is searchable and is what the resolver joins: it is simply not something
    anybody manages, so a test that needs one seeds it exactly as the downloader would.
    """
    site_id, username_id = new_id(), new_id()
    write(
        db_path(client),
        [
            (
                "INSERT INTO sites (id, name) VALUES (?, ?) ON CONFLICT(name) DO NOTHING",
                (site_id, site),
            ),
            (
                "INSERT INTO usernames (id, site_id, name, created_at) "
                "VALUES (?, (SELECT id FROM sites WHERE name = ?), ?, 0)",
                (username_id, site, name),
            ),
        ],
    )
    return username_id


def assign(client: TestClient, asset_ids: list[str], person_ids: list[str], *, add: bool = True):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/assets/people",
        json={"asset_ids": asset_ids, "person_ids": person_ids, "add": add},
    )
