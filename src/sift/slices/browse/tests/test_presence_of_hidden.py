# SPDX-License-Identifier: AGPL-3.0-or-later
"""A thing kept from the viewer never makes a file "have" one: the Has and No rows and the walls
they open, in every vault state, for an admin and a guest."""

from __future__ import annotations

import asyncio
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import AssetFilter, Concealment, Role, Viewer, Where
from sift.kernel.access.repository.read_files import FileReads
from sift.kernel.ids import new_id
from sift.slices.auth import current_viewer
from sift.slices.browse.tests.conftest import Library, db_path, share, sign_in, write

pytestmark = pytest.mark.integration

#: Each dimension's thing, link and per-user table, as (table, link, link column, state table).
_THINGS = {
    "tags": ("tags", "asset_tags", "tag_id", "tag_user_state"),
    "people": ("people", "asset_people", "person_id", "person_user_state"),
    "collections": ("collections", "collection_items", "collection_id", "collection_user_state"),
    "photo_sets": ("photo_sets", "photo_set_items", "photo_set_id", "photo_set_user_state"),
    "songs": ("songs", "song_files", "song_id", "song_user_state"),
}


def _linked(client: TestClient, dimension: str, asset_id: str, name: str) -> str:
    """A thing of this dimension holding one file: the id whose hiding keeps it from a viewer."""
    thing = new_id()
    if dimension == "sites":
        # Hidden by the network above it, so the chain is what is tested.
        network, username = new_id(), new_id()
        write(
            db_path(client),
            [
                ("INSERT INTO sites (id, name) VALUES (?, ?)", (network, name + " Network")),
                (
                    "INSERT INTO sites (id, name, parent_id) VALUES (?, ?, ?)",
                    (thing, name, network),
                ),
                (
                    "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, 0)",
                    (username, thing, name.lower()),
                ),
                (
                    "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
                    (asset_id, username),
                ),
            ],
        )
        return network
    table, link, column, _ = _THINGS[dimension]
    write(
        db_path(client),
        [
            (f"INSERT INTO {table} (id, name, created_at) VALUES (?, ?, 0)", (thing, name)),  # noqa: S608
            (f"INSERT INTO {link} (asset_id, {column}) VALUES (?, ?)", (asset_id, thing)),  # noqa: S608
        ],
    )
    return thing


def _hide(client: TestClient, dimension: str, thing: str, user_id: str) -> None:
    table, column = ("site_user_state", "site_id")
    if dimension != "sites":
        _, _, column, table = _THINGS[dimension]
    write(
        db_path(client),
        [
            (
                f"INSERT INTO {table} ({column}, user_id, hidden, hidden_at, updated_at)"  # noqa: S608
                " VALUES (?, ?, 1, 0, 0)",
                (thing, user_id),
            )
        ],
    )


@contextmanager
def _as(client: TestClient, viewer: Viewer) -> Iterator[None]:
    client.app.dependency_overrides[current_viewer] = lambda: viewer  # type: ignore[attr-defined]
    try:
        yield
    finally:
        client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def _column(client: TestClient, facet: str) -> dict[str, int]:
    answer = client.get("/api/assets/facets", params={"facet": facet, "limit": "200"})
    assert answer.status_code == 200, answer.text
    return {row["label"] or row["value"]: row["count"] for row in answer.json()["values"]}


def _total(client: TestClient, **params: str) -> int:
    answer = client.get("/api/assets", params={"limit": "200", **params})
    assert answer.status_code == 200, answer.text
    return int(answer.json()["total"])


def _rows(client: TestClient, dimension: str) -> tuple[dict[str, int], int, int, int]:
    """The Has and No rows, and the totals of the walls they open and of the whole wall."""
    column = _column(client, dimension)
    heads = {value: n for value, n in column.items() if value in ("any", "none")}
    has = _total(client, q=f"{dimension}:any")
    no = _total(client, q=f"{dimension}:none")
    assert heads.get("any", 0) == has and heads.get("none", 0) == no, (heads, has, no)
    assert has + no == _total(client)
    return column, has, no, _total(client)


@pytest.mark.parametrize("role", [Role.ADMIN, Role.GUEST])
@pytest.mark.parametrize(
    "dimension", ["tags", "people", "sites", "collections", "photo_sets", "songs"]
)
def test_a_thing_kept_from_the_viewer_does_not_make_a_file_have_one(
    client: TestClient, library: Library, dimension: str, role: Role
) -> None:
    """The private file's only thing of this kind is hidden; the shared file's is not."""
    user_id = sign_in(client, role.value)
    if role is Role.GUEST:
        sign_in(client, "admin")
        share(client, library.shared, user_id)
        share(client, library.private, user_id)
    _linked(client, dimension, library.shared, "Harbour")
    _hide(client, dimension, _linked(client, dimension, library.private, "Lantern"), user_id)

    def viewer(open_vault: bool, concealment: Concealment) -> Viewer:
        return Viewer(id=user_id, role=role, show_hidden=open_vault, concealment=concealment)

    # A locked tile stays on the wall, so only the rule keeps its hidden thing from counting.
    with _as(client, viewer(False, Concealment.PLACEHOLDER)):
        column, has, no, total = _rows(client, dimension)
        assert (has, no, total) == (1, 1, 2)
        assert "Lantern" not in column and column["Harbour"] == 1
    with _as(client, viewer(True, Concealment.PLACEHOLDER)):
        column, has, no, total = _rows(client, dimension)
        assert (has, no, total) == (2, 0, 2)
        assert column["Lantern"] == 1
    with _as(client, viewer(False, Concealment.FULLY_GONE)):
        assert _rows(client, dimension)[1:] == (1, 0, 1)


def test_a_locked_tile_counts_under_no_track(client: TestClient, library: Library) -> None:
    user_id = sign_in(client, "admin")
    write(
        db_path(client),
        [
            ("UPDATE assets SET music = ? WHERE id = ?", ("Harbour Lights", library.shared)),
            ("UPDATE assets SET music = ? WHERE id = ?", ("Lantern Hum", library.private)),
            (
                "INSERT INTO asset_user_state (asset_id, user_id, hidden, updated_at)"
                " VALUES (?, ?, 1, 0)",
                (library.private, user_id),
            ),
        ],
    )
    for open_vault, shown in (
        (False, {"Harbour Lights": 1}),
        (True, {"Harbour Lights": 1, "Lantern Hum": 1}),
    ):
        viewer = Viewer(
            id=user_id, role=Role.ADMIN, show_hidden=open_vault, concealment=Concealment.PLACEHOLDER
        )
        with _as(client, viewer):
            assert _total(client) == 2
            assert _column(client, "music") == shown


class _Stepped:
    """A plain connection counting the statement steps of every read."""

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.steps = 0
        self.connection.set_progress_handler(self._step, 1)

    def _step(self) -> int:
        self.steps += 1
        return 0

    async def fetch_all(self, statement: Any, params: Any = ()) -> list[Any]:
        return self.connection.execute(getattr(statement, "sql", statement), params).fetchall()

    async def fetch_one(self, statement: Any, params: Any = ()) -> Any:
        rows = await self.fetch_all(statement, params)
        return rows[0] if rows else None


def _wall_steps(path: Path, user_id: str) -> tuple[int, int]:
    stepped = _Stepped(path)
    viewer = Viewer(id=user_id, role=Role.ADMIN)
    try:
        page = asyncio.run(
            FileReads(cast(Any, stepped), cast(Any, None)).visible_assets(
                viewer, asset_filter=AssetFilter(where=Where("has_people"))
            )
        )
        return stepped.steps, page.total
    finally:
        stepped.connection.close()


def test_a_wall_that_shows_no_locked_tile_asks_nothing_of_hidden_things(
    client: TestClient, library: Library
) -> None:
    """The same files concealed by a hidden person, then by hiding each one, cost a wall under Has
    people the same steps when no locked tile can be on it."""
    path = db_path(client)
    user_id = sign_in(client, "admin")
    _linked(client, "people", library.shared, "Harbour")
    hidden = _linked(client, "people", library.private, "Lantern")
    files = [library.private]
    for n in range(12):
        files.append(new_id())
        write(
            path,
            [
                (
                    "INSERT INTO assets (id, identity, media_type, original_filename, added_at)"
                    " VALUES (?, ?, 'video', ?, 0)",
                    (files[-1], f"digest-{n}", f"kept {n}.mp4"),
                ),
                (
                    "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
                    (files[-1], hidden),
                ),
            ],
        )
    _hide(client, "people", hidden, user_id)
    by_person = _wall_steps(path, user_id)
    write(
        path,
        [("DELETE FROM person_user_state WHERE user_id = ?", (user_id,))]
        + [
            (
                "INSERT INTO asset_user_state (asset_id, user_id, hidden, updated_at)"
                " VALUES (?, ?, 1, 0)",
                (asset_id, user_id),
            )
            for asset_id in files
        ],
    )
    assert by_person == _wall_steps(path, user_id)
    assert by_person[1] == 1
