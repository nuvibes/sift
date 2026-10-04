# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real application, a real database, and three files a song can be put on.

Over HTTP, for the reason the Photo Sets suite gives: a song is a permission surface and a promise
about every file that carries it, and both live in the router, the dependencies and the access layer
together. Everything is seeded through a connection of this helper's own, on its own loop, for the
reason written there too.
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
from sift.kernel.content.identity import MUSIC_FROM_ACOUSTID, seed_music_on
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.main import create_app
from sift.testing.auth import establish_session, give_pin

_EPOCH = 1_700_000_000

PASSWORD = "A-Songs-Test-Passw0rd!"

#: Well-formed, and never minted. The control every refusal is compared against.
NEVER_EXISTED = "01HX0000000000000000000098"

_INSERT_ASSET = """
INSERT INTO assets
    (id, identity, media_type, width, height, duration_ms, size_bytes, original_filename, added_at)
VALUES (?, ?, 'video', 1280, 720, 200000, ?, ?, ?)
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
class Clips:
    """Three clips in one folder: what songs are put on in these tests."""

    first: str
    second: str
    third: str

    @property
    def every(self) -> list[str]:
        return [self.first, self.second, self.third]


def run(db_path: Path, work) -> object:  # type: ignore[no-untyped-def]
    """Run `work(database)` on a connection of this helper's own, on its own loop."""

    async def go() -> object:
        database = Database(db_path, readers=1)
        await database.connect()
        try:
            return await work(database)
        finally:
            await database.close()

    return asyncio.run(go())


def read(db_path: Path, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
    async def work(database: Database) -> list[dict[str, object]]:
        return [dict(row) for row in await database.fetch_all(sql, params)]

    return run(db_path, work)  # type: ignore[return-value]


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
    """Become somebody, holding a PIN. Returns their user id."""
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"songs-{role}-{who}", password=PASSWORD
    )
    give_pin(db_path(client), user_id)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


@pytest.fixture
def clips(client: TestClient, tmp_path: Path) -> Clips:
    """A library folder holding three clips, with real bytes on disk."""
    media = tmp_path / "media" / "clips"
    media.mkdir(parents=True, exist_ok=True)
    root, folder = new_id(), new_id()
    made: list[str] = []

    async def work(database: Database) -> None:
        async with database.write() as connection:
            await connection.execute(
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                (root, "library", str(tmp_path / "media"), _EPOCH),
            )
            await connection.execute(
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
                " VALUES (?, ?, ?, ?, ?)",
                (folder, root, None, "clips", "clips"),
            )
            for offset, name in enumerate(("first", "second", "third")):
                asset_id = new_id()
                payload = f"bytes of {name}".encode()
                (media / f"{name}.mp4").write_bytes(payload)
                await connection.execute(
                    _INSERT_ASSET,
                    (asset_id, f"digest-{name}", len(payload), f"{name}.mp4", _EPOCH + offset),
                )
                await connection.execute(
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
                made.append(asset_id)

    run(db_path(client), work)
    return Clips(first=made[0], second=made[1], third=made[2])


def named_by_acoustid(
    client: TestClient, asset_id: str, song: str, recording: str = "rec-1"
) -> str:
    """AcoustID names a file's song, as the lookup writes it. Returns the song's id."""

    async def work(database: Database) -> str:
        async with database.write() as connection:
            assert await seed_music_on(
                connection,
                asset_id,
                song,
                source=MUSIC_FROM_ACOUSTID,
                facts={"score": 0.9, "recording": recording},
                recording_id=recording,
                score=0.9,
            )
        row = await database.fetch_one(
            "SELECT song_id FROM song_files WHERE asset_id = ?", (asset_id,)
        )
        assert row is not None
        return str(row["song_id"])

    return run(db_path(client), work)  # type: ignore[return-value]


def share(client: TestClient, asset_id: str, user_id: str) -> None:
    """Share one file with one person, at the item level."""

    async def work(database: Database) -> None:
        await database.execute(_SHARE_ITEM, (new_id(), asset_id, user_id))

    run(db_path(client), work)


def field_of(client: TestClient, asset_id: str) -> str | None:
    rows = read(db_path(client), "SELECT music FROM assets WHERE id = ?", (asset_id,))
    music = rows[0]["music"]
    return None if music is None else str(music)
