# SPDX-License-Identifier: AGPL-3.0-or-later
"""A library with one of everything in it, so a count can be wrong in a visible way.

The world here is deliberately ASYMMETRIC. A fixture where every entity reaches the same number of
things cannot fail a test about which number goes beside which word: every wrong answer is also a
right one. So there are two people spread unevenly over two files, two tags one each, one site, one
collection, one photo set and one mark, and no two numbers on a page are the same.

Seeded through a connection of this file's own, for the reason every other slice's fixtures give: a
write issued from the test's event loop meets a lock held on the application's and fails as "bound
to a different event loop", which is not what any of these tests is about.
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
PASSWORD = "A-Related-Test-Passw0rd!"

_INSERT_ASSET = """
INSERT INTO assets
    (id, identity, media_type, width, height, duration_ms, size_bytes, original_filename, added_at)
VALUES (?, ?, ?, 1920, 1080, ?, ?, ?, ?)
"""
_INSERT_LOCATION = """
INSERT INTO asset_locations
    (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""


@dataclass(frozen=True, slots=True)
class World:
    """Everything the counts are taken over. See the module note on why it is lopsided.

    `jane` is on both files; `rick` is on the video only. So Jane reaches two files and two tags
    while Rick reaches one of each, and each of them is "seen with" exactly one other person,
    which is only one number if the route has correctly dropped the person whose page it is.
    """

    video: str
    photo: str
    jane: str
    rick: str
    portrait: str
    outdoors: str
    site: str
    collection: str
    photo_set: str
    loop: str


def write(db_path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> None:
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


def sign_in(client: TestClient, role: str = "admin", *, who: str = "one") -> str:
    """Become somebody, holding a PIN. Returns their user id."""
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"related-{role}-{who}", password=PASSWORD
    )
    give_pin(db_path(client), user_id)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


@pytest.fixture
def world(client: TestClient, tmp_path: Path) -> World:
    media = tmp_path / "media" / "shoot"
    media.mkdir(parents=True, exist_ok=True)

    root, folder = new_id(), new_id()
    video, photo = new_id(), new_id()
    jane, rick = new_id(), new_id()
    portrait, outdoors = new_id(), new_id()
    site, username = new_id(), new_id()
    collection, photo_set, loop = new_id(), new_id(), new_id()

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
    for asset_id, kind, name, duration in (
        (video, "video", "clip.mp4", 60_000),
        (photo, "image", "still.jpg", None),
    ):
        payload = f"bytes of {name}".encode()
        (media / name).write_bytes(payload)
        statements.append(
            (
                _INSERT_ASSET,
                (asset_id, f"digest-{name}", kind, duration, len(payload), name, _EPOCH),
            )
        )
        statements.append(
            (
                _INSERT_LOCATION,
                (new_id(), asset_id, root, folder, f"shoot/{name}", name, _EPOCH, _EPOCH),
            )
        )

    statements += [
        ("INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)", (jane, "Jane", _EPOCH)),
        ("INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)", (rick, "Rick", _EPOCH)),
        # Jane is on both files, Rick only on the video. That is what makes the two file counts
        # differ, and what makes "seen with" a number that is not simply "how many people exist".
        ("INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (video, jane)),
        ("INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (photo, jane)),
        ("INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (video, rick)),
        (
            "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)",
            (portrait, "portrait", _EPOCH),
        ),
        (
            "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)",
            (outdoors, "outdoors", _EPOCH),
        ),
        ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (video, portrait)),
        ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (photo, outdoors)),
        (
            "INSERT INTO sites (id, name) VALUES (?, ?)",
            (site, "example.test"),
        ),
        (
            "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, ?)",
            (username, site, "someone", _EPOCH),
        ),
        ("INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)", (video, username)),
        (
            "INSERT INTO collections (id, name, created_at) VALUES (?, ?, ?)",
            (collection, "best of", _EPOCH),
        ),
        (
            "INSERT INTO collection_items (collection_id, asset_id) VALUES (?, ?)",
            (collection, video),
        ),
        (
            "INSERT INTO photo_sets (id, name, origin, created_at) VALUES (?, ?, 'manual', ?)",
            (photo_set, "the shoot", _EPOCH),
        ),
        (
            "INSERT INTO photo_set_items (photo_set_id, asset_id, position) VALUES (?, ?, 0)",
            (photo_set, photo),
        ),
        (
            "INSERT INTO loops (id, asset_id, start_ms, end_ms, name, created_at)"
            " VALUES (?, ?, 1000, 4000, ?, ?)",
            (loop, video, "the good bit", _EPOCH),
        ),
    ]
    write(db_path(client), statements)
    return World(
        video=video,
        photo=photo,
        jane=jane,
        rick=rick,
        portrait=portrait,
        outdoors=outdoors,
        site=site,
        collection=collection,
        photo_set=photo_set,
        loop=loop,
    )
