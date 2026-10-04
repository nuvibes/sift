# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stores that cannot be cascaded, and the promise that they are cleared anyway.

The mechanics are checked against fakes: one owner per table, a failing store does not stop the
others, an empty set asks nothing. The promise is checked against a real asset removed the way the
application removes one, with the index looked in afterwards: a declaration is only a claim.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest

# Imported for `workbench_decisions`, which this door writes to: `initialize_schema()` creates only
# what is registered.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import forgetting
from sift.kernel.access import Repository, index_assets
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.kernel.forgetting import (
    declared_tables,
    forget_everywhere,
    register_forgetting,
    registered_forgettings,
)
from sift.kernel.ledger import Actor, record_event
from sift.kernel.vocabulary import Subject
from sift.testing.fixtures import World

pytestmark = pytest.mark.unit


@pytest.fixture
def own_registry() -> Iterator[None]:
    """A registry this test can write into, restored afterwards: it is process-global."""
    saved = dict(forgetting._REGISTERED)
    try:
        yield None
    finally:
        forgetting._REGISTERED.clear()
        forgetting._REGISTERED.update(saved)


class Fake:
    """A store that records what it was asked to let go of."""

    def __init__(self, name: str, tables: tuple[str, ...], *, cleared: int = 1) -> None:
        self.name = name
        self.tables = tables
        self._cleared = cleared
        self.asked: list[tuple[str, ...]] = []

    def __call__(self, database: Database) -> Fake:
        """Its own builder: a registered builder is handed the handle and answers with the store."""
        return self

    async def forget(self, asset_ids: Sequence[str]) -> int:
        self.asked.append(tuple(asset_ids))
        return self._cleared


class Broken(Fake):
    async def forget(self, asset_ids: Sequence[str]) -> int:
        raise RuntimeError("the index is on a drive that is not there")


# --- the mechanics


def test_a_name_cannot_be_claimed_twice(own_registry: None) -> None:
    """A name cannot be claimed twice: two owners of one store's clearing is worse than none."""
    register_forgetting("one", ("first_table",), Fake("one", ("first_table",)))

    with pytest.raises(ValueError, match="already registered"):
        register_forgetting("one", ("second_table",), Fake("one", ("second_table",)))


def test_a_table_cannot_have_two_owners(own_registry: None) -> None:
    """A table cannot have two owners, or the gate reports it covered with neither reliable."""
    register_forgetting("first", ("shared_table",), Fake("first", ("shared_table",)))

    with pytest.raises(ValueError, match="already cleared by 'first'"):
        register_forgetting("second", ("shared_table",), Fake("second", ("shared_table",)))


def test_a_registration_naming_no_table_is_refused(own_registry: None) -> None:
    """A registration naming no table is a claim nothing can check."""
    with pytest.raises(ValueError, match="names no table"):
        register_forgetting("silent", (), Fake("silent", ()))


def test_what_is_declared_says_which_store_answers_for_it(own_registry: None) -> None:
    register_forgetting("store", ("a_table", "b_table"), Fake("store", ("a_table", "b_table")))

    declared = declared_tables()

    assert declared["a_table"] == "store"
    assert declared["b_table"] == "store"


def test_the_registry_is_handed_out_as_a_copy(own_registry: None) -> None:
    """The registry is handed out as a copy, so no caller can drop an entry from it."""
    register_forgetting("store", ("a_table",), Fake("store", ("a_table",)))

    borrowed = registered_forgettings()
    assert borrowed["store"].tables == ("a_table",)
    borrowed.clear()

    assert "store" in registered_forgettings()


async def test_every_store_is_asked_and_says_what_it_cleared(
    own_registry: None, temp_db: Database
) -> None:
    first = Fake("first", ("a_table",), cleared=3)
    second = Fake("second", ("b_table",), cleared=7)
    forgetting._REGISTERED.clear()
    register_forgetting("first", first.tables, first)
    register_forgetting("second", second.tables, second)

    cleared = await forget_everywhere(temp_db, ("gone-one", "gone-two"))

    assert cleared == {"first": 3, "second": 7}
    assert first.asked == [("gone-one", "gone-two")]
    assert second.asked == [("gone-one", "gone-two")]


async def test_a_store_that_fails_does_not_stop_the_others(
    own_registry: None, temp_db: Database
) -> None:
    """A failing store does not raise: the asset has already gone and the delete has committed."""
    working = Fake("working", ("a_table",), cleared=2)
    forgetting._REGISTERED.clear()
    register_forgetting("broken", ("b_table",), Broken("broken", ("b_table",)))
    register_forgetting("working", working.tables, working)

    cleared = await forget_everywhere(temp_db, ("gone",))

    assert cleared == {"working": 2}, "the one that failed is absent, not zero: it did not answer"
    assert working.asked == [("gone",)]


async def test_nothing_is_asked_about_an_empty_set(own_registry: None, temp_db: Database) -> None:
    """An empty set asks nothing: a store's sweep is an anti-join over its whole map."""
    store = Fake("store", ("a_table",))
    forgetting._REGISTERED.clear()
    register_forgetting("store", store.tables, store)

    assert await forget_everywhere(temp_db, ()) == {}
    assert store.asked == []


# --- the promise, against the real index


_FTS_IDS = "SELECT asset_id FROM assets_fts_rows ORDER BY asset_id"
_FTS_ROWS = "SELECT asset_id FROM assets_fts ORDER BY asset_id"


async def _indexed(database: Database) -> set[str]:
    return {str(row["asset_id"]) for row in await database.fetch_all(_FTS_IDS)}


async def _searchable(database: Database) -> set[str]:
    return {str(row["asset_id"]) for row in await database.fetch_all(_FTS_ROWS)}


async def test_a_deleted_file_stops_being_findable_by_name(
    temp_db: Database, content_store: ContentStore, world: World
) -> None:
    """A removed file leaves neither `assets_fts_rows` (the map later passes read) nor `assets_fts`
    (the index searched), asserted separately since they are two statements."""
    await index_assets(temp_db, rebuild=True)
    assert world.solo in await _indexed(temp_db)
    assert world.solo in await _searchable(temp_db)

    for location in await content_store.locations(world.solo):
        await content_store.remove_location(location.id)
    assert await content_store.remove_asset_if_unplaced(world.solo)

    assert world.solo not in await _indexed(temp_db)
    assert world.solo not in await _searchable(temp_db)


async def test_a_file_that_only_lost_one_of_its_places_keeps_its_entry(
    temp_db: Database, content_store: ContentStore, world: World
) -> None:
    """`twin` losing one of its two folders stays indexed: only the last copy ending ends it."""
    await index_assets(temp_db, rebuild=True)
    places = await content_store.locations(world.twin)
    assert len(places) == 2

    await content_store.remove_location(places[0].id)
    assert not await content_store.remove_asset_if_unplaced(world.twin)

    assert world.twin in await _indexed(temp_db)
    assert world.twin in await _searchable(temp_db)


async def test_the_application_declares_both_halves_of_the_search_index() -> None:
    """The search index's store claims both its tables."""
    import sift.main  # noqa: F401 (imported for its side effect: the stores register)

    declared = declared_tables()

    assert declared["assets_fts"] == "search-index"
    assert declared["assets_fts_rows"] == "search-index"
    assert declared["semantic_frames"] == "vector-index"


# --- the ledger: deleting a file leaves every event that named it


async def _events(database: Database) -> list[str]:
    rows = await database.fetch_all("SELECT id FROM workbench_decisions ORDER BY id")
    return [str(row["id"]) for row in rows]


async def _write_event(database: Database, **named: object) -> str:
    async with database.write() as connection:
        return await record_event(connection, **named)  # type: ignore[arg-type]


async def test_deleting_a_file_leaves_its_events_exactly_where_they_are(
    temp_db: Database, access: Repository, world: World
) -> None:
    """Events outlive their subjects: the subject link carries no key, so the record of what was
    removed and when survives."""
    event = await _write_event(
        temp_db,
        actor=Actor.sift("folder"),
        verb="deleted",
        subject=Subject(kind="asset", id=world.solo, name="beach-walk.mp4"),
    )

    await temp_db.execute("DELETE FROM assets WHERE id = ?", (world.solo,))

    assert await _events(temp_db) == [event]
