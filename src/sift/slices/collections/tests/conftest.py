# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real application, a real database, and three files with real bytes on disk.

These run over HTTP rather than against the service object. A collection is a permission surface
and a promise about the filesystem at once, and those live in the router, the
dependencies and the access layer together: a test of the service alone would exercise the half
that was never in question.

The bytes on disk are not decoration. The load-bearing claim in this slice is that adding something
to a collection moves nothing, and the only way to assert that is to have something real to compare
before and after.

Three assets rather than two, because with two an order and its reverse are the only orders.

Everything is seeded through a connection of its own rather than the running app's handle. The test
client drives the application on its own event loop, and a write issued from the test's loop meets
a lock held on the app's, which fails as "bound to a different event loop" and has nothing to do
with what is being tested.
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
from sift.testing.auth import establish_session, give_pin, hide_for_caller

_EPOCH = 1_700_000_000

PASSWORD = "A-Collections-Test-Passw0rd!"

#: Well-formed, and never minted. The control every refusal is compared against.
NEVER_EXISTED = "01HX0000000000000000000099"

#: The three files, in the order they are created. `first` is the one shared with a guest in the
#: scoping tests, so the other two are the ones a guest must never be told about.
NAMES = ("first", "second", "third")

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

_GRANT_ON_COLLECTION = """
INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)
VALUES (?, 'collection', ?, ?, ?, 0)
ON CONFLICT DO NOTHING
"""


@dataclass(frozen=True, slots=True)
class Library:
    """Three assets in one folder, and the ids needed to talk about them."""

    root: str
    folder: str
    first: str
    second: str
    third: str
    media: Path

    @property
    def all_ids(self) -> list[str]:
        return [self.first, self.second, self.third]

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
        db_path(client), role=role, username=f"collections-{role}-{who}", password=PASSWORD
    )
    give_pin(db_path(client), user_id)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


@pytest.fixture
def library(client: TestClient, tmp_path: Path) -> Library:
    """A root with a folder and three assets in it, with real bytes on disk."""
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
    for offset, name in enumerate(NAMES):
        asset_id = new_id()
        ids.append(asset_id)
        payload = f"bytes of {name}".encode()
        (media / f"{name}.mp4").write_bytes(payload)
        # Staggered `added_at`, so newest first and oldest first are two different orders.
        statements.append(
            (
                _INSERT_ASSET,
                (asset_id, f"digest-{name}", len(payload), f"{name}.mp4", _EPOCH + offset),
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
    return Library(
        root=root,
        folder=folder,
        first=ids[0],
        second=ids[1],
        third=ids[2],
        media=tmp_path / "media",
    )


def share(client: TestClient, asset_id: str, user_id: str) -> None:
    """Share one asset with one person, at the item level."""
    write(db_path(client), [(_SHARE_ITEM, (new_id(), asset_id, user_id))])


def grant_on_collection(
    client: TestClient, collection_id: str, user_id: str, effect: str = "share"
) -> None:
    """Point a grant at a collection, which is what makes forgetting one on delete matter."""
    write(db_path(client), [(_GRANT_ON_COLLECTION, (new_id(), collection_id, user_id, effect))])


def grants_naming(client: TestClient, collection_id: str) -> list[dict[str, object]]:
    return read(
        db_path(client),
        "SELECT * FROM acl_grants WHERE object_type = 'collection' AND object_id = ?",
        (collection_id,),
    )


def vault_asset(client: TestClient, asset_id: str) -> None:
    """Put one file in the vault, which conceals it from everybody, admins included."""
    hide_for_caller(client, "asset", asset_id)


def make_collection(client: TestClient, name: str) -> str:
    """Create a collection through the API and return its id. Signed in as an admin already."""
    response = client.post("/api/collections", json={"name": name})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def set_vault(client: TestClient, collection_id: str, vault: bool):  # type: ignore[no-untyped-def]
    """Conceal a collection, or reveal it again. Answered with no body."""
    return client.put(f"/api/collections/{collection_id}/vault", json={"vault": vault})


def edit_items(client: TestClient, collection_id: str, asset_ids: list[str], action: str = "add"):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/collections/{collection_id}/items",
        json={"asset_ids": asset_ids, "action": action},
    )


def item_ids(client: TestClient, collection_id: str) -> list[str]:
    """The collection's contents in the order the API serves them."""
    response = client.get(f"/api/collections/{collection_id}/items")
    assert response.status_code == 200, response.text
    return [str(item["id"]) for item in response.json()["items"]]
