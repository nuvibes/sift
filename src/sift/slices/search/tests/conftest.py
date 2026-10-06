# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real application, a real database, and a library with something of everything in it.

These run over HTTP rather than against the service object, because what is being tested is mostly
not the service. A search result is a parse, a scoped resolve, a full-text match and a permission
rule arriving at the same row together, and testing the middle of that would exercise the half that
was never in doubt.

The library is deliberately awkward, and every awkward thing in it is load-bearing:

  * **A person with three names.** Jane Doe has an alias and a linked username, so the same woman is
    findable three ways, and a test that only used her real name would pass with the alias
    resolver removed entirely.
  * **A nested folder.** `in:` on a parent has to find what is in the child, which a flat library
    cannot tell apart from `in:` matching the folder itself.
  * **A file only an admin may see, and one nobody may.** The first is the restricted case, the
    second the vault. They fail differently and both have to be invisible in results, in counts
    and in the dropdown.
  * **Filenames that share a middle.** `beach_sunset_2024` and `sunsetwalk` overlap on `sunset`,
    which is what makes a substring match provable rather than a coincidence of prefixes.
  * **One filename with NO underscore in it.** `sunsetwalk` is spelled that way deliberately. `_`
    is a LIKE wildcard meaning "any single character", so if every name here contained one, a test
    searching for `_` would return the same rows whether the wildcard was escaped or not, and
    the test asserting the escaping works would be unable to fail.

Everything is seeded through a connection of its own rather than the running app's handle. The
test client drives the application on its own event loop, and a write issued from the test's loop
meets a lock held on the app's, which fails as "bound to a different event loop" and has nothing
to do with what is being tested.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.access import index_assets
from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.kernel.jobs import WorkerPool
from sift.main import create_app
from sift.testing.auth import establish_session
from sift.testing.library import hide_for

PASSWORD = "A-Search-Test-Passw0rd!"

#: Fixed so that `added:` can be asserted against a known day rather than against "now".
#: Midnight UTC on the first of July, 2026.
EPOCH = 1_782_864_000

_INSERT_ASSET = """
INSERT INTO assets
    (id, identity, media_type, duration_ms, size_bytes, original_filename, added_at)
VALUES (?, ?, ?, ?, 1024, ?, ?)
"""

_INSERT_LOCATION = """
INSERT INTO asset_locations
    (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_GRANT = """
INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)
VALUES (?, ?, ?, ?, ?, 0)
ON CONFLICT DO NOTHING
"""


@dataclass(frozen=True, slots=True)
class World:
    """Everything seeded, and the ids needed to talk about it."""

    root: str
    clips: str
    holiday: str

    beach: str
    walk: str
    private: str
    vaulted: str

    tag_beach: str
    tag_city: str
    person: str
    username: str
    site: str
    collection: str
    photo_set: str

    names: dict[str, str] = field(default_factory=dict)

    @property
    def visible_to_guest(self) -> str:
        return self.beach


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


def put_song(db_path: Path, asset_id: str, name: str) -> str:
    """A song on one file, as a person's hand puts it there. Its id."""
    song_id = new_id()
    write(
        db_path,
        [
            (
                "INSERT INTO songs (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
                (song_id, name, name.lower()),
            ),
            (
                "INSERT INTO song_files (asset_id, song_id, added_at) VALUES (?, ?, 0)",
                (asset_id, song_id),
            ),
        ],
    )
    return song_id


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


def reindex(
    db_path: Path, *, asset_id: str | None = None, asset_ids: list[str] | None = None
) -> int:
    """Build the search index the way the job does: through the job's own function.

    Called from the tests rather than left to a worker, so that a test asserting what a search
    finds is not also asserting that a background queue got round to it.
    """

    async def run() -> int:
        database = Database(db_path, readers=1)
        await database.connect()
        try:
            if asset_ids is not None:
                return await index_assets(database, asset_ids=asset_ids)
            return await index_assets(database, asset_id=asset_id, rebuild=asset_id is None)
        finally:
            await database.close()

    return asyncio.run(run())


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """The application, with the worker pool held idle.

    The tests that use this fixture drive indexing themselves (through `reindex` and `_drain`),
    precisely so that asserting what a search finds is not also asserting a background queue got
    round to it. A live pool does not help that and actively fights it: a search that finds the
    index behind queues a catch-up, and a worker claiming it between the request and the assertion
    turns a test that reads the queue into a coin flip that lands wrong under load. So the pool is
    kept from starting here. The refresh is off too (see `quiet_search_refresh`, autouse for
    every test that boots an app), and the tests that DO want a running pool build their own
    client rather than this one. `stop()` tolerates a `start()` that never populated its tasks.
    """
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))

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
    """Become somebody. Returns their user id."""
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"search-{role}-{who}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


@pytest.fixture
def world(client: TestClient, tmp_path: Path) -> World:
    """The library described in this module's docstring, indexed and ready to search."""
    media = tmp_path / "media"
    (media / "clips" / "holiday").mkdir(parents=True, exist_ok=True)

    root, clips, holiday = new_id(), new_id(), new_id()
    beach, walk, private, vaulted = new_id(), new_id(), new_id(), new_id()
    tag_beach, tag_city = new_id(), new_id()
    person, username, site, collection = new_id(), new_id(), new_id(), new_id()
    photo_set = new_id()

    statements: list[tuple[str, tuple[object, ...]]] = [
        (
            "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
            (root, "library", str(media), EPOCH),
        ),
        (
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (clips, root, None, "clips", "clips"),
        ),
        (
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (holiday, root, clips, "clips/holiday", "holiday"),
        ),
        ("INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)", (tag_beach, "beach", EPOCH)),
        ("INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)", (tag_city, "city", EPOCH)),
        (
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
            (person, "Jane Doe", EPOCH),
        ),
        (
            "INSERT INTO people_aliases (id, person_id, alias) VALUES (?, ?, ?)",
            (new_id(), person, "JD"),
        ),
        ("INSERT INTO sites (id, name, kind) VALUES (?, ?, ?)", (site, "TikTok", "video")),
        (
            "INSERT INTO usernames (id, site_id, name, person_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, site, "janed", person, EPOCH),
        ),
        (
            "INSERT INTO collections (id, name, created_at) VALUES (?, ?, ?)",
            (collection, "Best of", EPOCH),
        ),
        (
            "INSERT INTO photo_sets (id, name, origin, created_at) VALUES (?, ?, ?, ?)",
            (photo_set, "Beach shoot", "manual", EPOCH),
        ),
    ]

    # name -> (asset id, folder, media type, duration, added at)
    files = {
        "beach_sunset_2024.mp4": (beach, holiday, "video", 4_000, EPOCH),
        "sunsetwalk.mp4": (walk, clips, "video", 600_000, EPOCH + 86_400),
        "private_notes.mp4": (private, clips, "video", 1_000, EPOCH + 172_800),
        "vaulted_secret.mp4": (vaulted, clips, "image", None, EPOCH + 259_200),
    }
    for filename, (asset_id, folder, media_type, duration, added) in files.items():
        rel = "clips/holiday" if folder == holiday else "clips"
        (media / rel / filename).write_bytes(b"bytes of " + filename.encode())
        statements.append(
            (
                _INSERT_ASSET,
                (asset_id, f"digest-{filename}", media_type, duration, filename, added),
            )
        )
        statements.append(
            (
                _INSERT_LOCATION,
                (new_id(), asset_id, root, folder, f"{rel}/{filename}", filename, EPOCH, EPOCH),
            )
        )

    statements.extend(
        [
            ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (beach, tag_beach)),
            ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (walk, tag_city)),
            ("INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (beach, person)),
            (
                "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
                (beach, username),
            ),
            (
                "INSERT INTO collection_items (collection_id, asset_id) VALUES (?, ?)",
                (collection, beach),
            ),
            (
                "INSERT INTO photo_set_items (photo_set_id, asset_id, position) VALUES (?, ?, ?)",
                (photo_set, beach, 0),
            ),
        ]
    )

    write(db_path(client), statements)
    # Hidden by an admin, because that is the only way anything is hidden: a row is
    # concealed from the user who hid it and from nobody else. That admin is the user the searches
    # below run as, so what they may not find is what that user put out of sight.
    hide_for(db_path(client), "asset", vaulted, sign_in(client, "admin"))
    reindex(db_path(client))

    return World(
        root=root,
        clips=clips,
        holiday=holiday,
        beach=beach,
        walk=walk,
        private=private,
        vaulted=vaulted,
        tag_beach=tag_beach,
        tag_city=tag_city,
        person=person,
        username=username,
        site=site,
        collection=collection,
        photo_set=photo_set,
        names={value[0]: name for name, value in files.items()},
    )


def share(client: TestClient, object_type: str, object_id: str | None, user_id: str) -> None:
    """Open one thing up to one person."""
    write(db_path(client), [(_GRANT, (new_id(), object_type, object_id, user_id, "share"))])


def restrict(client: TestClient, object_type: str, object_id: str | None, user_id: str) -> None:
    """Close one thing off to one person. Any restrict anywhere wins."""
    write(db_path(client), [(_GRANT, (new_id(), object_type, object_id, user_id, "restrict"))])


def submit(client: TestClient, query: str) -> None:
    """Tell the server a search was RUN, which is what pressing Enter does.

    Distinct from `found` below, and the distinction is the point. The grid answers a query on every
    keystroke and on every page load; submitting one happens when somebody commits to it, and that
    is the moment the word index is asked whether it has fallen behind. A test that needs the
    catch-up to have been asked for has to submit rather than merely read.
    """
    # A typed search is its own subject and its own label: the memory holds two kinds of row now,
    # and the words are both what identifies this one and what it shows.
    response = client.post(
        "/api/search/history", json={"kind": "query", "subject": query, "label": query}
    )
    assert response.status_code == 204, response.text


def found(client: TestClient, **params: object) -> tuple[list[str], int]:
    """Run a search and return the ids it gave back, with the total beside them.

    Both, always. Half the claims in this slice are about the number rather than the rows, and a
    helper that returned only the rows would let a leaking count through every test that used it.

    It asks the library, which is what the search box asks. There is one address for a page of
    assets and a query is a filter of it, so every claim in this slice about what a query
    matches is a claim about the endpoint somebody's typing actually reaches, rather than about a
    second one that agreed with it right up until it did not.
    """
    response = client.get("/api/assets", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    return [item["id"] for item in body["items"]], int(body["total"])
