# SPDX-License-Identifier: AGPL-3.0-or-later
"""Catalog steps that add marks and columns to tables made before them, one call each from
`schema.initialize_catalog`, so that function stays the list of steps in their order."""

from __future__ import annotations

from sift.kernel.db import Connection
from sift.kernel.migrations import column_exists

#: "DO NOT SWAP" on a person, a Site and a tag: a swap offers nothing under it and the stash-box
#: lookups go on, where `keep_local` stops both. Partial indexes: nearly every row is a zero and
#: nobody asks for those.
_ADD_PEOPLE_KEPT_FROM_SWAPS = (
    "ALTER TABLE people ADD COLUMN keep_from_swaps INTEGER NOT NULL DEFAULT 0"
)
_ADD_SITES_KEPT_FROM_SWAPS = (
    "ALTER TABLE sites ADD COLUMN keep_from_swaps INTEGER NOT NULL DEFAULT 0"
)
_ADD_TAGS_KEPT_FROM_SWAPS = "ALTER TABLE tags ADD COLUMN keep_from_swaps INTEGER NOT NULL DEFAULT 0"
INDEX_PEOPLE_KEPT_FROM_SWAPS = (
    "CREATE INDEX IF NOT EXISTS ix_people_kept_from_swaps ON people(id) WHERE keep_from_swaps = 1"
)
INDEX_SITES_KEPT_FROM_SWAPS = (
    "CREATE INDEX IF NOT EXISTS ix_sites_kept_from_swaps ON sites(id) WHERE keep_from_swaps = 1"
)
INDEX_TAGS_KEPT_FROM_SWAPS = (
    "CREATE INDEX IF NOT EXISTS ix_tags_kept_from_swaps ON tags(id) WHERE keep_from_swaps = 1"
)

#: The three filing tables that carry `box_id` (the note over `asset_people` in `schema`).
FILINGS_BY_A_BOX = ("asset_people", "asset_tags", "asset_usernames")
_ADD_BOX_ID = "ALTER TABLE {table} ADD COLUMN box_id TEXT"

#: "DON'T ENRICH" AND "DON'T SWAP" ON A FOLDER, reaching every file under it. The table is the
#: content component's, the columns the catalog's, as on `assets`. Partial, as above.
_ADD_FOLDERS_KEPT_LOCAL = "ALTER TABLE folders ADD COLUMN keep_local INTEGER NOT NULL DEFAULT 0"
_ADD_FOLDERS_KEPT_FROM_SWAPS = (
    "ALTER TABLE folders ADD COLUMN keep_from_swaps INTEGER NOT NULL DEFAULT 0"
)
_INDEX_FOLDERS_KEPT_LOCAL = (
    "CREATE INDEX IF NOT EXISTS ix_folders_kept_local ON folders(id) WHERE keep_local = 1"
)
_INDEX_FOLDERS_KEPT_FROM_SWAPS = (
    "CREATE INDEX IF NOT EXISTS ix_folders_kept_from_swaps ON folders(id) WHERE keep_from_swaps = 1"
)


async def keep_from_swaps(connection: Connection) -> None:
    """Step 72: "Do not swap" on people, Sites and tags, and the indexes that find the few."""
    for statement in (
        _ADD_PEOPLE_KEPT_FROM_SWAPS,
        _ADD_SITES_KEPT_FROM_SWAPS,
        _ADD_TAGS_KEPT_FROM_SWAPS,
        INDEX_PEOPLE_KEPT_FROM_SWAPS,
        INDEX_SITES_KEPT_FROM_SWAPS,
        INDEX_TAGS_KEPT_FROM_SWAPS,
    ):
        await connection.execute(statement)


async def name_the_box_on_filings(connection: Connection) -> None:
    """Step 87: which stash-box made each filing a box made. Only the column: the stash-box
    feature fills it from its own answers (`stash_boxes.filed_by.backfill`)."""
    for table in FILINGS_BY_A_BOX:
        if not await column_exists(connection, table, "box_id"):
            # nosemgrep: sift-no-string-built-sql
            await connection.execute(_ADD_BOX_ID.format(table=table))


async def mark_folders(connection: Connection) -> None:
    """Step 88: a folder's two marks. A new library takes them here too, since `folders` is made
    by the content component."""
    if not await column_exists(connection, "folders", "keep_local"):
        await connection.execute(_ADD_FOLDERS_KEPT_LOCAL)
    if not await column_exists(connection, "folders", "keep_from_swaps"):
        await connection.execute(_ADD_FOLDERS_KEPT_FROM_SWAPS)
    await connection.execute(_INDEX_FOLDERS_KEPT_LOCAL)
    await connection.execute(_INDEX_FOLDERS_KEPT_FROM_SWAPS)
