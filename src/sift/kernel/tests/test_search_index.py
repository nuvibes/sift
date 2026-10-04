# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writing the search index, and the one claim the whole design rests on.

The index is a CACHE: a full rebuild from an empty table must reproduce exactly what the incremental
writes produced, or two libraries differ by the order things happened in. It holds every asset,
unscoped: who may see a row is decided when it is READ, against the person asking.
"""

from __future__ import annotations

import pytest

from sift.kernel.access import (
    anything_unindexed,
    index_assets,
    index_new_assets,
    search_index,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.testing.fixtures import World

_ROWS = "SELECT asset_id, filename, path, tags, people, usernames FROM assets_fts ORDER BY asset_id"

_INSERT = """
INSERT INTO assets (id, identity, media_type, original_filename, added_at)
VALUES (?, ?, 'video', ?, 0)
"""


async def _index(database: Database) -> list[dict[str, object]]:
    return [dict(row) for row in await database.fetch_all(_ROWS)]


async def _ids(database: Database) -> set[str]:
    return {str(row["asset_id"]) for row in await _index(database)}


async def test_a_full_rebuild_reproduces_the_identical_index(
    temp_db: Database, world: World
) -> None:
    """Rebuilding twice gives byte-identical rows: no dependence on row order, dict iteration or the
    clock. The incremental-versus-rebuild comparison is
    `test_reindexing_one_asset_at_a_time_gives_what_a_rebuild_would_have`, not this."""
    await index_assets(temp_db, rebuild=True)
    built = await _index(temp_db)
    assert built

    await index_assets(temp_db, rebuild=True)

    assert await _index(temp_db) == built


async def test_reindexing_one_asset_at_a_time_gives_what_a_rebuild_would_have(
    temp_db: Database, world: World
) -> None:
    """One asset at a time gives what a rebuild would have: the two paths are one function."""
    for asset_id in (world.solo, world.twin, world.loose):
        await index_assets(temp_db, asset_id=asset_id)
    incremental = await _index(temp_db)

    await index_assets(temp_db, rebuild=True)

    assert await _index(temp_db) == incremental


async def test_indexing_the_same_asset_twice_leaves_one_row(
    temp_db: Database, world: World
) -> None:
    """Reindexing an asset replaces its row: an FTS5 table has no uniqueness of its own."""
    await index_assets(temp_db, asset_id=world.solo)
    once = await _index(temp_db)

    await index_assets(temp_db, asset_id=world.solo)

    assert await _index(temp_db) == once


async def test_everything_an_asset_can_be_found_by_is_in_its_row(
    temp_db: Database, world: World
) -> None:
    """The tag, the person, the username and the path are all searchable, so a join dropped from
    the gathering query shows as a missing column."""
    await index_assets(temp_db, rebuild=True)
    row = next(row for row in await _index(temp_db) if row["asset_id"] == world.solo)

    assert "tag" in str(row["tags"])
    assert "person" in str(row["people"])
    assert "handle" in str(row["usernames"])
    assert "top/mid/leaf/solo.mp4" in str(row["path"])


async def test_an_alias_is_indexed_beside_the_name_it_stands_for(
    temp_db: Database, world: World
) -> None:
    """Free text finds a person by every name they go by, as the `people:` token does."""
    await temp_db.execute(
        "INSERT INTO people_aliases (id, person_id, alias) VALUES (?, ?, ?)",
        (new_id(), world.person, "otherwise-known-as"),
    )
    await index_assets(temp_db, rebuild=True)

    row = next(row for row in await _index(temp_db) if row["asset_id"] == world.solo)
    assert "otherwise-known-as" in str(row["people"])


async def test_a_file_with_nothing_on_it_is_still_indexed(temp_db: Database, world: World) -> None:
    """A file with nothing on it is indexed: no inner join may drop it, and its name is the only way
    to reach it."""
    await index_assets(temp_db, rebuild=True)
    assert world.loose in await _ids(temp_db)


async def test_an_asset_that_is_gone_is_swept_out_of_the_index(
    temp_db: Database, world: World
) -> None:
    """A deleted asset's row is swept: no foreign key cascades into an FTS5 table, and a left row
    says something by that name was here."""
    await index_assets(temp_db, rebuild=True)
    assert world.solo in await _ids(temp_db)

    await temp_db.execute("DELETE FROM assets WHERE id = ?", (world.solo,))
    # The single-asset path finds no row to read, so only the sweep acts.
    await index_assets(temp_db, asset_id=world.solo)

    assert world.solo not in await _ids(temp_db)


async def test_a_library_larger_than_one_batch_is_indexed_whole(
    temp_db: Database, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The paging loop advances and terminates, shown with the batch shrunk to two rows."""
    monkeypatch.setattr(search_index, "BATCH", 2)
    extra = [new_id() for _ in range(5)]
    for number, asset_id in enumerate(extra):
        await temp_db.execute(_INSERT, (asset_id, f"digest-extra-{number}", f"extra{number}.mp4"))

    written = await index_assets(temp_db, rebuild=True)

    assert written == 8
    assert set(extra) <= await _ids(temp_db)


async def test_a_file_indexed_by_its_own_write_during_a_rebuild_is_not_written_twice(
    temp_db: Database, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file changed between two rebuild pages is indexed by its own write, and the later page does
    not insert it again."""
    monkeypatch.setattr(search_index, "BATCH", 2)
    extra = sorted(new_id() for _ in range(5))
    for number, asset_id in enumerate(extra):
        await temp_db.execute(_INSERT, (asset_id, f"digest-mid-{number}", f"mid{number}.mp4"))
    reads = 0
    read_page = search_index._read_page

    async def read_then_change(connection, **asked):  # type: ignore[no-untyped-def]
        nonlocal reads
        reads += 1
        if reads == 2:
            await search_index.index_on(connection, [extra[-1]])
        return await read_page(connection, **asked)

    monkeypatch.setattr(search_index, "_read_page", read_then_change)

    await index_assets(temp_db, rebuild=True)

    rows = [row["asset_id"] for row in await _index(temp_db)]
    assert rows.count(extra[-1]) == 1
    assert set(extra) <= set(rows)


async def test_a_library_that_is_exactly_one_batch_stops_without_a_second_pass(
    temp_db: Database, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop's boundary in the other direction."""
    monkeypatch.setattr(search_index, "BATCH", 3)

    assert await index_assets(temp_db, rebuild=True) == 3


async def test_an_empty_library_indexes_to_an_empty_index(temp_db: Database) -> None:
    await temp_db.initialize_schema()
    assert await index_assets(temp_db, rebuild=True) == 0
    assert await _ids(temp_db) == set()


async def test_a_write_naming_no_files_does_no_work_on_the_index(
    temp_db: Database, world: World
) -> None:
    """A write touching no file reads no page and skips the sweep; an orphaned row waits for the
    next real write."""
    _ = world
    await temp_db.execute("INSERT INTO assets_fts_rows (asset_id, fts_rowid) VALUES ('gone', 1)")
    async with temp_db.write() as connection:
        assert await search_index.index_on(connection, []) == 0
    left = await temp_db.fetch_all("SELECT asset_id FROM assets_fts_rows")
    assert [row["asset_id"] for row in left] == ["gone"]


async def test_reindexing_one_asset_leaves_the_others_alone(
    temp_db: Database, world: World
) -> None:
    """Without `rebuild` the pass replaces only what it writes."""
    await index_assets(temp_db, rebuild=True)
    before = await _ids(temp_db)

    await index_assets(temp_db, asset_id=world.solo)

    assert await _ids(temp_db) == before


# --- the cheap pass


async def test_the_cheap_pass_indexes_only_what_has_no_row_yet(
    temp_db: Database, world: World
) -> None:
    """The frequent pass touches only files arrived since, so an import is findable promptly
    without a rebuild."""
    await index_assets(temp_db, rebuild=True)
    before = await _index(temp_db)
    assert before

    arrived = new_id()
    await temp_db.execute(_INSERT, (arrived, "digest-arrived", "arrived.mp4"))

    assert await index_new_assets(temp_db) == 1

    after = await _index(temp_db)
    assert arrived in await _ids(temp_db)
    # Every other row is left exactly as it was.
    assert [row for row in after if row["asset_id"] != arrived] == before


async def test_the_cheap_pass_does_nothing_when_everything_is_indexed(
    temp_db: Database, world: World
) -> None:
    """With nothing new it writes nothing."""
    await index_assets(temp_db, rebuild=True)
    assert await index_new_assets(temp_db) == 0


async def test_the_cheap_pass_is_blind_to_edits(temp_db: Database, world: World) -> None:
    """The cheap pass is blind to edits, the honest limit that is why the periodic rebuild
    exists."""
    await index_assets(temp_db, rebuild=True)

    tag = new_id()
    await temp_db.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)", (tag, "brandnew")
    )
    await temp_db.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (world.solo, tag)
    )

    assert await index_new_assets(temp_db) == 0
    row = next(row for row in await _index(temp_db) if row["asset_id"] == world.solo)
    assert "brandnew" not in str(row["tags"])

    await index_assets(temp_db, rebuild=True)
    row = next(row for row in await _index(temp_db) if row["asset_id"] == world.solo)
    assert "brandnew" in str(row["tags"])


async def test_the_cheap_pass_takes_no_more_than_it_was_asked_for(
    temp_db: Database, world: World
) -> None:
    """Bounded, so a huge import is caught up over several passes, never holding the write lock."""
    for number in range(5):
        await temp_db.execute(_INSERT, (new_id(), f"digest-many-{number}", f"many{number}.mp4"))

    assert await index_new_assets(temp_db, limit=2) == 2
    assert await index_new_assets(temp_db, limit=2) == 2


async def test_asking_whether_anything_is_unindexed_is_a_yes_or_a_no(
    temp_db: Database, world: World
) -> None:
    """`anything_unindexed` decides whether work is queued, stopping at the first row, so an idle
    library queues nothing."""
    assert await anything_unindexed(temp_db) is True

    await index_assets(temp_db, rebuild=True)
    assert await anything_unindexed(temp_db) is False

    await temp_db.execute(_INSERT, (new_id(), "digest-fresh", "fresh.mp4"))
    assert await anything_unindexed(temp_db) is True


async def test_a_named_set_indexes_those_assets_and_no_others(
    temp_db: Database, world: World
) -> None:
    """A bulk write refreshes a whole selection in one call."""
    await index_assets(temp_db, asset_ids=[world.solo])

    rows = await temp_db.fetch_all("SELECT asset_id FROM assets_fts")
    assert {row["asset_id"] for row in rows} == {world.solo}


async def test_an_empty_selection_writes_nothing_without_opening_a_transaction(
    temp_db: Database, world: World
) -> None:
    """An empty selection takes no write lock and runs no orphan sweep (an empty list would match
    nothing anyway)."""
    opened = 0
    real_write = temp_db.write

    def counting_write() -> object:
        nonlocal opened
        opened += 1
        return real_write()

    temp_db.write = counting_write  # type: ignore[method-assign,assignment]
    try:
        assert await index_assets(temp_db, asset_ids=[]) == 0
    finally:
        temp_db.write = real_write  # type: ignore[method-assign]

    assert opened == 0
    assert await temp_db.fetch_all("SELECT asset_id FROM assets_fts") == []


_KEPT = "SELECT n FROM search_unindexed WHERE id = 1"
_COUNTED = """
SELECT COUNT(*) AS n FROM assets a
 WHERE NOT EXISTS (SELECT 1 FROM assets_fts_rows m WHERE m.asset_id = a.id)
"""


async def _kept_agrees(database: Database) -> int:
    """The kept number, checked against the anti-join it stands in for."""
    (kept,) = await database.fetch_all(_KEPT)
    (counted,) = await database.fetch_all(_COUNTED)
    assert int(kept["n"]) == int(counted["n"]), "the kept unindexed count drifted"
    # And the pass's own denominator is that number.
    assert await search_index.unindexed_count(database) == int(counted["n"])
    return int(kept["n"])


async def test_the_kept_unindexed_count_follows_every_way_the_map_and_the_library_move(
    temp_db: Database, world: World
) -> None:
    """The trigger-kept unindexed count equals the anti-join after every kind of write: a rebuild,
    an arrival, a reindex, a file leaving indexed or not, and the orphan sweep."""
    waiting = await _kept_agrees(temp_db)
    assert waiting > 0

    await index_assets(temp_db, rebuild=True)
    assert await _kept_agrees(temp_db) == 0

    fresh, never = new_id(), new_id()
    await temp_db.execute(_INSERT, (fresh, "digest-fresh", "fresh.mp4"))
    await temp_db.execute(_INSERT, (never, "digest-never", "never.mp4"))
    assert await _kept_agrees(temp_db) == 2

    await index_assets(temp_db, asset_id=fresh)
    await index_assets(temp_db, asset_id=fresh)
    assert await _kept_agrees(temp_db) == 1

    await temp_db.execute("DELETE FROM assets WHERE id = ?", (never,))
    assert await _kept_agrees(temp_db) == 0

    await temp_db.execute("DELETE FROM assets WHERE id = ?", (world.solo,))
    await index_assets(temp_db, asset_id=world.solo)
    assert await _kept_agrees(temp_db) == 0

    await index_assets(temp_db, rebuild=True)
    assert await _kept_agrees(temp_db) == 0
    assert await anything_unindexed(temp_db) is False


async def test_a_trigger_lost_with_its_table_is_put_back_and_the_count_redone(
    temp_db: Database, world: World
) -> None:
    """A table rebuilt the SQLite way drops its triggers; the boot invariant remakes them and counts
    afresh."""
    from sift.kernel.access.schema import _keep_the_unindexed_count

    await temp_db.execute("DROP TRIGGER search_unindexed_asset_in")
    await temp_db.execute(_INSERT, (new_id(), "digest-unseen", "unseen.mp4"))
    (kept,) = await temp_db.fetch_all(_KEPT)
    (counted,) = await temp_db.fetch_all(_COUNTED)
    assert int(kept["n"]) == int(counted["n"]) - 1

    async with temp_db.write() as connection:
        await _keep_the_unindexed_count(connection)
    await _kept_agrees(temp_db)
