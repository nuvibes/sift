# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two things an import files by: the Site a site key files under, and a tag's one parent.

The keys live in the download feature's table, so a library that never had downloads files no
key and remembers none. A parent an import gives a tag is refused where it would make a loop.
"""

from __future__ import annotations

import pytest

import sift.slices.workbench.schema  # noqa: F401 (the ledger a filing is recorded in)
from sift.kernel.access import Repository
from sift.kernel.access.sites import keep_site_key_on, site_for_key_on, site_names_for_keys_on
from sift.kernel.access.tag_tree import file_under_if_unfiled
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.kernel.migrations import table_exists

pytestmark = pytest.mark.anyio

KEY = "@quillhouse.invalid"


async def test_a_library_with_no_downloads_files_no_site_key_and_keeps_none(
    temp_db: Database,
) -> None:
    async with temp_db.write() as connection:
        assert not await table_exists(connection, "download_sites")
        assert await site_for_key_on(connection, KEY) is None
        assert await site_names_for_keys_on(connection, [KEY]) == {}
        assert await site_names_for_keys_on(connection, []) == {}
        await keep_site_key_on(connection, KEY, "site-1")
        assert not await table_exists(connection, "download_sites")


async def test_a_parent_that_would_make_a_loop_is_refused_and_files_nothing(
    temp_db: Database, access: Repository
) -> None:
    """A tag is not its own parent, and not filed under a tag already somewhere under it."""
    await temp_db.execute(
        "INSERT INTO tags (id, name, name_sort, created_at)"
        " VALUES ('outdoors', 'Outdoors', 'outdoors', 0)"
    )
    await temp_db.execute(
        "INSERT INTO tags (id, name, name_sort, created_at, parent_id)"
        " VALUES ('sunset', 'Sunset', 'sunset', 0, 'outdoors')"
    )
    actor = Actor.sift("stash")

    async with temp_db.write() as connection:
        assert not await file_under_if_unfiled(connection, "outdoors", "outdoors", actor=actor)
        assert not await file_under_if_unfiled(connection, "outdoors", "sunset", actor=actor)

    row = await temp_db.fetch_one("SELECT parent_id FROM tags WHERE id = 'outdoors'")
    assert row is not None and row["parent_id"] is None
    assert await temp_db.fetch_one("SELECT 1 FROM workbench_decisions") is None
