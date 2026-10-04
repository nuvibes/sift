# SPDX-License-Identifier: AGPL-3.0-or-later
"""The picture token for a page of files, asked once for the page: the three screens drawing stills
off rows that are not the grid's go through this one reader (`art_of_files`), or their bare
addresses are refused the week-long promise."""

from __future__ import annotations

import pytest

from sift.kernel.access.catalog import art_of_files
from sift.kernel.db import Database
from sift.kernel.ids import new_id

pytestmark = pytest.mark.unit

ADDED_AT = 1_700_000_000
STAMP = 4


async def a_file(database: Database) -> str:
    """One file in the library. The schema is raised here rather than in a fixture because the
    empty-page test must not need one: that it costs no read at all is the thing it proves."""
    await database.initialize_schema()
    asset_id = new_id()
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
        " VALUES (?, ?, 1, 'video', ?)",
        (asset_id, f"digest-{asset_id}", ADDED_AT),
    )
    return asset_id


async def a_picture(database: Database, asset_id: str, *, kind: str, digest: str | None) -> None:
    await database.execute(
        "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, content_hash, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), asset_id, kind, f"{kind}/{asset_id}", digest, ADDED_AT),
    )


async def test_nothing_asked_about_is_no_read_at_all(temp_db: Database) -> None:
    """An empty page must not cost a statement, because an empty page is the ordinary case."""
    assert await art_of_files(temp_db, [], stamp=STAMP) == {}


async def test_a_file_with_no_pictures_yet_is_absent_rather_than_None(temp_db: Database) -> None:
    """A file with nothing built has no token: one invented would pin an empty frame for a week."""
    asset_id = await a_file(temp_db)

    assert await art_of_files(temp_db, [asset_id], stamp=STAMP) == {}


async def test_a_file_with_pictures_gets_a_token(temp_db: Database) -> None:
    asset_id = await a_file(temp_db)
    await a_picture(temp_db, asset_id, kind="thumb", digest="aaa")

    found = await art_of_files(temp_db, [asset_id], stamp=STAMP)

    assert found[asset_id]


async def test_the_token_moves_when_the_picture_is_rebuilt(temp_db: Database) -> None:
    """Which is the whole point of it: an address that did not move could not be kept safely."""
    asset_id = await a_file(temp_db)
    await a_picture(temp_db, asset_id, kind="thumb", digest="aaa")
    before = await art_of_files(temp_db, [asset_id], stamp=STAMP)

    await temp_db.execute(
        "UPDATE derivatives SET content_hash = ? WHERE asset_id = ?", ("bbb", asset_id)
    )
    after = await art_of_files(temp_db, [asset_id], stamp=STAMP)

    assert before[asset_id] != after[asset_id]


async def test_the_token_moves_with_what_this_user_may_see(temp_db: Database) -> None:
    """The stamp is the viewer's. A vault that shuts changes which pictures exist for them, and an
    address that stayed put would go on being drawn out of the browser's own store."""
    asset_id = await a_file(temp_db)
    await a_picture(temp_db, asset_id, kind="thumb", digest="aaa")

    one = await art_of_files(temp_db, [asset_id], stamp=4)
    two = await art_of_files(temp_db, [asset_id], stamp=5)

    assert one[asset_id] != two[asset_id]


async def test_one_answer_per_file_however_many_pictures_it_has(temp_db: Database) -> None:
    """A file has a still, a clip and a strip, and a join that fans out (as one over
    `asset_locations` can) gives a caller the same id twice and a screen keyed by id a duplicate
    key."""
    asset_id = await a_file(temp_db)
    await a_picture(temp_db, asset_id, kind="thumb", digest="aaa")
    await a_picture(temp_db, asset_id, kind="preview", digest="bbb")
    await a_picture(temp_db, asset_id, kind="sprite", digest="ccc")

    found = await art_of_files(temp_db, [asset_id, asset_id], stamp=STAMP)

    assert list(found) == [asset_id]
