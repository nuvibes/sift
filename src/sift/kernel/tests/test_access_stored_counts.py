# SPDX-License-Identifier: AGPL-3.0-or-later
"""The numbers read off the stored counts are the numbers the page's own statement counts: every
Filter column read that way, its Has and No rows, and a wall narrowed by one term, for every way a
vault can stand; an empty term answers immediately, and a question is counted once."""

from __future__ import annotations

import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
from dataclasses import replace
from typing import Any, cast

import pytest

from sift.kernel.access import (
    AllOf,
    AssetFilter,
    Concealment,
    Repository,
    Viewer,
    Where,
    visibility,
    visibility_panel,
)
from sift.kernel.access.repository import read_files
from sift.kernel.access.repository.asset_facets import FACET_PRESENCE, STORED_COLUMNS
from sift.kernel.access.repository.read_files import FileReads
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.tests.access_helpers import carry_the_song
from sift.testing.fixtures import Actors, World, hide

#: True of every file, and no stored count answers it: the same question asked the counted way.
_ALWAYS = Where("added_from", (0,))


def _counted_way(*terms: Where) -> AssetFilter:
    return AssetFilter(where=AllOf((*terms, _ALWAYS)))


def _ways(actors: Actors) -> list[Viewer]:
    """Each user with the vault shut, open, and shut behind placeholders."""
    return [
        one
        for base in (actors.admin, actors.guest)
        for one in (
            base,
            replace(base, show_hidden=True),
            replace(base, concealment=Concealment.PLACEHOLDER),
        )
    ]


async def _nothing_differs(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        found = await visibility.differences(connection)
    assert found == [], f"the stored counts disagree with the facts: {found[:5]}"


async def _columns_agree(access: Repository, actors: Actors) -> None:
    for viewer in _ways(actors):
        for hidden_only in (False, True):
            for facet in sorted({*STORED_COLUMNS, *FACET_PRESENCE}):
                stored = await access.facet_counts(
                    viewer, facet, hidden_only=hidden_only, limit=200
                )
                counted = await access.facet_counts(
                    viewer, facet, asset_filter=_counted_way(), hidden_only=hidden_only, limit=200
                )
                assert stored == counted, (facet, viewer.role, viewer.show_hidden, hidden_only)


async def test_every_stored_column_says_what_the_statement_counts(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await carry_the_song(temp_db, world)
    await _columns_agree(access, actors)
    # A name two folders share counts a file once; a renamed folder moves its files' name.
    await temp_db.execute(
        "UPDATE folders SET name = (SELECT name FROM folders WHERE id = ?) WHERE id = ?",
        (world.leaf, world.other),
    )
    await _nothing_differs(temp_db)
    await _columns_agree(access, actors)
    for kind, thing, who in (
        ("folder", world.other, actors.admin),
        ("tag", world.tag, actors.guest),
        ("asset", world.solo, actors.admin),
        ("person", world.person, actors.admin),
    ):
        await hide(temp_db, kind, thing, who.id)
        await _columns_agree(access, actors)
    await temp_db.execute("UPDATE folders SET name = 'elsewhere' WHERE id = ?", (world.leaf,))
    await _nothing_differs(temp_db)
    await _columns_agree(access, actors)


async def test_a_file_changing_kind_moves_the_media_column(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    before = {c.value: c.count for c in await access.facet_counts(actors.admin, "media")}
    await temp_db.execute("UPDATE assets SET media_type = 'gif' WHERE id = ?", (world.solo,))
    await _nothing_differs(temp_db)
    after = {c.value: c.count for c in await access.facet_counts(actors.admin, "media")}
    assert after.get("gif", 0) == before.get("gif", 0) + 1
    await _columns_agree(access, actors)


async def test_a_one_term_wall_totals_what_its_statement_counts(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    await carry_the_song(temp_db, world)
    await hide(temp_db, "asset", world.twin, actors.admin.id)
    terms = [
        Where("people", (world.person,)),
        Where("usernames", (world.username,)),
        Where("collections", (world.collection,)),
        Where("photo_sets", (world.photo_set,)),
        Where("songs", (world.song,)),
        Where("sites", (world.site,)),
        Where("tags", (world.tag,)),
        Where("media_type", ("video",)),
        Where("media_type", ("image",)),
    ]
    for viewer in _ways(actors):
        for hidden_only in (False, True):
            for term in terms:
                page = await access.visible_assets(
                    viewer, asset_filter=AssetFilter(where=AllOf((term,))), hidden_only=hidden_only
                )
                counted = await access.visible_assets(
                    viewer, asset_filter=_counted_way(term), hidden_only=hidden_only
                )
                assert (page.total, page.total_bytes) == (counted.total, counted.total_bytes)
                assert [i.asset.id for i in page.items] == [i.asset.id for i in counted.items]
            wall = await access.visible_assets(viewer, collection_id=world.collection)
            counted = await access.visible_assets(
                viewer, asset_filter=_counted_way(Where("collections", (world.collection,)))
            )
            assert (wall.total, [i.asset.id for i in wall.items]) == (
                counted.total,
                [i.asset.id for i in counted.items],
            )


class _Heard:
    """A plain connection that keeps every statement a read sends."""

    def __init__(self, path: Any) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.sent: list[str] = []

    async def fetch_all(self, statement: Any, params: Any = ()) -> list[Any]:
        sql = getattr(statement, "sql", statement)
        self.sent.append(sql)
        return self.connection.execute(sql, params).fetchall()

    async def fetch_one(self, statement: Any, params: Any = ()) -> Any:
        rows = await self.fetch_all(statement, params)
        return rows[0] if rows else None


async def test_an_empty_collection_answers_without_reading_a_file(
    actors: Actors, temp_db: Database
) -> None:
    empty = new_id()
    await temp_db.execute(
        "INSERT INTO collections (id, name, name_sort, created_at) VALUES (?, 'empty', 'empty', 0)",
        (empty,),
    )
    heard = _Heard(temp_db.path)
    try:
        reads = FileReads(cast(Any, heard), cast(Any, None))
        page = await reads.visible_assets(actors.admin, collection_id=empty)
        column = await reads.facet_counts(
            actors.admin, "media", asset_filter=AssetFilter(where=Where("collections", (empty,)))
        )
    finally:
        heard.connection.close()
    assert (page.total, page.items, column) == (0, [], [])
    assert not [sql for sql in heard.sent if "FROM assets a" in sql]


async def test_a_question_is_counted_once_while_nothing_is_announced(
    actors: Actors, world: World, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    mark = ["first"]
    monkeypatch.setattr(read_files, "current_mark", lambda: mark[0])
    heard = _Heard(temp_db.path)
    try:
        reads = FileReads(cast(Any, heard), cast(Any, None))
        asked = _counted_way()
        first = await reads.visible_assets(actors.admin, limit=1, asset_filter=asked)
        after = first.items[0].asset.id
        second = await reads.visible_assets(actors.admin, limit=1, asset_filter=asked, after=after)
        counts = [sql for sql in heard.sent if "COUNT(*) AS total_count" in sql]
        assert len(counts) == 1, "a continued page counted its question again"
        assert second.total == first.total
        mark[0] = "moved"
        await reads.visible_assets(actors.admin, limit=1, asset_filter=asked, after=after)
        counts = [sql for sql in heard.sent if "COUNT(*) AS total_count" in sql]
        assert len(counts) == 2, "an announcement since did not count it afresh"
    finally:
        heard.connection.close()


async def test_a_library_at_version_fifteen_is_given_the_panels_counts(
    temp_db: Database, world: World, actors: Actors, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The version 16 step: the panel's kinds counted from the stored rows, the triggers
    rewritten, nothing rebuilt, and run again it changes nothing."""
    await hide(temp_db, "asset", world.solo, actors.admin.id)
    async with temp_db.write() as connection:
        await visibility._drop_triggers(connection)
        for kind in visibility_panel.PANEL_KINDS:
            await connection.execute("DELETE FROM viewer_entity_counts WHERE kind = ?", (kind,))
    stored = await temp_db.fetch_all("SELECT * FROM viewer_assets ORDER BY user_id, asset_id")

    async def no_rebuild(_connection: object) -> None:
        raise AssertionError("the step rebuilt the stored answers")

    monkeypatch.setattr(visibility, "refresh_everything", no_rebuild)
    for _ in range(2):
        async with temp_db.write() as connection:
            await visibility.initialize(connection, 15)
    monkeypatch.undo()
    after = await temp_db.fetch_all("SELECT * FROM viewer_assets ORDER BY user_id, asset_id")
    assert [tuple(row) for row in after] == [tuple(row) for row in stored]
    present = await temp_db.fetch_all(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'vis_%'"
    )
    wanted = {name: visibility._body_of(ddl) for name, _t, ddl in visibility.triggers()}
    assert {str(r["name"]): visibility._body_of(str(r["sql"])) for r in present} == wanted
    await _nothing_differs(temp_db)
    kinds = await temp_db.fetch_all("SELECT DISTINCT kind FROM viewer_entity_counts")
    assert {"media", "place", "has_tags"} <= {str(row["kind"]) for row in kinds}


async def _sent(temp_db: Database, viewer: Viewer, asked: AssetFilter) -> list[str]:
    heard = _Heard(temp_db.path)
    try:
        await FileReads(cast(Any, heard), cast(Any, None)).visible_assets(
            viewer, asset_filter=asked
        )
    finally:
        heard.connection.close()
    return heard.sent


async def test_browses_own_question_reads_the_stored_total(
    actors: Actors, temp_db: Database
) -> None:
    """Browse always carries its resume setting, which narrows nothing."""
    sent = await _sent(temp_db, actors.admin, AssetFilter(resume_min_ms=60_000))
    assert not [sql for sql in sent if "COUNT(*) AS total_count" in sql]


async def test_a_narrowed_page_sorts_its_ids_before_reading_its_rows(
    actors: Actors, world: World, temp_db: Database
) -> None:
    """A page the planner may read from its members sorts their ids, then reads its own rows."""
    sent = await _sent(temp_db, actors.admin, _counted_way())
    assert [sql for sql in sent if "paged(paged_id)" in sql]


async def test_the_term_with_fewest_files_is_asked_first(
    actors: Actors, world: World, temp_db: Database
) -> None:
    """The planner reads the first term's members and tests the rest against them."""
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (world.twin, world.person)
    )
    asked = AssetFilter(
        where=AllOf((Where("people", (world.person,)), Where("collections", (world.collection,))))
    )
    counted = [sql for sql in await _sent(temp_db, actors.admin, asked) if "paged(paged_id)" in sql]
    assert counted
    assert counted[0].index("ci.collection_id IN") < counted[0].index("ap.person_id IN")


async def test_the_oldest_kept_total_goes_when_the_keep_is_full(
    actors: Actors, world: World, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Totals are kept for the most recent questions only: past the keep, the oldest is counted
    afresh when its next page is asked."""
    monkeypatch.setattr(read_files, "current_mark", lambda: "still")
    monkeypatch.setattr(read_files, "_TOTALS_KEPT", 1)
    heard = _Heard(temp_db.path)
    try:
        reads = FileReads(cast(Any, heard), cast(Any, None))
        asked, other = _counted_way(), _counted_way(Where("media_type", ("video",)))
        first = await reads.visible_assets(actors.admin, limit=1, asset_filter=asked)
        await reads.visible_assets(actors.admin, limit=1, asset_filter=other)
        after = first.items[0].asset.id
        again = await reads.visible_assets(actors.admin, limit=1, asset_filter=asked, after=after)
    finally:
        heard.connection.close()
    counts = [sql for sql in heard.sent if "COUNT(*) AS total_count" in sql]
    assert len(counts) == 3, "a total pushed out of the keep was read back"
    assert again.total == first.total


async def test_a_tag_or_collection_wall_reads_its_total_off_the_stored_count(
    access: Repository, actors: Actors, world: World, temp_db: Database
) -> None:
    for key, thing in (("tags", world.tag), ("collections", world.collection)):
        for viewer in _ways(actors):
            asked = _counted_way(Where(key, (thing,)))
            counted = await access.visible_assets(viewer, asset_filter=asked)
            heard = _Heard(temp_db.path)
            try:
                reads = FileReads(cast(Any, heard), cast(Any, None))
                if key == "tags":
                    page = await reads.visible_assets(viewer, tag_id=thing)
                else:
                    page = await reads.visible_assets(viewer, collection_id=thing)
            finally:
                heard.connection.close()
            assert page.total == counted.total, (key, viewer.role, viewer.show_hidden)
            assert not [sql for sql in heard.sent if "COUNT(*) AS total_count" in sql], key
