# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real application, a real database, and a small library to look at.

The grid is a permission surface, so these run over HTTP against the app as it ships rather than
against the service object. What matters is what a request gets back, and the checks that decide
that live in dependencies, the router and the access layer together: testing the service alone
would test the half that was never in doubt.

Everything is seeded through a connection of its own rather than through the running app's
database handle. The test client drives the application on its own event loop, and a write issued
from the test's loop meets a lock held on the app's, which fails as "bound to a different event
loop" and has nothing to do with what is being tested.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.content import DerivativeKind, derivative_relpath, params_key
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.main import create_app
from sift.testing.auth import establish_session

_EPOCH = 1_700_000_000

PASSWORD = "A-Browse-Test-Passw0rd!"

_INSERT_ASSET = """
INSERT INTO assets
    (id, identity, media_type, width, height, duration_ms, size_bytes, original_filename, added_at)
VALUES (?, ?, 'video', 1920, 1080, 4000, 14, ?, ?)
"""

_INSERT_LOCATION = """
INSERT INTO asset_locations
    (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_DERIVATIVE = """
INSERT INTO derivatives
       (id, asset_id, kind, rel_cache_path, params, size_bytes, content_hash, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_SHARE_ITEM = """
INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)
VALUES (?, 'item', ?, ?, 'share', 0)
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


def cache_dir(client: TestClient) -> Path:
    return client.app.state.settings.cache_dir  # type: ignore[attr-defined,no-any-return]


def sign_in(client: TestClient, role: str) -> str:
    """Become somebody. Returns their user id."""
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"browse-{role}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


@pytest.fixture
def library(client: TestClient, tmp_path: Path) -> Library:
    """A root with a folder and two assets in it, with real bytes on disk.

    One of them gets shared with the guest by the tests that need it; the other never is, and is
    what "a file this person may not see" means in here.
    """
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
        (media / f"{name}.mp4").write_bytes(f"bytes of {name}".encode())
        statements.append((_INSERT_ASSET, (asset_id, f"digest-{name}", f"{name}.mp4", _EPOCH)))
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


def give_derivative(
    client: TestClient,
    asset_id: str,
    kind: DerivativeKind = DerivativeKind.THUMB,
    *,
    extension: str = "jpg",
    body: bytes = b"jpeg-bytes",
    params: Mapping[str, Any] | None = None,
    digest: str | None = None,
) -> Path:
    """Register a derivative and write its bytes where the row says they are.

    `params` are the settings it was built with, and they are part of what makes a derivative
    unique: a scrub strip is filed under its own layout, so a test that leaves them empty is
    seeding a row nothing will find by that layout. Written through the same two functions the
    application uses, so a test cannot file a row somewhere the application would not look.

    `digest` is what the picture's bytes are, and NONE is the default on purpose. That is the state
    of every derivative built before Sift recorded one, and it is the state that makes a picture be
    served the careful way, so a test that says nothing about it is testing the careful path,
    which is what almost every test here means to do.
    """
    rel = derivative_relpath(asset_id, kind, extension=extension, params=params)
    write(
        db_path(client),
        [
            (
                _INSERT_DERIVATIVE,
                (
                    new_id(),
                    asset_id,
                    kind.value,
                    rel,
                    params_key(params),
                    len(body),
                    digest,
                    _EPOCH,
                ),
            )
        ],
    )
    path = cache_dir(client) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def set_app_setting(client: TestClient, key: str, value: str) -> None:
    """Write an app setting straight into its table, the way an admin turning it on would."""
    write(
        db_path(client),
        [
            (
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        ],
    )
