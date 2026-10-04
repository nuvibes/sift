# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one step a library at the content baseline takes: the index nothing reads any more goes.

`ix_assets_undepthed` held the files whose colour depth a finished sweep still had to read. The
probe records the depth itself, so nothing reads the index, and every row written pays for it.
"""

from __future__ import annotations

import pytest

from sift.kernel.content import schema as content_schema
from sift.kernel.db import Connection, Database

pytestmark = pytest.mark.anyio

_UNDEPTHED = (
    "CREATE INDEX ix_assets_undepthed ON assets(added_at, id)"
    " WHERE bit_depth IS NULL OR (bit_depth >= 10 AND color_transfer IS NULL)"
)
_INDEXES = "SELECT name FROM sqlite_master WHERE type = 'index'"


async def _library(connection: Connection) -> None:
    await content_schema.initialize_library(connection, on_disk=0)
    await content_schema.initialize_content(connection, on_disk=0)


async def _indexes(connection: Connection) -> set[str]:
    return {str(row[0]) for row in await connection.execute_fetchall(_INDEXES)}


async def test_a_new_library_never_has_it(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await _library(connection)

        assert "ix_assets_undepthed" not in await _indexes(connection)


async def test_a_library_at_the_baseline_loses_it(
    temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """From the baseline itself, so every step after it runs as it would on a real library. Asked
    from the version before today's, this would skip the one step it is about and run another that
    adds a column the table already has."""
    # The baseline's assets table: today's, less the columns the later steps add (the first of
    # them is "Do not swap", and the moment the still was cut at follows it), and so less the index
    # on the first. Module constants cut short, not values from anywhere.
    today = content_schema._CREATE_ASSETS
    cut = today.rfind(",\n", 0, today.index("Do not swap:"))
    monkeypatch.setattr(content_schema, "_CREATE_ASSETS", today[:cut] + "\n)")
    monkeypatch.setattr(
        content_schema,
        "_CONTENT_INDEXES",
        tuple(
            one
            for one in content_schema._CONTENT_INDEXES
            if one != content_schema._INDEX_KEPT_FROM_SWAPS
        ),
    )
    async with temp_db.write() as connection:
        await _library(connection)
        await connection.execute(_UNDEPTHED)
        monkeypatch.undo()

        await content_schema.initialize_content(connection, on_disk=22)

        assert "ix_assets_undepthed" not in await _indexes(connection)
