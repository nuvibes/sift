# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real application, a library on disk, and two users to share it between.

Over HTTP against the app as it ships, because what is being tested is not whether a row lands in
a table (that much the kernel already proves), but whether making that row through the endpoint
changes what somebody else's next request comes back with. Half of this slice's value is that the
two halves meet: the panel writes a grant and the resolver reads it, on the very next request, with
nothing cached in between.

Everything is seeded on a connection of this file's own. The test client drives the application on
its own event loop, and a write issued from the test's loop meets a lock held on the app's.
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
from sift.testing.auth import establish_session

_EPOCH = 1_700_000_000

PASSWORD = "A-Sharing-Test-Passw0rd!"

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

_INSERT_PERSON = "INSERT INTO people (id, name, notes, created_at) VALUES (?, ?, NULL, 0)"

_LINK_PERSON = "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)"

_INSERT_TAG = "INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)"

_LINK_TAG = "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)"


@dataclass(frozen=True, slots=True)
class Library:
    """Two clips in one folder, one of them attributed to a person and carrying a tag.

    Two, so that "the guest sees what was shared" can be told apart from "the guest sees
    everything": with one clip those two are the same answer.
    """

    root: str
    folder: str
    first: str
    second: str
    person: str
    tag: str


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


def sign_in(client: TestClient, role: str, *, username: str | None = None) -> str:
    """Become somebody, and return their user id.

    The session is swapped on the same client rather than a second one being built, so a test can
    make a change as an admin and then look at the result as the guest without anything having
    been kept between the two but the database.
    """
    who = username or f"sharing-{role}"
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=who, password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


@pytest.fixture
def library(client: TestClient, tmp_path: Path) -> Library:
    media = tmp_path / "media" / "clips"
    media.mkdir(parents=True, exist_ok=True)

    root, folder, person, tag = new_id(), new_id(), new_id(), new_id()
    statements: list[tuple[str, tuple[object, ...]]] = [
        (
            "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
            (root, "library", str(tmp_path / "media"), _EPOCH),
        ),
        (
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (folder, root, None, "clips", "clips"),
        ),
        (_INSERT_PERSON, (person, "seeded person")),
        (_INSERT_TAG, (tag, "seeded tag")),
    ]

    ids: list[str] = []
    for name in ("first", "second"):
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

    # Only the first clip carries the person and the tag, so a logical share reaches one of the two
    # rather than all of them, which is what makes a logical share observably a share.
    statements.append((_LINK_PERSON, (ids[0], person)))
    statements.append((_LINK_TAG, (ids[0], tag)))

    write(db_path(client), statements)
    return Library(root=root, folder=folder, first=ids[0], second=ids[1], person=person, tag=tag)


def visible_ids(client: TestClient) -> list[str]:
    """Every asset the user currently signed in can see, by id."""
    response = client.get("/api/assets")
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


_INSERT_SITE = "INSERT INTO sites (id, name, parent_id) VALUES (?, ?, ?)"

_INSERT_USERNAME = "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, 0)"

_FILE_UNDER_USERNAME = "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)"

_INSERT_PHOTO_SET = "INSERT INTO photo_sets (id, name, created_at) VALUES (?, ?, 0)"

_LINK_PHOTO_SET = "INSERT INTO photo_set_items (photo_set_id, asset_id) VALUES (?, ?)"


@dataclass(frozen=True, slots=True)
class Reach:
    """The library again, with the two shapes a reach report has to follow up and out of.

    A NETWORK over a LABEL, and the second clip released by the label: the files are filed under
    what published them and never under the network, so "shared by the network" is a sentence only
    a reader that walks `sites.parent_id` can say. A PHOTO SET over the first clip, for the
    other kind of membership a grant can name: it is the one kind that can be shared and not the
    one kind a file's grant chain was asking about.
    """

    network: str
    label: str
    username: str
    photo_set: str


@pytest.fixture
def reach(client: TestClient, library: Library) -> Reach:
    network, label, username, photo_set = new_id(), new_id(), new_id(), new_id()
    write(
        db_path(client),
        [
            (_INSERT_SITE, (network, "seeded network", None)),
            (_INSERT_SITE, (label, "seeded label", network)),
            (_INSERT_USERNAME, (username, label, "seeded-username")),
            (_FILE_UNDER_USERNAME, (library.second, username)),
            (_INSERT_PHOTO_SET, (photo_set, "seeded set")),
            (_LINK_PHOTO_SET, (photo_set, library.first)),
        ],
    )
    return Reach(network=network, label=label, username=username, photo_set=photo_set)
