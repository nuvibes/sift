# SPDX-License-Identifier: AGPL-3.0-or-later
"""The sessions table, and the one thing its initializer has to get right twice.

A schema component is handed the version already on disk and decides what, if anything, to do about
it. Creating the table when there is nothing there is the easy half. Doing nothing when it is
already there is the half worth a test: an initializer that ran its creation again on every boot
would either fail on an existing database or, worse, be written defensively enough to succeed and
quietly re-create indexes on every start.
"""

from __future__ import annotations

import pytest

from sift.kernel.db import Database
from sift.slices.auth.schema import SESSIONS_VERSION, initialize_sessions

pytestmark = pytest.mark.integration


async def test_a_database_that_already_has_the_table_is_left_alone(temp_db: Database) -> None:
    """Handed a version that is already on disk, the initializer does nothing at all.

    Driven with a connection that has no `sessions` table, so the check is not merely that it did
    not fail: if it tried to create anything the statement would raise. Doing nothing is the only
    way this passes.
    """
    async with temp_db.write() as connection:
        await initialize_sessions(connection, on_disk=SESSIONS_VERSION)

        rows = list(
            await connection.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
            )
        )

    assert rows == []


async def test_a_fresh_database_gets_the_table_and_its_indexes(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await initialize_sessions(connection, on_disk=0)

        tables = list(
            await connection.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
            )
        )
        indexes = list(
            await connection.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'sessions' "
                "AND name LIKE 'ix_%'"
            )
        )

    assert [row["name"] for row in tables] == ["sessions"]
    # Both of them: one for revoking every session a user has, one for the expiry sweep.
    assert len(indexes) == 2
