# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real application, a real library, and real bytes on disk.

These run over HTTP because that is the only place the claim can be checked. The whole feature is
"a hidden thing is not in the answer", and the answer is what a route returns: a test against the
service would be asking the wrong object. Several of these deliberately call the grid, the counts
and the media routes rather than anything in this slice, because the point of vaulting something is
what happens *everywhere else*.

Two assets in one folder, plus a second folder holding a third, so a test can vault a folder and
show that what it conceals is the tree and not the one row.
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
from sift.slices.auth.crypto import hash_token
from sift.testing.auth import TEST_PIN, establish_session, give_pin

_EPOCH = 1_700_000_000

PASSWORD = "A-Vault-Test-Passw0rd!"

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


@dataclass(frozen=True, slots=True)
class Library:
    """Three assets across two folders, and the ids needed to talk about them."""

    root: str
    clips: str
    other: str
    first: str
    second: str
    elsewhere: str
    media: Path

    @property
    def in_clips(self) -> list[str]:
        return [self.first, self.second]


def write(db_path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    """Run writes on a connection of this helper's own, on its own loop.

    The test client drives the application on its own event loop, and a write issued from the
    test's loop meets a lock held on the app's.
    """

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
def second_browser(client: TestClient) -> TestClient:
    """Another client against the same running application.

    A second browser, not a second install: same database, same process, its own session. What it
    is for is showing that unlocking is a fact about one screen rather than about the user.

    Built around the already-running application rather than started as a second one, because the
    lifespan may only run once per process: the worker pool registers its job handlers globally,
    and a second boot is refused. Nothing here needs a second boot: it needs a second session.
    """
    return TestClient(client.app)


def db_path(client: TestClient) -> Path:
    return client.app.state.database.path  # type: ignore[attr-defined,no-any-return]


def sign_in(client: TestClient, role: str = "admin", *, who: str = "one") -> str:
    """Become somebody who has no PIN. Returns their user id.

    No PIN on purpose, and the opposite of what the other slices' helpers do. Half of what this
    file tests is the refusal that stands in front of the vault for a user with no way to open
    it, so the user has to start without one and be given one deliberately.
    """
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"vault-{role}-{who}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def sign_in_with_pin(client: TestClient, role: str = "admin", *, who: str = "one") -> str:
    """Become somebody who can actually use the vault. Returns their user id."""
    user_id = sign_in(client, role, who=who)
    give_pin(db_path(client), user_id)
    return user_id


def unlock(client: TestClient, pin: str = TEST_PIN) -> int:
    """Open the vault for this client's session. Returns the status, for the tests that want it."""
    return int(client.post("/api/vault/unlock", json={"pin": pin}).status_code)


def lock(client: TestClient) -> int:
    return int(client.post("/api/vault/lock").status_code)


def set_asset_vault(client: TestClient, asset_id: str, vault: bool):  # type: ignore[no-untyped-def]
    return client.put(f"/api/assets/{asset_id}/vault", json={"vault": vault})


def set_folder_vault(client: TestClient, folder_id: str, vault: bool):  # type: ignore[no-untyped-def]
    return client.put(f"/api/folders/{folder_id}/vault", json={"vault": vault})


_SHARE_ITEM = """
INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)
VALUES (?, 'item', ?, ?, 'share', 0)
ON CONFLICT DO NOTHING
"""


def share(client: TestClient, asset_id: str, user_id: str) -> None:
    """Share one file with one user.

    Only used to build the awkward case: somebody who was given a file and then had it vaulted out
    from under them. The grant outlives the vaulting, which is exactly what makes it worth a test.
    """
    write(db_path(client), [(_SHARE_ITEM, (new_id(), asset_id, user_id))])


_DEMOTE = "UPDATE users SET role = 'guest' WHERE id = ?"


def demote(client: TestClient, user_id: str) -> None:
    """Take a user's admin role away, leaving their live session alone.

    Written straight to the row because no route does this yet, which is the whole reason it is
    worth a test: the day one exists, the rule it depends on has to already hold. Everything else
    about a viewer is re-read from this row on every request, so a role written here takes effect
    on the user's very next call.
    """
    write(db_path(client), [(_DEMOTE, (user_id,))])


def unlock_key(client: TestClient) -> str:
    """The handle this client's session is known by in the unlock store.

    Derived here the way the server derives it, so a test can ask a store directly whether it holds
    an unlock for a session, which is how the question "what survives a restart?" is asked
    without booting a second application in a process that only allows one.
    """
    token = client.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None
    return hash_token(token)


def grid_ids(client: TestClient) -> list[str]:
    """The ids on the grid, which is the surface the whole feature is judged on."""
    return [item["id"] for item in client.get("/api/assets").json()["items"]]


def grid_total(client: TestClient) -> int:
    """The count beside the grid. Tested separately from the rows because a count that leaks is a
    way to report the size of a set without showing one of it."""
    return int(client.get("/api/assets").json()["total"])


@pytest.fixture
def library(client: TestClient, tmp_path: Path) -> Library:
    """One root, two folders, three assets, with real bytes on disk."""
    media = tmp_path / "media"
    (media / "clips").mkdir(parents=True, exist_ok=True)
    (media / "other").mkdir(parents=True, exist_ok=True)

    root, clips, other = new_id(), new_id(), new_id()
    statements: list[tuple[str, tuple[object, ...]]] = [
        (
            "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
            (root, "library", str(media), _EPOCH),
        ),
        (
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (clips, root, None, "clips", "clips"),
        ),
        (
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (other, root, None, "other", "other"),
        ),
    ]

    ids: list[str] = []
    for offset, (folder, where, name) in enumerate(
        ((clips, "clips", "first"), (clips, "clips", "second"), (other, "other", "elsewhere"))
    ):
        asset_id = new_id()
        ids.append(asset_id)
        payload = f"bytes of {name}".encode()
        (media / where / f"{name}.mp4").write_bytes(payload)
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
                    f"{where}/{name}.mp4",
                    f"{name}.mp4",
                    _EPOCH,
                    _EPOCH,
                ),
            )
        )

    write(db_path(client), statements)
    return Library(
        root=root,
        clips=clips,
        other=other,
        first=ids[0],
        second=ids[1],
        elsewhere=ids[2],
        media=media,
    )
