# SPDX-License-Identifier: AGPL-3.0-or-later
"""The slice's schema: what a new library gets, and that a library at this version is left alone.

The kernel records the declared version once an initializer returns, whatever it did, so a step
that runs when it should not is a table rebuilt under a running install.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from sift.kernel.db import Database
from sift.slices.download.schema import _CREATE_DOWNLOADS, DOWNLOAD_VERSION, initialize_download
from sift.slices.download.service import UsernameFrom

pytestmark = pytest.mark.anyio

_TABLES = "SELECT name FROM sqlite_master WHERE type = 'table'"


async def _bare(tmp_path: Path, name: str) -> Database:
    """A database with nothing in it: the shared fixture has already run every initializer."""
    database = Database(tmp_path / f"{name}.sqlite3")
    await database.connect()
    return database


async def _table_names(database: Database) -> set[str]:
    return {str(row["name"]) for row in await database.fetch_all(_TABLES)}


async def test_a_new_database_gets_every_table(tmp_path: Path) -> None:
    database = await _bare(tmp_path, "new")
    try:
        async with database.write() as connection:
            await initialize_download(connection, 0)
        # `secrets` is not here: it is the kernel's, since more than one slice needs sealed values.
        # Nor are the tunnels and their routes, which are the kernel component `tunnels`'s.
        tables = await _table_names(database)
        assert {
            "downloads",
            "site_connections",
            "download_items",
            "site_options",
            "site_art",
        } <= tables
        assert not {"tunnels", "tunnel_routes", "secrets"} & tables
    finally:
        await database.close()


async def test_a_database_already_at_this_version_is_left_alone(tmp_path: Path) -> None:
    """Nothing runs at all. That the tables are absent afterwards is what proves the step is
    guarded rather than merely idempotent."""
    database = await _bare(tmp_path, "current")
    try:
        async with database.write() as connection:
            await initialize_download(connection, DOWNLOAD_VERSION)
        assert await _table_names(database) == set()
    finally:
        await database.close()


def test_username_from_agrees_with_its_column() -> None:
    """The list is written twice, the service's type and the column's CHECK, and this keeps it one
    list: a source added to one and not the other is a write the database refuses."""
    spelled = "CHECK(username_from IN ({}))".format(
        ",".join(f"'{value}'" for value in get_args(UsernameFrom))
    )
    assert spelled in _CREATE_DOWNLOADS


async def test_the_step_to_33_adds_who_asked_and_leaves_older_rows_unasked(
    tmp_path: Path,
) -> None:
    """v33 writes who asked for a download. A row from before names nobody, and the column's
    key is the users table's, so a removed user's rows keep their downloads."""
    from sift.slices.download.schema import _LEFT_OUT, _NAMED_FROM

    database = await _bare(tmp_path, "at32")
    try:
        async with database.write() as connection:
            # The tables its keys name, which the kernel's components own.
            for owner in (
                "CREATE TABLE folders (id TEXT PRIMARY KEY)",
                "CREATE TABLE assets (id TEXT PRIMARY KEY)",
                "CREATE TABLE jobs (id TEXT PRIMARY KEY)",
                "CREATE TABLE users (id TEXT PRIMARY KEY)",
            ):
                await connection.execute(owner)
            await connection.execute(_CREATE_DOWNLOADS)
            for statement in (*_NAMED_FROM, *_LEFT_OUT):
                await connection.execute(statement)
            await connection.execute(
                "INSERT INTO downloads (id, url, url_hash, created_at) VALUES ('d1', 'u', 'h', 0)"
            )
            await initialize_download(connection, 32)
            keys = list(await connection.execute_fetchall("PRAGMA foreign_key_list(downloads)"))
        row = await database.fetch_one("SELECT requested_by FROM downloads WHERE id = 'd1'")
        assert row is not None and row["requested_by"] is None
        assert ("users", "requested_by", "SET NULL") in {
            (str(one["table"]), str(one["from"]), str(one["on_delete"])) for one in keys
        }
    finally:
        await database.close()


async def test_the_step_to_38_adds_the_refused_reads_and_leaves_older_rows_without(
    tmp_path: Path,
) -> None:
    database = await _bare(tmp_path, "at37")
    try:
        async with database.write() as connection:
            for owner in (
                "CREATE TABLE folders (id TEXT PRIMARY KEY)",
                "CREATE TABLE assets (id TEXT PRIMARY KEY)",
                "CREATE TABLE jobs (id TEXT PRIMARY KEY)",
            ):
                await connection.execute(owner)
            await connection.execute(_CREATE_DOWNLOADS)
            await connection.execute(
                "INSERT INTO downloads (id, url, url_hash, created_at) VALUES ('d1', 'u', 'h', 0)"
            )
            await initialize_download(connection, 37)
        row = await database.fetch_one("SELECT reads_refused FROM downloads WHERE id = 'd1'")
        assert row is not None and row["reads_refused"] is None
    finally:
        await database.close()
