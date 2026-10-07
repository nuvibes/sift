# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real application, a real database, and files with real bytes on disk.

Over HTTP rather than against the service object, for the reason the collections slice gives: a
photo set is a permission surface, a promise about the filesystem and a grouping all together, and
those live in the router, the dependencies and the access layer together. A test of the service
alone would exercise the half that was never in doubt.

The bytes on disk are not decoration. The load-bearing claim in this slice is that grouping pictures
moves nothing, and the only way to assert that is to have something real to compare.

Everything is seeded through a connection of its own rather than the running app's handle: the test
client drives the application on its own event loop, and a write issued from the test's loop meets a
lock held on the app's, which fails as "bound to a different event loop" and has nothing to do with
what is being tested.
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

PASSWORD = "A-PhotoSets-Test-Passw0rd!"

#: Well-formed, and never minted. The control every refusal is compared against.
NEVER_EXISTED = "01HX0000000000000000000099"

#: Two stills and one video in one folder. The video matters: a set derived from a folder is a set
#: of PICTURES, so a folder holding a clip is the case that proves the filter rather than the happy
#: path that would pass either way.
STILLS = ("one", "two")
CLIP = "moving"

_INSERT_ASSET = """
INSERT INTO assets
    (id, identity, media_type, width, height, duration_ms, size_bytes, original_filename, added_at)
VALUES (?, ?, ?, 1600, 1200, ?, ?, ?, ?)
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

_GRANT_ON_SET = """
INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)
VALUES (?, 'photo_set', ?, ?, ?, 0)
ON CONFLICT DO NOTHING
"""


@dataclass(frozen=True, slots=True)
class Shoot:
    """Two pictures and a clip in one folder, and the ids needed to talk about them."""

    root: str
    folder: str
    first: str
    second: str
    clip: str
    media: Path

    @property
    def pictures(self) -> list[str]:
        return [self.first, self.second]

    def path_of(self, name: str, suffix: str = "jpg") -> Path:
        return self.media / "shoot" / f"{name}.{suffix}"


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
    there would be nothing to open it with again.
    """
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"sets-{role}-{who}", password=PASSWORD
    )
    give_pin(db_path(client), user_id)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


@pytest.fixture
def shoot(client: TestClient, tmp_path: Path) -> Shoot:
    """A root with one folder holding two stills and one clip, with real bytes on disk."""
    media = tmp_path / "media" / "shoot"
    media.mkdir(parents=True, exist_ok=True)

    root, folder = new_id(), new_id()
    statements: list[tuple[str, tuple[object, ...]]] = [
        (
            "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
            (root, "library", str(tmp_path / "media"), _EPOCH),
        ),
        (
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (folder, root, None, "shoot", "shoot"),
        ),
    ]

    made: dict[str, str] = {}
    # Deliberately out of alphabetical order in the ADDED order: a set derived from a folder is
    # numbered by filename, so seeding in filename order would let a date ordering pass as one.
    seeds = ((STILLS[1], "image", "jpg"), (STILLS[0], "image", "jpg"), (CLIP, "video", "mp4"))
    for offset, (name, kind, suffix) in enumerate(seeds):
        asset_id = new_id()
        made[name] = asset_id
        payload = f"bytes of {name}".encode()
        (media / f"{name}.{suffix}").write_bytes(payload)
        statements.append(
            (
                _INSERT_ASSET,
                (
                    asset_id,
                    f"digest-{name}",
                    kind,
                    4000 if kind == "video" else None,
                    len(payload),
                    f"{name}.{suffix}",
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
                    f"shoot/{name}.{suffix}",
                    f"{name}.{suffix}",
                    _EPOCH,
                    _EPOCH,
                ),
            )
        )

    write(db_path(client), statements)
    return Shoot(
        root=root,
        folder=folder,
        first=made[STILLS[0]],
        second=made[STILLS[1]],
        clip=made[CLIP],
        media=tmp_path / "media",
    )


def share(client: TestClient, asset_id: str, user_id: str) -> None:
    """Share one file with one person, at the item level."""
    write(db_path(client), [(_SHARE_ITEM, (new_id(), asset_id, user_id))])


def grant_on_set(client: TestClient, set_id: str, user_id: str, effect: str = "share") -> None:
    """Point a grant at a set, which is what makes forgetting one on delete matter."""
    write(db_path(client), [(_GRANT_ON_SET, (new_id(), set_id, user_id, effect))])


def grants_naming(client: TestClient, set_id: str) -> list[dict[str, object]]:
    return read(
        db_path(client),
        "SELECT * FROM acl_grants WHERE object_type = 'photo_set' AND object_id = ?",
        (set_id,),
    )


def make_set(client: TestClient, name: str) -> str:
    """Create a set through the API and return its id. Signed in as an admin already."""
    response = client.post("/api/photo-sets", json={"name": name})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def edit_items(client: TestClient, set_id: str, asset_ids: list[str], *, remove: bool = False):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/photo-sets/{set_id}/items",
        params={"remove": str(remove).lower()},
        json={"asset_ids": asset_ids},
    )


def set_ids(client: TestClient) -> list[str]:
    return [entry["id"] for entry in client.get("/api/photo-sets").json()["items"]]


def contents(client: TestClient, set_id: str) -> list[str]:
    """What is in a set, read the way the screen reads it: the ordinary asset query, narrowed.

    Deliberately NOT a listing of its own. The set's order lives in that query's ORDER BY, so asking
    any other way would be asking a second question and the two could come to differ.
    """
    answer = client.get(
        "/api/assets", params={"photo_sets": set_id, "photo_set": set_id, "limit": 50}
    )
    assert answer.status_code == 200, answer.text
    return [item["id"] for item in answer.json()["items"]]


def positions(client: TestClient, set_id: str) -> dict[str, int]:
    """The stored positions, read straight from the table.

    Behind the API on purpose: the order coming back correctly is one claim, and the sequence
    actually being written down is another. A view that sorted in Python would satisfy the first
    and fail this.
    """
    rows = read(
        db_path(client),
        "SELECT asset_id, position FROM photo_set_items WHERE photo_set_id = ?",
        (set_id,),
    )
    return {str(row["asset_id"]): int(str(row["position"])) for row in rows}
