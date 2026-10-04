# SPDX-License-Identifier: AGPL-3.0-or-later
"""The record of searches: kept, stamped with the window it came from, forgotten with the box, and
what was opened from the wall a search narrowed."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sift.kernel.client import CLIENT_HEADER, DEVICE_COOKIE_NAME
from sift.kernel.db import Database
from sift.slices.search import jobs, schema
from sift.slices.search.tests.conftest import World, db_path, read

pytestmark = pytest.mark.integration

DEVICE = "a-search-device-16"

#: Every row a User's search history holds: the box's memory, the record, and what was opened.
_EVERY_ROW = (
    "SELECT id FROM search_history",
    "SELECT id FROM search_events",
    "SELECT id FROM search_opens",
)


def _search(client: TestClient, words: str, *, kind: str = "phone") -> None:
    answered = client.post(
        "/api/search/history",
        json={"kind": "query", "subject": words, "label": words, "results": 3},
        headers={CLIENT_HEADER: kind},
    )
    assert answered.status_code == 204, answered.text


def _opened(client: TestClient, words: str, asset_id: str) -> None:
    answered = client.post("/api/search/opened", json={"query": words, "asset_id": asset_id})
    assert answered.status_code == 204, answered.text


def test_a_search_is_stamped_with_the_window_and_device_it_came_from(
    client: TestClient, world: World
) -> None:
    client.cookies.set(DEVICE_COOKIE_NAME, DEVICE)
    _search(client, "harbour lights", kind="tablet")
    (row,) = read(db_path(client), "SELECT subject, device_id, client_kind FROM search_events")
    assert row == {"subject": "harbour lights", "device_id": DEVICE, "client_kind": "tablet"}


def test_a_file_opened_from_a_searched_wall_is_linked_to_that_search(
    client: TestClient, world: World
) -> None:
    _search(client, "sunset")
    _search(client, "sunset")
    _opened(client, "sunset", world.beach)
    _opened(client, " sunset ", world.walk)
    latest = read(db_path(client), "SELECT id FROM search_events ORDER BY id DESC LIMIT 1")[0]
    opens = read(db_path(client), "SELECT event_id, subject, asset_id FROM search_opens")
    assert {(one["event_id"], one["subject"]) for one in opens} == {(latest["id"], "sunset")}
    assert {one["asset_id"] for one in opens} == {world.beach, world.walk}


def test_an_open_with_no_search_behind_it_keeps_its_words(client: TestClient, world: World) -> None:
    _opened(client, "pasted in", world.beach)
    (row,) = read(db_path(client), "SELECT event_id, subject FROM search_opens")
    assert row == {"event_id": None, "subject": "pasted in"}


def test_a_file_the_user_may_not_open_is_never_written(client: TestClient, world: World) -> None:
    # The vaulted file is in this admin's own Hidden, which is shut.
    _opened(client, "secret", world.vaulted)
    _opened(client, "secret", "not-a-file-at-all")
    assert read(db_path(client), "SELECT id FROM search_opens") == []


def test_forget_in_the_box_takes_the_record_of_that_search_with_it(
    client: TestClient, world: World
) -> None:
    _search(client, "sunset")
    _search(client, "harbour")
    _opened(client, "sunset", world.beach)
    assert client.delete("/api/search/history", params={"q": "sunset"}).status_code == 204
    left = read(db_path(client), "SELECT subject FROM search_events")
    assert left == [{"subject": "harbour"}]
    assert read(db_path(client), "SELECT id FROM search_opens") == []


def test_clearing_the_box_clears_every_record_and_open(client: TestClient, world: World) -> None:
    _search(client, "sunset")
    _opened(client, "sunset", world.beach)
    _opened(client, "pasted in", world.beach)
    assert client.delete("/api/search/history").status_code == 204
    for statement in _EVERY_ROW:
        assert read(db_path(client), statement) == [], statement


def test_a_paused_history_keeps_the_recent_list_and_writes_no_record(
    client: TestClient, world: World
) -> None:
    assert client.put("/api/settings", json={"values": {"history.keep": False}}).status_code == 204
    _search(client, "sunset")
    _opened(client, "sunset", world.beach)
    assert read(db_path(client), "SELECT subject FROM search_history") == [{"subject": "sunset"}]
    assert read(db_path(client), "SELECT id FROM search_events") == []
    assert read(db_path(client), "SELECT id FROM search_opens") == []


def test_searches_are_kept_for_ever_unless_the_install_sets_a_limit() -> None:
    assert jobs.DEFAULT_KEEP_DAYS == 0
    assert jobs.keep_days_from(None) == 0


async def test_version_ten_stamps_the_old_record_and_makes_the_opens(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await connection.execute(
            "CREATE TABLE search_events (id TEXT PRIMARY KEY, user_id TEXT NOT NULL,"
            " kind TEXT NOT NULL, subject TEXT NOT NULL, results INTEGER, opened_id TEXT,"
            " at INTEGER NOT NULL)"
        )
        await connection.execute(
            "CREATE TABLE search_history (id TEXT PRIMARY KEY, user_id TEXT, kind TEXT,"
            " subject TEXT, label TEXT, created_at INTEGER)"
        )
        await connection.execute(
            "CREATE TABLE saved_searches (id TEXT PRIMARY KEY, user_id TEXT, kind TEXT,"
            " name TEXT, query TEXT, created_at INTEGER)"
        )
        await schema.initialize(connection, 9)
        await schema.initialize(connection, 9)
    columns = {
        str(row["name"])
        for row in await temp_db.fetch_all("SELECT name FROM pragma_table_info('search_events')")
    }
    assert {"device_id", "client_kind"} <= columns
    assert await temp_db.fetch_all("SELECT id FROM search_opens") == []


def test_the_record_and_the_box_are_both_cleared_with_a_users_whole_history(
    client: TestClient, world: World
) -> None:
    _search(client, "sunset")
    _opened(client, "sunset", world.beach)
    assert client.delete("/api/insights/history").status_code == 204
    for statement in _EVERY_ROW:
        assert read(db_path(client), statement) == [], statement
