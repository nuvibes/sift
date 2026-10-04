# SPDX-License-Identifier: AGPL-3.0-or-later
"""The inline connection never parses the schema on the loop.

A SQLite connection whose schema is behind the file's parses all of it again on its next statement,
and the inline connection's statements run on the event loop. On a library carrying a lot of
trigger text that parse holds every page and video, so the read that finds the schema moved goes to
a thread and the parse is done on one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.kernel.db import Database, point_read

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("clean_registry")]


async def test_a_schema_that_moved_is_parsed_for_the_inline_connection_on_a_thread(
    tmp_path: Path,
) -> None:
    declared = point_read("test.by_id", "SELECT v FROM t WHERE id = ?")
    database = Database(tmp_path / "test.sqlite3", readers=1)
    await database.connect()
    try:
        assert database._inline(declared) is not None
        async with database.write() as connection:
            await connection.execute("CREATE TABLE t (id TEXT PRIMARY KEY, v INTEGER)")
            await connection.execute("INSERT INTO t VALUES ('a', 1)")

        assert database._inline(declared) is None, "the moved schema was taken on the loop"
        parsing = database._point_parsing
        assert parsing is not None
        row = await database.fetch_one(declared, ("a",))
        await parsing

        assert database._inline(declared) is not None
        again = await database.fetch_one(declared, ("a",))
    finally:
        await database.close()

    assert row is not None and row["v"] == 1
    assert again is not None and again["v"] == 1
