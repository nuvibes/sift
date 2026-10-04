# SPDX-License-Identifier: AGPL-3.0-or-later
"""A folder's "Don't enrich" and "Don't swap": the marks reach every file at any depth under the
folder, through every reader of the refusal, and the reach seeks from the marked folders rather
than walking the library."""

from __future__ import annotations

import pytest

from sift.kernel.access import Repository
from sift.kernel.access.catalog import (
    files_kept_from_swaps,
    kept_local_over,
    marked_folders,
    refused_for_swaps_among,
    refused_here,
    refused_over,
    refusers_of_file,
)
from sift.kernel.access.catalog.refusals import (
    _FILES_KEPT_FROM_SWAPS,
    _KEPT_FROM_SWAPS_OVER,
    _KEPT_LOCAL_OVER,
)
from sift.kernel.access.filter_parts import KEPT_FROM_SWAPS_FILES, KEPT_LOCAL_FILES
from sift.kernel.db import Database

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000


async def _tree(db: Database) -> None:
    """A library `r1` with `top/inner/deep`, a file in `deep`, a file in `top` and one beside it
    in `other`, so a mark on `top` has a file two folders down to reach and one it must not."""
    await db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES ('r1', 'r1', '/r1', ?)",
        (_EPOCH,),
    )
    for folder, parent, path in (
        ("top", None, "top"),
        ("inner", "top", "top/inner"),
        ("deep", "inner", "top/inner/deep"),
        ("other", None, "other"),
    ):
        await db.execute(
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, 'r1', ?, ?, ?)",
            (folder, parent, path, path.rsplit("/", 1)[-1]),
        )
    for asset, folder in (("a-deep", "deep"), ("a-top", "top"), ("a-other", "other")):
        await db.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'image', ?)",
            (asset, f"digest-{asset}", _EPOCH),
        )
        await db.execute(
            "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
            " first_seen_at, last_seen_at) VALUES (?, ?, 'r1', ?, ?, ?, ?, ?)",
            (f"l-{asset}", asset, folder, f"{folder}/{asset}.jpg", f"{asset}.jpg", _EPOCH, _EPOCH),
        )


async def _files(db: Database, fragment: str) -> set[str]:
    rows = await db.fetch_all(fragment)
    return {str(row[0]) for row in rows}


async def test_a_folder_kept_local_keeps_every_file_under_it_at_any_depth(
    temp_db: Database, access: Repository
) -> None:
    del access  # the store that builds the schema
    await _tree(temp_db)
    assert not await kept_local_over(temp_db, "a-deep")

    await temp_db.execute("UPDATE folders SET keep_local = 1 WHERE id = 'top'")

    assert await kept_local_over(temp_db, "a-deep"), "two folders down"
    assert await kept_local_over(temp_db, "a-top")
    assert not await kept_local_over(temp_db, "a-other"), "a folder beside it"
    assert await refused_over(temp_db, "enrich", "asset", "a-deep")
    assert await _files(temp_db, KEPT_LOCAL_FILES) == {"a-deep", "a-top"}
    # "Don't enrich" keeps a file out of swaps too.
    assert await refused_over(temp_db, "swap", "asset", "a-deep")
    assert await files_kept_from_swaps(temp_db) == {"a-deep", "a-top"}
    # The folder inside answers for the mark above it; only `top` carries it on its own row.
    assert await refused_over(temp_db, "enrich", "folder", "deep")
    assert not await refused_here(temp_db, "enrich", "folder", "deep")
    assert await refused_here(temp_db, "enrich", "folder", "top")
    assert not await refused_over(temp_db, "enrich", "folder", "other")
    assert await marked_folders(temp_db) == {"top": (True, False)}


async def test_a_folder_kept_out_of_swaps_leaves_the_lookups_alone(
    temp_db: Database, access: Repository
) -> None:
    del access
    await _tree(temp_db)
    await temp_db.execute("UPDATE folders SET keep_from_swaps = 1 WHERE id = 'inner'")

    assert await refused_over(temp_db, "swap", "asset", "a-deep")
    assert not await refused_over(temp_db, "swap", "asset", "a-top"), "the folder above is free"
    assert not await refused_over(temp_db, "enrich", "asset", "a-deep")
    assert await _files(temp_db, KEPT_FROM_SWAPS_FILES) == {"a-deep"}
    assert await files_kept_from_swaps(temp_db) == {"a-deep"}
    assert await refused_for_swaps_among(temp_db, ["a-deep", "a-top", "a-other"]) == {"a-deep"}
    assert [
        (one.kind, one.id, one.kept_local) for one in await refusers_of_file(temp_db, "a-deep")
    ] == [("folder", "inner", False)]
    assert await refused_over(temp_db, "swap", "folder", "deep")
    assert not await refused_over(temp_db, "swap", "folder", "top")


async def test_the_schema_step_gives_folders_both_marks_and_their_partial_indexes(
    temp_db: Database, access: Repository
) -> None:
    del access
    columns = {str(row["name"]) for row in await temp_db.fetch_all("PRAGMA table_info(folders)")}
    assert {"keep_local", "keep_from_swaps"} <= columns
    indexes = {
        str(row["name"])
        for row in await temp_db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'folders'"
        )
    }
    assert {"ix_folders_kept_local", "ix_folders_kept_from_swaps"} <= indexes


#: Every statement that walks a folder's mark down to its files over the whole library, by name.
_WHOLE_LIBRARY = (
    ("kept local, the walls' fragment", KEPT_LOCAL_FILES),
    ("kept from swaps, the walls' fragment", KEPT_FROM_SWAPS_FILES),
    ("kept from swaps, the guest's read", _FILES_KEPT_FROM_SWAPS),
)


@pytest.mark.parametrize(
    ("label", "statement"), _WHOLE_LIBRARY, ids=[label for label, _ in _WHOLE_LIBRARY]
)
async def test_the_folder_arm_starts_from_the_marked_folders_and_seeks_the_rest(
    temp_db: Database, access: Repository, label: str, statement: str
) -> None:
    """The folder arm reads the partial index of marked folders and seeks `folder_ancestry` and
    `asset_locations` from there. A walk of either would read every copy in the library on every
    wall that asks whether a file is kept local."""
    del access
    await _tree(temp_db)
    plan = await temp_db.fetch_all(
        "EXPLAIN QUERY PLAN " + statement  # nosemgrep: sift-no-string-built-sql
    )
    steps = [str(row["detail"]) for row in plan]
    walked = [s for s in steps if s.startswith("SCAN ") and " kf" not in s and "fan" in s]
    assert not walked, (label, steps)
    assert not [s for s in steps if s.startswith("SCAN fl")], (label, steps)
    marked = [s for s in steps if s.startswith(("SCAN kf", "SEARCH kf"))]
    assert marked, (label, steps)
    assert all("USING COVERING INDEX ix_folders_kept_" in s for s in marked), (label, steps)
    assert any(s.startswith("SEARCH fan USING") for s in steps), (label, steps)
    assert any(s.startswith("SEARCH fl USING") and "ix_loc_folder" in s for s in steps), (
        label,
        steps,
    )


@pytest.mark.parametrize(
    ("label", "statement"),
    [
        ("kept local, one file", _KEPT_LOCAL_OVER.sql),
        ("kept from swaps, one file", _KEPT_FROM_SWAPS_OVER.sql),
    ],
    ids=["kept local, one file", "kept from swaps, one file"],
)
async def test_one_files_folders_are_sought_from_its_copies(
    temp_db: Database, access: Repository, label: str, statement: str
) -> None:
    """The door's point read finds a file's folders from its own copies, by index, never by
    walking the folders or the copies of other files."""
    del access
    await _tree(temp_db)
    plan = await temp_db.fetch_all(
        "EXPLAIN QUERY PLAN " + statement,  # nosemgrep: sift-no-string-built-sql
        ("a-deep",) * statement.count("?"),
    )
    steps = [str(row["detail"]) for row in plan]
    assert not [s for s in steps if s.startswith(("SCAN fl", "SCAN fan", "SCAN kf"))], (
        label,
        steps,
    )
    assert any(s.startswith("SEARCH fl USING") and "ix_loc_asset" in s for s in steps), (
        label,
        steps,
    )
