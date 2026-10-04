# SPDX-License-Identifier: AGPL-3.0-or-later
"""A catalog at its baseline (version 70) is brought to this build's by the steps written since.

Each step adds what an older catalog lacks: a tag's one parent, the mark that keeps a thing out of
swaps, the two cover columns, the logo pack's mark on a Site and the act that made a tag. A catalog
this build makes has every one, so the test takes them off again, with the triggers and indexes
that name them, and replays every step from the baseline: the columns come back, and the triggers
the steps and the boot's own checks write are this build's.
"""

from __future__ import annotations

import pytest

# Imported for their side effect: the ledger and the stash-boxes' tables the later steps read.
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import default_covers, edited, schema
from sift.kernel.db import Connection, Database
from sift.kernel.migrations import column_exists, table_exists

pytestmark = pytest.mark.anyio

BASELINE = 70

#: What a catalog at the baseline does not have, by the step that adds it.
LATER_COLUMNS = (
    ("tags", "parent_id"),
    ("people", "keep_from_swaps"),
    ("sites", "keep_from_swaps"),
    ("tags", "keep_from_swaps"),
    *(
        (table, column)
        for table in schema._COVER_TABLES
        for column in ("cover_cleared_at", "cover_by_default")
    ),
    ("sites", "drawn_by_pack"),
    ("tags", "created_by_act"),
)
LATER_INDEXES = (
    "ix_tags_parent",
    "ix_people_kept_from_swaps",
    "ix_sites_kept_from_swaps",
    "ix_tags_kept_from_swaps",
)


async def _as_at_the_baseline(connection: Connection) -> None:
    """Every later column off, after what names it: SQLite drops no column a trigger or an index
    still reads. The file-editing feature's table goes too, since a library may never have had it."""
    for statement in default_covers.drop_triggers():
        await connection.execute(statement)
    for name in edited.TRIGGERS:
        # Names from this build's own list: nothing from outside reaches the text.
        # nosemgrep: sift-no-string-built-sql
        await connection.execute(f"DROP TRIGGER IF EXISTS {name}")
    for name in LATER_INDEXES:
        # nosemgrep: sift-no-string-built-sql
        await connection.execute(f"DROP INDEX {name}")
    for table, column in LATER_COLUMNS:
        # nosemgrep: sift-no-string-built-sql
        await connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    await connection.execute("DROP TABLE IF EXISTS produced_files")


async def _triggers(connection: Connection) -> set[str]:
    rows = await connection.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
    )
    return {str(row[0]) for row in rows}


async def test_a_catalog_at_its_baseline_is_brought_up_by_every_step_since(
    temp_db: Database,
) -> None:
    await temp_db.initialize_schema()
    async with temp_db.write() as connection:
        await _as_at_the_baseline(connection)
        assert not any([await column_exists(connection, *one) for one in LATER_COLUMNS])

        await schema.initialize_catalog(connection, BASELINE)
        await edited.keep_true(connection)
        await default_covers.keep_true(connection)

        missing = [one for one in LATER_COLUMNS if not await column_exists(connection, *one)]
        assert missing == []
        assert not await table_exists(connection, "produced_files")
        triggers = await _triggers(connection)
        assert set(edited.TRIGGERS) <= triggers
        assert set(default_covers.triggers()) <= triggers
        indexes = await connection.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
        assert set(LATER_INDEXES) <= {str(row[0]) for row in indexes}
