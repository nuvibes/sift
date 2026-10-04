# SPDX-License-Identifier: AGPL-3.0-or-later
"""The access layer's count of what arrived, as one user may see it."""

from __future__ import annotations

from typing import Any

import pytest

import sift.main  # noqa: F401 (every component registers its tables on import)
from sift.kernel.access import arrivals
from sift.kernel.db import Database

pytestmark = pytest.mark.unit

USER = "u-viewer"
OTHER = "u-other"
SITE = "site-1"
SITE_TWO = "site-2"


async def _run(database: Database, sql: str, params: tuple[Any, ...] = ()) -> None:
    async with database.write() as connection:
        await connection.execute(sql, params)


async def _file(database: Database, asset_id: str, added_at: int, *, hidden: bool | None) -> None:
    """A file, with its verdict for USER. `hidden=None` writes no verdict at all, which is a file
    the user may not see."""
    await _run(
        database,
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', ?)",
        (asset_id, "identity-" + asset_id, added_at),
    )
    if hidden is not None:
        await _verdict(database, asset_id, hidden)


async def _verdict(database: Database, asset_id: str, hidden: bool) -> None:
    """The stored verdict, written directly: what is under test is how it is read, not how it is
    worked out. Written LAST, after any link the file gets: the tables the verdict reads carry
    triggers that recompute a file's rows on every change, and a file with no folder or grant
    recomputes to no row at all."""
    await _run(
        database,
        "INSERT OR REPLACE INTO viewer_assets (user_id, asset_id, concealed) VALUES (?, ?, ?)",
        (USER, asset_id, 1 if hidden else 0),
    )


async def _under(database: Database, asset_id: str, site_id: str, username: str) -> None:
    await _run(
        database,
        "INSERT OR IGNORE INTO sites (id, name, created_at) VALUES (?, ?, 1)",
        (site_id, "Site " + site_id),
    )
    await _run(
        database,
        "INSERT OR IGNORE INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, 1)",
        (username, site_id, username),
    )
    await _run(
        database,
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
        (asset_id, username),
    )


@pytest.fixture
async def database(temp_db: Database) -> Database:
    await temp_db.initialize_schema()
    for user in (USER, OTHER):
        await _run(
            temp_db,
            "INSERT INTO users (id, username, password_hash, role, created_at)"
            " VALUES (?, ?, 'not-a-hash', 'admin', 1)",
            (user, user),
        )
    return temp_db


async def test_the_files_that_arrived_are_the_ones_this_user_may_see(database: Database) -> None:
    await _file(database, "a-seen", 100, hidden=False)
    await _file(database, "a-hidden", 110, hidden=True)
    await _file(database, "a-not-mine", 120, hidden=None)
    await _file(database, "a-yesterday", 50, hidden=False)
    await _file(database, "a-tomorrow", 200, hidden=False)

    (row,) = await arrivals.files_added(database.fetch_all, user_id=USER, start=100, end=200)

    assert (row["key"], row["whole"], row["hidden"]) == ("", 2, 1)


async def test_nothing_arrived_is_no_row_rather_than_a_zero(database: Database) -> None:
    await _file(database, "a-yesterday", 50, hidden=False)

    assert await arrivals.files_added(database.fetch_all, user_id=USER, start=100, end=200) == []
    assert await arrivals.files_added(database.fetch_all, user_id=OTHER, start=0, end=200) == []


async def test_by_site_a_file_counts_once_under_a_site_and_a_hidden_site_hides_it_all(
    database: Database,
) -> None:
    await _file(database, "a-one", 100, hidden=None)
    await _file(database, "a-two", 101, hidden=None)
    await _file(database, "a-three", 102, hidden=None)
    await _file(database, "a-loose", 103, hidden=False)
    # Two usernames of one Site on one file: one file under that Site.
    await _under(database, "a-one", SITE, "name-a")
    await _under(database, "a-one", SITE, "name-b")
    await _under(database, "a-two", SITE_TWO, "name-c")
    await _under(database, "a-three", SITE_TWO, "name-c")
    await _run(
        database,
        "INSERT INTO site_user_state (site_id, user_id, hidden, updated_at) VALUES (?, ?, 1, 1)",
        (SITE, USER),
    )
    await _verdict(database, "a-one", False)
    await _verdict(database, "a-two", False)
    await _verdict(database, "a-three", True)

    rows = await arrivals.files_added_by_site(database.fetch_all, user_id=USER, start=100, end=200)

    assert sorted((row["key"], row["whole"], row["hidden"]) for row in rows) == [
        (SITE, 1, 1),
        (SITE_TWO, 2, 1),
    ]
    # The loose file is in the total and under no Site.
    (total,) = await arrivals.files_added(database.fetch_all, user_id=USER, start=100, end=200)
    assert total["whole"] == 4


def test_every_statement_joins_the_stored_verdict() -> None:
    """The reason this module is in the access layer: a count that forgot the verdict would hand a
    user the whole library's arrivals."""
    for name, statement in arrivals.STATEMENTS.items():
        assert "JOIN viewer_assets va ON va.user_id = :user" in statement, name
